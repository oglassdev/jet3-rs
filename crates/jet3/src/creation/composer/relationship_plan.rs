//! Typed, bounded inputs for relationship composition. Schema and
//! placement use the existing EXP-0059/0087/0093 planners; only the two hidden
//! selector/name cases recorded by EXP-0059 and EXP-0114 are admitted.

use super::*;
use crate::creation::schema_plan::{TableSchemaPlan, plan_table_schema};
use crate::{RelationshipSpec, TableRef};

pub(super) struct RelationshipPlan<'a> {
    pub(super) tables: &'a [TableSpec<'a>],
    pub(super) spec: &'a RelationshipSpec<'a>,
    pub(super) parent: TableSchemaPlan,
    pub(super) child: TableSchemaPlan,
    pub(super) parent_column: u16,
    pub(super) child_column: u16,
    pub(super) hidden_name: &'static [u8],
    pub(super) selector: u32,
    pub(super) end_of_tables: u64,
}

fn invalid(detail: &'static str) -> ComposeError {
    ComposeError::UnsupportedRelationship { detail }
}

impl<'a> RelationshipPlan<'a> {
    pub(super) fn new(
        tables: &'a [TableSpec<'a>],
        spec: &'a RelationshipSpec<'a>,
    ) -> Result<Self, ComposeError> {
        if tables.len() != 2 {
            return Err(invalid("exactly two tables required"));
        }
        // EXP-0060 bounds the one-byte column count before reference scans.
        if tables
            .iter()
            .any(|table| table.columns.len() > u8::MAX as usize)
        {
            return Err(invalid("at most 255 columns per table"));
        }
        let mut key = [0_u8; INDEX_KEY_CAPACITY];
        encode_catalog_name_key(RELATIONSHIPS_ID, spec.name, &mut key)?;
        let parent = plan_table_schema(&tables[0], EMPTY_DATABASE_PAGE_COUNT, true)?;
        let child_root = parent.definition_root().get() + parent.appended_page_count();
        let child = plan_table_schema(&tables[1], child_root, false)?;
        if tables[0].name.eq_ignore_ascii_case(tables[1].name) {
            return Err(ComposeError::DuplicateTableName {
                first: 0,
                second: 1,
            });
        }
        let resolve_table = |reference| match reference {
            TableRef::Ordinal(position) => (position < tables.len()).then_some(position),
            TableRef::Name(name) => tables.iter().position(|table| table.name == name),
        };
        if resolve_table(spec.parent.table) != Some(0) || resolve_table(spec.child.table) != Some(1)
        {
            return Err(invalid(
                "parent must reference first table and child second table",
            ));
        }
        let parent_column = spec
            .parent
            .column
            .resolve(tables[0].columns)
            .ok_or(invalid("parent column reference"))?;
        let child_column = spec
            .child
            .column
            .resolve(tables[1].columns)
            .ok_or(invalid("child column reference"))?;
        if tables[0].columns[usize::from(parent_column)].column_type() != ColumnType::Long
            || tables[1].columns[usize::from(child_column)].column_type() != ColumnType::Long
        {
            return Err(invalid("relationship columns must both be Long"));
        }
        if tables[1].indexes.len() > 1
            || tables[1].indexes.first().is_some_and(|index| {
                index.kind != IndexKind::Primary
                    || index.fields.len() != 1
                    || index.fields[0].direction != IndexDirection::Ascending
                    || index.fields[0]
                        .column
                        .resolve(tables[1].columns)
                        .is_none_or(|ordinal| {
                            ordinal == child_column
                                || !matches!(
                                    tables[1].columns[usize::from(ordinal)].column_type(),
                                    ColumnType::Long | ColumnType::AutoIncrement
                                )
                        })
            })
        {
            return Err(invalid(
                "child admits one separate ascending Long primary index",
            ));
        }
        let (hidden_name, selector) = match tables[0].indexes.len() {
            1 => (b".rB".as_slice(), 1), // EXP-0059.
            2 => (b".rC".as_slice(), 2), // EXP-0114.
            _ => {
                return Err(invalid(
                    "parent needs one primary and at most one additional unique index",
                ));
            }
        };
        let primary = &tables[0].indexes[0];
        if primary.kind != IndexKind::Primary
            || primary.fields.len() != 1
            || primary.fields[0].column.resolve(tables[0].columns) != Some(parent_column)
            || primary.fields[0].direction != IndexDirection::Ascending
        {
            return Err(invalid(
                "first parent index must be ascending primary on the referenced column",
            ));
        }
        if let Some(extra) = tables[0].indexes.get(1)
            && (extra.kind != IndexKind::Unique
                || extra.fields.len() != 1
                || extra.fields[0].direction != IndexDirection::Ascending
                || extra.fields[0]
                    .column
                    .resolve(tables[0].columns)
                    .is_none_or(|ordinal| {
                        tables[0].columns[usize::from(ordinal)].column_type() != ColumnType::Long
                    }))
        {
            return Err(invalid(
                "additional parent index must be ascending unique on one Long column",
            ));
        }
        let end_of_tables = child.definition_root().get() + child.appended_page_count();
        Ok(Self {
            tables,
            spec,
            parent,
            child,
            parent_column,
            child_column,
            hidden_name,
            selector,
            end_of_tables,
        })
    }

    pub(super) fn data_page(&self) -> u64 {
        self.end_of_tables
    }
    /// EXP-0059/0114/0268: each selector matches the opposite relation ordinal.
    pub(super) fn logical(&self, parent: bool, target: PageNumber) -> LogicalIndexSpec<'a> {
        LogicalIndexSpec {
            name: if parent {
                self.hidden_name
            } else {
                self.spec.name
            },
            physical_index: if parent {
                0
            } else {
                self.tables[1].indexes.len() as u16
            },
            kind: LogicalIndexKindSpec::Relationship {
                side: if parent {
                    crate::RelationshipSide::PrimaryTable
                } else {
                    crate::RelationshipSide::ForeignTable
                },
                related_table: target,
                raw_selector: if parent {
                    self.selector
                } else {
                    self.tables[1].indexes.len() as u32
                },
                relation_ordinal: if parent {
                    self.tables[1].indexes.len() as u32
                } else {
                    self.selector
                },
                cascade_updates: false,
                cascade_deletes: false,
            },
        }
    }
}
