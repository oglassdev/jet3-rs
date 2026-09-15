//! Catalog overflow rows use the shared locator and hidden-target grammar
//! from EXP-0060, observed in native catalog records by EXP-0228.

use crate::data_page_directory::{DataPageEntry, PAGE_BYTES};
use crate::row::decode_pointer;
use crate::row_directory::RowDirectory;
use crate::{ByteCount, Error, OwnedPages, PageKind, PageNumber, ReadAt, RowError, RowLocator};

#[derive(Debug)]
pub(crate) struct CatalogOverflow {
    page: [u8; PAGE_BYTES],
    chain: Vec<RowLocator>,
}

impl CatalogOverflow {
    pub(crate) fn new() -> Self {
        Self {
            page: [0; PAGE_BYTES],
            chain: Vec::new(),
        }
    }

    pub(crate) fn resolve<'row, S: ReadAt>(
        &'row mut self,
        root: PageNumber,
        page: PageNumber,
        entry: &DataPageEntry,
        source: &'row [u8; PAGE_BYTES],
        owned: &mut OwnedPages<'_, S>,
    ) -> Result<&'row [u8], RowError> {
        if !entry.overflow() {
            return Ok(&source[entry.range()]);
        }
        let slot = u8::try_from(entry.row()).map_err(|_| {
            RowError::Resource(Error::IntegerConversion {
                value: u128::from(entry.row()),
                target: "u8",
            })
        })?;
        let logical = RowLocator::new(page, slot);
        let mut current = logical;
        let mut pointer = source[entry.range()]
            .try_into()
            .map_err(|_| RowError::InvalidOverflowTarget { locator: logical })?;
        self.chain.clear();
        loop {
            let target = decode_pointer(pointer);
            if target == current {
                return Err(RowError::SelfLink { locator: target });
            }
            let length = u64::try_from(self.chain.len()).map_err(|_| {
                RowError::Resource(Error::IntegerConversion {
                    value: self.chain.len() as u128,
                    target: "u64",
                })
            })?;
            let budget = owned.budget_mut();
            budget
                .charge_work_units(length)
                .map_err(RowError::Resource)?;
            if target == logical || self.chain.contains(&target) {
                return Err(RowError::Cycle { locator: target });
            }
            let depth = length
                .checked_add(1)
                .ok_or(RowError::Resource(Error::Arithmetic {
                    operation: "count catalog overflow depth",
                }))?;
            budget
                .check_chain_depth(depth)
                .map_err(RowError::Resource)?;
            budget.charge_items(1).map_err(RowError::Resource)?;
            if self.chain.len() == self.chain.capacity() {
                budget
                    .charge_allocation(ByteCount::new(size_of::<RowLocator>() as u64))
                    .map_err(RowError::Resource)?;
                self.chain.try_reserve_exact(1).map_err(|_| {
                    RowError::Resource(Error::Io {
                        operation: "reserve catalog overflow chain",
                        kind: std::io::ErrorKind::OutOfMemory,
                    })
                })?;
            }
            self.chain.push(target);
            let kind = owned
                .read_classified_page_into(target.page(), &mut self.page)
                .map_err(RowError::Allocation)?;
            if kind != PageKind::Data {
                return Err(RowError::UnexpectedOwnedPageKind {
                    page: target.page(),
                    actual: kind,
                });
            }
            let directory =
                RowDirectory::validate(target.page(), root, &self.page, owned.budget_mut())
                    .map_err(RowError::Directory)?;
            let stored = directory
                .entry(&self.page, target.slot())
                .map_err(RowError::Directory)?;
            if !stored.hidden() || stored.range().is_empty() {
                return Err(RowError::InvalidOverflowTarget { locator: target });
            }
            if !stored.overflow() {
                return Ok(&self.page[stored.range()]);
            }
            pointer = self.page[stored.range()]
                .try_into()
                .map_err(|_| RowError::InvalidOverflowTarget { locator: target })?;
            current = target;
        }
    }
}
