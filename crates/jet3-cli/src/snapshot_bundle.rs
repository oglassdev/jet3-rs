//! Atomic publication of the fixed semantic snapshot bundle.

use std::path::Path;

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
use std::ffi::{OsStr, OsString};
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
use std::fs::File;
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
use std::io::{self, Write};
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
pub(crate) const SNAPSHOT_NAME: &str = "snapshot.json";
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
pub(crate) const RECEIPT_NAME: &str = "coverage-receipt.json";
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
const STAGE_ATTEMPTS: u64 = 128;
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
static NEXT_STAGE: AtomicU64 = AtomicU64::new(0);

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
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

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
pub(crate) trait PublishHook {
    fn before(&mut self, point: PublishPoint, destination: &Path) -> io::Result<()>;
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
struct NoFailures;

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
impl PublishHook for NoFailures {
    fn before(&mut self, _point: PublishPoint, _destination: &Path) -> io::Result<()> {
        Ok(())
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
struct OwnedStage<'parent> {
    parent: &'parent File,
    directory: File,
    leaf: OsString,
    published: bool,
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
impl OwnedStage<'_> {
    fn cleanup(mut self, error: PublishError) -> Result<(), PublishError> {
        self.published = true;
        self.remove().map_err(|_| PublishError::CleanupFailed)?;
        Err(error)
    }

    fn remove(&self) -> io::Result<()> {
        use rustix::fs::{AtFlags, unlinkat};

        let mut first_error = None;
        for artifact in [SNAPSHOT_NAME, RECEIPT_NAME] {
            if let Err(error) = unlinkat(&self.directory, artifact, AtFlags::empty())
                && error != rustix::io::Errno::NOENT
                && first_error.is_none()
            {
                first_error = Some(io::Error::from(error));
            }
        }
        if let Err(error) = unlinkat(self.parent, &self.leaf, AtFlags::REMOVEDIR)
            && error != rustix::io::Errno::NOENT
            && first_error.is_none()
        {
            first_error = Some(io::Error::from(error));
        }
        first_error.map_or(Ok(()), Err)
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
impl Drop for OwnedStage<'_> {
    fn drop(&mut self) {
        if !self.published {
            let _ = self.remove();
        }
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
pub(crate) fn publish(
    destination: &Path,
    snapshot: &[u8],
    receipt: &[u8],
) -> Result<(), PublishError> {
    publish_with(destination, snapshot, receipt, &mut NoFailures)
}

#[cfg(not(any(target_os = "linux", target_vendor = "apple")))]
pub(crate) fn publish(
    _destination: &Path,
    _snapshot: &[u8],
    _receipt: &[u8],
) -> Result<(), PublishError> {
    platform_preflight()
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn publish_with(
    destination: &Path,
    snapshot: &[u8],
    receipt: &[u8],
    hook: &mut impl PublishHook,
) -> Result<(), PublishError> {
    use rustix::fs::{Mode, OFlags, open};

    platform_preflight()?;
    let leaf = destination
        .file_name()
        .filter(|leaf| !leaf.is_empty())
        .ok_or(PublishError::InvalidDestination)?;
    let parent_path = destination.parent().unwrap_or_else(|| Path::new("."));
    let parent_path = parent_path
        .canonicalize()
        .map_err(|_| PublishError::InvalidDestination)?;
    let parent = open(
        &parent_path,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map(File::from)
    .map_err(|_| PublishError::InvalidDestination)?;
    let destination = parent_path.join(leaf);
    reject_existing_leaf(&parent, leaf)?;
    let stage = create_stage(&parent, hook, &destination)?;

    if let Err(error) = write_artifact(
        &stage.directory,
        SNAPSHOT_NAME,
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
        &stage.directory,
        RECEIPT_NAME,
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
        .and_then(|()| stage.directory.sync_all())
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
    if let Err(error) = rename_no_replace(&parent, &stage.leaf, leaf) {
        let publication_error = if error.kind() == io::ErrorKind::AlreadyExists {
            PublishError::RenameCollision
        } else {
            PublishError::RenameFailed
        };
        return stage.cleanup(publication_error);
    }

    let mut stage = stage;
    stage.published = true;
    if hook
        .before(PublishPoint::SyncParentDirectory, &destination)
        .and_then(|()| parent.sync_all())
        .is_err()
    {
        return Err(PublishError::PublishedDurabilityUncertain);
    }
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn reject_existing_leaf(parent: &File, destination: &OsStr) -> Result<(), PublishError> {
    use rustix::fs::{AtFlags, statat};

    match statat(parent, destination, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(_) => Err(PublishError::DestinationExists),
        Err(rustix::io::Errno::NOENT) => Ok(()),
        Err(_) => Err(PublishError::InvalidDestination),
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn create_stage<'parent>(
    parent: &'parent File,
    hook: &mut impl PublishHook,
    destination: &Path,
) -> Result<OwnedStage<'parent>, PublishError> {
    use rustix::fs::{AtFlags, Mode, OFlags, mkdirat, openat, unlinkat};

    hook.before(PublishPoint::CreateStage, destination)
        .map_err(|_| PublishError::StageFailed)?;
    for _ in 0..STAGE_ATTEMPTS {
        let sequence = NEXT_STAGE.fetch_add(1, Ordering::Relaxed);
        let leaf = OsString::from(format!(
            ".jet3-bundle-stage-{}-{sequence}",
            std::process::id()
        ));
        match mkdirat(parent, &leaf, Mode::RUSR | Mode::WUSR | Mode::XUSR) {
            Ok(()) => {
                let directory = match openat(
                    parent,
                    &leaf,
                    OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
                    Mode::empty(),
                ) {
                    Ok(directory) => File::from(directory),
                    Err(_) => {
                        let _ = unlinkat(parent, &leaf, AtFlags::REMOVEDIR);
                        return Err(PublishError::StageFailed);
                    }
                };
                return Ok(OwnedStage {
                    parent,
                    directory,
                    leaf,
                    published: false,
                });
            }
            Err(rustix::io::Errno::EXIST) => continue,
            Err(_) => return Err(PublishError::StageFailed),
        }
    }
    Err(PublishError::StageFailed)
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
#[allow(clippy::too_many_arguments)]
fn write_artifact(
    stage: &File,
    leaf: &str,
    bytes: &[u8],
    create_point: PublishPoint,
    write_point: PublishPoint,
    sync_point: PublishPoint,
    hook: &mut impl PublishHook,
    destination: &Path,
) -> Result<(), PublishError> {
    use rustix::fs::{Mode, OFlags, openat};

    let artifact_error = match create_point {
        PublishPoint::CreateSnapshot => PublishError::SnapshotFailed,
        PublishPoint::CreateReceipt => PublishError::ReceiptFailed,
        _ => PublishError::StageFailed,
    };
    hook.before(create_point, destination)
        .map_err(|_| artifact_error)?;
    let mut file = openat(
        stage,
        leaf,
        OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::RUSR | Mode::WUSR,
    )
    .map(File::from)
    .map_err(|_| artifact_error)?;
    hook.before(write_point, destination)
        .and_then(|()| file.write_all(bytes))
        .map_err(|_| artifact_error)?;
    hook.before(sync_point, destination)
        .and_then(|()| file.sync_all())
        .map_err(|_| artifact_error)
}

fn platform_preflight() -> Result<(), PublishError> {
    if cfg!(any(target_os = "linux", target_vendor = "apple")) {
        Ok(())
    } else {
        Err(PublishError::UnsupportedPlatform)
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn rename_no_replace(parent: &File, stage: &OsStr, destination: &OsStr) -> io::Result<()> {
    use rustix::fs::{RenameFlags, renameat_with};

    renameat_with(parent, stage, parent, destination, RenameFlags::NOREPLACE)
        .map_err(io::Error::from)
}

#[cfg(test)]
#[path = "snapshot_bundle_tests.rs"]
mod tests;
