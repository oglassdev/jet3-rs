//! EXP-0051 global usage-map location and inline iteration.

use super::{AllocationMap, AllocationMapError, InlineAllocationMap, decode_allocation_map};
use crate::{
    ByteCount, ClassifiedPage, Error, JET3_PAGE_SIZE, PageGeometry, PageKind, PageNumber,
    PageOffset, ResourceBudget,
};
use std::fmt;

const GLOBAL_USAGE_MAP_SLACK_BYTE: u8 = u8::MAX;

/// Physical page containing the global usage-map record.
///
/// This narrow location is the independently validated observation recorded
/// by `EXP-0051`; it is not a claim about table-owned usage maps.
pub const GLOBAL_USAGE_MAP_PAGE: PageNumber = PageNumber::new(1);

/// Inclusive page-local start of the global usage-map record from `EXP-0051`.
pub const GLOBAL_USAGE_MAP_RECORD_START: PageOffset = PageOffset::new(1_915);

/// Exclusive page-local end of the global usage-map record from `EXP-0051`.
pub const GLOBAL_USAGE_MAP_RECORD_END: PageOffset = PageOffset::new(JET3_PAGE_SIZE.get());

/// Polarity-relative not-in-use suffix measured by `EXP-0051`.
///
/// Under the established `set_means_not_in_use` polarity these bytes are
/// `0xff`; "zero suffix" describes zero represented use, not raw zero bytes.
pub const GLOBAL_USAGE_MAP_ZERO_SUFFIX_SLACK: ByteCount = ByteCount::new(92);

/// A global usage-map operation that remains blocked by `EXP-0051`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum UnsupportedGlobalUsageMap {
    /// The record has converted to the type-1 indirect/extended layout.
    IndirectOrExtendedLayout,
    /// The captured database has pages beyond the inline record's capacity.
    BeyondInlineRecord {
        /// First database page not represented by the inline bitmap.
        first_unrepresented_page: PageNumber,
    },
    /// No absolute page base for an extended bitmap is established.
    ExtendedPageBase,
    /// No table-definition usage-map pointer-pair layout is established.
    TableDefinitionPointerPairs,
}

impl fmt::Display for UnsupportedGlobalUsageMap {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::IndirectOrExtendedLayout => {
                formatter.write_str("global usage-map indirect/extended layout")
            }
            Self::BeyondInlineRecord {
                first_unrepresented_page,
            } => write!(
                formatter,
                "global usage-map traversal from page {} beyond the inline record",
                first_unrepresented_page.get()
            ),
            Self::ExtendedPageBase => formatter.write_str("global usage-map extended page base"),
            Self::TableDefinitionPointerPairs => {
                formatter.write_str("table-definition usage-map pointer pairs")
            }
        }
    }
}

/// A structured failure while locating or iterating the global usage map.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum GlobalUsageMapError {
    /// The caller supplied a classified page other than page 1.
    UnexpectedPage {
        /// Actual physical page number.
        actual: PageNumber,
    },
    /// Classified page 1 is not a data page.
    UnexpectedPageKind {
        /// Actual lossless page classification.
        actual: PageKind,
    },
    /// The fixed record does not satisfy the detached type-0/type-1 decoder.
    Record(AllocationMapError),
    /// One byte in the declared polarity-relative slack is not `0xff`.
    InvalidZeroSuffixSlack {
        /// Page-local position of the first invalid byte.
        offset: PageOffset,
        /// Raw byte found at `offset`.
        actual: u8,
    },
    /// A checked offset, length, or page calculation failed.
    Checked(Error),
    /// The requested iteration or validation work exceeded its budget.
    Resource(Error),
    /// The requested operation lies beyond the validated inline phase.
    Unsupported(UnsupportedGlobalUsageMap),
}

impl fmt::Display for GlobalUsageMapError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnexpectedPage { actual } => write!(
                formatter,
                "expected global usage-map page {}, found page {}",
                GLOBAL_USAGE_MAP_PAGE.get(),
                actual.get()
            ),
            Self::UnexpectedPageKind { actual } => write!(
                formatter,
                "expected global usage-map page to be Data, found {actual:?}"
            ),
            Self::Record(source) => write!(formatter, "invalid global usage-map record: {source}"),
            Self::InvalidZeroSuffixSlack { offset, actual } => write!(
                formatter,
                "global usage-map zero suffix has byte 0x{actual:02x} at page offset {}",
                offset.get()
            ),
            Self::Checked(source) => {
                write!(
                    formatter,
                    "global usage-map checked calculation failed: {source}"
                )
            }
            Self::Resource(source) => {
                write!(formatter, "global usage-map operation rejected: {source}")
            }
            Self::Unsupported(feature) => {
                write!(formatter, "{feature} is blocked on recorded evidence")
            }
        }
    }
}

impl std::error::Error for GlobalUsageMapError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Record(source) => Some(source),
            Self::Checked(source) | Self::Resource(source) => Some(source),
            Self::UnexpectedPage { .. }
            | Self::UnexpectedPageKind { .. }
            | Self::InvalidZeroSuffixSlack { .. }
            | Self::Unsupported(_) => None,
        }
    }
}

/// The validated inline phase of the global usage-map record.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GlobalUsageMapRecord<'page> {
    inline: InlineAllocationMap<'page>,
}

impl<'page> GlobalUsageMapRecord<'page> {
    /// Returns the first page represented by bitmap bit zero.
    #[must_use]
    pub const fn start_page(&self) -> PageNumber {
        self.inline.start_page()
    }

    /// Returns the fixed page-local half-open record range.
    #[must_use]
    pub const fn record_range(&self) -> (PageOffset, PageOffset) {
        (GLOBAL_USAGE_MAP_RECORD_START, GLOBAL_USAGE_MAP_RECORD_END)
    }

    /// Returns the fixed polarity-relative not-in-use suffix length.
    #[must_use]
    pub const fn zero_suffix_slack(&self) -> ByteCount {
        GLOBAL_USAGE_MAP_ZERO_SUFFIX_SLACK
    }

    /// Pre-charges and returns an allocation-free cursor over allocated pages.
    ///
    /// Every represented database page is charged before the cursor is
    /// returned, including pages whose state does not match the cursor. No
    /// allocation is performed.
    pub fn allocated_pages(
        &self,
        geometry: PageGeometry,
        budget: &mut ResourceBudget,
    ) -> Result<GlobalUsagePages<'page>, GlobalUsageMapError> {
        GlobalUsagePages::new(self.inline, geometry, GlobalPageState::Allocated, budget)
    }

    /// Pre-charges and returns an allocation-free cursor over unallocated pages.
    ///
    /// Set bits mean not in use for this global record. Every represented
    /// database page is charged before the cursor is returned.
    pub fn unallocated_pages(
        &self,
        geometry: PageGeometry,
        budget: &mut ResourceBudget,
    ) -> Result<GlobalUsagePages<'page>, GlobalUsageMapError> {
        GlobalUsagePages::new(self.inline, geometry, GlobalPageState::Unallocated, budget)
    }
}

/// State selected by a bounded global usage-map cursor.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum GlobalPageState {
    Allocated,
    Unallocated,
}

/// Allocation-free cursor over one selected global page state.
#[derive(Debug)]
pub struct GlobalUsagePages<'map> {
    start_page: PageNumber,
    bitmap: &'map [u8],
    state: GlobalPageState,
    next_bit: u64,
    bit_count: u64,
    done: bool,
}

impl<'map> GlobalUsagePages<'map> {
    fn new(
        map: InlineAllocationMap<'map>,
        geometry: PageGeometry,
        state: GlobalPageState,
        budget: &mut ResourceBudget,
    ) -> Result<Self, GlobalUsageMapError> {
        let bitmap_bytes = u64::try_from(map.bitmap.len()).map_err(|_| {
            GlobalUsageMapError::Checked(Error::IntegerConversion {
                value: map.bitmap.len() as u128,
                target: "u64",
            })
        })?;
        let capacity = bitmap_bytes
            .checked_mul(8)
            .ok_or(GlobalUsageMapError::Checked(Error::Arithmetic {
                operation: "derive global usage-map inline bit capacity",
            }))?;
        let represented = if geometry.page_count() > map.start_page().get() {
            geometry
                .page_count()
                .checked_sub(map.start_page().get())
                .ok_or(GlobalUsageMapError::Checked(Error::Arithmetic {
                    operation: "derive represented global usage-map page count",
                }))?
        } else {
            0
        };
        if represented > capacity {
            let first_unrepresented = map.start_page().get().checked_add(capacity).ok_or(
                GlobalUsageMapError::Checked(Error::Arithmetic {
                    operation: "derive first page beyond global inline usage map",
                }),
            )?;
            return Err(GlobalUsageMapError::Unsupported(
                UnsupportedGlobalUsageMap::BeyondInlineRecord {
                    first_unrepresented_page: PageNumber::new(first_unrepresented),
                },
            ));
        }
        map.start_page()
            .get()
            .checked_add(represented)
            .ok_or(GlobalUsageMapError::Checked(Error::Arithmetic {
                operation: "validate global inline usage-map page range",
            }))?;
        budget
            .charge_items(represented)
            .map_err(GlobalUsageMapError::Resource)?;

        Ok(Self {
            start_page: map.start_page(),
            bitmap: map.bitmap,
            state,
            next_bit: 0,
            bit_count: represented,
            done: false,
        })
    }

    /// Returns the next page in the selected state without further charging.
    pub fn next_page(&mut self) -> Result<Option<PageNumber>, GlobalUsageMapError> {
        while !self.done && self.next_bit < self.bit_count {
            let bit = self.next_bit;
            self.next_bit = self.next_bit.checked_add(1).ok_or_else(|| {
                self.done = true;
                GlobalUsageMapError::Checked(Error::Arithmetic {
                    operation: "advance global usage-map bit cursor",
                })
            })?;
            if self.next_bit == self.bit_count {
                self.done = true;
            }

            let byte_index = usize::try_from(bit / 8).map_err(|_| {
                self.done = true;
                GlobalUsageMapError::Checked(Error::IntegerConversion {
                    value: u128::from(bit / 8),
                    target: "usize",
                })
            })?;
            let bit_in_byte = u32::try_from(bit % 8).map_err(|_| {
                self.done = true;
                GlobalUsageMapError::Checked(Error::IntegerConversion {
                    value: u128::from(bit % 8),
                    target: "u32",
                })
            })?;
            let byte = self.bitmap.get(byte_index).ok_or_else(|| {
                self.done = true;
                GlobalUsageMapError::Checked(Error::UnexpectedEnd {
                    offset: crate::ByteOffset::new(bit / 8),
                    needed: ByteCount::new(1),
                    available: ByteCount::new(0),
                })
            })?;
            let is_unallocated = byte & (1_u8 << bit_in_byte) != 0;
            let matches = match self.state {
                GlobalPageState::Allocated => !is_unallocated,
                GlobalPageState::Unallocated => is_unallocated,
            };
            if matches {
                let page = self.start_page.get().checked_add(bit).ok_or_else(|| {
                    self.done = true;
                    GlobalUsageMapError::Checked(Error::Arithmetic {
                        operation: "add global usage-map bit to starting page",
                    })
                })?;
                return Ok(Some(PageNumber::new(page)));
            }
        }
        Ok(None)
    }
}

/// Locates the global usage-map record in an already classified page 1.
///
/// `EXP-0051` independently validated the exact page, half-open byte range,
/// `set_means_not_in_use` polarity, and 92-byte not-in-use suffix used here.
/// The record slice is still decoded by [`decode_allocation_map`], whose
/// type-0/type-1 physical layout comes from `SRC-0020`. Locator validation is
/// allocation-free and charges its bounded context and suffix work before
/// inspecting those bytes. A valid type-1 record returns structured
/// [`UnsupportedGlobalUsageMap::IndirectOrExtendedLayout`].
pub fn locate_global_usage_map_record<'page>(
    page: ClassifiedPage<'page>,
    budget: &mut ResourceBudget,
) -> Result<GlobalUsageMapRecord<'page>, GlobalUsageMapError> {
    budget
        .charge_work_units(1)
        .map_err(GlobalUsageMapError::Resource)?;
    if page.number() != GLOBAL_USAGE_MAP_PAGE {
        return Err(GlobalUsageMapError::UnexpectedPage {
            actual: page.number(),
        });
    }
    if page.kind() != PageKind::Data {
        return Err(GlobalUsageMapError::UnexpectedPageKind {
            actual: page.kind(),
        });
    }

    let start = GLOBAL_USAGE_MAP_RECORD_START
        .to_usize()
        .map_err(GlobalUsageMapError::Checked)?;
    let end = GLOBAL_USAGE_MAP_RECORD_END
        .to_usize()
        .map_err(GlobalUsageMapError::Checked)?;
    let record_len = GLOBAL_USAGE_MAP_RECORD_END
        .get()
        .checked_sub(GLOBAL_USAGE_MAP_RECORD_START.get())
        .ok_or(GlobalUsageMapError::Checked(Error::Arithmetic {
            operation: "derive global usage-map record length",
        }))?;
    let record = page
        .raw_bytes()
        .get(start..end)
        .ok_or(GlobalUsageMapError::Checked(Error::UnexpectedEnd {
            offset: crate::ByteOffset::new(GLOBAL_USAGE_MAP_RECORD_START.get()),
            needed: ByteCount::new(record_len),
            available: ByteCount::new(0),
        }))?;
    let decoded = decode_allocation_map(record, budget).map_err(GlobalUsageMapError::Record)?;
    let AllocationMap::Inline(inline) = decoded else {
        return Err(GlobalUsageMapError::Unsupported(
            UnsupportedGlobalUsageMap::IndirectOrExtendedLayout,
        ));
    };

    budget
        .charge_work_units(GLOBAL_USAGE_MAP_ZERO_SUFFIX_SLACK.get())
        .map_err(GlobalUsageMapError::Resource)?;
    let suffix_start = GLOBAL_USAGE_MAP_RECORD_END
        .get()
        .checked_sub(GLOBAL_USAGE_MAP_ZERO_SUFFIX_SLACK.get())
        .ok_or(GlobalUsageMapError::Checked(Error::Arithmetic {
            operation: "derive global usage-map zero suffix start",
        }))?;
    let suffix_start = PageOffset::new(suffix_start);
    let suffix = page
        .raw_bytes()
        .get(
            suffix_start
                .to_usize()
                .map_err(GlobalUsageMapError::Checked)?..end,
        )
        .ok_or(GlobalUsageMapError::Checked(Error::UnexpectedEnd {
            offset: crate::ByteOffset::new(suffix_start.get()),
            needed: GLOBAL_USAGE_MAP_ZERO_SUFFIX_SLACK,
            available: ByteCount::new(0),
        }))?;
    if let Some((relative, actual)) = suffix
        .iter()
        .copied()
        .enumerate()
        .find(|(_, byte)| *byte != GLOBAL_USAGE_MAP_SLACK_BYTE)
    {
        let relative = u64::try_from(relative).map_err(|_| {
            GlobalUsageMapError::Checked(Error::IntegerConversion {
                value: relative as u128,
                target: "u64",
            })
        })?;
        let offset =
            suffix_start
                .get()
                .checked_add(relative)
                .ok_or(GlobalUsageMapError::Checked(Error::Arithmetic {
                    operation: "locate invalid global usage-map slack byte",
                }))?;
        return Err(GlobalUsageMapError::InvalidZeroSuffixSlack {
            offset: PageOffset::new(offset),
            actual,
        });
    }

    Ok(GlobalUsageMapRecord { inline })
}
