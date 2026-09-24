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
mod api_relationship;
mod api_relationship_graph;
#[cfg(all(test, any(unix, windows)))]
mod api_relationship_graph_tests;
#[cfg(all(test, any(unix, windows)))]
mod api_relationship_tests;
#[cfg(all(test, any(unix, windows)))]
mod api_tests;
#[cfg(all(test, any(unix, windows)))]
mod autoincrement_tests;
mod columns;
pub(crate) mod composer;
#[cfg(all(test, any(unix, windows)))]
mod composite_index_tests;
#[cfg(all(test, any(unix, windows)))]
mod composite_relationship_tests;
#[cfg(all(test, any(unix, windows)))]
mod creation_tables_tests;
#[cfg(all(test, any(unix, windows)))]
mod descending_parent_tests;
#[cfg(all(test, any(unix, windows)))]
mod determinism_tests;
#[cfg(all(test, any(unix, windows)))]
mod empty_value_options_tests;
#[cfg(all(test, any(unix, windows)))]
mod fixed_text_index_tests;
mod graph_schema_check;
#[cfg(all(test, any(unix, windows)))]
mod index_capacity_tests;
mod index_options;
#[cfg(all(test, any(unix, windows)))]
mod initial_index_tests;
#[cfg(all(test, any(unix, windows)))]
mod initial_long_values_tests;
#[cfg(all(test, any(unix, windows)))]
mod initial_rows_tests;
#[cfg(all(test, any(unix, windows)))]
mod larger_relationship_graph_tests;
#[cfg(all(test, any(unix, windows)))]
mod memo_option_tests;
#[cfg(all(test, any(unix, windows)))]
mod multi_level_index_tests;
#[cfg(all(test, any(unix, windows)))]
mod multi_table_rows_tests;
#[cfg(all(test, any(unix, windows)))]
mod multiple_index_tests;
#[cfg(all(test, any(unix, windows)))]
mod multiple_long_values_tests;
#[cfg(all(test, any(unix, windows)))]
mod nullable_index_tests;
#[cfg(all(test, any(unix, windows)))]
mod numeric_index_tests;
pub(crate) mod page_append_plan;
#[cfg(test)]
mod page_append_plan_tests;
#[cfg(all(test, any(unix, windows)))]
mod relationship_index_tests;
mod relationship_indexes;
pub(crate) mod relationship_name;
#[cfg(all(test, any(unix, windows)))]
mod relationship_rows_tests;
#[cfg(all(test, any(unix, windows)))]
mod required_column_tests;
#[cfg(all(test, any(unix, windows)))]
mod rich_relationship_tests;
#[cfg(all(test, any(unix, windows)))]
mod scalar_relationship_tests;
mod schema;
#[cfg(all(test, any(unix, windows)))]
mod schema_name_tests;
pub(crate) mod schema_plan;
#[cfg(test)]
mod schema_plan_tests;
#[cfg(all(test, any(unix, windows)))]
mod self_reference_order_tests;
#[cfg(all(test, any(unix, windows)))]
mod text_property_edit_tests;
#[cfg(all(test, any(unix, windows)))]
mod text_property_tests;
pub(crate) mod whole_file_plan;
#[cfg(test)]
mod whole_file_plan_tests;
#[cfg(all(test, any(unix, windows)))]
mod wide_variable_tests;

pub use api::{
    CreateDatabaseError, TableRows, create_database, create_database_with_relationship,
    create_database_with_relationship_rows, create_database_with_relationships,
    create_database_with_relationships_and_rows, create_database_with_rows,
    create_database_with_table_rows,
};
pub use columns::{ColumnSpec, ColumnStorageKind, ColumnType};
pub use composer::ComposeError;
pub use index_options::{IndexKind, IndexNullPolicy};
pub use schema::{
    ColumnRef, IndexColumnSpec, IndexSpec, RelationshipField, RelationshipJoin, RelationshipSpec,
    TableRef, TableSpec, TableValidation,
};
pub use schema_plan::TableSchemaPlanError;
