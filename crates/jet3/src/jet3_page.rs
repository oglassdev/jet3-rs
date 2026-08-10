//! Safe, bounded reads of complete Jet 3 pages.
//!
//! The page size comes from [`JET3_PAGE_SIZE`], traced to provenance source
//! `SRC-0005`. This module assigns no meaning to page contents.

use crate::{Error, JET3_PAGE_SIZE, PageGeometry, PageNumber, ReadAt, ResourceBudget};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

/// A random-access reader for complete 2 KiB Jet 3 pages.
///
/// Construction only derives arithmetic page geometry from the source's
/// captured length and performs no reads. In particular, a length aligned to
/// [`JET3_PAGE_SIZE`] does not identify the source as Jet 3 or validate any
/// page or database content.
#[derive(Debug)]
pub struct Jet3PageReader<S> {
    source: S,
    geometry: PageGeometry,
}

impl<S> Jet3PageReader<S>
where
    S: ReadAt,
{
    /// Owns `source` after deriving its fixed 2 KiB page geometry.
    ///
    /// This method performs no reads. Page alignment establishes only
    /// arithmetic geometry; it does not identify or validate Jet 3 data.
    pub fn new(source: S) -> Result<Self, Error> {
        let geometry = PageGeometry::for_source(&source, JET3_PAGE_SIZE)?;
        Ok(Self { source, geometry })
    }

    /// Returns the captured 2 KiB page geometry.
    #[must_use]
    pub const fn geometry(&self) -> PageGeometry {
        self.geometry
    }

    /// Borrows the owned random-access source.
    #[must_use]
    pub const fn source(&self) -> &S {
        &self.source
    }

    /// Returns the owned random-access source.
    #[must_use]
    pub fn into_inner(self) -> S {
        self.source
    }

    /// Reads one complete page into `destination`.
    ///
    /// The 2 KiB read limits are preflighted before page geometry and
    /// page/aggregate work limits. Rejection during preflight changes no
    /// counters. Once source access is attempted, both the page visit and the
    /// attempted 2 KiB read remain charged even if the source fails or returns
    /// a short read. `destination` is changed only after a successful complete
    /// read.
    pub fn read_page(
        &mut self,
        page: PageNumber,
        destination: &mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<(), Error> {
        budget.read_budget().check_read(JET3_PAGE_SIZE)?;
        let (offset, _) = self.geometry.page_byte_range(page)?;
        budget.charge_page_visits(1)?;
        let mut page_bytes = [0_u8; PAGE_BYTES];
        self.source
            .read_exact_at(offset, &mut page_bytes, budget.read_budget())?;
        destination.copy_from_slice(&page_bytes);
        Ok(())
    }
}

#[cfg(test)]
#[path = "jet3_page_tests.rs"]
mod tests;
