//! Table, index and relationship requests for fresh database creation.

use super::IndexKind;
use crate::{ColumnSpec, IndexDirection};

/// A reference to one column of a [`TableSpec`], by position or by name.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ColumnRef<'a> {
    /// The column at this zero-based position in [`TableSpec::columns`].
    Ordinal(u16),
    /// The column whose raw name bytes equal these.
    Name(&'a [u8]),
}

impl ColumnRef<'_> {
    /// Returns the ordinal this reference names among `columns`, if any.
    pub(crate) fn resolve(self, columns: &[ColumnSpec<'_>]) -> Option<u16> {
        match self {
            Self::Ordinal(ordinal) => (usize::from(ordinal) < columns.len()).then_some(ordinal),
            Self::Name(name) => columns
                .iter()
                .position(|column| column.name() == name)
                .and_then(|position| u16::try_from(position).ok()),
        }
    }
}

impl From<u16> for ColumnRef<'_> {
    fn from(ordinal: u16) -> Self {
        Self::Ordinal(ordinal)
    }
}

impl<'a> From<&'a [u8]> for ColumnRef<'a> {
    fn from(name: &'a [u8]) -> Self {
        Self::Name(name)
    }
}

impl<'a, const N: usize> From<&'a [u8; N]> for ColumnRef<'a> {
    fn from(name: &'a [u8; N]) -> Self {
        Self::Name(name)
    }
}

/// One key column of an [`IndexSpec`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct IndexColumnSpec<'a> {
    /// The table column the key uses.
    pub column: ColumnRef<'a>,
    /// Key direction.
    pub direction: IndexDirection,
}

impl<'a> IndexColumnSpec<'a> {
    /// Describes an ascending key on `column`.
    #[must_use]
    pub fn ascending(column: impl Into<ColumnRef<'a>>) -> Self {
        Self {
            column: column.into(),
            direction: IndexDirection::Ascending,
        }
    }

    /// Describes a descending key on `column`.
    #[must_use]
    pub fn descending(column: impl Into<ColumnRef<'a>>) -> Self {
        Self {
            column: column.into(),
            direction: IndexDirection::Descending,
        }
    }
}

/// One index of a [`TableSpec`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct IndexSpec<'a> {
    /// Index name encoded in Windows-1252, at most 63 bytes.
    pub name: &'a [u8],
    /// Ordered key columns.
    pub fields: &'a [IndexColumnSpec<'a>],
    /// The index's uniqueness class.
    pub kind: IndexKind,
}

/// Table-level validation stored in the table's EXP-0299 property block.
///
/// Values are opaque database-code-page bytes and are never evaluated. A
/// stored nonempty rule makes Rust refuse inserts and updates on the table.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct TableValidation<'a> {
    /// ValidationRule expression, subject to the limits of
    /// [`crate::ColumnSpec::with_default_value`].
    pub rule: Option<&'a [u8]>,
    /// ValidationText message.
    pub text: Option<&'a [u8]>,
}

impl TableValidation<'_> {
    /// No table-level validation properties.
    pub const NONE: Self = Self {
        rule: None,
        text: None,
    };
}

/// One user table to create.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TableSpec<'a> {
    /// Table name encoded in Windows-1252, at most 64 bytes.
    pub name: &'a [u8],
    /// The table's columns in ordinal order.
    pub columns: &'a [ColumnSpec<'a>],
    /// The table's indexes in physical (append) order; at most 32.
    pub indexes: &'a [IndexSpec<'a>],
    /// Table ValidationRule and ValidationText.
    pub validation: TableValidation<'a>,
}

/// A table in the ordered input to the relationship creation APIs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TableRef<'a> {
    /// Zero-based position in the supplied table slice.
    Ordinal(usize),
    /// Exact database-encoded table name; matching is case-sensitive.
    Name(&'a [u8]),
}

/// One ordered pair of matching relationship key columns.
#[derive(Debug, Clone, Copy)]
pub struct RelationshipField<'a> {
    /// Column in the referenced table.
    pub parent: ColumnRef<'a>,
    /// Matching column in the referencing table.
    pub child: ColumnRef<'a>,
}

/// Access's default join type for a relationship (SRC-0026).
///
/// Stored only in the relationship's attributes (EXP-0301); it is display
/// metadata and never affects referential integrity.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum RelationshipJoin {
    /// No join attribute: Access displays an inner join.
    #[default]
    Inner,
    /// `dbRelationLeft`: include all parent rows.
    Left,
    /// `dbRelationRight`: include all child rows.
    Right,
    /// Both join attributes, which DAO accepts and stores unchanged.
    LeftAndRight,
}

/// One relationship between two tables.
///
/// Names are database-encoded bytes, subject to the bounded creation name
/// encoder. See [`crate::create_database_with_relationships`] for scalar graph
/// support and [`crate::create_database_with_relationship`] for singular API limits.
/// Unenforced relationships are created through [`crate::SchemaEdit`].
///
/// ```
/// use jet3::{ColumnRef, RelationshipField, RelationshipJoin, RelationshipSpec, TableRef};
///
/// let relationship = RelationshipSpec {
///     enforce: true,
///     join: RelationshipJoin::Inner,
///     cascade_updates: false,
///     cascade_deletes: false,
///     name: b"AccountsEvents",
///     parent: TableRef::Name(b"Accounts"),
///     child: TableRef::Name(b"Events"),
///     fields: &[RelationshipField {
///         parent: ColumnRef::Name(b"Id"),
///         child: ColumnRef::Name(b"AccountId"),
///     }],
/// };
/// ```
#[derive(Debug, Clone, Copy)]
pub struct RelationshipSpec<'a> {
    /// Check referential integrity. Unenforced relationships (EXP-0301) have no
    /// indexes, cascades or key checks; their keys may be any columns.
    pub enforce: bool,
    /// Default join type shown by Access; ignored for integrity.
    pub join: RelationshipJoin,
    /// Update matching foreign keys when a parent key is assigned.
    pub cascade_updates: bool,
    /// Delete matching child rows when a parent row is deleted.
    pub cascade_deletes: bool,
    /// Caller-chosen relationship and child foreign-index name.
    pub name: &'a [u8],
    /// Referenced table.
    pub parent: TableRef<'a>,
    /// Referencing table.
    pub child: TableRef<'a>,
    /// Ordered key column pairs, matching the parent unique index's order.
    pub fields: &'a [RelationshipField<'a>],
}
