//! Memo and OLE long values: streaming reads, payload encoding and mutation.

pub(crate) mod mutation;
#[cfg(all(test, any(unix, windows)))]
mod mutation_empty_tests;
#[cfg(all(test, any(unix, windows)))]
mod mutation_field_tests;
mod mutation_load;
#[cfg(all(test, any(unix, windows)))]
mod mutation_tests;
pub(crate) mod reader;
#[cfg(test)]
mod reader_tests;
pub(crate) mod writer;
#[cfg(test)]
mod writer_tests;
