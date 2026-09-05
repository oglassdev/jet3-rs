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
    /// Index name; bytes must be at most `0x7E`.
    pub name: &'a [u8],
    /// Ordered key columns.
    pub fields: &'a [IndexColumnSpec<'a>],
    /// The index's uniqueness class.
    pub kind: IndexKind,
}

/// One user table to create.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TableSpec<'a> {
    /// Table name; bytes must be at most `0x7E`.
    pub name: &'a [u8],
    /// The table's columns in ordinal order.
    pub columns: &'a [ColumnSpec<'a>],
    /// The table's indexes in physical (append) order; at most three.
    pub indexes: &'a [IndexSpec<'a>],
}

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
