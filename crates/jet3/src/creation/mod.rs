//! Fresh database creation from typed table, index, relationship and row requests.
//! Binary encoders remain in their dedicated low-level modules.

mod api;
mod columns;
pub(crate) mod composer;
mod index_options;
mod schema;
pub(crate) mod schema_plan;

pub use crate::RowValue;
pub use api::{
    CandidateCheckError, CreateDatabaseError, TableRows, create_database,
    create_database_with_relationship, create_database_with_relationship_rows,
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
