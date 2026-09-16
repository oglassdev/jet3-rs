//! Fresh database creation from typed table, index, relationship and row requests.
//! Binary encoders remain in their dedicated low-level modules.
//!
//! # Reproducible output
//!
//! Each creation API produces identical MDB bytes for the same ordered request
//! and library version. The destination name and directory, wall clock, and
//! resource limits do not affect a successful image. No reproducibility option
//! is needed: table, column, index and row order are supplied by the caller;
//! generated AutoIncrement values follow that order. Fixed catalog timestamps
//! and header metadata come from the existing creation templates
//! (`EXP-0073`, `EXP-0087`, `EXP-0208`).
//!
//! Filesystem metadata is outside this guarantee. Different request order,
//! later database mutations or a different library version may change the bytes.

mod api;
mod columns;
pub(crate) mod composer;
mod index_options;
mod relationship_indexes;
mod relationship_name;
mod schema;
pub(crate) mod schema_plan;

pub use crate::RowValue;
pub use api::{
    CandidateCheckError, CreateDatabaseError, TableRows, create_database,
    create_database_with_relationship, create_database_with_relationship_rows,
    create_database_with_relationships, create_database_with_relationships_and_rows,
    create_database_with_rows, create_database_with_table_rows,
};
pub use columns::{ColumnSpec, ColumnStorageKind, ColumnType};
pub use composer::ComposeError;
pub use index_options::{IndexKind, IndexNullPolicy};
pub use schema::{
    ColumnRef, IndexColumnSpec, IndexSpec, RelationshipColumn, RelationshipSpec, TableRef,
    TableSpec,
};
pub use schema_plan::TableSchemaPlanError;
