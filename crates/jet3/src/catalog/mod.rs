//! The `MSysObjects` catalog: records, name keys and streaming discovery.

pub(crate) mod cursor;
#[cfg(test)]
mod cursor_tests;
pub(crate) mod name_key;
#[cfg(test)]
mod name_key_tests;
pub(crate) mod overflow;
pub(crate) mod record;
#[cfg(test)]
mod record_tests;
pub(crate) mod record_writer;
pub(crate) mod system_rows;
