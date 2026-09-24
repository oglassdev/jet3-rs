pub(crate) mod catalog;
pub(crate) mod column;
pub(crate) mod column_create;
pub(crate) mod column_drop;
pub(crate) mod column_options;
pub(crate) mod definition;
pub(crate) mod edit;
pub(crate) mod index;
#[cfg(all(test, any(unix, windows)))]
mod index_matrix_tests;
#[cfg(all(test, any(unix, windows)))]
mod index_tests;
pub(crate) mod map;
pub(crate) mod properties;
pub(crate) mod relationship_catalog;
pub(crate) mod relationship_create;
pub(crate) mod relationship_drop;
#[cfg(all(test, any(unix, windows)))]
mod relationship_form_tests;
#[cfg(all(test, any(unix, windows)))]
mod relationship_tests;
pub(crate) mod storage;
pub(crate) mod table;
pub(crate) mod table_drop;
