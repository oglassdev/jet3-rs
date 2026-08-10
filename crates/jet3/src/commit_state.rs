//! Bounded access to the documented Jet 3 database-header commit region.
//!
//! Microsoft source `SRC-0013` identifies bytes `[0x600, 0x800)` of the first
//! database page as 256 volatile, contextual two-byte commit-state slots. Slot
//! zero is associated with an exclusive connection; slots 1 through 255 are
//! associated with shared connections. The applicable shared slot cannot be
//! identified from MDB bytes alone: it requires contemporaneous operating-
//! system byte-range lock evidence from the companion `.ldb` file.
//!
//! This module deliberately preserves every pair. It names only the two values
//! documented by `SRC-0013`; neither those names nor any other bytes establish
//! database validity, corruption, clean shutdown, Jet generation, user
//! ownership, or compatibility.

use crate::header::JET3_PAGE_BYTES;
use crate::{ByteCount, ByteOffset, Error, ReadAt, ReadBudget};

/// Absolute offset of the documented commit region in the first database page.
pub const COMMIT_REGION_OFFSET: ByteOffset = ByteOffset::new(0x600);
/// Size in bytes of the documented commit region.
pub const COMMIT_REGION_LENGTH: ByteCount = ByteCount::new(0x200);
/// Number of two-byte slots in the documented commit region.
pub const COMMIT_SLOT_COUNT: usize = 256;
/// Number of slots associated with shared connections.
pub const SHARED_COMMIT_SLOT_COUNT: usize = COMMIT_SLOT_COUNT - 1;

const COMMIT_REGION_BYTES: usize = COMMIT_REGION_LENGTH.get() as usize;
const COMMIT_SLOT_BYTES: usize = 2;

/// The documented connection role associated with a commit-region slot.
///
/// A role does not identify a user. In particular, mapping a shared slot to a
/// connection requires contemporaneous `.ldb` byte-range lock evidence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CommitSlotRole {
    /// Slot zero, associated with an exclusive connection.
    Exclusive,
    /// One of the 255 slots associated with shared connections.
    Shared {
        /// Zero-based ordinal among the shared slots (0 through 254).
        ordinal: u8,
    },
}

/// A deliberately narrow label for one raw commit-state pair.
///
/// These values are volatile and contextual. A label reports only the literal
/// pair documented by `SRC-0013`; it cannot establish validity, corruption,
/// version, clean shutdown, user ownership, or compatibility. Meaningful
/// diagnosis requires contemporaneous `.ldb` lock evidence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum CommitStateClass {
    /// The documented label for the raw pair `[0x00, 0x00]`.
    PhysicallyWriting,
    /// The documented label for the raw pair `[0x01, 0x00]`.
    CorruptedPageAccess,
    /// Any pair not explicitly named by the source, preserved without change.
    Other([u8; COMMIT_SLOT_BYTES]),
}

impl CommitStateClass {
    /// Classifies one pair without rejecting or normalizing undocumented values.
    ///
    /// The classification is not a database diagnosis. Values are volatile and
    /// contextual, and require contemporaneous `.ldb` lock evidence.
    #[must_use]
    pub const fn classify(raw: [u8; COMMIT_SLOT_BYTES]) -> Self {
        match raw {
            [0x00, 0x00] => Self::PhysicallyWriting,
            [0x01, 0x00] => Self::CorruptedPageAccess,
            other => Self::Other(other),
        }
    }

    /// Returns the exact pair represented by this classification.
    #[must_use]
    pub const fn raw(self) -> [u8; COMMIT_SLOT_BYTES] {
        match self {
            Self::PhysicallyWriting => [0x00, 0x00],
            Self::CorruptedPageAccess => [0x01, 0x00],
            Self::Other(raw) => raw,
        }
    }
}

/// One raw two-byte slot and its positional role.
///
/// Slot contents are volatile and contextual. This value cannot associate a
/// shared slot with a user without contemporaneous `.ldb` lock evidence, nor
/// can it establish validity, corruption, version, or compatibility.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct CommitSlot {
    index: u8,
    raw: [u8; COMMIT_SLOT_BYTES],
}

impl CommitSlot {
    /// Returns the zero-based index in the complete 256-slot region.
    #[must_use]
    pub const fn index(self) -> u8 {
        self.index
    }

    /// Returns the slot's documented positional connection role.
    #[must_use]
    pub const fn role(self) -> CommitSlotRole {
        if self.index == 0 {
            CommitSlotRole::Exclusive
        } else {
            CommitSlotRole::Shared {
                ordinal: self.index - 1,
            }
        }
    }

    /// Returns the exact two bytes stored in this slot.
    #[must_use]
    pub const fn raw(self) -> [u8; COMMIT_SLOT_BYTES] {
        self.raw
    }

    /// Returns the narrow documented classification of this slot's raw pair.
    ///
    /// The label is not a diagnosis; the pair remains volatile and contextual
    /// and requires contemporaneous `.ldb` lock evidence.
    #[must_use]
    pub const fn classification(self) -> CommitStateClass {
        CommitStateClass::classify(self.raw)
    }
}

/// A complete raw snapshot of the documented 512-byte commit region.
///
/// The snapshot is allocation-free and retains every byte. Its contents are
/// volatile and contextual: without contemporaneous `.ldb` lock evidence they
/// cannot establish database validity, corruption, clean shutdown, Jet
/// generation, user ownership, or compatibility.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct CommitRegion {
    raw: [u8; COMMIT_REGION_BYTES],
}

impl CommitRegion {
    /// Wraps an exact raw 512-byte snapshot without interpreting its values.
    ///
    /// The caller remains responsible for the snapshot's provenance and
    /// timing. These volatile, contextual bytes require contemporaneous `.ldb`
    /// lock evidence and cannot establish validity, corruption, version, or
    /// compatibility alone.
    #[must_use]
    pub const fn from_raw_bytes(raw: [u8; COMMIT_REGION_BYTES]) -> Self {
        Self { raw }
    }

    /// Returns all 512 bytes exactly as read from `[0x600, 0x800)`.
    #[must_use]
    pub const fn raw_bytes(&self) -> &[u8; COMMIT_REGION_BYTES] {
        &self.raw
    }

    /// Returns the indexed two-byte slot, or `None` outside `0..256`.
    ///
    /// Index zero has [`CommitSlotRole::Exclusive`]; indices 1 through 255
    /// have [`CommitSlotRole::Shared`]. A shared index cannot be associated
    /// with a user without contemporaneous `.ldb` lock evidence.
    #[must_use]
    pub fn slot(&self, index: usize) -> Option<CommitSlot> {
        let byte_start = index.checked_mul(COMMIT_SLOT_BYTES)?;
        let byte_end = byte_start.checked_add(COMMIT_SLOT_BYTES)?;
        let pair = self.raw.get(byte_start..byte_end)?;
        let mut raw = [0_u8; COMMIT_SLOT_BYTES];
        raw.copy_from_slice(pair);
        Some(CommitSlot {
            index: u8::try_from(index).ok()?,
            raw,
        })
    }

    /// Iterates over all 256 raw two-byte slots in positional order.
    ///
    /// Iteration preserves every value and performs no allocation.
    pub fn raw_slots(
        &self,
    ) -> impl ExactSizeIterator<Item = [u8; COMMIT_SLOT_BYTES]> + DoubleEndedIterator + '_ {
        self.raw.chunks_exact(COMMIT_SLOT_BYTES).map(|pair| {
            let mut raw = [0_u8; COMMIT_SLOT_BYTES];
            raw.copy_from_slice(pair);
            raw
        })
    }
}

pub(crate) fn commit_region_from_database_header_page(
    page: &[u8; JET3_PAGE_BYTES],
) -> CommitRegion {
    let start = COMMIT_REGION_OFFSET.get() as usize;
    let end = start + COMMIT_REGION_BYTES;
    let mut raw = [0_u8; COMMIT_REGION_BYTES];
    raw.copy_from_slice(&page[start..end]);
    CommitRegion::from_raw_bytes(raw)
}

/// Reads exactly the documented 512-byte region at offset `0x600`.
///
/// The read is performed once into a private fixed-size buffer, so failure
/// exposes no partial result and allocates no heap memory. The returned values
/// are volatile and contextual. They require contemporaneous `.ldb` lock
/// evidence and cannot establish validity, corruption, version, clean
/// shutdown, user ownership, or compatibility on their own.
pub fn read_commit_region(
    source: &mut (impl ReadAt + ?Sized),
    budget: &mut ReadBudget,
) -> Result<CommitRegion, Error> {
    budget.check_read(COMMIT_REGION_LENGTH)?;
    let mut raw = [0_u8; COMMIT_REGION_BYTES];
    source.read_exact_at(COMMIT_REGION_OFFSET, &mut raw, budget)?;
    Ok(CommitRegion::from_raw_bytes(raw))
}

/// Atomically replaces `destination` with one complete commit-region snapshot.
///
/// `destination` is unchanged if budget enforcement, geometry, or source I/O
/// fails. A success still exposes only volatile, contextual bytes; meaningful
/// interpretation requires contemporaneous `.ldb` lock evidence and cannot
/// establish validity, corruption, version, or compatibility alone.
pub fn read_commit_region_into(
    source: &mut (impl ReadAt + ?Sized),
    destination: &mut CommitRegion,
    budget: &mut ReadBudget,
) -> Result<(), Error> {
    let observed = read_commit_region(source, budget)?;
    *destination = observed;
    Ok(())
}

#[cfg(test)]
#[path = "commit_state_tests.rs"]
mod tests;
