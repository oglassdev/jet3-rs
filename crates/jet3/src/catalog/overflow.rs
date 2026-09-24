//! Catalog overflow rows use the shared locator and hidden-target grammar
//! from EXP-0060, observed in native catalog records by EXP-0228.

use crate::{
    ByteCount, CatalogError, Error, OwnedPages, PageKind, PageNumber, ReadAt, RowError, RowLocator,
    format::data_page_directory::{DataPageEntry, PAGE_BYTES},
    row::{directory::RowDirectory, reader::decode_pointer},
};

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
    ) -> Result<&'row [u8], CatalogError> {
        if !entry.overflow() {
            return Ok(&source[entry.range()]);
        }
        let pointer = source[entry.range()].try_into().map_err(|_| {
            CatalogError::InvalidOverflowPointerLength {
                page,
                row: entry.row(),
                length: entry.range().len(),
            }
        })?;
        self.follow(root, (page, entry.row()), pointer, owned)
            .map_err(CatalogError::Overflow)
    }

    fn follow<S: ReadAt>(
        &mut self,
        root: PageNumber,
        logical: (PageNumber, u16),
        mut pointer: [u8; 4],
        owned: &mut OwnedPages<'_, S>,
    ) -> Result<&[u8], RowError> {
        let mut current = logical;
        self.chain.clear();
        loop {
            let target = decode_pointer(pointer);
            let address = (target.page(), u16::from(target.slot()));
            if address == current {
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
            if address == logical || self.chain.contains(&target) {
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
            current = address;
        }
    }
}
