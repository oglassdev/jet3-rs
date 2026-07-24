//! Copy-on-write publication for updates to an existing file.
//!
//! A private copy is created in the target's directory, mutated, independently
//! validated, synchronized, and then published with [`std::fs::rename`].
//! Same-directory placement avoids cross-filesystem rename. Atomic replacement
//! and crash durability remain subject to the operating system and filesystem
//! guarantees documented for `rename` and `sync_all`; network and unusual
//! filesystems may provide weaker guarantees. Directory synchronization is
//! attempted on Unix. Rust's standard library does not expose a portable
//! directory-sync operation on every other platform, so that step is a
//! documented no-op there.
//!
//! Callers must exclude concurrent writers to the target. This foundation does
//! not implement database or multi-user locking.
//! Validation through this API is only a publication prerequisite. A validator
//! must not modify or replace the private path, and validation by the writer or
//! another project component is not independent verification or compatibility
//! evidence.

use std::convert::Infallible;
use std::error::Error as StdError;
use std::ffi::OsString;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use crate::{Error, ResourceBudget};

const COPY_BUFFER_BYTES: usize = 64 * 1024;
const MAX_PRIVATE_NAME_ATTEMPTS: u64 = 128;

type BoxError = Box<dyn StdError + Send + Sync + 'static>;

/// A fault-injection and diagnostic boundary in atomic publication.
///
/// Hooks run immediately before the named work. A hook failure through
/// [`PublishStage::Publish`] occurs before rename and leaves the original
/// target in place. [`PublishStage::DirectorySync`] occurs after rename; an
/// error at that stage reports that the verified replacement was published but
/// its directory entry may not be durable across a crash.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PublishStage {
    /// Create an exclusive private file beside the target.
    PrivateCopyCreation,
    /// Copy the complete original into the private file.
    Copy,
    /// Run the caller's mutation on the private file.
    Mutation,
    /// Preserve standard-library file permissions on the private copy.
    Metadata,
    /// Run the caller's independent validation on the private path.
    Validation,
    /// Synchronize the validated private file.
    FileSync,
    /// Final barrier after validation and synchronization.
    PrePublish,
    /// Atomically rename the private file over the target.
    Publish,
    /// Synchronize the containing directory where supported.
    DirectorySync,
}

impl fmt::Display for PublishStage {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let name = match self {
            Self::PrivateCopyCreation => "private-copy creation",
            Self::Copy => "copy",
            Self::Mutation => "mutation",
            Self::Metadata => "metadata preservation",
            Self::Validation => "validation",
            Self::FileSync => "file synchronization",
            Self::PrePublish => "pre-publication barrier",
            Self::Publish => "publication",
            Self::DirectorySync => "directory synchronization",
        };
        formatter.write_str(name)
    }
}

/// A structured atomic-publication failure.
#[derive(Debug)]
pub struct PublishError {
    stage: PublishStage,
    replacement_published: bool,
    source: BoxError,
}

impl PublishError {
    /// Returns the stage that failed.
    #[must_use]
    pub const fn stage(&self) -> PublishStage {
        self.stage
    }

    /// Returns whether rename had published the verified replacement.
    ///
    /// `true` currently occurs only for directory-sync failures.
    #[must_use]
    pub const fn replacement_published(&self) -> bool {
        self.replacement_published
    }

    fn new<E>(stage: PublishStage, replacement_published: bool, source: E) -> Self
    where
        E: StdError + Send + Sync + 'static,
    {
        Self {
            stage,
            replacement_published,
            source: Box::new(source),
        }
    }
}

impl fmt::Display for PublishError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.replacement_published {
            write!(
                formatter,
                "{} failed after replacement publication: {}",
                self.stage, self.source
            )
        } else {
            write!(
                formatter,
                "{} failed before replacement publication: {}",
                self.stage, self.source
            )
        }
    }
}

impl StdError for PublishError {
    fn source(&self) -> Option<&(dyn StdError + 'static)> {
        Some(self.source.as_ref())
    }
}

/// Updates an existing file through a validated same-directory private copy.
///
/// `mutate` receives the private file positioned at byte zero. `validate`
/// receives the private path and must independently reopen or inspect it
/// without modifying it. Copy work is charged to the caller-owned operation
/// budget before each chunk. The original file's standard-library permissions
/// are applied to the private copy after mutation and before validation.
///
/// The validator must treat its path as read-only and must not rename, replace,
/// or otherwise mutate the private file. Passing an internal/self-validator
/// here does not constitute independent writer verification or compatibility
/// evidence.
pub fn atomic_update<M, V, ME, VE>(
    target: impl AsRef<Path>,
    budget: &mut ResourceBudget,
    mutate: M,
    validate: V,
) -> Result<(), PublishError>
where
    M: FnOnce(&mut File) -> Result<(), ME>,
    V: FnOnce(&Path) -> Result<(), VE>,
    ME: StdError + Send + Sync + 'static,
    VE: StdError + Send + Sync + 'static,
{
    atomic_update_with_hook(target, budget, mutate, validate, |_| {
        Ok::<(), Infallible>(())
    })
}

/// Updates an existing file with a hook before every publication stage.
///
/// This is intended for deterministic fault injection as well as diagnostics.
/// A hook error is wrapped in [`PublishError`] with the stage and publication
/// state at which it occurred.
pub fn atomic_update_with_hook<M, V, H, ME, VE, HE>(
    target: impl AsRef<Path>,
    budget: &mut ResourceBudget,
    mutate: M,
    validate: V,
    mut before_stage: H,
) -> Result<(), PublishError>
where
    M: FnOnce(&mut File) -> Result<(), ME>,
    V: FnOnce(&Path) -> Result<(), VE>,
    H: FnMut(PublishStage) -> Result<(), HE>,
    ME: StdError + Send + Sync + 'static,
    VE: StdError + Send + Sync + 'static,
    HE: StdError + Send + Sync + 'static,
{
    let target = target.as_ref();
    let parent = normalized_parent(target);
    let metadata = fs::symlink_metadata(target)
        .map_err(|error| PublishError::new(PublishStage::PrivateCopyCreation, false, error))?;
    if !metadata.file_type().is_file() {
        return Err(PublishError::new(
            PublishStage::PrivateCopyCreation,
            false,
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "atomic update target must be an existing regular file",
            ),
        ));
    }

    call_hook(&mut before_stage, PublishStage::PrivateCopyCreation, false)?;
    let mut private = PrivateCopy::create(target, parent)
        .map_err(|error| PublishError::new(PublishStage::PrivateCopyCreation, false, error))?;

    call_hook(&mut before_stage, PublishStage::Copy, false)?;
    copy_original(target, private.file_mut()?, metadata.len(), budget)?;

    call_hook(&mut before_stage, PublishStage::Mutation, false)?;
    let private_file = private.file_mut()?;
    private_file
        .seek(SeekFrom::Start(0))
        .map_err(|error| PublishError::new(PublishStage::Mutation, false, error))?;
    mutate(private_file)
        .map_err(|error| PublishError::new(PublishStage::Mutation, false, error))?;

    call_hook(&mut before_stage, PublishStage::Metadata, false)?;
    fs::set_permissions(private.path(), metadata.permissions())
        .map_err(|error| PublishError::new(PublishStage::Metadata, false, error))?;

    call_hook(&mut before_stage, PublishStage::Validation, false)?;
    validate(private.path())
        .map_err(|error| PublishError::new(PublishStage::Validation, false, error))?;

    call_hook(&mut before_stage, PublishStage::FileSync, false)?;
    private
        .file_mut()?
        .sync_all()
        .map_err(|error| PublishError::new(PublishStage::FileSync, false, error))?;
    private.close();

    call_hook(&mut before_stage, PublishStage::PrePublish, false)?;
    call_hook(&mut before_stage, PublishStage::Publish, false)?;
    fs::rename(private.path(), target)
        .map_err(|error| PublishError::new(PublishStage::Publish, false, error))?;
    private.mark_published();

    call_hook(&mut before_stage, PublishStage::DirectorySync, true)?;
    sync_directory(parent)
        .map_err(|error| PublishError::new(PublishStage::DirectorySync, true, error))?;
    Ok(())
}

fn call_hook<H, HE>(
    hook: &mut H,
    stage: PublishStage,
    replacement_published: bool,
) -> Result<(), PublishError>
where
    H: FnMut(PublishStage) -> Result<(), HE>,
    HE: StdError + Send + Sync + 'static,
{
    hook(stage).map_err(|error| PublishError::new(stage, replacement_published, error))
}

fn copy_original(
    target: &Path,
    destination: &mut File,
    original_len: u64,
    budget: &mut ResourceBudget,
) -> Result<(), PublishError> {
    let mut source =
        File::open(target).map_err(|error| PublishError::new(PublishStage::Copy, false, error))?;
    let mut buffer = [0_u8; COPY_BUFFER_BYTES];
    let mut remaining = original_len;
    while remaining != 0 {
        let chunk_u64 = remaining.min(COPY_BUFFER_BYTES as u64);
        let chunk = usize::try_from(chunk_u64)
            .map_err(|error| PublishError::new(PublishStage::Copy, false, error))?;
        budget
            .charge_work_units(chunk_u64)
            .map_err(|error| PublishError::new(PublishStage::Copy, false, error))?;
        let bytes = buffer.get_mut(..chunk).ok_or_else(|| {
            PublishError::new(
                PublishStage::Copy,
                false,
                Error::Arithmetic {
                    operation: "select atomic copy buffer range",
                },
            )
        })?;
        source
            .read_exact(bytes)
            .map_err(|error| PublishError::new(PublishStage::Copy, false, error))?;
        destination
            .write_all(bytes)
            .map_err(|error| PublishError::new(PublishStage::Copy, false, error))?;
        remaining = remaining.checked_sub(chunk_u64).ok_or_else(|| {
            PublishError::new(
                PublishStage::Copy,
                false,
                Error::Arithmetic {
                    operation: "decrement atomic copy remainder",
                },
            )
        })?;
    }
    Ok(())
}

fn normalized_parent(target: &Path) -> &Path {
    match target.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent,
        _ => Path::new("."),
    }
}

struct PrivateCopy {
    path: PathBuf,
    file: Option<File>,
    published: bool,
}

impl PrivateCopy {
    fn create(target: &Path, parent: &Path) -> io::Result<Self> {
        let file_name = target.file_name().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "atomic update target has no file name",
            )
        })?;
        for attempt in 0..MAX_PRIVATE_NAME_ATTEMPTS {
            let mut private_name = OsString::from(".");
            private_name.push(file_name);
            private_name.push(format!(".jet3-private-{}-{attempt}", std::process::id()));
            let path = parent.join(private_name);
            match OpenOptions::new()
                .read(true)
                .write(true)
                .create_new(true)
                .open(&path)
            {
                Ok(file) => {
                    return Ok(Self {
                        path,
                        file: Some(file),
                        published: false,
                    });
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
                Err(error) => return Err(error),
            }
        }
        Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "could not create a unique private copy",
        ))
    }

    fn path(&self) -> &Path {
        &self.path
    }

    fn file_mut(&mut self) -> Result<&mut File, PublishError> {
        self.file.as_mut().ok_or_else(|| {
            PublishError::new(
                PublishStage::PrePublish,
                false,
                io::Error::other("private copy is already closed"),
            )
        })
    }

    fn close(&mut self) {
        self.file = None;
    }

    fn mark_published(&mut self) {
        self.published = true;
    }
}

impl Drop for PrivateCopy {
    fn drop(&mut self) {
        if !self.published {
            let _cleanup_result = fs::remove_file(&self.path);
        }
    }
}

#[cfg(unix)]
fn sync_directory(parent: &Path) -> io::Result<()> {
    File::open(parent)?.sync_all()
}

#[cfg(not(unix))]
fn sync_directory(_parent: &Path) -> io::Result<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::io::{Seek, SeekFrom, Write};
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU64, Ordering};

    use super::{PublishStage, atomic_update, atomic_update_with_hook};
    use crate::{ReadLimits, ResourceBudget, ResourceLimits};

    static NEXT_TEST_DIRECTORY: AtomicU64 = AtomicU64::new(0);
    type TestResult = Result<(), Box<dyn std::error::Error>>;

    #[derive(Debug)]
    struct TestFailure(&'static str);

    impl std::fmt::Display for TestFailure {
        fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str(self.0)
        }
    }

    impl std::error::Error for TestFailure {}

    struct TestDirectory {
        path: PathBuf,
    }

    impl TestDirectory {
        fn create() -> Result<Self, std::io::Error> {
            let sequence = NEXT_TEST_DIRECTORY.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "jet3-atomic-test-{}-{sequence}",
                std::process::id()
            ));
            fs::create_dir(&path)?;
            Ok(Self { path })
        }

        fn target(&self) -> PathBuf {
            self.path.join("target.bin")
        }

        fn private_entries(&self) -> Result<usize, std::io::Error> {
            let mut count = 0_usize;
            for entry in fs::read_dir(&self.path)? {
                let name = entry?.file_name();
                if name.to_string_lossy().contains(".jet3-private-") {
                    count = count.saturating_add(1);
                }
            }
            Ok(count)
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _cleanup_result = fs::remove_dir_all(&self.path);
        }
    }

    fn limits(max_work: u64) -> ResourceLimits {
        ResourceLimits::new(ReadLimits::default()).with_max_total_work_units(max_work)
    }

    fn replace_contents(file: &mut std::fs::File) -> Result<(), std::io::Error> {
        file.set_len(0)?;
        file.seek(SeekFrom::Start(0))?;
        file.write_all(b"replacement")
    }

    fn validate_replacement(path: &Path) -> Result<(), TestFailure> {
        if fs::read(path).map_err(|_| TestFailure("validation read failed"))? == b"replacement" {
            Ok(())
        } else {
            Err(TestFailure("replacement did not validate"))
        }
    }

    #[test]
    fn successful_update_publishes_validated_replacement_and_preserves_permissions() -> TestResult {
        let directory = TestDirectory::create()?;
        let target = directory.target();
        fs::write(&target, b"original")?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            fs::set_permissions(&target, fs::Permissions::from_mode(0o640))?;
        }
        let original_permissions = fs::metadata(&target)?.permissions();
        let mut budget = ResourceBudget::new(limits(8));
        atomic_update(&target, &mut budget, replace_contents, validate_replacement)?;
        assert_eq!(fs::read(&target)?, b"replacement");
        assert_eq!(
            fs::metadata(&target)?.permissions().readonly(),
            original_permissions.readonly()
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            assert_eq!(
                fs::metadata(&target)?.permissions().mode() & 0o777,
                original_permissions.mode() & 0o777
            );
        }
        assert_eq!(budget.total_work_units(), 8);
        assert_eq!(directory.private_entries()?, 0);
        Ok(())
    }

    #[test]
    fn every_injected_prepublication_failure_preserves_original_and_cleans_private_copy()
    -> TestResult {
        let stages = [
            PublishStage::PrivateCopyCreation,
            PublishStage::Copy,
            PublishStage::Mutation,
            PublishStage::Metadata,
            PublishStage::Validation,
            PublishStage::FileSync,
            PublishStage::PrePublish,
            PublishStage::Publish,
        ];
        for fault in stages {
            let directory = TestDirectory::create()?;
            let target = directory.target();
            fs::write(&target, b"original")?;
            let original = fs::read(&target)?;
            let mut budget = ResourceBudget::new(limits(1024));
            let error = atomic_update_with_hook(
                &target,
                &mut budget,
                replace_contents,
                validate_replacement,
                |stage| {
                    if stage == fault {
                        Err(TestFailure("injected stage failure"))
                    } else {
                        Ok(())
                    }
                },
            )
            .err()
            .ok_or(TestFailure("fault injection unexpectedly succeeded"))?;
            assert_eq!(error.stage(), fault);
            assert!(!error.replacement_published());
            assert_eq!(fs::read(&target)?, original);
            assert_eq!(directory.private_entries()?, 0);
        }
        Ok(())
    }

    #[test]
    fn directory_sync_fault_reports_published_validated_replacement() -> TestResult {
        let directory = TestDirectory::create()?;
        let target = directory.target();
        fs::write(&target, b"original")?;
        let mut budget = ResourceBudget::new(limits(1024));
        let error = atomic_update_with_hook(
            &target,
            &mut budget,
            replace_contents,
            validate_replacement,
            |stage| {
                if stage == PublishStage::DirectorySync {
                    Err(TestFailure("injected directory-sync failure"))
                } else {
                    Ok(())
                }
            },
        )
        .err()
        .ok_or(TestFailure("directory-sync fault unexpectedly succeeded"))?;
        assert_eq!(error.stage(), PublishStage::DirectorySync);
        assert!(error.replacement_published());
        assert_eq!(fs::read(&target)?, b"replacement");
        assert_eq!(directory.private_entries()?, 0);
        Ok(())
    }

    #[test]
    fn mutation_and_validation_errors_preserve_original() -> TestResult {
        for fail_validation in [false, true] {
            let directory = TestDirectory::create()?;
            let target = directory.target();
            fs::write(&target, b"original")?;
            let mut budget = ResourceBudget::new(limits(1024));
            let result = atomic_update(
                &target,
                &mut budget,
                |file| {
                    if fail_validation {
                        replace_contents(file).map_err(|_| TestFailure("mutation I/O"))
                    } else {
                        Err(TestFailure("mutation rejected"))
                    }
                },
                |_path| {
                    if fail_validation {
                        Err(TestFailure("validation rejected"))
                    } else {
                        Ok(())
                    }
                },
            );
            let error = result
                .err()
                .ok_or(TestFailure("callback failure unexpectedly succeeded"))?;
            let expected = if fail_validation {
                PublishStage::Validation
            } else {
                PublishStage::Mutation
            };
            assert_eq!(error.stage(), expected);
            assert_eq!(fs::read(&target)?, b"original");
            assert_eq!(directory.private_entries()?, 0);
        }
        Ok(())
    }

    #[test]
    fn copy_work_limit_covers_one_below_exact_and_one_above() -> TestResult {
        for (maximum, succeeds) in [(7, false), (8, true), (9, true)] {
            let directory = TestDirectory::create()?;
            let target = directory.target();
            fs::write(&target, b"original")?;
            let mut budget = ResourceBudget::new(limits(maximum));
            let result =
                atomic_update(&target, &mut budget, replace_contents, validate_replacement);
            assert_eq!(result.is_ok(), succeeds);
            if succeeds {
                assert_eq!(fs::read(&target)?, b"replacement");
            } else {
                let error = result
                    .err()
                    .ok_or(TestFailure("limited copy unexpectedly succeeded"))?;
                assert_eq!(error.stage(), PublishStage::Copy);
                assert_eq!(fs::read(&target)?, b"original");
            }
            assert_eq!(directory.private_entries()?, 0);
        }
        Ok(())
    }

    #[test]
    fn nonexistent_and_non_regular_targets_are_rejected() -> TestResult {
        let directory = TestDirectory::create()?;
        let mut budget = ResourceBudget::new(limits(1024));
        for target in [directory.path.join("missing"), directory.path.clone()] {
            let result = atomic_update(target, &mut budget, replace_contents, validate_replacement);
            let error = result
                .err()
                .ok_or(TestFailure("invalid target unexpectedly succeeded"))?;
            assert_eq!(error.stage(), PublishStage::PrivateCopyCreation);
            assert!(!error.replacement_published());
        }
        Ok(())
    }
}
