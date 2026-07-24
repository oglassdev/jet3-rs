#![forbid(unsafe_code)]
#![doc = "Safe, clean-room primitives for Access 97 / Jet 3 databases."]

pub mod atomic;
pub mod binary;
pub mod error;
pub mod header;
pub mod limits;
pub mod offset;
pub mod page;
pub mod resource;
pub mod source;

pub use atomic::{PublishError, PublishStage, atomic_update, atomic_update_with_hook};
pub use binary::BinaryCursor;
pub use error::{Error, LimitKind, ResourceLimitKind};
pub use header::{
    HeaderError, JET3_PAGE_SIZE, JetFileKind, jet3_page_geometry, read_jet_signature,
};
pub use limits::{ReadBudget, ReadLimits};
pub use offset::{ByteCount, ByteOffset};
pub use page::{PageGeometry, PageNumber, PageOffset};
pub use resource::{ResourceBudget, ResourceLimits};
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
