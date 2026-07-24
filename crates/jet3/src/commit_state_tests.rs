use std::io;

use super::{
    COMMIT_REGION_LENGTH, COMMIT_REGION_OFFSET, COMMIT_SLOT_COUNT, CommitRegion, CommitSlotRole,
    CommitStateClass, SHARED_COMMIT_SLOT_COUNT, read_commit_region, read_commit_region_into,
};
use crate::{ByteCount, ByteOffset, Error, LimitKind, ReadAt, ReadBudget, ReadLimits, SliceSource};

const REGION_START: usize = COMMIT_REGION_OFFSET.get() as usize;
const REGION_BYTES: usize = COMMIT_REGION_LENGTH.get() as usize;
const REGION_END: usize = REGION_START + REGION_BYTES;

#[derive(Debug, Clone, Copy)]
enum Behavior {
    Exact,
    PartialThenShort,
    PartialThenFault,
}

#[derive(Debug)]
struct RecordingSource {
    bytes: Vec<u8>,
    behavior: Behavior,
    requests: Vec<(ByteOffset, usize)>,
}

impl RecordingSource {
    fn new(bytes: Vec<u8>, behavior: Behavior) -> Self {
        Self {
            bytes,
            behavior,
            requests: Vec::new(),
        }
    }
}

impl ReadAt for RecordingSource {
    fn len(&self) -> ByteCount {
        ByteCount::new(self.bytes.len() as u64)
    }

    fn read_exact_at(
        &mut self,
        offset: ByteOffset,
        destination: &mut [u8],
        budget: &mut ReadBudget,
    ) -> Result<(), Error> {
        let count = ByteCount::from_usize(destination.len())?;
        budget.charge_read_attempt(count)?;
        self.requests.push((offset, destination.len()));

        match self.behavior {
            Behavior::Exact => {
                let start = offset.to_usize()?;
                let end = start
                    .checked_add(destination.len())
                    .ok_or(Error::Arithmetic {
                        operation: "compute recording-source read end",
                    })?;
                let available = ByteCount::new(self.bytes.len().saturating_sub(start) as u64);
                let source = self.bytes.get(start..end).ok_or(Error::UnexpectedEnd {
                    offset,
                    needed: count,
                    available,
                })?;
                destination.copy_from_slice(source);
                Ok(())
            }
            Behavior::PartialThenShort => {
                let partial = destination.get_mut(..17).ok_or(Error::Arithmetic {
                    operation: "select recording-source short prefix",
                })?;
                partial.fill(0xA7);
                Err(Error::ShortRead {
                    offset,
                    needed: count,
                    actual: ByteCount::new(17),
                })
            }
            Behavior::PartialThenFault => {
                let partial = destination.get_mut(..31).ok_or(Error::Arithmetic {
                    operation: "select recording-source fault prefix",
                })?;
                partial.fill(0xD3);
                Err(Error::Io {
                    operation: "read recording source",
                    kind: io::ErrorKind::Other,
                })
            }
        }
    }
}

fn limits(max_input: u64, single_read: u64, total_read: u64) -> ReadLimits {
    ReadLimits::new(
        ByteCount::new(max_input),
        ByteCount::new(single_read),
        ByteCount::new(total_read),
    )
}

fn permissive_budget() -> ReadBudget {
    ReadBudget::new(limits(u64::MAX, u64::MAX, u64::MAX))
}

fn patterned_input() -> Vec<u8> {
    let mut bytes = vec![0xE1; REGION_END + 19];
    for (index, byte) in bytes[REGION_START..REGION_END].iter_mut().enumerate() {
        *byte = index as u8;
    }
    bytes
}

fn sentinel_region() -> CommitRegion {
    CommitRegion::from_raw_bytes([0x5A; REGION_BYTES])
}

#[test]
fn reads_once_and_only_inside_the_documented_range() -> Result<(), Error> {
    let bytes = patterned_input();
    let expected = bytes[REGION_START..REGION_END].to_vec();
    let mut source = RecordingSource::new(bytes, Behavior::Exact);
    let mut budget = permissive_budget();

    let region = read_commit_region(&mut source, &mut budget)?;

    assert_eq!(source.requests, vec![(COMMIT_REGION_OFFSET, REGION_BYTES)]);
    assert_eq!(region.raw_bytes().as_slice(), expected);
    assert_eq!(budget.total_read(), COMMIT_REGION_LENGTH);
    Ok(())
}

#[test]
fn exact_region_end_is_sufficient_and_trailing_bytes_are_irrelevant() -> Result<(), Error> {
    for trailing in [0_usize, 1, 257] {
        let mut bytes = vec![0xCC; REGION_END + trailing];
        bytes[REGION_START..REGION_END].fill(0x37);
        let mut budget = permissive_budget();
        let mut source = SliceSource::new(&bytes, &budget)?;

        let region = read_commit_region(&mut source, &mut budget)?;
        assert_eq!(region.raw_bytes(), &[0x37; REGION_BYTES]);
        assert_eq!(budget.total_read(), COMMIT_REGION_LENGTH);
    }
    Ok(())
}

#[test]
fn every_truncation_before_region_end_returns_structured_error() {
    for length in 0..REGION_END {
        let bytes = vec![0_u8; length];
        let mut budget = permissive_budget();
        let mut source = SliceSource::new(&bytes, &budget)
            .unwrap_or_else(|error| unreachable!("permissive input limit rejected data: {error}"));
        let result = read_commit_region(&mut source, &mut budget);

        if length < REGION_START {
            assert_eq!(
                result,
                Err(Error::OffsetOutOfBounds {
                    offset: COMMIT_REGION_OFFSET,
                    input_len: ByteCount::new(length as u64),
                }),
                "unexpected result for length {length}"
            );
        } else {
            assert_eq!(
                result,
                Err(Error::UnexpectedEnd {
                    offset: COMMIT_REGION_OFFSET,
                    needed: COMMIT_REGION_LENGTH,
                    available: ByteCount::new((length - REGION_START) as u64),
                }),
                "unexpected result for length {length}"
            );
        }
        assert_eq!(budget.total_read(), ByteCount::new(0));
    }
}

#[test]
fn exact_read_limits_are_accepted() -> Result<(), Error> {
    let bytes = patterned_input();
    let mut budget = ReadBudget::new(limits(
        bytes.len() as u64,
        COMMIT_REGION_LENGTH.get(),
        COMMIT_REGION_LENGTH.get(),
    ));
    let mut source = SliceSource::new(&bytes, &budget)?;

    let region = read_commit_region(&mut source, &mut budget)?;

    assert_eq!(
        region.raw_bytes().as_slice(),
        &bytes[REGION_START..REGION_END]
    );
    assert_eq!(budget.total_read(), COMMIT_REGION_LENGTH);
    Ok(())
}

#[test]
fn one_below_each_read_limit_is_rejected_without_source_access() {
    let bytes = patterned_input();
    let maximum = ByteCount::new(COMMIT_REGION_LENGTH.get() - 1);

    for (single, total, kind) in [
        (
            maximum.get(),
            COMMIT_REGION_LENGTH.get(),
            LimitKind::SingleReadBytes,
        ),
        (
            COMMIT_REGION_LENGTH.get(),
            maximum.get(),
            LimitKind::TotalReadBytes,
        ),
    ] {
        let mut source = RecordingSource::new(bytes.clone(), Behavior::Exact);
        let mut budget = ReadBudget::new(limits(bytes.len() as u64, single, total));

        assert_eq!(
            read_commit_region(&mut source, &mut budget),
            Err(Error::LimitExceeded {
                kind,
                requested: COMMIT_REGION_LENGTH,
                maximum,
            })
        );
        assert!(source.requests.is_empty());
        assert_eq!(budget.total_read(), ByteCount::new(0));
    }
}

#[test]
fn cumulative_budget_accounts_for_prior_reads_at_the_boundary() -> Result<(), Error> {
    let bytes = patterned_input();
    let mut exact = ReadBudget::new(limits(
        bytes.len() as u64,
        COMMIT_REGION_LENGTH.get(),
        COMMIT_REGION_LENGTH.get() + 7,
    ));
    exact.charge_read_attempt(ByteCount::new(7))?;
    let mut exact_source = RecordingSource::new(bytes.clone(), Behavior::Exact);
    assert!(read_commit_region(&mut exact_source, &mut exact).is_ok());
    assert_eq!(
        exact.total_read(),
        ByteCount::new(COMMIT_REGION_LENGTH.get() + 7)
    );

    let mut short = ReadBudget::new(limits(
        bytes.len() as u64,
        COMMIT_REGION_LENGTH.get(),
        COMMIT_REGION_LENGTH.get() + 6,
    ));
    short.charge_read_attempt(ByteCount::new(7))?;
    let mut short_source = RecordingSource::new(bytes, Behavior::Exact);
    assert_eq!(
        read_commit_region(&mut short_source, &mut short),
        Err(Error::LimitExceeded {
            kind: LimitKind::TotalReadBytes,
            requested: ByteCount::new(COMMIT_REGION_LENGTH.get() + 7),
            maximum: ByteCount::new(COMMIT_REGION_LENGTH.get() + 6),
        })
    );
    assert!(short_source.requests.is_empty());
    assert_eq!(short.total_read(), ByteCount::new(7));
    Ok(())
}

#[test]
fn destination_is_unchanged_after_partial_short_read() {
    let mut source = RecordingSource::new(patterned_input(), Behavior::PartialThenShort);
    let mut budget = permissive_budget();
    let original = sentinel_region();
    let mut destination = original.clone();

    assert_eq!(
        read_commit_region_into(&mut source, &mut destination, &mut budget),
        Err(Error::ShortRead {
            offset: COMMIT_REGION_OFFSET,
            needed: COMMIT_REGION_LENGTH,
            actual: ByteCount::new(17),
        })
    );
    assert_eq!(destination, original);
    assert_eq!(source.requests, vec![(COMMIT_REGION_OFFSET, REGION_BYTES)]);
    assert_eq!(budget.total_read(), COMMIT_REGION_LENGTH);
}

#[test]
fn destination_is_unchanged_after_partial_io_failure() {
    let mut source = RecordingSource::new(patterned_input(), Behavior::PartialThenFault);
    let mut budget = permissive_budget();
    let original = sentinel_region();
    let mut destination = original.clone();

    assert_eq!(
        read_commit_region_into(&mut source, &mut destination, &mut budget),
        Err(Error::Io {
            operation: "read recording source",
            kind: io::ErrorKind::Other,
        })
    );
    assert_eq!(destination, original);
    assert_eq!(source.requests, vec![(COMMIT_REGION_OFFSET, REGION_BYTES)]);
    assert_eq!(budget.total_read(), COMMIT_REGION_LENGTH);
}

#[test]
fn destination_changes_only_after_complete_success() -> Result<(), Error> {
    let bytes = patterned_input();
    let expected = bytes[REGION_START..REGION_END].to_vec();
    let mut source = RecordingSource::new(bytes, Behavior::Exact);
    let mut budget = permissive_budget();
    let mut destination = sentinel_region();

    read_commit_region_into(&mut source, &mut destination, &mut budget)?;

    assert_eq!(destination.raw_bytes().as_slice(), expected);
    assert_eq!(source.requests, vec![(COMMIT_REGION_OFFSET, REGION_BYTES)]);
    Ok(())
}

#[test]
fn slot_indices_preserve_exclusive_and_shared_semantics() -> Result<(), Error> {
    let mut raw = [0_u8; REGION_BYTES];
    raw[0..2].copy_from_slice(&[0x10, 0x11]);
    raw[2..4].copy_from_slice(&[0x20, 0x21]);
    raw[510..512].copy_from_slice(&[0xFE, 0xFF]);
    let region = CommitRegion::from_raw_bytes(raw);

    let exclusive = region.slot(0).ok_or(Error::Arithmetic {
        operation: "access test exclusive commit slot",
    })?;
    assert_eq!(exclusive.index(), 0);
    assert_eq!(exclusive.role(), CommitSlotRole::Exclusive);
    assert_eq!(exclusive.raw(), [0x10, 0x11]);
    assert_eq!(
        exclusive.classification(),
        CommitStateClass::Other([0x10, 0x11])
    );

    let first_shared = region.slot(1).ok_or(Error::Arithmetic {
        operation: "access test first shared commit slot",
    })?;
    assert_eq!(first_shared.index(), 1);
    assert_eq!(first_shared.role(), CommitSlotRole::Shared { ordinal: 0 });
    assert_eq!(first_shared.raw(), [0x20, 0x21]);

    let last_shared = region.slot(255).ok_or(Error::Arithmetic {
        operation: "access test last shared commit slot",
    })?;
    assert_eq!(last_shared.index(), 255);
    assert_eq!(last_shared.role(), CommitSlotRole::Shared { ordinal: 254 });
    assert_eq!(last_shared.raw(), [0xFE, 0xFF]);

    assert!(region.slot(COMMIT_SLOT_COUNT).is_none());
    assert!(region.slot(usize::MAX).is_none());
    assert_eq!(SHARED_COMMIT_SLOT_COUNT, 255);
    Ok(())
}

#[test]
fn classification_names_only_the_two_documented_pairs() {
    for first in u8::MIN..=u8::MAX {
        for second in u8::MIN..=u8::MAX {
            let raw = [first, second];
            let expected = match raw {
                [0x00, 0x00] => CommitStateClass::PhysicallyWriting,
                [0x01, 0x00] => CommitStateClass::CorruptedPageAccess,
                other => CommitStateClass::Other(other),
            };
            let classification = CommitStateClass::classify(raw);
            assert_eq!(classification, expected);
            assert_eq!(classification.raw(), raw);
        }
    }
}

#[test]
fn raw_slot_iteration_needs_no_input_sized_collection() {
    let mut raw = [0_u8; REGION_BYTES];
    for (index, byte) in raw.iter_mut().enumerate() {
        *byte = index as u8;
    }
    let region = CommitRegion::from_raw_bytes(raw);
    let mut slots = region.raw_slots();

    assert_eq!(slots.len(), COMMIT_SLOT_COUNT);
    assert_eq!(slots.next(), Some([0, 1]));
    assert_eq!(slots.next_back(), Some([254, 255]));
    assert_eq!(slots.len(), COMMIT_SLOT_COUNT - 2);

    for (index, pair) in region.raw_slots().enumerate() {
        assert_eq!(region.slot(index).map(|slot| slot.raw()), Some(pair));
    }
}
