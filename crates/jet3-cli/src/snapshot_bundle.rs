//! Atomic publication of the fixed semantic snapshot bundle.

use std::ffi::OsStr;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

pub(crate) const SNAPSHOT_NAME: &str = "snapshot.json";
pub(crate) const RECEIPT_NAME: &str = "coverage-receipt.json";
const STAGE_ATTEMPTS: u64 = 128;
static NEXT_STAGE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum PublishPoint {
    CreateStage,
    CreateSnapshot,
    WriteSnapshot,
    SyncSnapshot,
    CreateReceipt,
    WriteReceipt,
    SyncReceipt,
    SyncStageDirectory,
    RenameBundle,
    SyncParentDirectory,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum PublishError {
    InvalidDestination,
    DestinationExists,
    UnsupportedPlatform,
    StageFailed,
    SnapshotFailed,
    ReceiptFailed,
    StageSyncFailed,
    RenameFailed,
    RenameCollision,
    CleanupFailed,
    PublishedDurabilityUncertain,
}

impl std::fmt::Display for PublishError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "bundle publication failed: {self:?}")
    }
}

impl std::error::Error for PublishError {}

pub(crate) trait PublishHook {
    fn before(&mut self, point: PublishPoint, destination: &Path) -> io::Result<()>;
}

struct NoFailures;

impl PublishHook for NoFailures {
    fn before(&mut self, _point: PublishPoint, _destination: &Path) -> io::Result<()> {
        Ok(())
    }
}

struct OwnedStage {
    path: PathBuf,
    published: bool,
}

impl OwnedStage {
    fn cleanup(mut self, error: PublishError) -> Result<(), PublishError> {
        self.published = true;
        fs::remove_dir_all(&self.path)
            .map_err(|_| PublishError::CleanupFailed)
            .and(Err(error))
    }
}

impl Drop for OwnedStage {
    fn drop(&mut self) {
        if !self.published {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}

pub(crate) fn publish(
    destination: &Path,
    snapshot: &[u8],
    receipt: &[u8],
) -> Result<(), PublishError> {
    publish_with(destination, snapshot, receipt, &mut NoFailures)
}

fn publish_with(
    destination: &Path,
    snapshot: &[u8],
    receipt: &[u8],
    hook: &mut impl PublishHook,
) -> Result<(), PublishError> {
    platform_preflight()?;
    let leaf = destination
        .file_name()
        .filter(|leaf| !leaf.is_empty())
        .ok_or(PublishError::InvalidDestination)?;
    let parent = destination.parent().unwrap_or_else(|| Path::new("."));
    let parent = parent
        .canonicalize()
        .map_err(|_| PublishError::InvalidDestination)?;
    if !parent.is_dir() {
        return Err(PublishError::InvalidDestination);
    }
    let destination = parent.join(leaf);
    reject_existing_leaf(&destination)?;
    let parent_file = File::open(&parent).map_err(|_| PublishError::InvalidDestination)?;
    let stage = create_stage(&parent, hook, &destination)?;

    if let Err(error) = write_artifact(
        &stage.path.join(SNAPSHOT_NAME),
        snapshot,
        PublishPoint::CreateSnapshot,
        PublishPoint::WriteSnapshot,
        PublishPoint::SyncSnapshot,
        hook,
        &destination,
    ) {
        return stage.cleanup(error);
    }
    if let Err(error) = write_artifact(
        &stage.path.join(RECEIPT_NAME),
        receipt,
        PublishPoint::CreateReceipt,
        PublishPoint::WriteReceipt,
        PublishPoint::SyncReceipt,
        hook,
        &destination,
    ) {
        return stage.cleanup(error);
    }
    if hook
        .before(PublishPoint::SyncStageDirectory, &destination)
        .and_then(|()| sync_directory(&stage.path))
        .is_err()
    {
        return stage.cleanup(PublishError::StageSyncFailed);
    }
    if hook
        .before(PublishPoint::RenameBundle, &destination)
        .is_err()
    {
        return stage.cleanup(PublishError::RenameFailed);
    }
    if let Err(error) = rename_no_replace(&parent, &parent_file, stage.path.file_name(), leaf) {
        let collision = fs::symlink_metadata(&destination).is_ok()
            || error.kind() == io::ErrorKind::AlreadyExists;
        return stage.cleanup(if collision {
            PublishError::RenameCollision
        } else {
            PublishError::RenameFailed
        });
    }

    let mut stage = stage;
    stage.published = true;
    if hook
        .before(PublishPoint::SyncParentDirectory, &destination)
        .and_then(|()| parent_file.sync_all())
        .is_err()
    {
        return Err(PublishError::PublishedDurabilityUncertain);
    }
    Ok(())
}

fn reject_existing_leaf(destination: &Path) -> Result<(), PublishError> {
    match fs::symlink_metadata(destination) {
        Ok(_) => Err(PublishError::DestinationExists),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(PublishError::InvalidDestination),
    }
}

fn create_stage(
    parent: &Path,
    hook: &mut impl PublishHook,
    destination: &Path,
) -> Result<OwnedStage, PublishError> {
    hook.before(PublishPoint::CreateStage, destination)
        .map_err(|_| PublishError::StageFailed)?;
    for _ in 0..STAGE_ATTEMPTS {
        let sequence = NEXT_STAGE.fetch_add(1, Ordering::Relaxed);
        let stage = parent.join(format!(
            ".jet3-bundle-stage-{}-{sequence}",
            std::process::id()
        ));
        match fs::create_dir(&stage) {
            Ok(()) => {
                set_private_permissions(&stage).map_err(|_| {
                    let _ = fs::remove_dir(&stage);
                    PublishError::StageFailed
                })?;
                return Ok(OwnedStage {
                    path: stage,
                    published: false,
                });
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(_) => return Err(PublishError::StageFailed),
        }
    }
    Err(PublishError::StageFailed)
}

fn write_artifact(
    path: &Path,
    bytes: &[u8],
    create_point: PublishPoint,
    write_point: PublishPoint,
    sync_point: PublishPoint,
    hook: &mut impl PublishHook,
    destination: &Path,
) -> Result<(), PublishError> {
    let artifact_error = match create_point {
        PublishPoint::CreateSnapshot => PublishError::SnapshotFailed,
        PublishPoint::CreateReceipt => PublishError::ReceiptFailed,
        _ => PublishError::StageFailed,
    };
    hook.before(create_point, destination)
        .map_err(|_| artifact_error)?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|_| artifact_error)?;
    hook.before(write_point, destination)
        .and_then(|()| file.write_all(bytes))
        .map_err(|_| artifact_error)?;
    hook.before(sync_point, destination)
        .and_then(|()| file.sync_all())
        .map_err(|_| artifact_error)
}

#[cfg(unix)]
fn set_private_permissions(path: &Path) -> io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(path, fs::Permissions::from_mode(0o700))
}

#[cfg(not(unix))]
fn set_private_permissions(_path: &Path) -> io::Result<()> {
    Ok(())
}

fn platform_preflight() -> Result<(), PublishError> {
    if cfg!(any(target_os = "linux", target_vendor = "apple")) {
        Ok(())
    } else {
        Err(PublishError::UnsupportedPlatform)
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn sync_directory(path: &Path) -> io::Result<()> {
    File::open(path)?.sync_all()
}

#[cfg(not(any(target_os = "linux", target_vendor = "apple")))]
fn sync_directory(_path: &Path) -> io::Result<()> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "safe directory synchronization is unavailable",
    ))
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn rename_no_replace(
    _parent_path: &Path,
    parent: &File,
    stage: Option<&OsStr>,
    destination: &OsStr,
) -> io::Result<()> {
    use rustix::fs::{RenameFlags, renameat_with};

    let stage = stage.ok_or_else(|| io::Error::other("stage leaf is missing"))?;
    Ok(renameat_with(
        parent,
        stage,
        parent,
        destination,
        RenameFlags::NOREPLACE,
    )?)
}

#[cfg(target_os = "windows")]
fn rename_no_replace(
    _parent_path: &Path,
    _parent: &File,
    _stage: Option<&OsStr>,
    _destination: &OsStr,
) -> io::Result<()> {
    // The standard library does not expose safe directory synchronization for
    // Windows. Failing before any rename is the no-clobber behavior until the
    // complete durability contract can be implemented with a safe primitive.
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "durable atomic bundle publication is unavailable",
    ))
}

#[cfg(not(any(target_os = "linux", target_vendor = "apple", target_os = "windows")))]
fn rename_no_replace(
    _parent_path: &Path,
    _parent: &File,
    _stage: Option<&OsStr>,
    _destination: &OsStr,
) -> io::Result<()> {
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "atomic no-replace directory rename is unavailable",
    ))
}

#[cfg(test)]
#[path = "snapshot_bundle_tests.rs"]
mod tests;
