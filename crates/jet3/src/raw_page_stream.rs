//! Allocation-free sequential access to complete raw Jet 3 candidate pages.
//!
//! Page width is the 2 KiB value traced to Microsoft source `SRC-0005`.
//! This module assigns no meaning to page bytes. A complete stream does not
//! establish the input's Jet generation, encryption state, structural
//! validity, or application compatibility.

use crate::{Error, JET3_PAGE_SIZE, Jet3PageReader, PageNumber, ReadAt, ResourceBudget};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

/// One borrowed page yielded by a [`RawPageCursor`].
///
/// The bytes remain valid only until the cursor is mutably borrowed again.
/// They are uninterpreted and carry no page-type or validity claim.
#[derive(Debug, PartialEq, Eq)]
pub struct RawPage<'a> {
    number: PageNumber,
    bytes: &'a [u8; PAGE_BYTES],
}

impl RawPage<'_> {
    /// Returns the zero-based physical page number.
    #[must_use]
    pub const fn number(&self) -> PageNumber {
        self.number
    }

    /// Returns all 2 KiB of uninterpreted page bytes.
    #[must_use]
    pub const fn bytes(&self) -> &[u8; PAGE_BYTES] {
        self.bytes
    }
}

/// A forward-only, allocation-free cursor over complete raw candidate pages.
///
/// The cursor reuses one fixed 2 KiB buffer. Every call to [`Self::next_page`]
/// uses the caller's persistent [`ResourceBudget`], so byte reads, page visits,
/// and aggregate work cannot be reset between pages. A failed read does not
/// advance the cursor; a call after exhaustion performs no I/O and charges no
/// work.
#[derive(Debug)]
pub struct RawPageCursor<'a, S> {
    pages: &'a mut Jet3PageReader<S>,
    next: u64,
    buffer: [u8; PAGE_BYTES],
}

impl<'a, S> RawPageCursor<'a, S>
where
    S: ReadAt,
{
    pub(crate) fn new(pages: &'a mut Jet3PageReader<S>) -> Self {
        Self {
            pages,
            next: 0,
            buffer: [0_u8; PAGE_BYTES],
        }
    }

    /// Returns the number of pages yielded successfully.
    #[must_use]
    pub const fn pages_read(&self) -> u64 {
        self.next
    }

    /// Returns the next page number, or `None` after exhaustion.
    #[must_use]
    pub fn next_page_number(&self) -> Option<PageNumber> {
        (self.next < self.pages.geometry().page_count()).then_some(PageNumber::new(self.next))
    }

    /// Reads and yields the next complete raw page.
    ///
    /// Success advances by exactly one page. If a resource check or source read
    /// fails, the cursor retains its current page number and its private buffer
    /// is not published. Once all captured pages have been yielded, repeated
    /// calls return `Ok(None)` without changing the budget.
    pub fn next_page<'page>(
        &'page mut self,
        budget: &mut ResourceBudget,
    ) -> Result<Option<RawPage<'page>>, Error> {
        let Some(number) = self.next_page_number() else {
            return Ok(None);
        };
        self.pages.read_page(number, &mut self.buffer, budget)?;
        self.next = self.next.checked_add(1).ok_or(Error::Arithmetic {
            operation: "advance raw page cursor",
        })?;
        Ok(Some(RawPage {
            number,
            bytes: &self.buffer,
        }))
    }
}

#[cfg(test)]
#[path = "raw_page_stream_tests.rs"]
mod tests;
