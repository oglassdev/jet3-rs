//! Bounded random-access input backed by memory or a file.

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;

use crate::{ByteCount, ByteOffset, Error, ResourceLimits, WorkBudget};

/// A stable-length source for checked, exact reads at absolute byte offsets.
///
/// Implementations enforce their [`ResourceLimits`] at construction and
/// before every read. The cumulative byte count includes reads that reach the
/// underlying source but fail, so repeatedly probing a changing or faulty
/// source cannot bypass the work limit.
pub trait ReadAt {
    /// Returns the source length captured when this source was created.
    fn len(&self) -> ByteCount;

    /// Returns whether the captured source length is zero.
    fn is_empty(&self) -> bool {
        self.len().get() == 0
    }

    /// Returns the cumulative bytes charged to this source.
    fn total_read(&self) -> ByteCount;

    /// Fills `destination` from `offset` or returns a structured error.
    ///
    /// A zero-length read at the captured end is valid. The requested byte
    /// count is checked against both the single-read and cumulative limits
    /// before the implementation accesses the underlying source.
    fn read_exact_at(&mut self, offset: ByteOffset, destination: &mut [u8]) -> Result<(), Error>;
}

/// An immutable borrowed byte slice exposed as a bounded random-access source.
#[derive(Debug)]
pub struct SliceSource<'a> {
    input: &'a [u8],
    input_len: ByteCount,
    budget: WorkBudget,
}

impl<'a> SliceSource<'a> {
    /// Creates a slice source after enforcing the input-length ceiling.
    pub fn new(input: &'a [u8], limits: ResourceLimits) -> Result<Self, Error> {
        let input_len = ByteCount::from_usize(input.len())?;
        let budget = WorkBudget::new(limits);
        budget.check_input(input_len)?;
        Ok(Self {
            input,
            input_len,
            budget,
        })
    }
}

impl ReadAt for SliceSource<'_> {
    fn len(&self) -> ByteCount {
        self.input_len
    }

    fn total_read(&self) -> ByteCount {
        self.budget.total_read()
    }

    fn read_exact_at(&mut self, offset: ByteOffset, destination: &mut [u8]) -> Result<(), Error> {
        let request = checked_request(&self.budget, self.input_len, offset, destination.len())?;
        self.budget.charge_read(request.count)?;
        let start = offset.to_usize()?;
        let end = request.end.to_usize()?;
        let bytes = self.input.get(start..end).ok_or(Error::UnexpectedEnd {
            offset,
            needed: request.count,
            available: available_at(self.input_len, offset),
        })?;
        if bytes.len() != destination.len() {
            return Err(Error::ShortRead {
                offset,
                needed: request.count,
                actual: ByteCount::from_usize(bytes.len())?,
            });
        }
        destination.copy_from_slice(bytes);
        Ok(())
    }
}

/// An owned file exposed through bounded random-access reads.
///
/// The file length is captured once during construction. Later growth is not
/// exposed, and later truncation is reported as a short read rather than
/// silently changing the source geometry.
#[derive(Debug)]
pub struct FileSource {
    file: File,
    input_len: ByteCount,
    budget: WorkBudget,
}

impl FileSource {
    /// Opens a file and captures its current length.
    pub fn open(path: impl AsRef<Path>, limits: ResourceLimits) -> Result<Self, Error> {
        let file = File::open(path).map_err(|source| Error::Io {
            operation: "open input file",
            kind: source.kind(),
        })?;
        Self::from_file(file, limits)
    }

    /// Wraps an open file and captures its current length.
    pub fn from_file(file: File, limits: ResourceLimits) -> Result<Self, Error> {
        let input_len = ByteCount::new(
            file.metadata()
                .map_err(|source| Error::Io {
                    operation: "read input metadata",
                    kind: source.kind(),
                })?
                .len(),
        );
        let budget = WorkBudget::new(limits);
        budget.check_input(input_len)?;
        Ok(Self {
            file,
            input_len,
            budget,
        })
    }
}

impl ReadAt for FileSource {
    fn len(&self) -> ByteCount {
        self.input_len
    }

    fn total_read(&self) -> ByteCount {
        self.budget.total_read()
    }

    fn read_exact_at(&mut self, offset: ByteOffset, destination: &mut [u8]) -> Result<(), Error> {
        let request = checked_request(&self.budget, self.input_len, offset, destination.len())?;
        self.budget.charge_read(request.count)?;

        self.file
            .seek(SeekFrom::Start(offset.get()))
            .map_err(|source| Error::Io {
                operation: "seek input file",
                kind: source.kind(),
            })?;

        let mut actual = 0_usize;
        while actual < destination.len() {
            let remaining = destination.get_mut(actual..).ok_or(Error::Arithmetic {
                operation: "advance file read buffer",
            })?;
            match self.file.read(remaining) {
                Ok(0) => {
                    return Err(Error::ShortRead {
                        offset,
                        needed: request.count,
                        actual: ByteCount::from_usize(actual)?,
                    });
                }
                Ok(read) => {
                    actual = actual.checked_add(read).ok_or(Error::Arithmetic {
                        operation: "accumulate file read length",
                    })?;
                }
                Err(source) if source.kind() == io::ErrorKind::Interrupted => {}
                Err(source) => {
                    return Err(Error::Io {
                        operation: "read input file",
                        kind: source.kind(),
                    });
                }
            }
        }
        Ok(())
    }
}

fn checked_request(
    budget: &WorkBudget,
    input_len: ByteCount,
    offset: ByteOffset,
    destination_len: usize,
) -> Result<Request, Error> {
    let count = ByteCount::from_usize(destination_len)?;
    let mut prospective_budget = *budget;
    prospective_budget.charge_read(count)?;
    let end = offset.checked_add(count)?;
    if offset.get() > input_len.get() {
        return Err(Error::OffsetOutOfBounds { offset, input_len });
    }
    if end.get() > input_len.get() {
        return Err(Error::UnexpectedEnd {
            offset,
            needed: count,
            available: available_at(input_len, offset),
        });
    }
    Ok(Request { count, end })
}

fn available_at(input_len: ByteCount, offset: ByteOffset) -> ByteCount {
    ByteCount::new(input_len.get().saturating_sub(offset.get()))
}

#[derive(Debug)]
struct Request {
    count: ByteCount,
    end: ByteOffset,
}

#[cfg(test)]
mod tests {
    use std::fs::{File, OpenOptions};
    use std::io::{Seek, SeekFrom, Write};
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU64, Ordering};

    use super::{FileSource, ReadAt, SliceSource};
    use crate::{ByteCount, ByteOffset, Error, LimitKind, ResourceLimits};

    static NEXT_TEMP_FILE: AtomicU64 = AtomicU64::new(0);
    type TestResult = Result<(), Box<dyn std::error::Error>>;

    fn limits(input: u64, single: u64, total: u64) -> ResourceLimits {
        ResourceLimits::new(
            ByteCount::new(input),
            ByteCount::new(single),
            ByteCount::new(total),
        )
    }

    struct TempFile {
        path: PathBuf,
    }

    impl TempFile {
        fn create(bytes: &[u8]) -> Result<Self, std::io::Error> {
            let id = NEXT_TEMP_FILE.fetch_add(1, Ordering::Relaxed);
            let mut path = std::env::temp_dir();
            path.push(format!("jet3-source-{}-{id}.tmp", std::process::id()));
            let mut file = OpenOptions::new()
                .create_new(true)
                .write(true)
                .open(&path)?;
            file.write_all(bytes)?;
            file.sync_all()?;
            Ok(Self { path })
        }

        fn path(&self) -> &Path {
            &self.path
        }

        fn open(&self) -> Result<File, std::io::Error> {
            File::open(&self.path)
        }

        fn set_len(&self, len: u64) -> Result<(), std::io::Error> {
            OpenOptions::new()
                .write(true)
                .open(&self.path)?
                .set_len(len)
        }
    }

    impl Drop for TempFile {
        fn drop(&mut self) {
            let _ignored = std::fs::remove_file(&self.path);
        }
    }

    #[test]
    fn slice_constructor_enforces_input_limit_at_boundary() {
        assert!(SliceSource::new(&[0; 3], limits(3, 3, 3)).is_ok());
        assert_eq!(
            SliceSource::new(&[0; 4], limits(3, 4, 4)).err(),
            Some(Error::LimitExceeded {
                kind: LimitKind::InputBytes,
                requested: ByteCount::new(4),
                maximum: ByteCount::new(3),
            })
        );
    }

    #[test]
    fn file_constructor_enforces_input_limit_at_boundary() -> TestResult {
        let temporary = TempFile::create(&[0; 4])?;
        assert!(FileSource::from_file(temporary.open()?, limits(4, 4, 4)).is_ok());
        assert_eq!(
            FileSource::from_file(temporary.open()?, limits(3, 4, 4)).err(),
            Some(Error::LimitExceeded {
                kind: LimitKind::InputBytes,
                requested: ByteCount::new(4),
                maximum: ByteCount::new(3),
            })
        );
        Ok(())
    }

    #[test]
    fn empty_sources_report_stable_geometry_and_accept_empty_end_read() -> TestResult {
        let temporary = TempFile::create(&[])?;
        let mut slice = SliceSource::new(&[], limits(0, 0, 0))?;
        let mut file = FileSource::open(temporary.path(), limits(0, 0, 0))?;
        assert!(slice.is_empty());
        assert!(file.is_empty());
        assert_eq!(slice.len(), ByteCount::new(0));
        assert_eq!(file.len(), ByteCount::new(0));
        assert_eq!(slice.read_exact_at(ByteOffset::new(0), &mut []), Ok(()));
        assert_eq!(file.read_exact_at(ByteOffset::new(0), &mut []), Ok(()));
        assert_eq!(slice.total_read(), ByteCount::new(0));
        assert_eq!(file.total_read(), ByteCount::new(0));
        Ok(())
    }

    #[test]
    fn slice_and_file_read_identical_ranges() -> TestResult {
        let bytes = [1, 2, 3, 4];
        let temporary = TempFile::create(&bytes)?;
        let mut slice = SliceSource::new(&bytes, limits(4, 4, 8))?;
        let mut file = FileSource::open(temporary.path(), limits(4, 4, 8))?;
        let mut from_slice = [0; 2];
        let mut from_file = [0; 2];
        assert_eq!(
            slice.read_exact_at(ByteOffset::new(1), &mut from_slice),
            Ok(())
        );
        assert_eq!(
            file.read_exact_at(ByteOffset::new(1), &mut from_file),
            Ok(())
        );
        assert_eq!(from_slice, [2, 3]);
        assert_eq!(from_file, from_slice);
        assert_eq!(slice.total_read(), ByteCount::new(2));
        assert_eq!(file.total_read(), ByteCount::new(2));
        Ok(())
    }

    #[test]
    fn both_sources_reject_the_same_out_of_range_requests() -> TestResult {
        let bytes = [1, 2, 3];
        let temporary = TempFile::create(&bytes)?;
        let mut slice = SliceSource::new(&bytes, limits(3, 3, 3))?;
        let mut file = FileSource::open(temporary.path(), limits(3, 3, 3))?;

        let expected_partial = Err(Error::UnexpectedEnd {
            offset: ByteOffset::new(2),
            needed: ByteCount::new(2),
            available: ByteCount::new(1),
        });
        assert_eq!(
            slice.read_exact_at(ByteOffset::new(2), &mut [0; 2]),
            expected_partial
        );
        assert_eq!(
            file.read_exact_at(ByteOffset::new(2), &mut [0; 2]),
            expected_partial
        );

        let expected_offset = Err(Error::OffsetOutOfBounds {
            offset: ByteOffset::new(4),
            input_len: ByteCount::new(3),
        });
        assert_eq!(
            slice.read_exact_at(ByteOffset::new(4), &mut []),
            expected_offset
        );
        assert_eq!(
            file.read_exact_at(ByteOffset::new(4), &mut []),
            expected_offset
        );
        assert_eq!(slice.total_read(), ByteCount::new(0));
        assert_eq!(file.total_read(), ByteCount::new(0));
        Ok(())
    }

    #[test]
    fn offset_plus_length_overflow_is_rejected_before_access() -> TestResult {
        let mut source = SliceSource::new(&[], limits(0, 1, 1))?;
        assert_eq!(
            source.read_exact_at(ByteOffset::new(u64::MAX), &mut [0]),
            Err(Error::Arithmetic {
                operation: "offset addition",
            })
        );
        assert_eq!(source.total_read(), ByteCount::new(0));
        Ok(())
    }

    #[test]
    fn single_and_total_read_limits_preserve_budget_on_preflight_failure() -> TestResult {
        let mut source = SliceSource::new(&[1, 2], limits(2, 1, 1))?;
        assert_eq!(
            source.read_exact_at(ByteOffset::new(0), &mut [0; 2]),
            Err(Error::LimitExceeded {
                kind: LimitKind::SingleReadBytes,
                requested: ByteCount::new(2),
                maximum: ByteCount::new(1),
            })
        );
        assert_eq!(source.read_exact_at(ByteOffset::new(0), &mut [0]), Ok(()));
        assert_eq!(
            source.read_exact_at(ByteOffset::new(1), &mut [0]),
            Err(Error::LimitExceeded {
                kind: LimitKind::TotalReadBytes,
                requested: ByteCount::new(2),
                maximum: ByteCount::new(1),
            })
        );
        assert_eq!(source.total_read(), ByteCount::new(1));
        Ok(())
    }

    #[test]
    fn file_length_is_captured_and_later_growth_is_not_exposed() -> TestResult {
        let temporary = TempFile::create(&[1, 2])?;
        let mut source = FileSource::open(temporary.path(), limits(4, 2, 2))?;
        temporary.set_len(4)?;
        assert_eq!(source.len(), ByteCount::new(2));
        assert_eq!(
            source.read_exact_at(ByteOffset::new(2), &mut [0]),
            Err(Error::UnexpectedEnd {
                offset: ByteOffset::new(2),
                needed: ByteCount::new(1),
                available: ByteCount::new(0),
            })
        );
        assert_eq!(source.total_read(), ByteCount::new(0));
        Ok(())
    }

    #[test]
    fn truncated_file_reports_short_read_and_charges_failed_io() -> TestResult {
        let temporary = TempFile::create(&[1, 2, 3, 4])?;
        let mut source = FileSource::open(temporary.path(), limits(4, 2, 3))?;
        temporary.set_len(0)?;
        assert_eq!(
            source.read_exact_at(ByteOffset::new(0), &mut [0; 2]),
            Err(Error::ShortRead {
                offset: ByteOffset::new(0),
                needed: ByteCount::new(2),
                actual: ByteCount::new(0),
            })
        );
        assert_eq!(source.total_read(), ByteCount::new(2));
        assert_eq!(
            source.read_exact_at(ByteOffset::new(0), &mut [0; 2]),
            Err(Error::LimitExceeded {
                kind: LimitKind::TotalReadBytes,
                requested: ByteCount::new(4),
                maximum: ByteCount::new(3),
            })
        );
        Ok(())
    }

    #[test]
    fn opening_missing_file_returns_structured_io_error() -> TestResult {
        let temporary = TempFile::create(&[])?;
        let path = temporary.path().to_path_buf();
        drop(temporary);
        assert!(matches!(
            FileSource::open(&path, limits(0, 0, 0)),
            Err(Error::Io {
                operation: "open input file",
                kind: std::io::ErrorKind::NotFound,
            })
        ));
        Ok(())
    }

    #[test]
    fn wrapping_file_does_not_depend_on_its_current_cursor() -> TestResult {
        let temporary = TempFile::create(&[1, 2, 3])?;
        let mut file = temporary.open()?;
        file.seek(SeekFrom::End(0))?;
        let mut source = FileSource::from_file(file, limits(3, 3, 3))?;
        let mut output = [0; 3];
        assert_eq!(
            source.read_exact_at(ByteOffset::new(0), &mut output),
            Ok(())
        );
        assert_eq!(output, [1, 2, 3]);
        Ok(())
    }
}
