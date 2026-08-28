//! Canonical snapshots of bounded whole-file page classification runs.
//!
//! The runner consumes only the public [`jet3::DatabaseReader`] boundary and
//! records the public [`jet3::PageKind`] returned for each complete page. It
//! assigns no meaning to unknown tags and does not retain database bytes.

use std::error::Error;
use std::fmt;
use std::path::Path;

use jet3::{
    ByteCount, DatabaseOpenError, DatabasePageError, DatabaseReader, JET3_PAGE_SIZE, PageKind,
    PageNumber, ReadLimits, ResourceBudget, ResourceLimits,
};

use crate::Sha256;

const SIGNATURE_READ_BYTES: u64 = 15;
const PAGE_BYTES: u64 = JET3_PAGE_SIZE.get();
const PAGE_BUFFER_BYTES: usize = PAGE_BYTES as usize;

/// A validated full Git commit identifier used to bind a retained run.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CommitId(String);

impl CommitId {
    /// Validates and constructs a 40-digit lowercase hexadecimal commit ID.
    pub fn new(value: impl Into<String>) -> Result<Self, ClassifierSnapshotError> {
        let value = value.into();
        if value.len() != 40
            || !value
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(ClassifierSnapshotError::InvalidCommit);
        }
        Ok(Self(value))
    }

    /// Returns the validated lowercase commit ID.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// Fixed-storage tallies for every public page classification.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PageKindHistogram {
    database_definition: u64,
    data: u64,
    table_definition: u64,
    intermediate_index: u64,
    leaf_index: u64,
    extended_usage_bitmap: u64,
    unknown: [u64; 256],
}

impl PageKindHistogram {
    const fn new() -> Self {
        Self {
            database_definition: 0,
            data: 0,
            table_definition: 0,
            intermediate_index: 0,
            leaf_index: 0,
            extended_usage_bitmap: 0,
            unknown: [0; 256],
        }
    }

    fn record(&mut self, kind: PageKind) -> Result<(), ClassifierSnapshotError> {
        let count = match kind {
            PageKind::DatabaseDefinition => &mut self.database_definition,
            PageKind::Data => &mut self.data,
            PageKind::TableDefinition => &mut self.table_definition,
            PageKind::IntermediateIndex => &mut self.intermediate_index,
            PageKind::LeafIndex => &mut self.leaf_index,
            PageKind::ExtendedUsageBitmap => &mut self.extended_usage_bitmap,
            PageKind::Unknown(tag) => &mut self.unknown[usize::from(tag)],
            _ => return Err(ClassifierSnapshotError::UnsupportedPageKind),
        };
        *count = count
            .checked_add(1)
            .ok_or(ClassifierSnapshotError::Arithmetic {
                operation: "increment page-kind histogram",
            })?;
        Ok(())
    }

    /// Returns the tally for one public page kind, including an exact unknown tag.
    #[must_use]
    pub const fn count(&self, kind: PageKind) -> u64 {
        match kind {
            PageKind::DatabaseDefinition => self.database_definition,
            PageKind::Data => self.data,
            PageKind::TableDefinition => self.table_definition,
            PageKind::IntermediateIndex => self.intermediate_index,
            PageKind::LeafIndex => self.leaf_index,
            PageKind::ExtendedUsageBitmap => self.extended_usage_bitmap,
            PageKind::Unknown(tag) => self.unknown[tag as usize],
            _ => 0,
        }
    }

    fn total(&self) -> Result<u64, ClassifierSnapshotError> {
        let named = [
            self.database_definition,
            self.data,
            self.table_definition,
            self.intermediate_index,
            self.leaf_index,
            self.extended_usage_bitmap,
        ];
        named
            .into_iter()
            .chain(self.unknown)
            .try_fold(0_u64, |total, count| {
                total
                    .checked_add(count)
                    .ok_or(ClassifierSnapshotError::Arithmetic {
                        operation: "sum page-kind histogram",
                    })
            })
    }

    pub(crate) const fn named_counts(&self) -> [(&'static str, u64); 6] {
        [
            ("data", self.data),
            ("database_definition", self.database_definition),
            ("extended_usage_bitmap", self.extended_usage_bitmap),
            ("intermediate_index", self.intermediate_index),
            ("leaf_index", self.leaf_index),
            ("table_definition", self.table_definition),
        ]
    }

    pub(crate) fn unknown_counts(&self) -> impl Iterator<Item = (u8, u64)> + '_ {
        (0_u8..=u8::MAX).filter_map(|tag| {
            let count = self.unknown[usize::from(tag)];
            (count != 0).then_some((tag, count))
        })
    }
}

/// One verified fixture identity paired with its complete classifier result.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ClassifiedFixture {
    sha256: Sha256,
    page_count: u64,
    histogram: PageKindHistogram,
}

impl ClassifiedFixture {
    /// Returns the preverified digest of the exact fixture that was read.
    #[must_use]
    pub const fn sha256(&self) -> &Sha256 {
        &self.sha256
    }

    /// Returns the number of complete pages visited exactly once.
    #[must_use]
    pub const fn page_count(&self) -> u64 {
        self.page_count
    }

    /// Returns the complete page-kind histogram.
    #[must_use]
    pub const fn histogram(&self) -> &PageKindHistogram {
        &self.histogram
    }
}

/// A commit-bound, canonically ordered collection of fixture classifications.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ClassifierSnapshot {
    source_commit: CommitId,
    fixtures: Vec<ClassifiedFixture>,
}

impl ClassifierSnapshot {
    /// Starts an empty snapshot bound to the code revision being exercised.
    #[must_use]
    pub const fn new(source_commit: CommitId) -> Self {
        Self {
            source_commit,
            fixtures: Vec::new(),
        }
    }

    /// Inserts one fixture and maintains canonical digest order.
    pub fn insert(&mut self, fixture: ClassifiedFixture) -> Result<(), ClassifierSnapshotError> {
        match self
            .fixtures
            .binary_search_by(|current| current.sha256.cmp(&fixture.sha256))
        {
            Ok(_) => Err(ClassifierSnapshotError::DuplicateFixture {
                sha256: fixture.sha256,
            }),
            Err(index) => {
                self.fixtures.insert(index, fixture);
                Ok(())
            }
        }
    }

    /// Returns the exact source commit bound into every fixture key.
    #[must_use]
    pub const fn source_commit(&self) -> &CommitId {
        &self.source_commit
    }

    /// Returns fixtures in canonical digest order.
    #[must_use]
    pub fn fixtures(&self) -> &[ClassifiedFixture] {
        &self.fixtures
    }

    /// Emits compact canonical UTF-8 JSON followed by exactly one LF.
    pub fn to_canonical_json(&self) -> Result<Vec<u8>, ClassifierSnapshotError> {
        for fixture in &self.fixtures {
            let total = fixture.histogram.total()?;
            if total != fixture.page_count {
                return Err(ClassifierSnapshotError::HistogramTotal {
                    expected: fixture.page_count,
                    actual: total,
                });
            }
        }
        Ok(crate::canonical_json::write_classifier_snapshot(self))
    }
}

/// Classifies every page of one preverified fixture under exact run limits.
///
/// The digest is an identity supplied by the caller after verifying the file
/// with the repository's corpus verifier. This function independently checks
/// the expected byte length before returning a result, but does not implement
/// a second SHA-256 algorithm.
pub fn classify_fixture(
    path: impl AsRef<Path>,
    verified_sha256: Sha256,
    expected_size: ByteCount,
) -> Result<ClassifiedFixture, ClassifierSnapshotError> {
    if !expected_size.get().is_multiple_of(PAGE_BYTES) {
        return Err(ClassifierSnapshotError::PartialExpectedPage {
            size: expected_size,
        });
    }
    let page_count = expected_size.get() / PAGE_BYTES;
    let page_read_bytes = expected_size
        .get()
        .checked_add(PAGE_BYTES)
        .and_then(|bytes| bytes.checked_add(SIGNATURE_READ_BYTES))
        .ok_or(ClassifierSnapshotError::Arithmetic {
            operation: "derive classifier read limit",
        })?;
    let page_visits = page_count
        .checked_add(1)
        .ok_or(ClassifierSnapshotError::Arithmetic {
            operation: "derive classifier page-visit limit",
        })?;
    let total_work = page_count
        .checked_mul(2)
        .and_then(|work| work.checked_add(1))
        .ok_or(ClassifierSnapshotError::Arithmetic {
            operation: "derive classifier work limit",
        })?;
    let read_limits = ReadLimits::new(
        expected_size,
        ByteCount::new(PAGE_BYTES),
        ByteCount::new(page_read_bytes),
    );
    let limits = ResourceLimits::new(read_limits)
        .with_max_page_visits(page_visits)
        .with_max_total_work_units(total_work);
    let mut budget = ResourceBudget::new(limits);
    let mut database =
        DatabaseReader::open(path, &mut budget).map_err(ClassifierSnapshotError::Open)?;
    let actual_size = database.geometry().source_len();
    if actual_size != expected_size {
        return Err(ClassifierSnapshotError::SizeMismatch {
            expected: expected_size,
            actual: actual_size,
        });
    }

    let mut histogram = PageKindHistogram::new();
    let mut page_bytes = [0_u8; PAGE_BUFFER_BYTES];
    for number in 0..page_count {
        let page = database
            .read_classified_page(PageNumber::new(number), &mut page_bytes, &mut budget)
            .map_err(ClassifierSnapshotError::Page)?;
        histogram.record(page.kind())?;
    }
    if budget.page_visits() != page_visits || budget.total_work_units() != total_work {
        return Err(ClassifierSnapshotError::Accounting {
            expected_page_visits: page_visits,
            actual_page_visits: budget.page_visits(),
            expected_work: total_work,
            actual_work: budget.total_work_units(),
        });
    }

    Ok(ClassifiedFixture {
        sha256: verified_sha256,
        page_count,
        histogram,
    })
}

/// A structured failure while producing a retained classifier snapshot.
#[derive(Debug)]
#[non_exhaustive]
pub enum ClassifierSnapshotError {
    /// The source revision is not a full lowercase hexadecimal Git commit ID.
    InvalidCommit,
    /// The expected fixture size does not contain only complete pages.
    PartialExpectedPage {
        /// Expected fixture size.
        size: ByteCount,
    },
    /// The opened fixture length differs from its preverified inventory entry.
    SizeMismatch {
        /// Preverified fixture size.
        expected: ByteCount,
        /// Captured source size.
        actual: ByteCount,
    },
    /// Opening the public database-reader boundary failed.
    Open(DatabaseOpenError),
    /// Reading or classifying one page failed.
    Page(DatabasePageError),
    /// A future public page kind cannot be represented by this schema version.
    UnsupportedPageKind,
    /// Checked count or limit arithmetic failed.
    Arithmetic {
        /// Operation that overflowed.
        operation: &'static str,
    },
    /// The exact resource-accounting postcondition did not hold.
    Accounting {
        /// Required page visits.
        expected_page_visits: u64,
        /// Observed page visits.
        actual_page_visits: u64,
        /// Required aggregate work units.
        expected_work: u64,
        /// Observed aggregate work units.
        actual_work: u64,
    },
    /// Two results use the same verified fixture digest.
    DuplicateFixture {
        /// Duplicated fixture digest.
        sha256: Sha256,
    },
    /// A fixture histogram does not sum to its complete page count.
    HistogramTotal {
        /// Complete page count.
        expected: u64,
        /// Sum of all histogram buckets.
        actual: u64,
    },
}

impl fmt::Display for ClassifierSnapshotError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidCommit => formatter.write_str("invalid full lowercase Git commit ID"),
            Self::PartialExpectedPage { size } => {
                write!(
                    formatter,
                    "expected fixture size {} is not page-aligned",
                    size.get()
                )
            }
            Self::SizeMismatch { expected, actual } => write!(
                formatter,
                "fixture size mismatch: expected {}, captured {}",
                expected.get(),
                actual.get()
            ),
            Self::Open(source) => write!(formatter, "fixture open failed: {source}"),
            Self::Page(source) => write!(formatter, "fixture page classification failed: {source}"),
            Self::UnsupportedPageKind => {
                formatter.write_str("classifier snapshot schema does not support this page kind")
            }
            Self::Arithmetic { operation } => write!(formatter, "arithmetic failed: {operation}"),
            Self::Accounting { .. } => {
                formatter.write_str("classifier run did not meet exact resource accounting")
            }
            Self::DuplicateFixture { sha256 } => {
                write!(formatter, "duplicate fixture digest {}", sha256.as_str())
            }
            Self::HistogramTotal { expected, actual } => write!(
                formatter,
                "histogram total {actual} does not match page count {expected}"
            ),
        }
    }
}

impl Error for ClassifierSnapshotError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Open(source) => Some(source),
            Self::Page(source) => Some(source),
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use jet3::PageKind;

    use super::{ClassifiedFixture, ClassifierSnapshot, CommitId, PageKindHistogram};
    use crate::Sha256;

    #[test]
    fn writer_orders_synthetic_fixture_and_unknown_keys_canonically()
    -> Result<(), Box<dyn std::error::Error>> {
        let commit = CommitId::new("c".repeat(40))?;
        let mut snapshot = ClassifierSnapshot::new(commit);
        for (digest, kinds) in [
            (
                "b".repeat(64),
                [PageKind::Unknown(0xaa), PageKind::Unknown(0x0f)],
            ),
            ("a".repeat(64), [PageKind::Data, PageKind::Data]),
        ] {
            let mut histogram = PageKindHistogram::new();
            for kind in kinds {
                histogram.record(kind)?;
            }
            snapshot.insert(ClassifiedFixture {
                sha256: Sha256::new(digest)?,
                page_count: 2,
                histogram,
            })?;
        }

        let json = String::from_utf8(snapshot.to_canonical_json()?)?;
        let first = json
            .find(&format!("{}@", "a".repeat(64)))
            .ok_or("missing a key")?;
        let second = json
            .find(&format!("{}@", "b".repeat(64)))
            .ok_or("missing b key")?;
        assert!(first < second);
        let low_tag = json.find("\"0f\":1").ok_or("missing low unknown tag")?;
        let high_tag = json.find("\"aa\":1").ok_or("missing high unknown tag")?;
        assert!(low_tag < high_tag);
        Ok(())
    }
}
