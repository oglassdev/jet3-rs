//! Row-reference validation for index leaf entries from `EXP-0060`/`EXP-0062`.

use crate::index_tree::{IndexTreeError, push_charged};
use crate::row_directory::{RowDirectory, RowDirectoryError};
use crate::{
    DatabaseReader, JET3_PAGE_SIZE, PageKind, PageNumber, ReadAt, ResourceBudget, RowLocator,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

/// Validates leaf row locators against their data-page directories.
///
/// Every validated page's row count is retained in charged scratch, so each
/// distinct data page costs one read per traversal regardless of how leaf
/// entries interleave across pages. The scratch is bounded by the page-visit
/// ceiling because each retained page was read exactly once.
#[derive(Debug, Default)]
pub(crate) struct RowReferenceValidator {
    validated: Vec<(PageNumber, u16)>,
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
        let cached = self
            .validated
            .iter()
            .find(|(page, _)| *page == locator.page())
            .map(|(_, row_count)| *row_count);
        let row_count = match cached {
            Some(row_count) => row_count,
            None => {
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
                push_charged(
                    &mut self.validated,
                    (locator.page(), row_count),
                    budget,
                    "reserve validated row page",
                )?;
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
