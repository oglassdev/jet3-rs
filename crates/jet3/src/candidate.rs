//! Bounded, non-semantic inspection of raw Jet 3 candidates.
//!
//! This module composes the generic Jet signature published by Microsoft
//! (`SRC-0004`) with the exact 2 KiB candidate geometry supported by
//! [`JET3_PAGE_SIZE`] (`SRC-0005`). A successful inspection does **not**
//! identify the Jet generation, inspect encryption, validate database
//! structure, or establish compatibility with Access, DAO, or any other
//! application.

use std::fmt;

use crate::{
    Error, HeaderError, JET3_PAGE_SIZE, Jet3PageReader, JetFileKind, PageGeometry, PageNumber,
    RawPageCursor, ReadAt, ResourceBudget, read_jet_signature,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

/// A structured failure while inspecting a raw Jet 3 candidate.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum CandidateError {
    /// The source's captured length exceeded the operation input policy.
    Input(Error),
    /// Generic Jet signature recognition failed.
    Signature(HeaderError),
    /// The captured source length was not exact 2 KiB candidate geometry.
    Geometry(Error),
}

impl fmt::Display for CandidateError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Input(source) => write!(formatter, "candidate input policy failed: {source}"),
            Self::Signature(source) => write!(formatter, "candidate signature failed: {source}"),
            Self::Geometry(source) => write!(formatter, "candidate geometry failed: {source}"),
        }
    }
}

impl std::error::Error for CandidateError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Input(source) | Self::Geometry(source) => Some(source),
            Self::Signature(source) => Some(source),
        }
    }
}

/// A source with a documented generic Jet signature and exact 2 KiB geometry.
///
/// The type deliberately exposes only raw, bounded page access. Its existence
/// is not evidence of a Jet generation, encryption state, valid database
/// structure, or application compatibility.
#[derive(Debug)]
pub struct RawJet3Candidate<S> {
    signature_kind: JetFileKind,
    pages: Jet3PageReader<S>,
}

impl<S> RawJet3Candidate<S>
where
    S: ReadAt,
{
    /// Inspects `source` without input-derived allocation.
    ///
    /// Inspection rechecks the generic source's captured length against the
    /// operation input policy, checks the `SRC-0004` signature, and then
    /// derives exact `SRC-0005` 2 KiB geometry, in that order. Success reads
    /// exactly the 15-byte signature window and visits no pages. It does not
    /// establish that the source is Jet 3, unencrypted, structurally valid, or
    /// compatible.
    pub fn inspect(mut source: S, budget: &mut ResourceBudget) -> Result<Self, CandidateError> {
        let input_len = source.len();
        budget
            .read_budget()
            .check_input(input_len)
            .map_err(CandidateError::Input)?;
        let signature_kind = read_jet_signature(&mut source, budget.read_budget())
            .map_err(CandidateError::Signature)?;
        let pages = Jet3PageReader::new(source).map_err(CandidateError::Geometry)?;
        Ok(Self {
            signature_kind,
            pages,
        })
    }

    /// Returns the generic Jet signature classification.
    #[must_use]
    pub const fn signature_kind(&self) -> JetFileKind {
        self.signature_kind
    }

    /// Returns the exact 2 KiB candidate geometry.
    #[must_use]
    pub const fn geometry(&self) -> PageGeometry {
        self.pages.geometry()
    }

    /// Borrows the owned source.
    #[must_use]
    pub const fn source(&self) -> &S {
        self.pages.source()
    }

    /// Returns the owned source.
    #[must_use]
    pub fn into_inner(self) -> S {
        self.pages.into_inner()
    }

    /// Reads one complete raw candidate page through the bounded page reader.
    ///
    /// No page bytes are interpreted. All read, page-visit, and aggregate-work
    /// limits are delegated to [`Jet3PageReader`], including its guarantee that
    /// `destination` changes only after a complete successful read.
    pub fn read_raw_page(
        &mut self,
        page: PageNumber,
        destination: &mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<(), Error> {
        self.pages.read_page(page, destination, budget)
    }

    /// Starts allocation-free sequential access at physical page zero.
    ///
    /// The returned cursor reuses one fixed 2 KiB buffer and shares the
    /// caller's operation budget on every read. Page bytes remain completely
    /// uninterpreted; exhausting the stream does not validate the database or
    /// establish compatibility.
    pub fn raw_pages(&mut self) -> RawPageCursor<'_, S> {
        RawPageCursor::new(&mut self.pages)
    }
}

#[cfg(test)]
#[path = "candidate_tests.rs"]
mod tests;
