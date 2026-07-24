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
//! Pre-publication failures explicitly close and remove the private copy. If
//! removal fails, [`PublishError::cleanup_error`] retains that secondary error.
//! [`Drop`] retries removal as a last resort, but cannot report its result and
//! therefore does not provide a cleanup guarantee.

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
/// its directory entry may not be durable across a crash. [`Self::Cleanup`] is
/// visited only after a pre-publication failure, after closing the private file
/// and immediately before removing it.
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
    /// Remove the closed private copy after a pre-publication failure.
    Cleanup,
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
            Self::Cleanup => "private-copy cleanup",
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
    cleanup_source: Option<BoxError>,
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

    /// Returns a secondary private-copy cleanup failure, when one occurred.
    ///
    /// The primary error remains available through [`StdError::source`].
    #[must_use]
    pub fn cleanup_error(&self) -> Option<&(dyn StdError + 'static)> {
        self.cleanup_source
            .as_deref()
            .map(|error| error as &(dyn StdError + 'static))
    }

    fn new<E>(stage: PublishStage, replacement_published: bool, source: E) -> Self
    where
        E: StdError + Send + Sync + 'static,
    {
        Self {
            stage,
            replacement_published,
            source: Box::new(source),
            cleanup_source: None,
        }
    }

    fn attach_cleanup_error(&mut self, cleanup_source: BoxError) {
        self.cleanup_source = Some(cleanup_source);
    }
}

impl fmt::Display for PublishError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.replacement_published {
            write!(
                formatter,
                "{} failed after replacement publication: {}",
                self.stage, self.source
            )?;
        } else {
            write!(
                formatter,
                "{} failed before replacement publication: {}",
                self.stage, self.source
            )?;
        }
        if let Some(cleanup_source) = &self.cleanup_source {
            write!(
                formatter,
                "; private-copy cleanup also failed: {cleanup_source}"
            )?;
        }
        Ok(())
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
/// state at which it occurred. After a pre-publication error, the hook receives
/// [`PublishStage::Cleanup`] after the private file is closed and before its
/// removal.
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

    let update_result: Result<(), PublishError> = (|| {
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
    })();

    match update_result {
        Err(mut error) if !error.replacement_published() => {
            if let Err(cleanup_error) = private.cleanup(&mut before_stage) {
                error.attach_cleanup_error(cleanup_error);
            }
            Err(error)
        }
        result => result,
    }
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
    cleaned: bool,
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
                        cleaned: false,
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

    fn cleanup<H, HE>(&mut self, before_stage: &mut H) -> Result<(), BoxError>
    where
        H: FnMut(PublishStage) -> Result<(), HE>,
        HE: StdError + Send + Sync + 'static,
    {
        self.close();
        before_stage(PublishStage::Cleanup).map_err(|error| Box::new(error) as BoxError)?;
        prepare_private_for_removal(&self.path).map_err(|error| Box::new(error) as BoxError)?;
        match fs::remove_file(&self.path) {
            Ok(()) => {
                self.cleaned = true;
                Ok(())
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                self.cleaned = true;
                Ok(())
            }
            Err(error) => Err(Box::new(error)),
        }
    }
}

impl Drop for PrivateCopy {
    fn drop(&mut self) {
        if !self.published && !self.cleaned {
            self.close();
            let _permission_result = prepare_private_for_removal(&self.path);
            let _cleanup_result = fs::remove_file(&self.path);
        }
    }
}

#[cfg(windows)]
fn prepare_private_for_removal(path: &Path) -> io::Result<()> {
    let mut permissions = fs::metadata(path)?.permissions();
    permissions.set_readonly(false);
    fs::set_permissions(path, permissions)
}

#[cfg(not(windows))]
fn prepare_private_for_removal(_path: &Path) -> io::Result<()> {
    Ok(())
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
#[path = "atomic_tests.rs"]
mod tests;
