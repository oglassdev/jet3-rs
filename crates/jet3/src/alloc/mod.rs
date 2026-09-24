//! Allocation and usage maps: decoding, traversal, encoding and mutable bitmaps.

pub(crate) mod map;
#[cfg(test)]
mod map_tests;
pub(crate) mod mutation_map;
pub(crate) mod mutation_map_guard;
#[cfg(test)]
mod mutation_map_tests;
pub(crate) mod patch;
pub(crate) mod traverse;
#[cfg(test)]
mod traverse_tests;
pub(crate) mod usage_map;
#[cfg(test)]
mod usage_map_tests;
pub(crate) mod usage_map_writer;
#[cfg(test)]
mod usage_map_writer_tests;
