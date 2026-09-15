//! Creation placement adapter for the shared EXP-0062 numeric tree builder.

use super::*;
use crate::numeric_index_pages::{NumericIndexPages, TreeBuildError};

#[derive(Debug, Clone)]
pub(super) struct IndexPages {
    layout: NumericIndexPages,
}

impl IndexPages {
    pub(super) fn new(
        entries: &[Entry],
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        NumericIndexPages::new(entries, (MAP_BITMAP_BYTES * 8) as usize, budget)
            .map(|layout| Self { layout })
            .map_err(creation_error)
    }

    pub(super) fn extra_count(&self) -> u64 {
        self.layout.len() as u64 - 1
    }

    pub(super) fn image(
        &self,
        entries: &[Entry],
        owner: PageNumber,
        root: PageNumber,
        first_extra: u64,
        ordinal: Option<usize>,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        let last = first_extra
            .checked_add(self.extra_count())
            .ok_or(Error::Arithmetic {
                operation: "place initial index pages",
            })?;
        if last > MAP_BITMAP_BYTES * 8 {
            return Err(UsageMapWriteError::PageOutOfMap {
                page: PageNumber::new(last - 1),
                first: PageNumber::new(0),
                page_count: MAP_BITMAP_BYTES * 8,
            }
            .into());
        }
        let page_for = |ordinal| {
            if ordinal >= self.layout.len() {
                None
            } else if ordinal + 1 == self.layout.len() {
                Some(root)
            } else {
                Some(PageNumber::new(first_extra + ordinal as u64))
            }
        };
        self.layout
            .image(
                ordinal.unwrap_or(self.layout.len() - 1),
                entries,
                page_for,
                owner,
                &[0; PAGE_BYTES],
                budget,
            )
            .map_err(creation_error)
    }
}

fn creation_error(error: TreeBuildError) -> ComposeError {
    match error {
        TreeBuildError::NodeLimit { maximum } => UsageMapWriteError::PageOutOfMap {
            page: PageNumber::new(maximum as u64),
            first: PageNumber::new(0),
            page_count: maximum as u64,
        }
        .into(),
        TreeBuildError::Encoding(error) => error.into(),
        TreeBuildError::Layout(operation) => Error::Arithmetic { operation }.into(),
    }
}
