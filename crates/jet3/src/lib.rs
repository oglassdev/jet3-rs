#![forbid(unsafe_code)]
#![doc = "Safe, clean-room primitives for Access 97 / Jet 3 databases."]

pub mod allocation;
pub mod allocation_traverse;
pub mod atomic;
pub mod binary;
pub mod binary_writer;
pub mod candidate;
pub mod catalog;
pub mod catalog_record;
pub mod catalog_record_writer;
pub mod column_definition;
pub mod column_definition_writer;
pub mod commit_state;
mod data_page_directory;
pub mod database;
pub mod database_header;
mod definition_name;
pub mod error;
pub mod header;
pub mod index_definition;
pub mod index_tree;
mod index_tree_page;
mod index_tree_rows;
pub mod jet3_page;
pub mod limits;
pub mod long_value;
pub mod long_value_map;
pub mod map_location;
pub mod offset;
pub mod page;
mod page_append_plan;
pub mod page_image;
pub mod page_kind;
mod physical_index_definition;
pub mod raw_page_stream;
pub mod relationships;
pub mod resource;
pub mod row;
pub mod row_directory;
pub mod row_writer;
pub mod source;
pub mod table_definition;
pub mod table_definition_writer;
pub mod text;
pub mod usage_map;
pub mod usage_map_writer;
pub mod value;
mod whole_file_plan;

pub use allocation::{
    AllocationMap, AllocationMapError, ExtendedAllocationBits, IndirectAllocationMap,
    InlineAllocatedPages, InlineAllocationMap, MapPageReferences, decode_allocation_map,
    extended_allocation_bits,
};
pub use allocation_traverse::{
    AllocationTraversalError, OwnedPages, PageChainWalker, ReachedMapPage, VisitedPages,
    follow_map_page_reference,
};
pub use atomic::{PublishError, PublishStage, atomic_update, atomic_update_with_hook};
pub use binary::BinaryCursor;
pub use binary_writer::BinaryWriter;
pub use candidate::{CandidateError, RawJet3Candidate};
pub use catalog::{CatalogCursor, CatalogError};
pub use catalog_record::{
    CatalogName, CatalogNameEncoding, CatalogObjectClass, CatalogObjectId, CatalogObjectKind,
    CatalogRecord, CatalogRecordError,
};
pub use catalog_record_writer::{
    CatalogRecordSpec, CatalogRecordWriteError, catalog_record_len, encode_catalog_record,
};
pub use column_definition::{
    ColumnDefinition, ColumnOrdinal, ColumnPhysicalType, ColumnStorageClass,
};
pub use column_definition_writer::{
    ColumnSpec, ColumnStorageKind, IndexFieldSpec, LogicalIndexKindSpec, LogicalIndexSpec,
    PhysicalIndexSpec,
};
pub use commit_state::{
    COMMIT_REGION_LENGTH, COMMIT_REGION_OFFSET, COMMIT_SLOT_COUNT, CommitRegion, CommitSlot,
    CommitSlotRole, CommitStateClass, SHARED_COMMIT_SLOT_COUNT, read_commit_region,
    read_commit_region_into,
};
pub use database::{DatabaseOpenError, DatabasePageError, DatabaseReader};
pub use database_header::{
    DATABASE_HEADER_PAGE_NUMBER, DatabaseFormatError, DatabaseHeaderPage, DatabaseHeaderPageError,
    DatabaseProtection, DatabaseVersion, SupportedDatabaseFormat,
};
pub use definition_name::{DefinitionName, DefinitionNameEncoding};
pub use error::{Error, LimitKind, ResourceLimitKind};
pub use header::{
    HeaderError, JET3_PAGE_SIZE, JetFileKind, jet3_page_geometry, read_jet_signature,
};
pub use index_definition::{
    IndexDefinition, IndexDefinitionError, IndexDefinitionKind, IndexDirection, IndexField,
    IndexUsageMapReference, PhysicalIndexDefinition, RelationshipReference, RelationshipSide,
};
pub use index_tree::{
    IndexEntry, IndexKey, IndexKeyEncoding, IndexNode, IndexNodeKind, IndexTree, IndexTreeError,
};
pub use jet3_page::Jet3PageReader;
pub use limits::{ReadBudget, ReadLimits};
pub use long_value::{
    ExternalLongValueStorage, InlineLongValue, LongValue, LongValueChunk, LongValueChunkValue,
    LongValueCursor, LongValueError, LongValueKind, LongValueReference,
};
pub use long_value_map::{LONG_VALUE_MAP_GROUP_LEN, LongValueMapDefinition, LongValueMapError};
pub use map_location::{MapLocationError, MapRowLocator, TableMapLocations, locate_table_maps};
pub use offset::{ByteCount, ByteOffset};
pub use page::{PageGeometry, PageNumber, PageOffset};
pub use page_image::{DataPageBuilder, PAGE_BYTES, PageImage, PageImageError, page_tag};
pub use page_kind::{ClassifiedPage, PageClassificationError, PageKind, classify_page};
pub use raw_page_stream::{RawPage, RawPageCursor};
pub use relationships::{Relationship, Relationships};
pub use resource::{ResourceBudget, ResourceLimits};
pub use row::{RawField, RowCursor, RowError, RowView};
pub use row_directory::{RowDirectoryError, RowLocator};
pub use row_writer::{RowColumnLayout, RowValue, RowWriteError, encode_row};
pub use source::{FileSource, ReadAt, SliceSource};
pub use table_definition::{TableDefinition, TableDefinitionError, TableDefinitionKind};
pub use table_definition_writer::{
    TableDefinitionSpec, TableDefinitionWriteError, encode_table_definition, table_definition_len,
};
pub use text::{DecodedText, TextCodePage, TextError};
pub use usage_map::{UsageMapError, UsageMapRecord, locate_usage_map};
pub use usage_map_writer::{
    EXTENDED_BITMAP_BITS, ExtendedUsageMapEncoder, InlineUsageMapEncoder, UsageMapWriteError,
    encode_indirect_references, indirect_record_len,
};
pub use value::{CurrencyValue, DateTimeValue, DecodedValue, GuidValue, ValueError, ValueKind};

/// Human-readable name of the only database format targeted by this crate.
pub const FORMAT_NAME: &str = "Access 97 / Jet 3";

#[cfg(test)]
mod tests {
    use super::FORMAT_NAME;

    #[test]
    fn format_name_identifies_the_narrow_scope() {
        assert_eq!(FORMAT_NAME, "Access 97 / Jet 3");
    }
}
