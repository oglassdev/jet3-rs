//! Typed, bounded inputs for relationship composition. Schema and
//! placement use the existing EXP-0059/0087/0093 planners; only the two hidden
//! selector/name cases recorded by EXP-0059 and EXP-0114 are admitted.

use super::*;
use crate::ColumnRef;
use crate::table_schema_plan::{TableSchemaPlan, plan_table_schema};

/// A table in the ordered input to [`crate::create_database_with_relationship`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TableRef<'a> {
    /// Zero-based position in the supplied table slice.
    Ordinal(usize),
    /// Exact database-encoded table name; matching is case-sensitive.
    Name(&'a [u8]),
}

/// A table and column forming one endpoint of a relationship.
#[derive(Debug, Clone, Copy)]
pub struct RelationshipColumn<'a> {
    /// Table containing the column.
    pub table: TableRef<'a>,
    /// Column name or ordinal within that table.
    pub column: ColumnRef<'a>,
}

/// One non-cascading relationship between two tables.
///
/// Names are database-encoded bytes, subject to the bounded creation name
/// encoder. See [`crate::create_database_with_relationship`] for schema limits.
///
/// ```
/// use jet3::{ColumnRef, RelationshipColumn, RelationshipSpec, TableRef};
///
/// let relationship = RelationshipSpec {
///     name: b"AccountsEvents",
///     parent: RelationshipColumn {
///         table: TableRef::Name(b"Accounts"),
///         column: ColumnRef::Name(b"Id"),
///     },
///     child: RelationshipColumn {
///         table: TableRef::Name(b"Events"),
///         column: ColumnRef::Name(b"AccountId"),
///     },
/// };
/// ```
#[derive(Debug, Clone, Copy)]
pub struct RelationshipSpec<'a> {
    /// Caller-chosen relationship and child foreign-index name.
    pub name: &'a [u8],
    /// Referenced primary-key column in the first table.
    pub parent: RelationshipColumn<'a>,
    /// Referencing Long column in the second table.
    pub child: RelationshipColumn<'a>,
}

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
        if parent.continuation_page().is_some() || child.continuation_page().is_some() {
            return Err(invalid("definition continuations are unsupported"));
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
        if tables.iter().flat_map(|table| table.columns).any(|column| {
            column.column_type().is_long_value()
                || column.column_type() == ColumnType::AutoIncrement
        }) {
            return Err(invalid(
                "AutoIncrement and long-value columns are unsupported",
            ));
        }
        if !tables[1].indexes.is_empty() {
            return Err(invalid("child must initially be unindexed"));
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
    pub(super) fn index_page(&self) -> u64 {
        self.data_page() + 1
    }
}
