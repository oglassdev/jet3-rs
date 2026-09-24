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

use std::{fmt, mem::size_of};

use crate::{
    ByteCount, Error, InlineUsageMapEncoder, PageImage, PageNumber, ResourceBudget,
    create::page_append_plan::{
        AppendPageError, AppendPagePlan, EMPTY_DATABASE_PAGE_COUNT, PlannedPage,
        plan_existing_pages,
    },
};

pub(super) const EXISTING_PAGE_COUNT: usize = EMPTY_DATABASE_PAGE_COUNT as usize;

/// Structured failure while aggregating a whole-file page plan.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WholeFilePlanError {
    /// A requested replacement has no planned slot.
    MissingPage {
        /// Requested page number.
        page: PageNumber,
    },
    /// Resource accounting or allocation failed while retaining page plans.
    Resource(Error),
    /// A fresh image could not be appended.
    Append(AppendPageError),
}

impl fmt::Display for WholeFilePlanError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingPage { page } => {
                write!(formatter, "no planned page {} to replace", page.get())
            }
            Self::Resource(source) => {
                write!(formatter, "whole-file plan resource failure: {source}")
            }
            Self::Append(source) => write!(formatter, "page append plan failed: {source}"),
        }
    }
}

impl std::error::Error for WholeFilePlanError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::MissingPage { .. } => None,
            Self::Resource(source) => Some(source),
            Self::Append(source) => Some(source),
        }
    }
}

impl From<Error> for WholeFilePlanError {
    fn from(source: Error) -> Self {
        Self::Resource(source)
    }
}

impl From<AppendPageError> for WholeFilePlanError {
    fn from(source: AppendPageError) -> Self {
        Self::Append(source)
    }
}

/// Complete page images ordered by their planned physical file slots.
#[derive(Debug, PartialEq, Eq)]
pub(crate) struct WholeFileImagePlan {
    pages: Vec<PlannedPage>,
    append_plan: AppendPagePlan,
}

impl WholeFileImagePlan {
    /// Pairs 20 caller-complete images with existing slots 0 through 19.
    ///
    /// `EXP-0065` Q1 establishes only the fixed page count and slot range.
    /// This constructor neither inspects page bytes nor assigns bootstrap
    /// roles, references, or sufficiency to them. The logical storage for all
    /// retained plans is charged to `budget` before reservation.
    pub(crate) fn from_existing_pages(
        images: [PageImage; EXISTING_PAGE_COUNT],
        budget: &mut ResourceBudget,
    ) -> Result<Self, WholeFilePlanError> {
        let mut pages = Vec::new();
        reserve_planned_pages(&mut pages, EXISTING_PAGE_COUNT, budget)?;
        pages.extend(plan_existing_pages(images));

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
    /// Logical storage for the page plan is charged and reserved before the
    /// append planner changes the page count or global free map. On any error,
    /// the retained sequence, count, and map are logically unchanged.
    pub(crate) fn append(
        &mut self,
        image: PageImage,
        global_free_map: &mut InlineUsageMapEncoder,
        budget: &mut ResourceBudget,
    ) -> Result<PageNumber, WholeFilePlanError> {
        self.page_count().checked_add(1).ok_or_else(|| {
            WholeFilePlanError::Append(AppendPageError::PageCountOverflow {
                page_count: self.page_count(),
            })
        })?;
        reserve_planned_pages(&mut self.pages, 1, budget)?;

        let planned = self.append_plan.append(image, global_free_map)?;
        let number = planned.number();
        self.pages.push(planned);
        Ok(number)
    }

    /// Appends a page whose allocation is composed after all placements are known.
    pub(crate) fn append_image(
        &mut self,
        image: PageImage,
        budget: &mut ResourceBudget,
    ) -> Result<PageNumber, WholeFilePlanError> {
        reserve_planned_pages(&mut self.pages, 1, budget)?;
        let page = self.append_plan.append_image(image)?;
        let number = page.number();
        self.pages.push(page);
        Ok(number)
    }

    /// Replaces one retained complete image without changing page numbering.
    /// The caller remains responsible for references and allocation maps.
    pub(crate) fn replace(
        &mut self,
        page: PageNumber,
        image: PageImage,
    ) -> Result<(), WholeFilePlanError> {
        let slot = usize::try_from(page.get())
            .ok()
            .and_then(|slot| self.pages.get_mut(slot))
            .ok_or(WholeFilePlanError::MissingPage { page })?;
        slot.replace_image(image);
        Ok(())
    }

    /// Consumes the aggregate and returns its ordered page plans.
    pub(crate) fn into_pages(self) -> Vec<PlannedPage> {
        self.pages
    }
}

fn reserve_planned_pages(
    pages: &mut Vec<PlannedPage>,
    additional: usize,
    budget: &mut ResourceBudget,
) -> Result<(), WholeFilePlanError> {
    let needed = pages
        .len()
        .checked_add(additional)
        .ok_or(Error::Arithmetic {
            operation: "count planned pages",
        })?;
    if needed <= pages.capacity() {
        return Ok(());
    }
    let capacity = needed.max(pages.capacity().saturating_mul(2));
    let bytes = (capacity - pages.capacity())
        .checked_mul(size_of::<PlannedPage>())
        .ok_or(Error::Arithmetic {
            operation: "size planned page storage",
        })?;
    budget.charge_allocation(ByteCount::new(bytes as u64))?;
    budget
        .charge_work_units((pages.len() as u64).saturating_mul(size_of::<PlannedPage>() as u64))?;

    pages
        .try_reserve_exact(capacity - pages.len())
        .map_err(|_| {
            WholeFilePlanError::Resource(Error::Io {
                operation: "reserve whole-file planned-page storage",
                kind: std::io::ErrorKind::OutOfMemory,
            })
        })
}
