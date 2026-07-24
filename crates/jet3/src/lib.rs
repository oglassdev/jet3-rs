#![forbid(unsafe_code)]
#![doc = "Safe, clean-room primitives for Access 97 / Jet 3 databases."]

pub mod atomic;
pub mod binary;
pub mod candidate;
pub mod commit_state;
pub mod error;
pub mod header;
pub mod jet3_page;
pub mod limits;
pub mod offset;
pub mod page;
pub mod resource;
pub mod source;

pub use atomic::{PublishError, PublishStage, atomic_update, atomic_update_with_hook};
pub use binary::BinaryCursor;
pub use candidate::{CandidateError, RawJet3Candidate};
pub use commit_state::{
    COMMIT_REGION_LENGTH, COMMIT_REGION_OFFSET, COMMIT_SLOT_COUNT, CommitRegion, CommitSlot,
    CommitSlotRole, CommitStateClass, SHARED_COMMIT_SLOT_COUNT, read_commit_region,
    read_commit_region_into,
};
pub use error::{Error, LimitKind, ResourceLimitKind};
pub use header::{
    HeaderError, JET3_PAGE_SIZE, JetFileKind, jet3_page_geometry, read_jet_signature,
};
pub use jet3_page::Jet3PageReader;
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
