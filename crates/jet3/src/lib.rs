#![forbid(unsafe_code)]
#![doc = "Safe, clean-room primitives for Access 97 / Jet 3 databases."]

pub mod binary;
pub mod error;
pub mod limits;
pub mod offset;
pub mod page;
pub mod source;

pub use binary::BinaryCursor;
pub use error::{Error, LimitKind};
pub use limits::{ReadBudget, ReadLimits};
pub use offset::{ByteCount, ByteOffset};
pub use page::{PageGeometry, PageNumber, PageOffset};
pub use source::{FileSource, ReadAt, SliceSource};

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
