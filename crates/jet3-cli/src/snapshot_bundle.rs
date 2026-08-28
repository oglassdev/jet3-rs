//! Atomic publication of the fixed semantic snapshot bundle.
//!
//! The publisher stages and syncs both fixed-name artifacts before one
//! no-replace rename. It rejects existing destinations, lexical aliases,
//! symlinks, and hard links. Unix publication is descriptor-relative after
//! opening the parent and stage. Windows retains native directory and artifact
//! handles while staging, denies later artifact write opens, validates the
//! exact paths and bytes, then releases every handle immediately before its
//! path-based rename. Focused tests cover ordinary failures, collisions, and
//! platform-specific handle guarantees.
//!
//! Hostile concurrent namespace mutation by another process with the same
//! filesystem authority is outside the CLI threat model. Windows publication
//! must release its validated handles before rename, and the outer stage's
//! create/open and identity-check/remove pairs are separate portable
//! operations. These path operations are therefore ordinary-condition safety
//! checks, not an atomic security boundary against that principal.

use std::path::Path;

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
use std::ffi::{OsStr, OsString};
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
use std::fs::File;
#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
use std::io;
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
use std::io::Write;
#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
use std::sync::atomic::{AtomicU64, Ordering};

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
pub(crate) const SNAPSHOT_NAME: &str = "snapshot.json";
#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
pub(crate) const RECEIPT_NAME: &str = "coverage-receipt.json";
#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
const STAGE_ATTEMPTS: u64 = 128;
#[cfg(any(target_os = "linux", target_vendor = "apple"))]
const BUNDLE_DIRECTORY_NAME: &str = "bundle";
#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
static NEXT_STAGE: AtomicU64 = AtomicU64::new(0);

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
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

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum PublishError {
    InvalidDestination,
    DestinationExists,
    StageFailed,
    SnapshotFailed,
    ReceiptFailed,
    StageSyncFailed,
    RenameFailed,
    RenameCollision,
    CleanupFailed,
    CleanupUncertain,
    PublishedCleanupUncertain,
    PublishedDurabilityUncertain,
}

#[cfg(not(any(target_os = "linux", target_vendor = "apple", windows)))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum PublishError {
    UnsupportedPlatform,
}

impl std::fmt::Display for PublishError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "bundle publication failed: {self:?}")
    }
}

impl std::error::Error for PublishError {}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
pub(crate) trait PublishHook {
    fn before(&mut self, point: PublishPoint, destination: &Path) -> io::Result<()>;
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
struct NoFailures;

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
impl PublishHook for NoFailures {
    fn before(&mut self, _point: PublishPoint, _destination: &Path) -> io::Result<()> {
        Ok(())
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
struct OwnedStage<'parent> {
    parent: &'parent File,
    outer: File,
    bundle: File,
    outer_leaf: OsString,
    outer_identity: DirectoryIdentity,
    bundle_published: bool,
    settled: bool,
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
impl OwnedStage<'_> {
    fn cleanup(mut self, error: PublishError) -> Result<(), PublishError> {
        self.settled = true;
        self.remove_bundle()
            .map_err(|_| PublishError::CleanupFailed)?;
        match self.remove_outer() {
            Ok(OuterCleanup::Removed) => Err(error),
            Ok(OuterCleanup::Displaced) => Err(PublishError::CleanupUncertain),
            Err(_) => Err(PublishError::CleanupFailed),
        }
    }

    fn remove_bundle(&self) -> io::Result<()> {
        use rustix::fs::{AtFlags, unlinkat};

        if self.bundle_published {
            return Ok(());
        }
        let mut first_error = None;
        for artifact in [SNAPSHOT_NAME, RECEIPT_NAME] {
            if let Err(error) = unlinkat(&self.bundle, artifact, AtFlags::empty())
                && error != rustix::io::Errno::NOENT
                && first_error.is_none()
            {
                first_error = Some(io::Error::from(error));
            }
        }
        if let Err(error) = unlinkat(&self.outer, BUNDLE_DIRECTORY_NAME, AtFlags::REMOVEDIR)
            && error != rustix::io::Errno::NOENT
            && first_error.is_none()
        {
            first_error = Some(io::Error::from(error));
        }
        first_error.map_or(Ok(()), Err)
    }

    fn remove_outer(&self) -> io::Result<OuterCleanup> {
        use rustix::fs::{AtFlags, unlinkat};

        if !directory_identity_matches(self.parent, &self.outer_leaf, &self.outer_identity)? {
            return Ok(OuterCleanup::Displaced);
        }
        match unlinkat(self.parent, &self.outer_leaf, AtFlags::REMOVEDIR) {
            Ok(()) => Ok(OuterCleanup::Removed),
            Err(rustix::io::Errno::NOENT) => Ok(OuterCleanup::Displaced),
            Err(error) => Err(io::Error::from(error)),
        }
    }

    fn finish_publication(&mut self) -> io::Result<OuterCleanup> {
        self.bundle_published = true;
        self.settled = true;
        self.remove_outer()
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
impl Drop for OwnedStage<'_> {
    fn drop(&mut self) {
        if !self.settled {
            let _ = self.remove_bundle();
            let _ = self.remove_outer();
        }
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
struct DirectoryIdentity(rustix::fs::Stat);

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
impl PartialEq for DirectoryIdentity {
    fn eq(&self, other: &Self) -> bool {
        self.0.st_dev == other.0.st_dev && self.0.st_ino == other.0.st_ino
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
impl Eq for DirectoryIdentity {}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum OuterCleanup {
    Removed,
    Displaced,
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
pub(crate) fn publish(
    destination: &Path,
    snapshot: &[u8],
    receipt: &[u8],
) -> Result<(), PublishError> {
    publish_with(destination, snapshot, receipt, &mut NoFailures)
}

#[cfg(windows)]
pub(crate) fn publish(
    destination: &Path,
    snapshot: &[u8],
    receipt: &[u8],
) -> Result<(), PublishError> {
    publish_with(destination, snapshot, receipt, &mut NoFailures)
}

#[cfg(windows)]
fn publish_with(
    destination: &Path,
    snapshot: &[u8],
    receipt: &[u8],
    hook: &mut impl PublishHook,
) -> Result<(), PublishError> {
    windows::publish_with(destination, snapshot, receipt, hook)
}

#[cfg(not(any(target_os = "linux", target_vendor = "apple", windows)))]
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
        &stage.bundle,
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
        &stage.bundle,
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
        .and_then(|()| stage.bundle.sync_all())
        .and_then(|()| stage.outer.sync_all())
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
    if let Err(error) = rename_no_replace(
        &stage.outer,
        OsStr::new(BUNDLE_DIRECTORY_NAME),
        &parent,
        leaf,
    ) {
        let publication_error = if error.kind() == io::ErrorKind::AlreadyExists {
            PublishError::RenameCollision
        } else {
            PublishError::RenameFailed
        };
        return stage.cleanup(publication_error);
    }

    let mut stage = stage;
    let outer_cleanup = stage.finish_publication();
    if hook
        .before(PublishPoint::SyncParentDirectory, &destination)
        .and_then(|()| parent.sync_all())
        .is_err()
    {
        return Err(PublishError::PublishedDurabilityUncertain);
    }
    match outer_cleanup {
        Ok(OuterCleanup::Removed) => Ok(()),
        Ok(OuterCleanup::Displaced) | Err(_) => Err(PublishError::PublishedCleanupUncertain),
    }
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
        let outer_leaf = OsString::from(format!(
            ".jet3-bundle-stage-{}-{sequence}",
            std::process::id()
        ));
        match mkdirat(parent, &outer_leaf, Mode::RUSR | Mode::WUSR | Mode::XUSR) {
            Ok(()) => {
                let outer = match openat(
                    parent,
                    &outer_leaf,
                    OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
                    Mode::empty(),
                ) {
                    Ok(directory) => File::from(directory),
                    Err(_) => {
                        let _ = unlinkat(parent, &outer_leaf, AtFlags::REMOVEDIR);
                        return Err(PublishError::StageFailed);
                    }
                };
                let outer_identity =
                    directory_identity(&outer).map_err(|_| PublishError::StageFailed)?;
                if mkdirat(
                    &outer,
                    BUNDLE_DIRECTORY_NAME,
                    Mode::RUSR | Mode::WUSR | Mode::XUSR,
                )
                .is_err()
                {
                    if directory_identity_matches(parent, &outer_leaf, &outer_identity)
                        .unwrap_or(false)
                    {
                        let _ = unlinkat(parent, &outer_leaf, AtFlags::REMOVEDIR);
                    }
                    return Err(PublishError::StageFailed);
                }
                let bundle = match openat(
                    &outer,
                    BUNDLE_DIRECTORY_NAME,
                    OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
                    Mode::empty(),
                ) {
                    Ok(directory) => File::from(directory),
                    Err(_) => {
                        let _ = unlinkat(&outer, BUNDLE_DIRECTORY_NAME, AtFlags::REMOVEDIR);
                        if directory_identity_matches(parent, &outer_leaf, &outer_identity)
                            .unwrap_or(false)
                        {
                            let _ = unlinkat(parent, &outer_leaf, AtFlags::REMOVEDIR);
                        }
                        return Err(PublishError::StageFailed);
                    }
                };
                return Ok(OwnedStage {
                    parent,
                    outer,
                    bundle,
                    outer_leaf,
                    outer_identity,
                    bundle_published: false,
                    settled: false,
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

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn platform_preflight() -> Result<(), PublishError> {
    Ok(())
}

#[cfg(windows)]
fn platform_preflight() -> Result<(), PublishError> {
    Ok(())
}

#[cfg(not(any(target_os = "linux", target_vendor = "apple", windows)))]
fn platform_preflight() -> Result<(), PublishError> {
    Err(PublishError::UnsupportedPlatform)
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn rename_no_replace(
    source_parent: &File,
    source: &OsStr,
    destination_parent: &File,
    destination: &OsStr,
) -> io::Result<()> {
    use rustix::fs::{RenameFlags, renameat_with};

    renameat_with(
        source_parent,
        source,
        destination_parent,
        destination,
        RenameFlags::NOREPLACE,
    )
    .map_err(io::Error::from)
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn directory_identity(directory: &File) -> io::Result<DirectoryIdentity> {
    let stat = rustix::fs::fstat(directory).map_err(io::Error::from)?;
    Ok(DirectoryIdentity(stat))
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn directory_identity_at(parent: &File, leaf: &OsStr) -> io::Result<Option<DirectoryIdentity>> {
    use rustix::fs::{AtFlags, statat};

    match statat(parent, leaf, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(stat) => Ok(Some(DirectoryIdentity(stat))),
        Err(rustix::io::Errno::NOENT) => Ok(None),
        Err(error) => Err(io::Error::from(error)),
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn directory_identity_matches(
    parent: &File,
    leaf: &OsStr,
    expected: &DirectoryIdentity,
) -> io::Result<bool> {
    Ok(directory_identity_at(parent, leaf)?.is_some_and(|current| current.eq(expected)))
}

#[cfg(windows)]
#[path = "snapshot_bundle_windows.rs"]
mod windows;

#[cfg(test)]
#[path = "snapshot_bundle_tests.rs"]
mod tests;
