#![forbid(unsafe_code)]
#![doc = "Safe, clean-room primitives for Access 97 / Jet 3 databases."]

/// Human-readable name of the only database format targeted by this crate.
pub const FORMAT_NAME: &str = "Access 97 / Jet 3";

#[cfg(test)]
mod tests {
    use super::FORMAT_NAME;

    #[test]
    fn format_name_identifies_the_narrow_scope() {
        assert_eq!(FORMAT_NAME, "Access 97 / Jet 3");
    }
}
