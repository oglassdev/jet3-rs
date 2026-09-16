//! EXP-0273/0279 reciprocal records and relationship index selection.
use super::*;
use crate::RelationshipSide;
use crate::creation::relationship_indexes::select_existing;
use crate::creation::relationship_name::HiddenName;
use crate::{
    ColumnRef, IndexColumnSpec, IndexKind, IndexSpec, RelationshipSpec, TableRef, TableRows,
};

pub(super) struct GraphRelation<'a> {
    pub name: &'a [u8],
    pub parent: usize,
    pub child: usize,
    pub parent_column: u16,
    pub child_column: u16,
    pub physical: u16,
    pub parent_physical: u16,
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
        let parent = table(relationship.parent.table).ok_or(invalid("parent table reference"))?;
        let child = table(relationship.child.table).ok_or(invalid("child table reference"))?;
        let parent_table = &requests[parent].table;
        let child_table = &requests[child].table;
        let parent_column = relationship
            .parent
            .column
            .resolve(parent_table.columns)
            .ok_or(invalid("parent column reference"))?;
        let child_column = relationship
            .child
            .column
            .resolve(child_table.columns)
            .ok_or(invalid("child column reference"))?;
        // EXP-0239/0270: AutoIncrement keys use the same physical Long encoding.
        if !matches!(
            parent_table.columns[usize::from(parent_column)].column_type(),
            ColumnType::Long | ColumnType::AutoIncrement
        ) || child_table.columns[usize::from(child_column)].column_type() != ColumnType::Long
        {
            return Err(invalid(
                "relationship requires a Long/AutoIncrement parent and Long child",
            ));
        }
        let parent_physical = select_existing(
            parent_table,
            parent_column,
            RelationshipSide::PrimaryTable,
            budget,
        )?
        .ok_or(invalid("parent requires an ascending unique Long index"))?;
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
            child_column,
            RelationshipSide::ForeignTable,
            budget,
        )?;
        let physical = if let Some(prior) = result
            .iter()
            .find(|edge| edge.child == child && edge.child_column == child_column)
        {
            prior.physical
        } else if let Some(ordinal) = existing_foreign {
            ordinal
        } else {
            let ordinal = physical_counts[child];
            physical_counts[child] += 1;
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
                parent_column,
                child_column,
                physical,
                parent_physical,
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
    pub fn field(&self) -> [IndexColumnSpec<'static>; 1] {
        [IndexColumnSpec {
            column: ColumnRef::Ordinal(self.child_column),
            direction: IndexDirection::Ascending,
        }]
    }
    pub fn foreign_index<'a>(&'a self, fields: &'a [IndexColumnSpec<'a>]) -> IndexSpec<'a> {
        IndexSpec {
            name: self.name,
            fields,
            kind: IndexKind::Ordinary,
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
