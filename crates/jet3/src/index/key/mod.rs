//! Index key encoders for scalar, text, locale-text and binary components.

pub(crate) mod binary;
pub(crate) mod locale_text;
#[cfg(test)]
mod locale_text_tests;
mod locale_text_weights;
pub(crate) mod scalar;
pub(crate) mod text;
#[cfg(test)]
mod text_tests;
mod text_weights;
