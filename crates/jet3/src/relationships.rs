//! Allocation-free relationship inventory over a decoded table definition,
//! based on `EXP-0059` records and `EXP-0062` option isolation.

use std::iter::FusedIterator;
use std::slice;

use crate::{
    DatabaseReader, DefinitionName, IndexDefinition, IndexDefinitionKind, PageNumber, ReadAt,
    RelationshipJoin, RelationshipReference, RelationshipSide, RelationshipValidationError,
    ResourceBudget, TableDefinition,
};

/// One logical relationship index and its lossless sourced metadata.
#[derive(Debug, Clone, Copy)]
pub struct Relationship<'a> {
    index: &'a IndexDefinition,
    reference: RelationshipReference,
}

impl<'a> Relationship<'a> {
    /// Returns the relationship's raw database-encoded logical name.
    #[must_use]
    pub const fn name(self) -> &'a DefinitionName {
        self.index.name()
    }

    /// Returns the referenced physical-index ordinal on this table.
    #[must_use]
    pub const fn physical_index(self) -> u16 {
        self.index.physical_index()
    }

    /// Returns whether this record belongs to the primary or foreign table.
    #[must_use]
    pub const fn side(self) -> RelationshipSide {
        self.reference.side()
    }

    /// Returns the related table-definition page.
    #[must_use]
    pub const fn related_table(self) -> PageNumber {
        self.reference.related_table()
    }

    /// Returns the first sourced relationship selector.
    #[must_use]
    pub const fn raw_selector(self) -> u32 {
        self.reference.raw_selector()
    }

    /// Returns the sourced relationship ordinal.
    #[must_use]
    pub const fn raw_relation_ordinal(self) -> u32 {
        self.reference.raw_relation_ordinal()
    }

    /// Returns the two sourced cascade-option bytes.
    #[must_use]
    pub const fn raw_context(self) -> [u8; 2] {
        self.reference.raw_context()
    }

    /// Returns whether DAO requested cascade updates.
    #[must_use]
    pub const fn cascade_updates(self) -> bool {
        self.reference.cascade_updates()
    }

    /// Returns whether DAO requested cascade deletes.
    #[must_use]
    pub const fn cascade_deletes(self) -> bool {
        self.reference.cascade_deletes()
    }

    /// Returns the complete sourced 20-byte logical-index record.
    #[must_use]
    pub const fn raw_record(self) -> &'a [u8; 20] {
        self.index.raw_record()
    }
}

/// Allocation-free iterator over relationship logical indexes.
#[derive(Debug, Clone)]
pub struct Relationships<'a> {
    indexes: slice::Iter<'a, IndexDefinition>,
}

impl<'a> Iterator for Relationships<'a> {
    type Item = Relationship<'a>;

    fn next(&mut self) -> Option<Self::Item> {
        self.indexes.find_map(|index| match index.kind() {
            IndexDefinitionKind::Relationship(reference) => Some(Relationship { index, reference }),
            IndexDefinitionKind::Ordinary | IndexDefinitionKind::Primary => None,
        })
    }

    fn size_hint(&self) -> (usize, Option<usize>) {
        (0, Some(self.indexes.len()))
    }
}

impl FusedIterator for Relationships<'_> {}

impl TableDefinition {
    /// Returns the table's logical relationship indexes without allocating.
    #[must_use]
    pub fn relationships(&self) -> Relationships<'_> {
        Relationships {
            indexes: self.indexes().iter(),
        }
    }
}

/// One `MSysRelationships` relationship, including forms without index records.
///
/// Attribute accessors decode the SRC-0026 `RelationAttributeEnum` bits;
/// EXP-0301 records how Jet 3 stores each one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogRelationship {
    pub(crate) name: Vec<u8>,
    pub(crate) parent: Vec<u8>,
    pub(crate) child: Vec<u8>,
    pub(crate) fields: Vec<CatalogRelationshipField>,
    pub(crate) raw_attributes: i32,
}

/// One ordered key-column pair of a [`CatalogRelationship`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogRelationshipField {
    pub(crate) parent: Vec<u8>,
    pub(crate) child: Vec<u8>,
}

impl CatalogRelationshipField {
    /// Returns the raw database-encoded referenced column name.
    #[must_use]
    pub fn parent(&self) -> &[u8] {
        &self.parent
    }

    /// Returns the raw database-encoded referencing column name.
    #[must_use]
    pub fn child(&self) -> &[u8] {
        &self.child
    }
}

impl CatalogRelationship {
    const KNOWN: i32 = 1 | 2 | 256 | 4096 | 0x0100_0000 | 0x0200_0000;

    /// Returns the raw database-encoded relationship name.
    #[must_use]
    pub fn name(&self) -> &[u8] {
        &self.name
    }

    /// Returns the raw database-encoded referenced table name.
    #[must_use]
    pub fn parent_table(&self) -> &[u8] {
        &self.parent
    }

    /// Returns the raw database-encoded referencing table name.
    #[must_use]
    pub fn child_table(&self) -> &[u8] {
        &self.child
    }

    /// Returns the key-column pairs in catalog order.
    #[must_use]
    pub fn fields(&self) -> &[CatalogRelationshipField] {
        &self.fields
    }

    /// Returns the stored `grbit` attribute word.
    #[must_use]
    pub const fn raw_attributes(&self) -> i32 {
        self.raw_attributes
    }

    /// Returns whether referential integrity is enforced (no `dbRelationDontEnforce`).
    #[must_use]
    pub const fn enforced(&self) -> bool {
        self.raw_attributes & 2 == 0
    }

    /// Returns whether `dbRelationUpdateCascade` is set.
    #[must_use]
    pub const fn cascade_updates(&self) -> bool {
        self.raw_attributes & 256 != 0
    }

    /// Returns whether `dbRelationDeleteCascade` is set.
    #[must_use]
    pub const fn cascade_deletes(&self) -> bool {
        self.raw_attributes & 4096 != 0
    }

    /// Returns whether `dbRelationUnique` (one-to-one) is set.
    #[must_use]
    pub const fn one_to_one(&self) -> bool {
        self.raw_attributes & 1 != 0
    }

    /// Returns the default join type from the two join bits.
    #[must_use]
    pub const fn join(&self) -> RelationshipJoin {
        RelationshipJoin::from_bits(
            self.raw_attributes & 0x0100_0000 != 0,
            self.raw_attributes & 0x0200_0000 != 0,
        )
    }

    /// Returns attribute bits outside the documented set this crate decodes.
    #[must_use]
    pub const fn unknown_attributes(&self) -> i32 {
        self.raw_attributes & !Self::KNOWN
    }

    /// Returns whether row writes and schema edits interpret this relationship.
    ///
    /// Unknown and invalid attribute combinations are preserved
    /// but make writes to their tables refuse.
    #[must_use]
    pub const fn interpreted(&self) -> bool {
        crate::relationship_flags::RelationshipFlags::decode(self.raw_attributes).is_some()
    }
}

impl<S: ReadAt> DatabaseReader<S> {
    /// Reads every relationship in `MSysRelationships`, including unenforced
    /// relationships that have no table-definition records.
    ///
    /// Components are grouped by relationship name and ordered by their stored
    /// ordinal; relationships with more than ten components are refused.
    /// Endpoint tables and columns are not resolved; use validation for that.
    ///
    /// # Errors
    ///
    /// Returns an error for malformed catalog rows, inconsistent component
    /// groups, or exhausted resource limits.
    pub fn relationship_catalog(
        &mut self,
        budget: &mut ResourceBudget,
    ) -> Result<Vec<CatalogRelationship>, RelationshipValidationError> {
        crate::relationship_catalog::catalog(self, budget).map_err(Into::into)
    }
}
