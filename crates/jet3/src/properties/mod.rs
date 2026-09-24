//! Catalog `LvProp` property blobs and the column and table properties they carry.

pub(crate) mod blob;
#[cfg(test)]
mod blob_tests;
pub(crate) mod column;
pub(crate) mod error;
pub(crate) mod ownership;
pub(crate) mod reader;
pub(crate) mod table;
pub(crate) mod value_policy;
pub(crate) mod values;
