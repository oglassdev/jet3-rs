//! EXP-0273/0279/0286/0290 reciprocal records and relationship index selection.
use super::*;
use crate::RelationshipSide;
use crate::creation::relationship_indexes::{select_descending_parent, select_existing};
use crate::creation::relationship_name::HiddenName;
use crate::numeric_index_key::NumericKeyType;
use crate::{
    ColumnRef, IndexColumnSpec, IndexKind, IndexSpec, RelationshipSpec, TableRef, TableRows,
};

pub(super) struct GraphRelation<'a> {
    pub name: &'a [u8],
    pub parent: usize,
    pub child: usize,
    pub parent_columns: Vec<u16>,
    pub child_columns: Vec<u16>,
    pub child_kinds: Vec<NumericKeyType>,
    pub physical: u16,
    pub parent_physical: u16,
    pub parent_kind: IndexKind,
    parent_id: u32,
    child_id: u32,
    hidden: HiddenName,
}

pub(super) fn invalid(detail: &'static str) -> ComposeError {
    ComposeError::UnsupportedRelationship { detail }
}

pub(super) fn push<T>(
    items: &mut Vec<T>,
    value: T,
    budget: &mut ResourceBudget,
) -> Result<(), ComposeError> {
    crate::resource::reserve(items, 1, budget)?;
    items.push(value);
    Ok(())
}

pub(super) fn resolve<'a>(
    requests: &[TableRows<'_>],
    relationships: &'a [RelationshipSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<Vec<GraphRelation<'a>>, ComposeError> {
    budget.charge_work_units(requests.len() as u64)?;
    if requests
        .iter()
        .any(|r| r.table.columns.len() > 255 || r.table.indexes.len() > 32)
    {
        return Err(invalid("graph table column or index count"));
    }
    let mut logical_counts = Vec::new();
    let mut physical_counts = Vec::new();
    for (position, request) in requests.iter().enumerate() {
        budget.charge_work_units(request.table.columns.len() as u64)?;
        if request.table.name.len() > 64
            || request
                .table
                .columns
                .iter()
                .any(|column| column.name().len() > 64)
        {
            return Err(invalid("graph table or column name exceeds 64 bytes"));
        }
        budget.charge_work_units((position as u64).saturating_mul(512))?;
        if let Some(first) = requests[..position]
            .iter()
            .position(|earlier| catalog_names_equal(earlier.table.name, request.table.name))
        {
            return Err(ComposeError::DuplicateTableName {
                first,
                second: position,
            });
        }
        push(
            &mut logical_counts,
            request.table.indexes.len() as u32,
            budget,
        )?;
        push(
            &mut physical_counts,
            request.table.indexes.len() as u16,
            budget,
        )?;
    }
    let mut result: Vec<GraphRelation<'a>> = Vec::new();
    for (position, relationship) in relationships.iter().enumerate() {
        budget.charge_work_units((position as u64).saturating_mul(513))?;
        budget.charge_work_units(
            (requests.len() as u64).saturating_mul(128) + 2 * 255 * 64 + 32 * 512 + 1024,
        )?;
        let mut key = [0; CATALOG_KEY_CAPACITY];
        encode_catalog_name_key(RELATIONSHIPS_ID, relationship.name, &mut key)?;
        if relationship.name.len() > 63
            || relationships[..position]
                .iter()
                .any(|prior| catalog_names_equal(prior.name, relationship.name))
        {
            return Err(invalid(
                "relationship names must be distinct and at most 63 bytes",
            ));
        }
        let table = |reference| match reference {
            TableRef::Ordinal(ordinal) => (ordinal < requests.len()).then_some(ordinal),
            TableRef::Name(name) => requests.iter().position(|r| r.table.name == name),
        };
        let parent = table(relationship.parent).ok_or(invalid("parent table reference"))?;
        let child = table(relationship.child).ok_or(invalid("child table reference"))?;
        let parent_table = &requests[parent].table;
        let child_table = &requests[child].table;
        if !(1..=crate::numeric_index_entry::MAX_FIELDS).contains(&relationship.fields.len()) {
            return Err(invalid("relationship requires one to ten fields"));
        }
        budget.charge_work_units((relationship.fields.len() as u64 - 1) * 2 * 255 * 64)?;
        let mut parent_columns = Vec::new();
        let mut child_columns = Vec::new();
        let mut child_kinds = Vec::new();
        for field in relationship.fields {
            let parent_column = field
                .parent
                .resolve(parent_table.columns)
                .ok_or(invalid("parent column reference"))?;
            let child_column = field
                .child
                .resolve(child_table.columns)
                .ok_or(invalid("child column reference"))?;
            if parent_columns.contains(&parent_column) || child_columns.contains(&child_column) {
                return Err(invalid("relationship repeats a column"));
            }
            let parent_type = parent_table.columns[usize::from(parent_column)].column_type();
            let child_type = child_table.columns[usize::from(child_column)].column_type();
            let parent_kind = NumericKeyType::from_column(parent_type)
                .ok_or(invalid("relationship parent key type"))?;
            let child_kind = NumericKeyType::from_column(child_type)
                .ok_or(invalid("relationship child key type"))?;
            if child_type == ColumnType::AutoIncrement
                || !crate::relationship_key::compatible(parent_kind, child_kind)
            {
                return Err(invalid("relationship requires compatible scalar key types"));
            }
            push(&mut parent_columns, parent_column, budget)?;
            push(&mut child_columns, child_column, budget)?;
            push(&mut child_kinds, child_kind, budget)?;
        }
        // EXP-0292: a partially overlapping self key is allowed, an identical key is not.
        if parent == child && parent_columns == child_columns {
            return Err(invalid("self relationship maps the whole key to itself"));
        }
        let parent_physical = select_existing(
            parent_table,
            &parent_columns,
            RelationshipSide::PrimaryTable,
            budget,
        )?;
        let descending_parent = if parent_physical.is_none() {
            select_descending_parent(parent_table, &parent_columns, budget)?
        } else {
            None
        };
        let source_parent = parent_physical.or(descending_parent).ok_or(invalid(
            "parent requires a unique index in relationship field order",
        ))?;
        let parent_kind = IndexKind::Unique.with_null_policy(
            parent_table.indexes[usize::from(source_parent)]
                .kind
                .null_policy(),
        );
        if child_table
            .indexes
            .iter()
            .any(|index| catalog_names_equal(index.name, relationship.name))
        {
            return Err(invalid(
                "relationship name collides with a declared child index",
            ));
        }
        let existing_foreign = select_existing(
            child_table,
            &child_columns,
            RelationshipSide::ForeignTable,
            budget,
        )?;
        let physical = if let Some(prior) = result
            .iter()
            .find(|edge| edge.child == child && edge.child_columns == child_columns)
        {
            prior.physical
        } else if let Some(ordinal) = existing_foreign {
            ordinal
        } else {
            let ordinal = physical_counts[child];
            physical_counts[child] += 1;
            ordinal
        };
        // EXP-0286: self-references allocate the child tree before the parent tree.
        let parent_physical = if let Some(ordinal) = parent_physical {
            ordinal
        } else if let Some(prior) = result
            .iter()
            .find(|edge| edge.parent == parent && edge.parent_columns == parent_columns)
        {
            prior.parent_physical
        } else {
            let ordinal = physical_counts[parent];
            physical_counts[parent] += 1;
            ordinal
        };
        // A self-reference adds its foreign record before its primary-side record.
        let child_id = logical_counts[child];
        if child_id as usize >= crate::creation::schema_plan::MAX_OBSERVED_INDEXES {
            return Err(ComposeError::Schema(
                crate::creation::schema_plan::TableSchemaPlanError::UnobservedIndexCount {
                    count: child_id as usize + 1,
                    observed: crate::creation::schema_plan::MAX_OBSERVED_INDEXES,
                },
            ));
        }
        logical_counts[child] += 1;
        let parent_id = logical_counts[parent];
        logical_counts[parent] += 1;
        let hidden = HiddenName::for_selector(parent_id).ok_or(ComposeError::Schema(
            crate::creation::schema_plan::TableSchemaPlanError::UnobservedIndexCount {
                count: parent_id as usize + 1,
                observed: crate::creation::schema_plan::MAX_OBSERVED_INDEXES,
            },
        ))?;
        push(
            &mut result,
            GraphRelation {
                name: relationship.name,
                parent,
                child,
                parent_columns,
                child_columns,
                child_kinds,
                physical,
                parent_physical,
                parent_kind,
                parent_id,
                child_id,
                hidden,
            },
            budget,
        )?;
    }
    Ok(result)
}

impl GraphRelation<'_> {
    pub fn fields(
        &self,
        parent: bool,
        budget: &mut ResourceBudget,
    ) -> Result<Vec<IndexColumnSpec<'static>>, ComposeError> {
        let columns = if parent {
            &self.parent_columns
        } else {
            &self.child_columns
        };
        let mut fields = Vec::new();
        for &column in columns {
            push(
                &mut fields,
                IndexColumnSpec {
                    column: ColumnRef::Ordinal(column),
                    direction: IndexDirection::Ascending,
                },
                budget,
            )?;
        }
        Ok(fields)
    }
    pub fn foreign_index<'a>(&'a self, fields: &'a [IndexColumnSpec<'a>]) -> IndexSpec<'a> {
        IndexSpec {
            name: self.name,
            fields,
            kind: IndexKind::Ordinary,
        }
    }
    pub fn parent_index<'a>(&'a self, fields: &'a [IndexColumnSpec<'a>]) -> IndexSpec<'a> {
        IndexSpec {
            name: self.hidden.bytes(),
            fields,
            kind: self.parent_kind,
        }
    }
    pub fn logical(&self, parent: bool) -> LogicalIndexSpec<'_> {
        LogicalIndexSpec {
            name: if parent {
                self.hidden.bytes()
            } else {
                self.name
            },
            physical_index: if parent {
                self.parent_physical
            } else {
                self.physical
            },
            kind: LogicalIndexKindSpec::Relationship {
                side: if parent {
                    crate::RelationshipSide::PrimaryTable
                } else {
                    crate::RelationshipSide::ForeignTable
                },
                related_table: PageNumber::new(if parent { self.child } else { self.parent } as u64),
                raw_selector: if parent {
                    self.parent_id
                } else {
                    self.child_id
                },
                relation_ordinal: if parent {
                    self.child_id
                } else {
                    self.parent_id
                },
                cascade_updates: false,
                cascade_deletes: false,
            },
        }
    }
}
