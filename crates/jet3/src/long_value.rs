//! Bounded inline and external long-value streaming from `EXP-0061`.

use std::fmt;
use std::mem::size_of;
use std::ops::Range;

use crate::text::{DecodedText, TextCodePage, TextError, decode_text, decoded_text_length};
use crate::{
    AllocationTraversalError, ByteCount, Error, JET3_PAGE_SIZE, OwnedPages, PageKind, PageNumber,
    ReadAt, ResourceBudget, RowCursor, RowLocator,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const HEADER_LEN: usize = 12;
const LENGTH_MASK: u32 = 0x00ff_ffff;
const INLINE_FLAG: u32 = 0x8000_0000;
const SINGLE_PAGE_FLAG: u32 = 0x4000_0000;
const LVAL_OWNER: [u8; 4] = *b"LVAL";
const DIRECTORY_OFFSET: usize = 10;
const ENTRY_LEN: usize = 2;
const OFFSET_MASK: u16 = 0x1fff;

/// The semantic kind of a Jet long value.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum LongValueKind {
    Memo,
    Ole,
}

/// Decoded inline long-value data retaining its raw payload.
#[derive(Debug, PartialEq)]
#[non_exhaustive]
pub enum InlineLongValue<'raw> {
    Text(DecodedText<'raw>),
    Binary(&'raw [u8]),
}

/// The observed storage form of an external long value.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ExternalLongValueStorage {
    SinglePage,
    Chained,
}

/// An owned, validated external long-value reference.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct LongValueReference {
    raw_header: [u8; HEADER_LEN],
    source: RowLocator,
    target: RowLocator,
    length: u32,
    storage: ExternalLongValueStorage,
    kind: LongValueKind,
    code_page: TextCodePage,
}

impl LongValueReference {
    #[must_use]
    pub const fn raw_header(self) -> [u8; HEADER_LEN] {
        self.raw_header
    }

    #[must_use]
    pub const fn source(self) -> RowLocator {
        self.source
    }

    #[must_use]
    pub const fn target(self) -> RowLocator {
        self.target
    }

    #[must_use]
    pub const fn length(self) -> u32 {
        self.length
    }

    #[must_use]
    pub const fn storage(self) -> ExternalLongValueStorage {
        self.storage
    }

    #[must_use]
    pub const fn kind(self) -> LongValueKind {
        self.kind
    }

    #[must_use]
    pub const fn code_page(self) -> TextCodePage {
        self.code_page
    }
}

/// A decoded inline value or an owned external reference.
#[derive(Debug, PartialEq)]
#[non_exhaustive]
pub enum LongValue<'raw> {
    Inline {
        raw_header: [u8; HEADER_LEN],
        value: InlineLongValue<'raw>,
    },
    External(LongValueReference),
}

impl<'raw> LongValue<'raw> {
    pub(crate) fn decode(
        raw: &'raw [u8],
        source: RowLocator,
        kind: LongValueKind,
        code_page: TextCodePage,
        budget: &mut ResourceBudget,
    ) -> Result<Self, LongValueError> {
        if raw.len() < HEADER_LEN {
            return Err(LongValueError::HeaderTooShort { actual: raw.len() });
        }
        let raw_header: [u8; HEADER_LEN] = raw[..HEADER_LEN]
            .try_into()
            .map_err(|_| LongValueError::HeaderTooShort { actual: raw.len() })?;
        let control = u32::from_le_bytes(
            raw_header[..4]
                .try_into()
                .map_err(|_| LongValueError::HeaderTooShort { actual: raw.len() })?,
        );
        let length = control & LENGTH_MASK;
        let flags = control & !LENGTH_MASK;
        let expected = usize::try_from(length).map_err(|_| {
            LongValueError::Resource(Error::IntegerConversion {
                value: u128::from(length),
                target: "usize",
            })
        })?;
        match flags {
            INLINE_FLAG => {
                if raw_header[4..].iter().any(|byte| *byte != 0) {
                    return Err(LongValueError::NonzeroReservedHeader);
                }
                let actual = raw.len() - HEADER_LEN;
                if actual != expected {
                    return Err(LongValueError::LengthMismatch {
                        expected: length,
                        actual: u64::try_from(actual).map_err(|_| {
                            LongValueError::Resource(Error::IntegerConversion {
                                value: actual as u128,
                                target: "u64",
                            })
                        })?,
                    });
                }
                let payload = &raw[HEADER_LEN..];
                let value = match kind {
                    LongValueKind::Memo => InlineLongValue::Text(
                        decode_text(payload, code_page, budget).map_err(LongValueError::Text)?,
                    ),
                    LongValueKind::Ole => {
                        budget
                            .charge_decoded_value(
                                ByteCount::from_usize(payload.len())
                                    .map_err(LongValueError::Resource)?,
                            )
                            .map_err(LongValueError::Resource)?;
                        InlineLongValue::Binary(payload)
                    }
                };
                Ok(Self::Inline { raw_header, value })
            }
            SINGLE_PAGE_FLAG | 0 => {
                if raw.len() != HEADER_LEN {
                    return Err(LongValueError::ExternalHeaderLength { actual: raw.len() });
                }
                if raw_header[8..].iter().any(|byte| *byte != 0) {
                    return Err(LongValueError::NonzeroReservedHeader);
                }
                let target = decode_locator(
                    raw_header[4..8]
                        .try_into()
                        .map_err(|_| LongValueError::HeaderTooShort { actual: raw.len() })?,
                );
                if target.page().get() == 0 && target.slot() == 0 {
                    return Err(LongValueError::MissingExternalTarget);
                }
                budget
                    .check_decoded_value(ByteCount::new(u64::from(length)))
                    .map_err(LongValueError::Resource)?;
                Ok(Self::External(LongValueReference {
                    raw_header,
                    source,
                    target,
                    length,
                    storage: if flags == SINGLE_PAGE_FLAG {
                        ExternalLongValueStorage::SinglePage
                    } else {
                        ExternalLongValueStorage::Chained
                    },
                    kind,
                    code_page,
                }))
            }
            _ => Err(LongValueError::UnsupportedFlags { raw: flags }),
        }
    }
}

/// One borrowed streamed fragment and its interpreted output.
#[derive(Debug, PartialEq)]
pub struct LongValueChunk<'page> {
    raw_row: &'page [u8],
    value: LongValueChunkValue<'page>,
}

impl<'page> LongValueChunk<'page> {
    #[must_use]
    pub const fn raw_row(&self) -> &'page [u8] {
        self.raw_row
    }

    #[must_use]
    pub const fn value(&self) -> &LongValueChunkValue<'page> {
        &self.value
    }
}

/// The converted output of one streamed long-value fragment.
#[derive(Debug, PartialEq)]
#[non_exhaustive]
pub enum LongValueChunkValue<'page> {
    Text(DecodedText<'page>),
    Binary(&'page [u8]),
}

/// A long-value header, page, chain, length, or resource failure.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum LongValueError {
    HeaderTooShort { actual: usize },
    ExternalHeaderLength { actual: usize },
    UnsupportedFlags { raw: u32 },
    NonzeroReservedHeader,
    MissingExternalTarget,
    UnexpectedPageKind { page: PageNumber, actual: PageKind },
    InvalidOwner { page: PageNumber, actual: [u8; 4] },
    InvalidDirectory { page: PageNumber },
    MissingRow { locator: RowLocator, row_count: u16 },
    InvalidRowFlags { locator: RowLocator, raw: u16 },
    ChainRowTooShort { locator: RowLocator, actual: usize },
    LengthMismatch { expected: u32, actual: u64 },
    NonterminalAtLength { locator: RowLocator },
    SelfLink { locator: RowLocator },
    Cycle { locator: RowLocator },
    Allocation(AllocationTraversalError),
    Text(TextError),
    Resource(Error),
}

impl fmt::Display for LongValueError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "long value failed: {self:?}")
    }
}

impl std::error::Error for LongValueError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Allocation(source) => Some(source),
            Self::Text(source) => Some(source),
            Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

/// A forward-only external long-value stream borrowing the row cursor's page.
#[derive(Debug)]
pub struct LongValueCursor<'cursor, 'operation, S> {
    owned: &'cursor mut OwnedPages<'operation, S>,
    page: &'cursor mut [u8; PAGE_BYTES],
    reference: LongValueReference,
    next: Option<RowLocator>,
    visited: Vec<RowLocator>,
    emitted: u64,
    decoded_emitted: u64,
    failed: bool,
}

impl<'cursor, 'operation, S: ReadAt> LongValueCursor<'cursor, 'operation, S> {
    fn new(
        owned: &'cursor mut OwnedPages<'operation, S>,
        page: &'cursor mut [u8; PAGE_BYTES],
        reference: LongValueReference,
    ) -> Result<Self, LongValueError> {
        let maximum = owned.budget_mut().limits().max_chain_depth();
        let capacity = usize::try_from(maximum).map_err(|_| {
            LongValueError::Resource(Error::IntegerConversion {
                value: u128::from(maximum),
                target: "usize",
            })
        })?;
        let bytes =
            maximum
                .checked_mul(size_of::<RowLocator>() as u64)
                .ok_or(LongValueError::Resource(Error::Arithmetic {
                    operation: "size long-value visited state",
                }))?;
        owned
            .budget_mut()
            .charge_allocation(ByteCount::new(bytes))
            .map_err(LongValueError::Resource)?;
        let mut visited = Vec::new();
        visited.try_reserve_exact(capacity).map_err(|_| {
            LongValueError::Resource(Error::Io {
                operation: "reserve long-value visited state",
                kind: std::io::ErrorKind::OutOfMemory,
            })
        })?;
        Ok(Self {
            owned,
            page,
            reference,
            next: Some(reference.target),
            visited,
            emitted: 0,
            decoded_emitted: 0,
            failed: false,
        })
    }

    /// Returns the next lossless payload fragment. Errors exhaust the stream.
    pub fn next_chunk(&mut self) -> Result<Option<LongValueChunk<'_>>, LongValueError> {
        if self.failed {
            return Ok(None);
        }
        self.failed = true;
        self.next_chunk_inner()
    }

    fn next_chunk_inner(&mut self) -> Result<Option<LongValueChunk<'_>>, LongValueError> {
        let Some(locator) = self.next.take() else {
            return Ok(None);
        };
        if locator == self.reference.source {
            return Err(LongValueError::SelfLink { locator });
        }
        let comparisons = u64::try_from(self.visited.len()).map_err(|_| {
            LongValueError::Resource(Error::IntegerConversion {
                value: self.visited.len() as u128,
                target: "u64",
            })
        })?;
        self.owned
            .budget_mut()
            .charge_work_units(comparisons)
            .map_err(LongValueError::Resource)?;
        if self.visited.contains(&locator) {
            return Err(LongValueError::Cycle { locator });
        }
        let depth = u64::try_from(self.visited.len())
            .ok()
            .and_then(|value| value.checked_add(1))
            .ok_or(LongValueError::Resource(Error::Arithmetic {
                operation: "advance long-value chain depth",
            }))?;
        self.owned
            .budget_mut()
            .check_chain_depth(depth)
            .map_err(LongValueError::Resource)?;
        self.owned
            .budget_mut()
            .charge_items(1)
            .map_err(LongValueError::Resource)?;
        self.visited.push(locator);
        let kind = self
            .owned
            .read_classified_page_into(locator.page(), self.page)
            .map_err(LongValueError::Allocation)?;
        if kind != PageKind::Data {
            return Err(LongValueError::UnexpectedPageKind {
                page: locator.page(),
                actual: kind,
            });
        }
        let range = validate_lval_row(locator, self.page, self.owned.budget_mut())?;
        let (payload_start, following) = match self.reference.storage {
            ExternalLongValueStorage::SinglePage => (range.start, None),
            ExternalLongValueStorage::Chained => {
                if range.len() < 4 {
                    return Err(LongValueError::ChainRowTooShort {
                        locator,
                        actual: range.len(),
                    });
                }
                let raw_pointer: [u8; 4] = self.page[range.start..range.start + 4]
                    .try_into()
                    .map_err(|_| LongValueError::ChainRowTooShort {
                        locator,
                        actual: range.len(),
                    })?;
                let next = (raw_pointer != [0; 4]).then(|| decode_locator(raw_pointer));
                (range.start + 4, next)
            }
        };
        let payload_length = range.end - payload_start;
        let payload_length = u64::try_from(payload_length).map_err(|_| {
            LongValueError::Resource(Error::IntegerConversion {
                value: payload_length as u128,
                target: "u64",
            })
        })?;
        let next_emitted =
            self.emitted
                .checked_add(payload_length)
                .ok_or(LongValueError::Resource(Error::Arithmetic {
                    operation: "accumulate long-value bytes",
                }))?;
        let expected = u64::from(self.reference.length);
        if next_emitted > expected || (following.is_none() && next_emitted != expected) {
            return Err(LongValueError::LengthMismatch {
                expected: self.reference.length,
                actual: next_emitted,
            });
        }
        if following.is_some() && next_emitted == expected {
            return Err(LongValueError::NonterminalAtLength { locator });
        }
        self.emitted = next_emitted;
        self.next = following;
        let row_end = range.end;
        let raw_row = &self.page[range];
        let payload = &self.page[payload_start..row_end];
        let value = match self.reference.kind {
            LongValueKind::Memo => {
                let decoded = decoded_text_length(payload, self.reference.code_page)
                    .map_err(LongValueError::Text)?;
                let decoded_total = self.decoded_emitted.checked_add(decoded.get()).ok_or(
                    LongValueError::Resource(Error::Arithmetic {
                        operation: "accumulate decoded long-value bytes",
                    }),
                )?;
                self.owned
                    .budget_mut()
                    .check_decoded_value(ByteCount::new(decoded_total))
                    .map_err(LongValueError::Resource)?;
                let text = decode_text(payload, self.reference.code_page, self.owned.budget_mut())
                    .map_err(LongValueError::Text)?;
                self.decoded_emitted = decoded_total;
                LongValueChunkValue::Text(text)
            }
            LongValueKind::Ole => {
                self.owned
                    .budget_mut()
                    .charge_decoded_value(
                        ByteCount::from_usize(payload.len()).map_err(LongValueError::Resource)?,
                    )
                    .map_err(LongValueError::Resource)?;
                LongValueChunkValue::Binary(payload)
            }
        };
        self.failed = false;
        Ok(Some(LongValueChunk { raw_row, value }))
    }
}

impl<'operation, 'schema, S: ReadAt> RowCursor<'operation, 'schema, S> {
    /// Starts streaming a copied external reference through this cursor's page.
    pub fn long_value<'cursor>(
        &'cursor mut self,
        reference: LongValueReference,
    ) -> Result<LongValueCursor<'cursor, 'operation, S>, LongValueError> {
        if self.resume_page.is_none() {
            self.resume_page = self.current_page;
        }
        LongValueCursor::new(&mut self.owned, &mut self.page, reference)
    }
}

fn validate_lval_row(
    locator: RowLocator,
    page: &[u8; PAGE_BYTES],
    budget: &mut ResourceBudget,
) -> Result<Range<usize>, LongValueError> {
    let actual_owner: [u8; 4] =
        page[4..8]
            .try_into()
            .map_err(|_| LongValueError::InvalidDirectory {
                page: locator.page(),
            })?;
    if actual_owner != LVAL_OWNER {
        return Err(LongValueError::InvalidOwner {
            page: locator.page(),
            actual: actual_owner,
        });
    }
    let row_count = u16::from_le_bytes([page[8], page[9]]);
    let maximum = (PAGE_BYTES - DIRECTORY_OFFSET) / ENTRY_LEN;
    if usize::from(row_count) > maximum || row_count > u16::from(u8::MAX) + 1 {
        return Err(LongValueError::InvalidDirectory {
            page: locator.page(),
        });
    }
    budget
        .charge_items(u64::from(row_count))
        .map_err(LongValueError::Resource)?;
    let directory_end = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(row_count);
    let mut prior = PAGE_BYTES;
    let mut target = None;
    for row in 0..row_count {
        let offset = DIRECTORY_OFFSET + ENTRY_LEN * usize::from(row);
        let raw = u16::from_le_bytes([page[offset], page[offset + 1]]);
        let row_locator = RowLocator::new(
            locator.page(),
            u8::try_from(row).map_err(|_| LongValueError::InvalidDirectory {
                page: locator.page(),
            })?,
        );
        if raw & !OFFSET_MASK != 0 {
            return Err(LongValueError::InvalidRowFlags {
                locator: row_locator,
                raw,
            });
        }
        let start = usize::from(raw & OFFSET_MASK);
        if start < directory_end || start >= prior {
            return Err(LongValueError::InvalidDirectory {
                page: locator.page(),
            });
        }
        if row_locator == locator {
            target = Some(start..prior);
        }
        prior = start;
    }
    target.ok_or(LongValueError::MissingRow { locator, row_count })
}

fn decode_locator(raw: [u8; 4]) -> RowLocator {
    let page = u32::from_le_bytes([raw[1], raw[2], raw[3], 0]);
    RowLocator::new(PageNumber::new(u64::from(page)), raw[0])
}

#[cfg(test)]
#[path = "long_value_tests.rs"]
mod tests;
