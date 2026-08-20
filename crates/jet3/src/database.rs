//! Bounded opening and initial structural checks for a Jet database candidate.
//!
//! This module composes only the generic Jet signature published by Microsoft
//! (`SRC-0004`), the documented Jet 3 2 KiB page size (`SRC-0005`), and the
//! documented identification of the first database page as the database header
//! page (`SRC-0013`). Initial validation does not identify a physical Jet
//! version discriminator, encryption state, page type, allocation structure,
//! catalog, table, row, or value. The experimental classified-page read
//! composes the byte-zero tags in `SRC-0020` without validating any other
//! page-header byte.

use std::fmt;
use std::path::Path;

use crate::{
    CandidateError, ClassifiedPage, DatabaseHeaderPage, DatabaseHeaderPageError, Error, FileSource,
    JET3_PAGE_SIZE, JetFileKind, PageClassificationError, PageGeometry, PageNumber,
    RawJet3Candidate, RawPageCursor, ReadAt, ResourceBudget, classify_page,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

/// A structured failure while opening a database candidate.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum DatabaseOpenError {
    /// Opening a file or capturing its bounded source length failed.
    Source(Error),
    /// Generic signature or exact 2 KiB geometry inspection failed.
    Candidate(CandidateError),
    /// Reading or revalidating the complete database-header page failed.
    Header(DatabaseHeaderPageError),
    /// The generic signature classification changed between bounded reads.
    SignatureChanged {
        /// Classification from the initial 15-byte signature read.
        initial: JetFileKind,
        /// Classification from the retained complete page-zero read.
        header: JetFileKind,
    },
}

impl fmt::Display for DatabaseOpenError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Source(source) => write!(formatter, "database source open failed: {source}"),
            Self::Candidate(source) => {
                write!(formatter, "database candidate inspection failed: {source}")
            }
            Self::Header(source) => {
                write!(formatter, "database header validation failed: {source}")
            }
            Self::SignatureChanged { initial, header } => write!(
                formatter,
                "database signature changed while opening: initial {initial:?}, header {header:?}"
            ),
        }
    }
}

impl std::error::Error for DatabaseOpenError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Source(source) => Some(source),
            Self::Candidate(source) => Some(source),
            Self::Header(source) => Some(source),
            Self::SignatureChanged { .. } => None,
        }
    }
}

/// A structured failure while reading and experimentally classifying a page.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum DatabasePageError {
    /// Reading the complete fixed page failed.
    Read(Error),
    /// Charging or performing byte-zero classification failed.
    Classification(PageClassificationError),
}

impl fmt::Display for DatabasePageError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Read(source) => write!(formatter, "database page read failed: {source}"),
            Self::Classification(source) => {
                write!(formatter, "database page classification failed: {source}")
            }
        }
    }
}

impl std::error::Error for DatabasePageError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Read(source) => Some(source),
            Self::Classification(source) => Some(source),
        }
    }
}

/// A bounded reader whose initial, narrowly supported structure was checked.
///
/// Construction requires a documented generic Jet signature, an input length
/// exactly divisible into 2 KiB pages, and a complete readable page zero whose
/// signature still matches when read as a whole. These checks are intentionally
/// insufficient to claim that the input is Jet 3, unencrypted, internally
/// consistent, or compatible with DAO.
#[derive(Debug)]
pub struct DatabaseReader<S> {
    candidate: RawJet3Candidate<S>,
    header: DatabaseHeaderPage,
}

impl DatabaseReader<FileSource> {
    /// Opens `path`, captures its length under the caller's read policy, and
    /// performs the initial bounded structural checks.
    pub fn open(
        path: impl AsRef<Path>,
        budget: &mut ResourceBudget,
    ) -> Result<Self, DatabaseOpenError> {
        let source =
            FileSource::open(path, budget.read_budget()).map_err(DatabaseOpenError::Source)?;
        Self::from_source(source, budget)
    }
}

impl<S> DatabaseReader<S>
where
    S: ReadAt,
{
    /// Owns a bounded source after checking its initial supported structure.
    ///
    /// Success performs no input-derived allocation. It reads the 15-byte
    /// generic signature window, validates exact page geometry, then reads one
    /// complete 2 KiB page zero into the retained header snapshot. The same
    /// operation-wide budget is used throughout.
    pub fn from_source(source: S, budget: &mut ResourceBudget) -> Result<Self, DatabaseOpenError> {
        let mut candidate =
            RawJet3Candidate::inspect(source, budget).map_err(DatabaseOpenError::Candidate)?;
        let header = candidate
            .read_database_header_page(budget)
            .map_err(DatabaseOpenError::Header)?;
        let initial = candidate.signature_kind();
        if header.signature_kind() != initial {
            return Err(DatabaseOpenError::SignatureChanged {
                initial,
                header: header.signature_kind(),
            });
        }
        Ok(Self { candidate, header })
    }

    /// Returns the retained, complete database-header-page snapshot.
    #[must_use]
    pub const fn header(&self) -> &DatabaseHeaderPage {
        &self.header
    }

    /// Returns the generic signature classification revalidated on page zero.
    #[must_use]
    pub const fn signature_kind(&self) -> JetFileKind {
        self.header.signature_kind()
    }

    /// Returns the exact 2 KiB page geometry captured at open time.
    #[must_use]
    pub const fn geometry(&self) -> PageGeometry {
        self.candidate.geometry()
    }

    /// Borrows the owned bounded source.
    #[must_use]
    pub const fn source(&self) -> &S {
        self.candidate.source()
    }

    /// Returns the owned bounded source.
    #[must_use]
    pub fn into_source(self) -> S {
        self.candidate.into_inner()
    }

    /// Reads one complete page without interpreting its contents.
    pub fn read_raw_page(
        &mut self,
        page: PageNumber,
        destination: &mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<(), Error> {
        self.candidate.read_raw_page(page, destination, budget)
    }

    /// Reads and experimentally classifies one complete page.
    ///
    /// The source read retains its existing byte and page-visit charges. After
    /// a successful read, classification charges exactly one additional
    /// explicit work unit and inspects only byte zero. A classification-budget
    /// rejection leaves the complete raw page in `destination` for explicit
    /// caller handling.
    pub fn read_classified_page<'a>(
        &mut self,
        page: PageNumber,
        destination: &'a mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<ClassifiedPage<'a>, DatabasePageError> {
        self.read_raw_page(page, destination, budget)
            .map_err(DatabasePageError::Read)?;
        classify_page(page, destination, budget).map_err(DatabasePageError::Classification)
    }

    /// Starts allocation-free sequential access to uninterpreted pages.
    pub fn raw_pages(&mut self) -> RawPageCursor<'_, S> {
        self.candidate.raw_pages()
    }
}

#[cfg(test)]
#[path = "database_tests.rs"]
mod tests;
