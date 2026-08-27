//! Row-reference validation for index leaf entries from `EXP-0060`/`EXP-0062`.

use crate::index_tree::IndexTreeError;
use crate::row_directory::{RowDirectory, RowDirectoryError};
use crate::{
    DatabaseReader, JET3_PAGE_SIZE, PageKind, PageNumber, ReadAt, ResourceBudget, RowLocator,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

/// Validates leaf row locators against their data-page directories.
///
/// The most recently validated page is cached so runs of entries on one page
/// cost one read; every distinct page read remains charged as a page visit.
#[derive(Debug, Default)]
pub(crate) struct RowReferenceValidator {
    validated: Option<(PageNumber, u16)>,
}

impl RowReferenceValidator {
    pub(crate) fn validate<S: ReadAt>(
        &mut self,
        database: &mut DatabaseReader<S>,
        table_root: PageNumber,
        locator: RowLocator,
        scratch: &mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<(), IndexTreeError> {
        let row_count = match self.validated {
            Some((page, row_count)) if page == locator.page() => row_count,
            _ => {
                let kind = database
                    .read_classified_page(locator.page(), scratch, budget)
                    .map_err(IndexTreeError::Page)?
                    .kind();
                if kind != PageKind::Data {
                    return Err(IndexTreeError::UnexpectedRowPageKind {
                        page: locator.page(),
                        actual: kind,
                    });
                }
                let directory = RowDirectory::validate(locator.page(), table_root, scratch, budget)
                    .map_err(|source| IndexTreeError::RowDirectory {
                        page: locator.page(),
                        source,
                    })?;
                let row_count = directory.row_count();
                self.validated = Some((locator.page(), row_count));
                row_count
            }
        };
        if u16::from(locator.slot()) >= row_count {
            return Err(IndexTreeError::RowDirectory {
                page: locator.page(),
                source: RowDirectoryError::MissingRow {
                    row: locator.slot(),
                    row_count,
                },
            });
        }
        Ok(())
    }
}
