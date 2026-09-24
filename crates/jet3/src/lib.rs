#![forbid(unsafe_code)]
//! Safe, clean-room primitives for Access 97 / Jet 3 databases.
//!
//! Reading starts at [`DatabaseReader`]. Creating a fresh database holding
//! empty user tables starts at [`create_database`]:
//!
//! ```no_run
//! use std::num::NonZeroU8;
//!
//! use jet3::{
//!     ColumnSpec, ColumnType, IndexColumnSpec, IndexKind, IndexSpec, ResourceBudget,
//!     ResourceLimits, TableSpec, TableValidation, create_database,
//! };
//!
//! const NAME_LEN: NonZeroU8 = NonZeroU8::new(50).unwrap();
//! let columns = [
//!     ColumnSpec::new(b"Id", ColumnType::AutoIncrement),
//!     ColumnSpec::new(b"Name", ColumnType::Text { max_len: NAME_LEN }),
//! ];
//! let indexes = [IndexSpec {
//!     name: b"PrimaryKey",
//!     fields: &[IndexColumnSpec::ascending(b"Id")],
//!     kind: IndexKind::Primary,
//! }];
//! let people = TableSpec {
//!     name: b"People",
//!     columns: &columns,
//!     indexes: &indexes,
//!     validation: TableValidation::NONE,
//! };
//! let mut budget = ResourceBudget::new(ResourceLimits::default());
//! create_database("people.mdb", &[people], &mut budget)?;
//! # Ok::<(), jet3::CreateDatabaseError>(())
//! ```
//!
//! The created image is reopened structurally before publication. That
//! self-check is not evidence of Microsoft Access or DAO compatibility; only
//! a recorded DAO differential can establish that.

pub mod alloc;
pub mod catalog;
pub mod create;
pub mod database;
#[cfg(test)]
mod database_tests;
pub mod definition;
pub mod format;
pub mod index;
pub mod long_value;
mod properties;
pub mod relationship;
pub mod row;
mod schema;
pub mod validate;
pub mod write;
pub use properties::error::ColumnPropertyError;

pub use alloc::map::{
    AllocationMap, AllocationMapError, ExtendedAllocationBits, IndirectAllocationMap,
    InlineAllocatedPages, InlineAllocationMap, MapPageReferences, decode_allocation_map,
    extended_allocation_bits,
};
pub use alloc::traverse::{
    AllocationTraversalError, OwnedPages, PageChainWalker, ReachedMapPage, VisitedPages,
    follow_map_page_reference,
};
pub use catalog::cursor::{CatalogCursor, CatalogError};
pub use catalog::name_key::CatalogNameKeyError;
pub use catalog::record::{
    CatalogName, CatalogNameEncoding, CatalogObjectClass, CatalogObjectId, CatalogObjectKind,
    CatalogRecord, CatalogRecordError,
};
pub use catalog::record_writer::{
    CatalogRecordSpec, CatalogRecordWriteError, catalog_record_len, encode_catalog_record,
};
pub use create::{
    CandidateCheckError, ColumnRef, ColumnSpec, ColumnStorageKind, ColumnType, ComposeError,
    CreateDatabaseError, IndexColumnSpec, IndexKind, IndexNullPolicy, IndexSpec, RelationshipField,
    RelationshipJoin, RelationshipSpec, TableRef, TableRows, TableSchemaPlanError, TableSpec,
    TableValidation, create_database, create_database_with_relationship,
    create_database_with_relationship_rows, create_database_with_relationships,
    create_database_with_relationships_and_rows, create_database_with_rows,
    create_database_with_table_rows,
};
pub use definition::column::{
    ColumnDefinition, ColumnOrdinal, ColumnPhysicalType, ColumnStorageClass,
};
pub use definition::column_writer::{
    IndexFieldSpec, LogicalIndexKindSpec, LogicalIndexSpec, PhysicalIndexSpec,
    SystemColumnClassSpec,
};
pub use format::binary::BinaryCursor;
pub use format::binary_writer::BinaryWriter;
pub use format::candidate::{CandidateError, RawJet3Candidate};
pub use format::commit_state::{
    COMMIT_REGION_LENGTH, COMMIT_REGION_OFFSET, COMMIT_SLOT_COUNT, CommitRegion, CommitSlot,
    CommitSlotRole, CommitStateClass, SHARED_COMMIT_SLOT_COUNT, read_commit_region,
    read_commit_region_into,
};
pub use write::atomic::{
    PublishError, PublishStage, atomic_create, atomic_create_with_hook, atomic_update,
    atomic_update_with_hook,
};

pub use alloc::usage_map::{UsageMapError, UsageMapRecord, locate_usage_map};
pub use alloc::usage_map_writer::{
    EXTENDED_BITMAP_BITS, ExtendedUsageMapEncoder, InlineUsageMapEncoder, UsageMapWriteError,
    encode_indirect_references, indirect_record_len,
};
pub use create::page_append_plan::AppendPageError;
pub use create::whole_file_plan::WholeFilePlanError;
pub use database::{DatabaseOpenError, DatabasePageError, DatabaseReader};
pub use definition::index::{
    IndexDefinition, IndexDefinitionError, IndexDefinitionKind, IndexDirection, IndexField,
    IndexUsageMapReference, PhysicalIndexDefinition, RelationshipReference, RelationshipSide,
};
pub use definition::long_value_map::{
    LONG_VALUE_MAP_GROUP_LEN, LongValueMapDefinition, LongValueMapError,
};
pub use definition::map_location::{
    MapLocationError, MapRowLocator, TableMapLocations, locate_table_maps,
};
pub use definition::name::{DefinitionName, DefinitionNameEncoding};
pub use definition::table::{TableDefinition, TableDefinitionError, TableDefinitionKind};
pub use definition::table_writer::{
    LongValueMapSpec, PhysicalIndexFlagsSpec, TableDefinitionSpec, TableDefinitionWriteError,
    encode_table_definition, table_definition_len,
};
pub use format::database_header::{
    DATABASE_HEADER_PAGE_NUMBER, DatabaseFormatError, DatabaseHeaderPage, DatabaseHeaderPageError,
    DatabaseProtection, DatabaseVersion, SortOrder, SupportedDatabaseFormat,
};
pub use format::error::{Error, LimitKind, ResourceLimitKind};
pub use format::header::{
    HeaderError, JET3_PAGE_SIZE, JetFileKind, jet3_page_geometry, read_jet_signature,
};
pub use format::jet3_page::Jet3PageReader;
pub use format::limits::{ReadBudget, ReadLimits};
pub use format::offset::{ByteCount, ByteOffset};
pub use format::page::{PageGeometry, PageNumber, PageOffset};
pub use format::page_image::{DataPageBuilder, PAGE_BYTES, PageImage, PageImageError, page_tag};
pub use format::page_kind::{ClassifiedPage, PageClassificationError, PageKind, classify_page};
pub use format::raw_page_stream::{RawPage, RawPageCursor};
pub use format::resource::{ResourceBudget, ResourceLimits};
pub use format::source::{FileSource, ReadAt, SliceSource};
pub use format::text::{DecodedText, TextCodePage, TextError};
pub use index::tree::reader::{
    IndexEntry, IndexKey, IndexKeyEncoding, IndexNode, IndexNodeKind, IndexTree, IndexTreeError,
};
pub use long_value::reader::{
    ExternalLongValueStorage, InlineLongValue, LongValue, LongValueChunk, LongValueChunkValue,
    LongValueCursor, LongValueError, LongValueKind, LongValueReference,
};
pub use properties::table::{ColumnProperties, TableProperties};
pub use relationship::inventory::{
    CatalogRelationship, CatalogRelationshipField, Relationship, Relationships,
};
pub use row::directory::{RowDirectoryError, RowLocator};
pub use row::reader::{RawField, RowCursor, RowError, RowView};
pub use row::value::{
    CurrencyValue, DateTimeValue, DecodedValue, GuidValue, ValueError, ValueKind,
};
pub use row::writer::{RowColumnLayout, RowValue, RowWriteError, encode_row};
pub use schema::edit::{PropertyChange, SchemaEdit, edit_schema};
pub use validate::{
    RelationshipValidationError, StorageValidationError, TableValidationError, ValidationError,
    ValidationReport,
};
pub use write::delete::{RowDelete, delete_row};
pub use write::insert::insert_row;
pub use write::row_update::{RowUpdate, update_row};
pub use write::update::{FieldUpdate, UpdateError, update_field};

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
