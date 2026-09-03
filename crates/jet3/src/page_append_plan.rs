//! Crate-private composition of complete page images with physical page slots.
//!
//! This module does not build pages or perform I/O. It pairs already-complete
//! [`PageImage`] values with existing slots in the fixed empty-database image,
//! or assigns fresh append slots and applies the matching global-map transition
//! observed in `EXP-0065`.

#![allow(
    dead_code,
    reason = "staged for the next crate-private writer composition layer"
)]

use std::fmt;

use crate::{InlineUsageMapEncoder, PageImage, PageNumber, UsageMapWriteError};

// EXP-0065 Q1: every empty Jet 3 database in the accepted A9 acquisition had
// 20 pages.
pub(crate) const EMPTY_DATABASE_PAGE_COUNT: u64 = 20;

/// A complete page image paired with its planned physical page number.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PlannedPage {
    number: PageNumber,
    image: PageImage,
}

impl PlannedPage {
    /// Returns the planned physical page number.
    pub(crate) const fn number(&self) -> PageNumber {
        self.number
    }

    /// Returns the complete page image without interpreting its contents.
    pub(crate) const fn image(&self) -> &PageImage {
        &self.image
    }

    /// Splits the planned page into its number and complete image.
    pub(crate) fn into_parts(self) -> (PageNumber, PageImage) {
        (self.number, self.image)
    }
}

/// Structured failure while planning one existing empty-database page.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ExistingPageError {
    /// The requested page is not part of the observed 20-page empty image.
    OutsideEmptyDatabase {
        /// Rejected existing page.
        page: PageNumber,
    },
}

impl fmt::Display for ExistingPageError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::OutsideEmptyDatabase { page } => write!(
                formatter,
                "page {} is outside the {EMPTY_DATABASE_PAGE_COUNT}-page empty database",
                page.get()
            ),
        }
    }
}

impl std::error::Error for ExistingPageError {}

/// Pairs a complete image with one existing page in the fixed empty database.
///
/// The image is moved through unchanged and is not inspected. `EXP-0065` Q1
/// establishes only that the observed empty database has pages 0 through 19;
/// this function assigns no semantic role to any page or byte.
pub(crate) fn plan_existing_page(
    number: PageNumber,
    image: PageImage,
) -> Result<PlannedPage, ExistingPageError> {
    if number.get() >= EMPTY_DATABASE_PAGE_COUNT {
        return Err(ExistingPageError::OutsideEmptyDatabase { page: number });
    }
    Ok(PlannedPage { number, image })
}

/// Pairs exactly 20 complete images with existing slots 0 through 19.
///
/// The fixed-size input makes every slot valid by construction. As with
/// [`plan_existing_page`], the images are moved through without inspection or
/// any assignment of bootstrap semantics.
pub(crate) fn plan_existing_pages(
    images: [PageImage; EMPTY_DATABASE_PAGE_COUNT as usize],
) -> impl ExactSizeIterator<Item = PlannedPage> {
    images
        .into_iter()
        .enumerate()
        .map(|(number, image)| PlannedPage {
            number: PageNumber::new(number as u64),
            image,
        })
}

/// Structured failure while planning one fresh page append.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AppendPageError {
    /// The current page count cannot advance after another append.
    PageCountOverflow {
        /// Page count that could not be incremented.
        page_count: u64,
    },
    /// The global free map already marks the append slot as in use.
    PageAlreadyInUse {
        /// Rejected append slot.
        page: PageNumber,
    },
    /// The supplied inline map cannot represent or update the append slot.
    GlobalMap(UsageMapWriteError),
}

impl fmt::Display for AppendPageError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::PageCountOverflow { page_count } => {
                write!(formatter, "page count {page_count} cannot advance")
            }
            Self::PageAlreadyInUse { page } => {
                write!(formatter, "append page {} is already in use", page.get())
            }
            Self::GlobalMap(source) => {
                write!(formatter, "global usage map rejected append: {source}")
            }
        }
    }
}

impl std::error::Error for AppendPageError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::GlobalMap(source) => Some(source),
            Self::PageCountOverflow { .. } | Self::PageAlreadyInUse { .. } => None,
        }
    }
}

impl From<UsageMapWriteError> for AppendPageError {
    fn from(source: UsageMapWriteError) -> Self {
        Self::GlobalMap(source)
    }
}

/// Tracks append-only page numbering for a fresh database image.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct AppendPagePlan {
    page_count: u64,
}

impl AppendPagePlan {
    /// Starts immediately after the 20-page empty-database image from A9 Q1.
    pub(crate) const fn after_empty_database() -> Self {
        Self {
            page_count: EMPTY_DATABASE_PAGE_COUNT,
        }
    }

    /// Returns the page count after all successfully planned appends.
    pub(crate) const fn page_count(self) -> u64 {
        self.page_count
    }

    /// Assigns the next page number and marks that global-map page in use.
    ///
    /// `image` is moved through unchanged. The map and page count remain
    /// unchanged if the next slot is not free, is outside the map, or the page
    /// count cannot advance.
    pub(crate) fn append(
        &mut self,
        image: PageImage,
        global_free_map: &mut InlineUsageMapEncoder,
    ) -> Result<PlannedPage, AppendPageError> {
        let next_count =
            self.page_count
                .checked_add(1)
                .ok_or(AppendPageError::PageCountOverflow {
                    page_count: self.page_count,
                })?;
        let number = PageNumber::new(self.page_count);
        if !global_free_map.is_set(number)? {
            return Err(AppendPageError::PageAlreadyInUse { page: number });
        }

        // EXP-0051 and EXP-0065 Q2: a set global-map bit means free, and each
        // newly appended page transitions to in use.
        global_free_map.clear_page(number)?;
        self.page_count = next_count;
        Ok(PlannedPage { number, image })
    }
}

#[cfg(test)]
#[path = "page_append_plan_tests.rs"]
mod tests;
