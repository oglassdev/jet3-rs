//! Safe Windows implementation of fixed-pair bundle publication.
//!
//! Both artifact handles deny later write opens while the fixed pair is staged.
//! Immediately before the same-parent directory rename, the publisher validates
//! every retained identity and the exact artifact bytes, then releases all
//! handles because Windows can reject a directory rename with open descendants.
//! The subsequent path-based rename and cleanup assume an ordinary uncontended
//! namespace; hostile mutation by another same-authority process is outside the
//! CLI threat model. Rust's safe standard-library Windows API provides no
//! documented directory-entry durability barrier, so a completed rename is
//! reported as published with uncertain durability rather than as success.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, Write};
use std::os::windows::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

use same_file::Handle;

use super::{
    NEXT_STAGE, PublishError, PublishHook, PublishPoint, RECEIPT_NAME, SNAPSHOT_NAME,
    STAGE_ATTEMPTS,
};

const FILE_SHARE_READ: u32 = 0x0000_0001;
const FILE_SHARE_DELETE: u32 = 0x0000_0004;
const ARTIFACT_SHARE_MODE: u32 = FILE_SHARE_READ | FILE_SHARE_DELETE;

struct ArtifactHandle {
    leaf: &'static str,
    handle: File,
    identity: Handle,
}

struct OwnedStage {
    parent_path: PathBuf,
    parent_identity: Option<Handle>,
    outer_path: PathBuf,
    outer_identity: Option<Handle>,
    bundle_path: PathBuf,
    bundle_identity: Option<Handle>,
    artifacts: Vec<ArtifactHandle>,
    settled: bool,
}

impl OwnedStage {
    fn validate_opened_paths(&mut self, snapshot: &[u8], receipt: &[u8]) -> std::io::Result<bool> {
        if !identity_matches(
            &self.parent_path,
            self.parent_identity
                .as_ref()
                .ok_or_else(|| std::io::Error::other("parent handle was released"))?,
        )? || !identity_matches(
            &self.outer_path,
            self.outer_identity
                .as_ref()
                .ok_or_else(|| std::io::Error::other("outer handle was released"))?,
        )? || !identity_matches(
            &self.bundle_path,
            self.bundle_identity
                .as_ref()
                .ok_or_else(|| std::io::Error::other("bundle handle was released"))?,
        )? {
            return Ok(false);
        }
        for artifact in &mut self.artifacts {
            let expected = match artifact.leaf {
                SNAPSHOT_NAME => snapshot,
                RECEIPT_NAME => receipt,
                _ => return Ok(false),
            };
            if artifact.handle.metadata().is_err()
                || !identity_matches(&self.bundle_path.join(artifact.leaf), &artifact.identity)?
                || !file_matches(&mut artifact.handle, expected)?
            {
                return Ok(false);
            }
        }
        Ok(fs::read_dir(&self.bundle_path)?.count() == self.artifacts.len())
    }

    fn prepare_rename(&mut self, snapshot: &[u8], receipt: &[u8]) -> std::io::Result<bool> {
        if !self.validate_opened_paths(snapshot, receipt)? {
            return Ok(false);
        }
        self.artifacts.clear();
        drop(self.bundle_identity.take());
        drop(self.outer_identity.take());
        drop(self.parent_identity.take());
        Ok(true)
    }

    fn cleanup(mut self, error: PublishError) -> Result<(), PublishError> {
        self.settled = true;
        let outer_matches = self.parent_identity.is_none()
            || self.outer_identity.as_ref().is_some_and(|identity| {
                identity_matches(&self.outer_path, identity).unwrap_or(false)
            });
        self.artifacts.clear();
        drop(self.bundle_identity.take());
        drop(self.outer_identity.take());
        drop(self.parent_identity.take());
        if !outer_matches {
            return Err(PublishError::CleanupUncertain);
        }
        match fs::remove_dir_all(&self.outer_path) {
            Ok(()) => Err(error),
            Err(_) => Err(PublishError::CleanupFailed),
        }
    }

    fn finish_publication(&mut self) -> std::io::Result<bool> {
        self.settled = true;
        let outer_matches = if self.parent_identity.is_none() {
            true
        } else {
            match self.outer_identity.as_ref() {
                Some(identity) => identity_matches(&self.outer_path, identity)?,
                None => false,
            }
        };
        drop(self.outer_identity.take());
        drop(self.parent_identity.take());
        if !outer_matches {
            return Ok(false);
        }
        fs::remove_dir(&self.outer_path)?;
        Ok(true)
    }
}

impl Drop for OwnedStage {
    fn drop(&mut self) {
        if self.settled {
            return;
        }
        let outer_matches = self.parent_identity.is_none()
            || self.outer_identity.as_ref().is_some_and(|identity| {
                identity_matches(&self.outer_path, identity).unwrap_or(false)
            });
        self.artifacts.clear();
        drop(self.bundle_identity.take());
        drop(self.outer_identity.take());
        drop(self.parent_identity.take());
        if outer_matches {
            let _ = fs::remove_dir_all(&self.outer_path);
        }
    }
}

pub(super) fn publish_with(
    destination: &Path,
    snapshot: &[u8],
    receipt: &[u8],
    hook: &mut impl PublishHook,
) -> Result<(), PublishError> {
    super::platform_preflight()?;
    let leaf = destination
        .file_name()
        .filter(|leaf| !leaf.is_empty())
        .ok_or(PublishError::InvalidDestination)?;
    let parent_path = destination.parent().unwrap_or_else(|| Path::new("."));
    let parent_path = parent_path
        .canonicalize()
        .map_err(|_| PublishError::InvalidDestination)?;
    let parent_identity =
        Handle::from_path(&parent_path).map_err(|_| PublishError::InvalidDestination)?;
    let destination = parent_path.join(leaf);
    reject_existing_leaf(&destination)?;
    let mut stage = create_stage(parent_path, parent_identity, hook, &destination)?;

    let snapshot_handle = match write_artifact(
        &stage.bundle_path,
        SNAPSHOT_NAME,
        snapshot,
        PublishPoint::CreateSnapshot,
        PublishPoint::WriteSnapshot,
        PublishPoint::SyncSnapshot,
        hook,
        &destination,
    ) {
        Ok(artifact) => artifact,
        Err(error) => return stage.cleanup(error),
    };
    stage.artifacts.push(snapshot_handle);
    let receipt_handle = match write_artifact(
        &stage.bundle_path,
        RECEIPT_NAME,
        receipt,
        PublishPoint::CreateReceipt,
        PublishPoint::WriteReceipt,
        PublishPoint::SyncReceipt,
        hook,
        &destination,
    ) {
        Ok(artifact) => artifact,
        Err(error) => return stage.cleanup(error),
    };
    stage.artifacts.push(receipt_handle);
    if hook
        .before(PublishPoint::SyncStageDirectory, &destination)
        .is_err()
        || !stage
            .validate_opened_paths(snapshot, receipt)
            .unwrap_or(false)
    {
        return stage.cleanup(PublishError::StageSyncFailed);
    }
    if hook
        .before(PublishPoint::RenameBundle, &destination)
        .is_err()
    {
        return stage.cleanup(PublishError::RenameFailed);
    }
    if !stage.prepare_rename(snapshot, receipt).unwrap_or(false) {
        return stage.cleanup(PublishError::RenameFailed);
    }
    if fs::rename(&stage.bundle_path, &destination).is_err() {
        let publication_error = if fs::symlink_metadata(&destination).is_ok() {
            PublishError::RenameCollision
        } else {
            PublishError::RenameFailed
        };
        return stage.cleanup(publication_error);
    }

    let cleanup_complete = stage.finish_publication();
    if hook
        .before(PublishPoint::SyncParentDirectory, &destination)
        .is_err()
    {
        return Err(PublishError::PublishedDurabilityUncertain);
    }
    match cleanup_complete {
        Ok(true) => Err(PublishError::PublishedDurabilityUncertain),
        Ok(false) | Err(_) => Err(PublishError::PublishedCleanupUncertain),
    }
}

fn reject_existing_leaf(destination: &Path) -> Result<(), PublishError> {
    match fs::symlink_metadata(destination) {
        Ok(_) => Err(PublishError::DestinationExists),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(_) => Err(PublishError::InvalidDestination),
    }
}

fn create_stage(
    parent_path: PathBuf,
    parent_identity: Handle,
    hook: &mut impl PublishHook,
    destination: &Path,
) -> Result<OwnedStage, PublishError> {
    hook.before(PublishPoint::CreateStage, destination)
        .map_err(|_| PublishError::StageFailed)?;
    for _ in 0..STAGE_ATTEMPTS {
        let sequence = NEXT_STAGE.fetch_add(1, super::Ordering::Relaxed);
        let outer_path = parent_path.join(format!(
            ".jet3-bundle-stage-{}-{sequence}",
            std::process::id()
        ));
        match fs::create_dir(&outer_path) {
            Ok(()) => {
                return open_stage(parent_path, parent_identity, outer_path);
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(_) => return Err(PublishError::StageFailed),
        }
    }
    Err(PublishError::StageFailed)
}

fn open_stage(
    parent_path: PathBuf,
    parent_identity: Handle,
    outer_path: PathBuf,
) -> Result<OwnedStage, PublishError> {
    let result = (|| {
        let outer_identity = Handle::from_path(&outer_path)?;
        let bundle_path = outer_path.join("bundle");
        fs::create_dir(&bundle_path)?;
        let bundle_identity = Handle::from_path(&bundle_path)?;
        Ok(OwnedStage {
            parent_path,
            parent_identity: Some(parent_identity),
            outer_path: outer_path.clone(),
            outer_identity: Some(outer_identity),
            bundle_path,
            bundle_identity: Some(bundle_identity),
            artifacts: Vec::with_capacity(2),
            settled: false,
        })
    })();
    if result.is_err() {
        let _ = fs::remove_dir_all(outer_path);
    }
    result.map_err(|_: std::io::Error| PublishError::StageFailed)
}

#[allow(clippy::too_many_arguments)]
fn write_artifact(
    stage: &Path,
    leaf: &'static str,
    bytes: &[u8],
    create_point: PublishPoint,
    write_point: PublishPoint,
    sync_point: PublishPoint,
    hook: &mut impl PublishHook,
    destination: &Path,
) -> Result<ArtifactHandle, PublishError> {
    let artifact_error = match create_point {
        PublishPoint::CreateSnapshot => PublishError::SnapshotFailed,
        PublishPoint::CreateReceipt => PublishError::ReceiptFailed,
        _ => PublishError::StageFailed,
    };
    hook.before(create_point, destination)
        .map_err(|_| artifact_error)?;
    let mut handle = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .share_mode(ARTIFACT_SHARE_MODE)
        .open(stage.join(leaf))
        .map_err(|_| artifact_error)?;
    hook.before(write_point, destination)
        .and_then(|()| handle.write_all(bytes))
        .map_err(|_| artifact_error)?;
    hook.before(sync_point, destination)
        .and_then(|()| handle.sync_all())
        .map_err(|_| artifact_error)?;
    let identity = handle
        .try_clone()
        .and_then(Handle::from_file)
        .map_err(|_| artifact_error)?;
    Ok(ArtifactHandle {
        leaf,
        handle,
        identity,
    })
}

fn file_matches(file: &mut File, expected: &[u8]) -> std::io::Result<bool> {
    if file.metadata()?.len() != expected.len() as u64 {
        return Ok(false);
    }
    file.rewind()?;
    let mut offset = 0;
    let mut buffer = [0_u8; 8 * 1024];
    while offset < expected.len() {
        let length = buffer.len().min(expected.len() - offset);
        file.read_exact(&mut buffer[..length])?;
        if buffer[..length] != expected[offset..offset + length] {
            return Ok(false);
        }
        offset += length;
    }
    let mut trailing = [0_u8; 1];
    Ok(file.read(&mut trailing)? == 0)
}

fn identity_matches(path: &Path, expected: &Handle) -> std::io::Result<bool> {
    match Handle::from_path(path) {
        Ok(actual) => Ok(&actual == expected),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error),
    }
}
