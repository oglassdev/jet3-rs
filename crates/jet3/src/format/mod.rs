//! Byte-level primitives: checked binary access, page geometry and images, the file header, sources and resource limits.

pub(crate) mod binary;
pub(crate) mod binary_writer;
#[cfg(test)]
mod binary_writer_tests;
pub(crate) mod candidate;
#[cfg(test)]
mod candidate_tests;
pub(crate) mod commit_state;
#[cfg(test)]
mod commit_state_tests;
pub(crate) mod data_page_directory;
pub(crate) mod database_header;
#[cfg(test)]
mod database_header_tests;
pub(crate) mod error;
pub(crate) mod header;
#[cfg(test)]
mod header_tests;
pub(crate) mod jet3_page;
#[cfg(test)]
mod jet3_page_tests;
pub(crate) mod limits;
pub(crate) mod offset;
pub(crate) mod page;
pub(crate) mod page_image;
#[cfg(test)]
mod page_image_tests;
pub(crate) mod page_kind;
#[cfg(test)]
mod page_kind_tests;
pub(crate) mod raw_page_stream;
#[cfg(test)]
mod raw_page_stream_tests;
pub(crate) mod resource;
#[cfg(test)]
mod resource_tests;
pub(crate) mod source;
pub(crate) mod text;
#[cfg(test)]
mod text_tests;
