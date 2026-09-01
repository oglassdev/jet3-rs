//! Crate-private aggregation of complete page plans in physical file order.
//!
//! This module combines the existing-slot and append planners without
//! assigning meaning to any page image. The caller supplies all 20 existing
//! images and every appended image already complete. The resulting sequence
//! does not establish that its bytes form a DAO-openable bootstrap image.

#![allow(
    dead_code,
    reason = "staged for the future crate-private writer composition layer"
)]

use std::fmt;

use crate::page_append_plan::{
    AppendPageError, AppendPagePlan, EMPTY_DATABASE_PAGE_COUNT, ExistingPageError, PlannedPage,
    plan_existing_page,
};
use crate::{InlineUsageMapEncoder, PageImage, PageNumber};

const EXISTING_PAGE_COUNT: usize = EMPTY_DATABASE_PAGE_COUNT as usize;

/// Structured failure while aggregating a whole-file page plan.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum WholeFilePlanError {
    /// Storage for the requested number of complete page plans was unavailable.
    PageStorageUnavailable {
        /// Total page count the plan attempted to retain.
        requested_page_count: u64,
    },
    /// An existing image could not be paired with its physical slot.
    Existing(ExistingPageError),
    /// A fresh image could not be appended.
    Append(AppendPageError),
}

impl fmt::Display for WholeFilePlanError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::PageStorageUnavailable {
                requested_page_count,
            } => write!(
                formatter,
                "storage unavailable for {requested_page_count} planned pages"
            ),
            Self::Existing(source) => write!(formatter, "existing page plan failed: {source}"),
            Self::Append(source) => write!(formatter, "page append plan failed: {source}"),
        }
    }
}

impl std::error::Error for WholeFilePlanError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Existing(source) => Some(source),
            Self::Append(source) => Some(source),
            Self::PageStorageUnavailable { .. } => None,
        }
    }
}

impl From<ExistingPageError> for WholeFilePlanError {
    fn from(source: ExistingPageError) -> Self {
        Self::Existing(source)
    }
}

impl From<AppendPageError> for WholeFilePlanError {
    fn from(source: AppendPageError) -> Self {
        Self::Append(source)
    }
}

/// Complete page images ordered by their planned physical file slots.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct WholeFileImagePlan {
    pages: Vec<PlannedPage>,
    append_plan: AppendPagePlan,
}

impl WholeFileImagePlan {
    /// Pairs 20 caller-complete images with existing slots 0 through 19.
    ///
    /// `EXP-0065` Q1 establishes only the fixed page count and slot range.
    /// This constructor neither inspects page bytes nor assigns bootstrap
    /// roles, references, or sufficiency to them.
    pub(crate) fn from_existing_pages(
        images: [PageImage; EXISTING_PAGE_COUNT],
    ) -> Result<Self, WholeFilePlanError> {
        let mut pages = Vec::new();
        pages.try_reserve_exact(EXISTING_PAGE_COUNT).map_err(|_| {
            WholeFilePlanError::PageStorageUnavailable {
                requested_page_count: EMPTY_DATABASE_PAGE_COUNT,
            }
        })?;

        for (number, image) in (0_u64..).zip(images) {
            pages.push(plan_existing_page(PageNumber::new(number), image)?);
        }

        Ok(Self {
            pages,
            append_plan: AppendPagePlan::after_empty_database(),
        })
    }

    /// Returns all retained page plans in increasing physical-page order.
    pub(crate) fn pages(&self) -> &[PlannedPage] {
        &self.pages
    }

    /// Returns the page count after all successful appends.
    pub(crate) const fn page_count(&self) -> u64 {
        self.append_plan.page_count()
    }

    /// Plans and retains one fresh page after the existing sequence.
    ///
    /// Storage is reserved before the append planner changes the page count or
    /// global free map. On any error, the retained sequence, count, and map are
    /// logically unchanged.
    pub(crate) fn append(
        &mut self,
        image: PageImage,
        global_free_map: &mut InlineUsageMapEncoder,
    ) -> Result<PageNumber, WholeFilePlanError> {
        let requested_page_count = self.page_count().checked_add(1).ok_or_else(|| {
            WholeFilePlanError::Append(AppendPageError::PageCountOverflow {
                page_count: self.page_count(),
            })
        })?;
        self.pages
            .try_reserve(1)
            .map_err(|_| WholeFilePlanError::PageStorageUnavailable {
                requested_page_count,
            })?;

        let planned = self.append_plan.append(image, global_free_map)?;
        let number = planned.number();
        self.pages.push(planned);
        Ok(number)
    }

    /// Consumes the aggregate and returns its ordered page plans.
    pub(crate) fn into_pages(self) -> Vec<PlannedPage> {
        self.pages
    }
}

#[cfg(test)]
#[path = "whole_file_plan_tests.rs"]
mod tests;
