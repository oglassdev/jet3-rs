//! Row writes to existing databases and their atomic, page-exact publication.

pub(crate) mod atomic;
#[cfg(all(test, any(unix, windows)))]
mod atomic_tests;
#[cfg(windows)]
mod atomic_windows;
pub(crate) mod auto_number;
pub(crate) mod delete;
#[cfg(all(test, any(unix, windows)))]
mod delete_tests;
mod driver;
pub(crate) mod error;
pub(crate) mod field_update;
pub(crate) mod insert;
#[cfg(all(test, any(unix, windows)))]
mod insert_eof_tests;
#[cfg(all(test, any(unix, windows)))]
mod insert_indexed_tests;
#[cfg(all(test, any(unix, windows)))]
mod insert_reuse_tests;
#[cfg(all(test, any(unix, windows)))]
mod insert_scalar_index_tests;
#[cfg(all(test, any(unix, windows)))]
mod insert_tests;
pub(crate) mod page_edits;
mod page_edits_sequence;
pub(crate) mod row_update;
#[cfg(all(test, any(unix, windows)))]
mod row_update_overflow_tests;
#[cfg(all(test, any(unix, windows)))]
mod row_update_tests;
pub(crate) mod update;
#[cfg(all(test, any(unix, windows)))]
mod update_field_rewrite_tests;
#[cfg(all(test, any(unix, windows)))]
mod update_key_tests;
pub(crate) mod update_pages;
#[cfg(all(test, any(unix, windows)))]
mod update_relationship_tests;
#[cfg(all(test, any(unix, windows)))]
mod update_tests;
