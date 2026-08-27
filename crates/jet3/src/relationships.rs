//! Allocation-free relationship inventory over a decoded table definition,
//! based on `EXP-0059` records and `EXP-0062` option isolation.

use std::iter::FusedIterator;
use std::slice;

use crate::{
    DefinitionName, IndexDefinition, IndexDefinitionKind, PageNumber, RelationshipReference,
    RelationshipSide, TableDefinition,
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
