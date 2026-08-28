//! Row traversal mechanics whose format assertions are bound to EXP-0060.

use std::mem::size_of;

use crate::data_page_directory::{DataPageDirectory, LONG_VALUE_OWNER};
use crate::row::PAGE_BYTES;
use crate::row_directory::{RowDirectory, RowEntry};
use crate::row_layout::RowLayout;
use crate::{
    ByteCount, DatabaseReader, Error, PageKind, PageNumber, ReadAt, ResourceBudget, RowCoverage,
    RowCursor, RowError, RowLocator, RowView, TableDefinition,
};

const OVERFLOW_POINTER_LEN: usize = 4;

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
            coverage: RowCoverage::default(),
        })
    }

    /// Returns storage branches observed so far without changing the stream.
    #[must_use]
    pub const fn coverage(&self) -> RowCoverage {
        self.coverage
    }

    /// Borrows the operation-wide budget held by this cursor.
    ///
    /// This permits a streaming adapter to charge retained allocations
    /// between rows without creating a second accounting state.
    pub fn budget_mut(&mut self) -> &mut ResourceBudget {
        self.owned.budget_mut()
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
        Ok(Some(RowView::new(
            metadata.locator,
            metadata.storage_locator,
            raw,
            self.definition,
            metadata.layout,
            budget,
        )))
    }

    fn next_metadata(&mut self) -> Result<Option<RowMetadata>, RowError> {
        loop {
            self.restore_page_if_needed()?;
            if let Some(directory) = &mut self.directory {
                let (entry, skipped_hidden) = directory
                    .next_primary(&self.page)
                    .map_err(RowError::Directory)?;
                self.coverage.deleted_skip |= skipped_hidden;
                if let Some(entry) = entry {
                    if entry.overflow() {
                        self.coverage.overflow_pointer = true;
                        let resume =
                            self.current_page
                                .ok_or(RowError::Resource(Error::Arithmetic {
                                    operation: "resume overflow source page",
                                }))?;
                        self.resume_page = Some(resume);
                        return self.follow_overflow(entry).map(Some);
                    }
                    self.coverage.direct = true;
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
        self.coverage.wide_variable_layout |= layout.wide;
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

fn decode_pointer(raw: [u8; OVERFLOW_POINTER_LEN]) -> RowLocator {
    let page = u32::from_le_bytes([raw[1], raw[2], raw[3], 0]);
    RowLocator::new(PageNumber::new(u64::from(page)), raw[0])
}
