//! Bounded streaming Jet 3 row access from `EXP-0060` with `EXP-0061` value composition.
//!
//! This layer validates row storage, returns lossless physical field slices,
//! and delegates typed interpretation to the value layer.

use std::fmt;
use std::mem::size_of;
use std::ops::Range;

use crate::data_page_directory::{DataPageDirectory, LONG_VALUE_OWNER};
use crate::row_directory::{RowDirectory, RowDirectoryError, RowEntry};
use crate::{
    AllocationTraversalError, ByteCount, ColumnOrdinal, ColumnPhysicalType, ColumnStorageClass,
    DatabaseReader, DecodedValue, Error, JET3_PAGE_SIZE, OwnedPages, PageKind, PageNumber, ReadAt,
    ResourceBudget, RowLocator, TableDefinition, TextCodePage, ValueError,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const OVERFLOW_POINTER_LEN: usize = 4;

/// A sourced field that is either null or represented by exact physical bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RawField<'row> {
    Null,
    Bytes(&'row [u8]),
}

impl RawField<'_> {
    #[must_use]
    pub const fn is_null(self) -> bool {
        matches!(self, Self::Null)
    }
}

impl<'row> RawField<'row> {
    #[must_use]
    pub const fn raw_bytes(self) -> Option<&'row [u8]> {
        match self {
            Self::Null => None,
            Self::Bytes(bytes) => Some(bytes),
        }
    }
}

/// One validated logical row borrowed from a streaming cursor.
#[derive(Debug)]
pub struct RowView<'row, 'schema> {
    locator: RowLocator,
    storage_locator: RowLocator,
    raw: &'row [u8],
    definition: &'schema TableDefinition,
    layout: RowLayout,
    budget: &'row mut ResourceBudget,
}

impl<'row> RowView<'row, '_> {
    #[must_use]
    pub const fn locator(&self) -> RowLocator {
        self.locator
    }

    #[must_use]
    pub const fn storage_locator(&self) -> RowLocator {
        self.storage_locator
    }

    #[must_use]
    pub const fn raw_bytes(&self) -> &'row [u8] {
        self.raw
    }

    #[must_use]
    pub fn field(&self, ordinal: ColumnOrdinal) -> Option<RawField<'row>> {
        let column = self.definition.columns().get(usize::from(ordinal.get()))?;
        if column.physical_type() == ColumnPhysicalType::Boolean {
            let empty = self.layout.fixed_boundary..self.layout.fixed_boundary;
            return Some(RawField::Bytes(&self.raw[empty]));
        }
        if !self.layout.present(self.raw, ordinal) {
            return Some(RawField::Null);
        }
        let range = match column.storage() {
            ColumnStorageClass::Fixed { offset } => {
                let start = 1 + usize::from(offset);
                start..start + usize::from(column.size())
            }
            ColumnStorageClass::Variable { index } => {
                self.layout.variable_range(self.raw, index)?
            }
        };
        Some(RawField::Bytes(&self.raw[range]))
    }

    /// Decodes one field with an explicitly selected text code page.
    ///
    /// The same operation-wide budget used by the row cursor is charged before
    /// decoded output is produced. No database code page is inferred.
    pub fn value(
        &mut self,
        ordinal: ColumnOrdinal,
        code_page: TextCodePage,
    ) -> Result<Option<DecodedValue<'_>>, ValueError> {
        let Some(column) = self.definition.columns().get(usize::from(ordinal.get())) else {
            return Ok(None);
        };
        let physical_type = column.physical_type();
        let boolean_bit = self.layout.present(self.raw, ordinal);
        let field = if physical_type == ColumnPhysicalType::Boolean {
            let empty = self.layout.fixed_boundary..self.layout.fixed_boundary;
            RawField::Bytes(&self.raw[empty])
        } else if !boolean_bit {
            RawField::Null
        } else {
            let range = match column.storage() {
                ColumnStorageClass::Fixed { offset } => {
                    let start = 1 + usize::from(offset);
                    start..start + usize::from(column.size())
                }
                ColumnStorageClass::Variable { index } => self
                    .layout
                    .variable_range(self.raw, index)
                    .ok_or(ValueError::Resource(Error::Arithmetic {
                        operation: "access validated variable field",
                    }))?,
            };
            RawField::Bytes(&self.raw[range])
        };
        crate::value::decode_value(
            physical_type,
            field,
            boolean_bit,
            self.locator,
            code_page,
            self.budget,
        )
        .map(Some)
    }
}

/// A structured failure while traversing or validating rows.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum RowError {
    Allocation(AllocationTraversalError),
    Directory(RowDirectoryError),
    UnexpectedOwnedPageKind {
        page: PageNumber,
        actual: PageKind,
    },
    ColumnCountNotRepresentable {
        count: usize,
    },
    ColumnCountMismatch {
        expected: u8,
        actual: u8,
    },
    RowTooShort {
        length: usize,
        minimum: usize,
    },
    InvalidFixedBoundary {
        expected: usize,
        actual: usize,
    },
    VariableCountMismatch {
        expected: u8,
        actual: u8,
    },
    UnsupportedWideVariableOffsets {
        variable_count: u8,
        row_length: usize,
    },
    InvalidVariableBounds {
        index: u16,
        start: usize,
        end: usize,
        data_end: usize,
    },
    NonzeroUnusedNullBits {
        raw: u8,
        mask: u8,
    },
    InvalidOverflowTarget {
        locator: RowLocator,
    },
    SelfLink {
        locator: RowLocator,
    },
    Cycle {
        locator: RowLocator,
    },
    Resource(Error),
}

impl fmt::Display for RowError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "row stream failed: {self:?}")
    }
}

impl std::error::Error for RowError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Allocation(source) => Some(source),
            Self::Directory(source) => Some(source),
            Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

/// Forward-only access to validated logical rows of one table definition.
#[derive(Debug)]
pub struct RowCursor<'operation, 'schema, S> {
    root: PageNumber,
    definition: &'schema TableDefinition,
    pub(crate) owned: OwnedPages<'operation, S>,
    pub(crate) page: [u8; PAGE_BYTES],
    pub(crate) current_page: Option<PageNumber>,
    pub(crate) resume_page: Option<PageNumber>,
    directory: Option<RowDirectory>,
    chain: Vec<RowLocator>,
    failed: bool,
}

#[derive(Debug, Clone, Copy)]
struct RowMetadata {
    locator: RowLocator,
    storage_locator: RowLocator,
    start: usize,
    end: usize,
    layout: RowLayout,
}

impl<'operation, 'schema, S: ReadAt> RowCursor<'operation, 'schema, S> {
    pub(crate) fn new(
        database: &'operation mut DatabaseReader<S>,
        definition: &'schema TableDefinition,
        budget: &'operation mut ResourceBudget,
    ) -> Result<Self, RowError> {
        let count = definition.columns().len();
        u8::try_from(count).map_err(|_| RowError::ColumnCountNotRepresentable { count })?;
        let mut owned = database
            .owned_pages(definition.root(), budget)
            .map_err(RowError::Allocation)?;
        let maximum_depth = owned.budget_mut().limits().max_chain_depth();
        let capacity = usize::try_from(maximum_depth).map_err(|_| {
            RowError::Resource(Error::IntegerConversion {
                value: u128::from(maximum_depth),
                target: "usize",
            })
        })?;
        let bytes = maximum_depth
            .checked_mul(size_of::<RowLocator>() as u64)
            .ok_or(RowError::Resource(Error::Arithmetic {
                operation: "size row-chain scratch state",
            }))?;
        owned
            .budget_mut()
            .charge_allocation(ByteCount::new(bytes))
            .map_err(RowError::Resource)?;
        let mut chain = Vec::new();
        chain.try_reserve_exact(capacity).map_err(|_| {
            RowError::Resource(Error::Io {
                operation: "reserve row-chain scratch state",
                kind: std::io::ErrorKind::OutOfMemory,
            })
        })?;
        Ok(Self {
            root: definition.root(),
            definition,
            owned,
            page: [0; PAGE_BYTES],
            current_page: None,
            resume_page: None,
            directory: None,
            chain,
            failed: false,
        })
    }

    /// Returns the next active logical row, following any validated overflow chain.
    ///
    /// The returned view borrows the cursor's only row-page buffer. Any error
    /// exhausts the cursor; subsequent calls perform no work.
    pub fn next_row(&mut self) -> Result<Option<RowView<'_, 'schema>>, RowError> {
        if self.failed {
            return Ok(None);
        }
        let metadata = match self.next_metadata() {
            Ok(metadata) => metadata,
            Err(error) => {
                self.failed = true;
                return Err(error);
            }
        };
        let Some(metadata) = metadata else {
            return Ok(None);
        };
        let raw = &self.page[metadata.start..metadata.end];
        let budget = self.owned.budget_mut();
        Ok(Some(RowView {
            locator: metadata.locator,
            storage_locator: metadata.storage_locator,
            raw,
            definition: self.definition,
            layout: metadata.layout,
            budget,
        }))
    }

    fn next_metadata(&mut self) -> Result<Option<RowMetadata>, RowError> {
        loop {
            self.restore_page_if_needed()?;
            if let Some(directory) = &mut self.directory {
                if let Some(entry) = directory
                    .next_primary(&self.page)
                    .map_err(RowError::Directory)?
                {
                    if entry.overflow() {
                        let resume =
                            self.current_page
                                .ok_or(RowError::Resource(Error::Arithmetic {
                                    operation: "resume overflow source page",
                                }))?;
                        self.resume_page = Some(resume);
                        return self.follow_overflow(entry).map(Some);
                    }
                    return self.finish_entry(entry.locator(), entry).map(Some);
                }
                self.directory = None;
                self.current_page = None;
            }
            let Some((page, kind)) = self
                .owned
                .next_classified_page_into(&mut self.page)
                .map_err(RowError::Allocation)?
            else {
                return Ok(None);
            };
            if kind != PageKind::Data {
                return Err(RowError::UnexpectedOwnedPageKind { page, actual: kind });
            }
            if DataPageDirectory::owner(&self.page) == LONG_VALUE_OWNER {
                continue;
            }
            self.directory = Some(
                RowDirectory::validate(page, self.root, &self.page, self.owned.budget_mut())
                    .map_err(RowError::Directory)?,
            );
            self.current_page = Some(page);
        }
    }

    fn restore_page_if_needed(&mut self) -> Result<(), RowError> {
        let Some(page) = self.resume_page.take() else {
            return Ok(());
        };
        let previous = self
            .directory
            .take()
            .ok_or(RowError::Resource(Error::Arithmetic {
                operation: "restore overflow source directory",
            }))?;
        let kind = self
            .owned
            .read_classified_page_into(page, &mut self.page)
            .map_err(RowError::Allocation)?;
        if kind != PageKind::Data {
            return Err(RowError::UnexpectedOwnedPageKind { page, actual: kind });
        }
        let directory =
            RowDirectory::validate(page, self.root, &self.page, self.owned.budget_mut())
                .and_then(|directory| directory.resume_after(&previous))
                .map_err(RowError::Directory)?;
        self.directory = Some(directory);
        self.current_page = Some(page);
        Ok(())
    }

    fn follow_overflow(&mut self, entry: RowEntry) -> Result<RowMetadata, RowError> {
        let logical = entry.locator();
        let range = entry.range();
        let mut pointer: [u8; OVERFLOW_POINTER_LEN] = self.page[range]
            .try_into()
            .map_err(|_| RowError::InvalidOverflowTarget { locator: logical })?;
        self.chain.clear();
        let mut current = logical;
        loop {
            let target = decode_pointer(pointer);
            if target == current {
                return Err(RowError::SelfLink { locator: target });
            }
            let comparisons = u64::try_from(self.chain.len()).map_err(|_| {
                RowError::Resource(Error::IntegerConversion {
                    value: self.chain.len() as u128,
                    target: "u64",
                })
            })?;
            self.owned
                .budget_mut()
                .charge_work_units(comparisons)
                .map_err(RowError::Resource)?;
            if target == logical || self.chain.contains(&target) {
                return Err(RowError::Cycle { locator: target });
            }
            let depth = u64::try_from(self.chain.len())
                .ok()
                .and_then(|length| length.checked_add(1))
                .ok_or({
                    RowError::Resource(Error::IntegerConversion {
                        value: self.chain.len() as u128,
                        target: "u64",
                    })
                })?;
            self.owned
                .budget_mut()
                .check_chain_depth(depth)
                .map_err(RowError::Resource)?;
            self.owned
                .budget_mut()
                .charge_items(1)
                .map_err(RowError::Resource)?;
            self.chain.push(target);
            let kind = self
                .owned
                .read_classified_page_into(target.page(), &mut self.page)
                .map_err(RowError::Allocation)?;
            if kind != PageKind::Data {
                return Err(RowError::UnexpectedOwnedPageKind {
                    page: target.page(),
                    actual: kind,
                });
            }
            let directory = RowDirectory::validate(
                target.page(),
                self.root,
                &self.page,
                self.owned.budget_mut(),
            )
            .map_err(RowError::Directory)?;
            let target_entry = directory
                .entry(&self.page, target.slot())
                .map_err(RowError::Directory)?;
            if !target_entry.hidden() || target_entry.range().is_empty() {
                return Err(RowError::InvalidOverflowTarget { locator: target });
            }
            if target_entry.overflow() {
                let target_range = target_entry.range();
                pointer = self.page[target_range]
                    .try_into()
                    .map_err(|_| RowError::InvalidOverflowTarget { locator: target })?;
                current = target;
                continue;
            }
            return self.finish_entry(logical, target_entry);
        }
    }

    fn finish_entry(
        &mut self,
        logical: RowLocator,
        entry: RowEntry,
    ) -> Result<RowMetadata, RowError> {
        let range = entry.range();
        let layout = RowLayout::validate(
            &self.page[range.clone()],
            self.definition,
            self.owned.budget_mut(),
        )?;
        Ok(RowMetadata {
            locator: logical,
            storage_locator: entry.locator(),
            start: range.start,
            end: range.end,
            layout,
        })
    }
}

impl<S: ReadAt> DatabaseReader<S> {
    /// Starts a bounded logical-row stream for an already decoded definition.
    pub fn rows<'operation, 'schema>(
        &'operation mut self,
        definition: &'schema TableDefinition,
        budget: &'operation mut ResourceBudget,
    ) -> Result<RowCursor<'operation, 'schema, S>, RowError> {
        RowCursor::new(self, definition, budget)
    }
}

#[derive(Debug, Clone, Copy)]
struct RowLayout {
    fixed_boundary: usize,
    offsets_start: usize,
    null_start: usize,
    variable_count: u8,
    wide: bool,
}

impl RowLayout {
    fn validate(
        row: &[u8],
        definition: &TableDefinition,
        budget: &mut ResourceBudget,
    ) -> Result<Self, RowError> {
        let expected_count = u8::try_from(definition.columns().len()).map_err(|_| {
            RowError::ColumnCountNotRepresentable {
                count: definition.columns().len(),
            }
        })?;
        let null_len = usize::from(expected_count).div_ceil(8);
        let minimum = 1 + null_len;
        if row.len() < minimum {
            return Err(RowError::RowTooShort {
                length: row.len(),
                minimum,
            });
        }
        if row[0] != expected_count {
            return Err(RowError::ColumnCountMismatch {
                expected: expected_count,
                actual: row[0],
            });
        }
        budget
            .charge_items(u64::from(expected_count))
            .map_err(RowError::Resource)?;
        let fixed_size = definition
            .columns()
            .iter()
            .filter_map(|column| match column.storage() {
                ColumnStorageClass::Fixed { offset }
                    if column.physical_type() != ColumnPhysicalType::Boolean =>
                {
                    Some(usize::from(offset) + usize::from(column.size()))
                }
                _ => None,
            })
            .max()
            .unwrap_or(0);
        let fixed_boundary = 1 + fixed_size;
        let variable_count = definition
            .columns()
            .iter()
            .filter(|column| matches!(column.storage(), ColumnStorageClass::Variable { .. }))
            .count();
        let variable_count =
            u8::try_from(variable_count).map_err(|_| RowError::ColumnCountNotRepresentable {
                count: definition.columns().len(),
            })?;
        let null_start = row.len() - null_len;
        validate_unused_null_bits(row, expected_count, null_start)?;
        if variable_count == 0 {
            if null_start != fixed_boundary {
                return Err(RowError::InvalidFixedBoundary {
                    expected: fixed_boundary,
                    actual: null_start,
                });
            }
            return Ok(Self {
                fixed_boundary,
                offsets_start: null_start,
                null_start,
                variable_count,
                wide: false,
            });
        }
        if null_start == 0 {
            return Err(RowError::RowTooShort {
                length: row.len(),
                minimum: minimum + 1,
            });
        }
        let count_position = null_start - 1;
        let actual_variable_count = row[count_position];
        if actual_variable_count != variable_count {
            return Err(RowError::VariableCountMismatch {
                expected: variable_count,
                actual: actual_variable_count,
            });
        }
        let wide = row.len() > usize::from(u8::MAX);
        if wide && variable_count != 1 {
            return Err(RowError::UnsupportedWideVariableOffsets {
                variable_count,
                row_length: row.len(),
            });
        }
        let low_count = usize::from(variable_count) + 1;
        let jump_count = usize::from(wide);
        let trailer = low_count + jump_count;
        let offsets_start = count_position
            .checked_sub(trailer)
            .ok_or(RowError::RowTooShort {
                length: row.len(),
                minimum: minimum + 1 + trailer,
            })?;
        let layout = Self {
            fixed_boundary,
            offsets_start,
            null_start,
            variable_count,
            wide,
        };
        let actual_fixed = layout.boundary(row, 0)?;
        if actual_fixed != fixed_boundary {
            return Err(RowError::InvalidFixedBoundary {
                expected: fixed_boundary,
                actual: actual_fixed,
            });
        }
        let mut start = actual_fixed;
        for index in 0..variable_count {
            let end = layout.boundary(row, index + 1)?;
            if start > end || end > offsets_start {
                return Err(RowError::InvalidVariableBounds {
                    index: u16::from(index),
                    start,
                    end,
                    data_end: offsets_start,
                });
            }
            start = end;
        }
        if start != offsets_start {
            return Err(RowError::InvalidVariableBounds {
                index: u16::from(variable_count.saturating_sub(1)),
                start,
                end: offsets_start,
                data_end: offsets_start,
            });
        }
        Ok(layout)
    }

    fn present(self, row: &[u8], ordinal: ColumnOrdinal) -> bool {
        let bit = usize::from(ordinal.get());
        let byte = self.null_start + bit / 8;
        row.get(byte)
            .is_some_and(|raw| raw & (1_u8 << (bit % 8)) != 0)
    }

    fn variable_range(self, row: &[u8], index: u16) -> Option<Range<usize>> {
        let index = u8::try_from(index).ok()?;
        if index >= self.variable_count {
            return None;
        }
        let start = self.boundary(row, index).ok()?;
        let end = self.boundary(row, index + 1).ok()?;
        Some(start..end)
    }

    fn boundary(self, row: &[u8], ordinal: u8) -> Result<usize, RowError> {
        let reversed = usize::from(self.variable_count - ordinal);
        let low = usize::from(row[self.offsets_start + reversed]);
        if !self.wide {
            return Ok(low);
        }
        let jump = row[self.offsets_start + usize::from(self.variable_count) + 1];
        let high = usize::from((jump >> reversed) & 1);
        Ok(low + 256 * high)
    }
}

fn validate_unused_null_bits(
    row: &[u8],
    column_count: u8,
    null_start: usize,
) -> Result<(), RowError> {
    let used = column_count % 8;
    if used == 0 || column_count == 0 {
        return Ok(());
    }
    let mask = !((1_u8 << used) - 1);
    let raw = *row.last().ok_or(RowError::RowTooShort {
        length: row.len(),
        minimum: null_start + 1,
    })?;
    if raw & mask != 0 {
        return Err(RowError::NonzeroUnusedNullBits { raw, mask });
    }
    Ok(())
}

fn decode_pointer(raw: [u8; OVERFLOW_POINTER_LEN]) -> RowLocator {
    let page = u32::from_le_bytes([raw[1], raw[2], raw[3], 0]);
    RowLocator::new(PageNumber::new(u64::from(page)), raw[0])
}

#[cfg(test)]
#[path = "row_tests.rs"]
mod tests;
