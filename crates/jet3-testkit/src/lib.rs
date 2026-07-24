#![forbid(unsafe_code)]
#![doc = "Test-only support for reproducible fixtures and independent checks."]

/// Returns the format name used in fixture metadata.
#[must_use]
pub const fn fixture_format_name() -> &'static str {
    jet3::FORMAT_NAME
}

#[cfg(test)]
mod tests {
    use super::fixture_format_name;

    #[test]
    fn fixture_metadata_uses_the_library_scope() {
        assert_eq!(fixture_format_name(), "Access 97 / Jet 3");
    }
}
