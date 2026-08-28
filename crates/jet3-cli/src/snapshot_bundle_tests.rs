#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
use std::{fs, io, path::PathBuf};

use super::PublishError;
#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
use super::{PublishHook, PublishPoint, RECEIPT_NAME, SNAPSHOT_NAME, publish_with};

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
struct FailAt(PublishPoint);

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
impl PublishHook for FailAt {
    fn before(&mut self, point: PublishPoint, _destination: &std::path::Path) -> io::Result<()> {
        if point == self.0 {
            Err(io::Error::other("injected publication failure"))
        } else {
            Ok(())
        }
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
fn stages(directory: &std::path::Path) -> Result<Vec<PathBuf>, io::Error> {
    fs::read_dir(directory)?
        .filter_map(|entry| match entry {
            Ok(entry)
                if entry
                    .file_name()
                    .to_string_lossy()
                    .starts_with(".jet3-bundle-stage-") =>
            {
                Some(Ok(entry.path()))
            }
            Ok(_) => None,
            Err(error) => Some(Err(error)),
        })
        .collect()
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
#[test]
fn complete_bundle_is_published_with_fixed_artifact_names() -> Result<(), Box<dyn std::error::Error>>
{
    let directory = tempfile::tempdir()?;
    let destination = directory.path().join("bundle");
    publish_with(
        &destination,
        b"snapshot\n",
        b"receipt\n",
        &mut FailAtImpossible,
    )?;
    assert_eq!(fs::read(destination.join(SNAPSHOT_NAME))?, b"snapshot\n");
    assert_eq!(fs::read(destination.join(RECEIPT_NAME))?, b"receipt\n");
    assert!(stages(directory.path())?.is_empty());
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
struct FailAtImpossible;

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
impl PublishHook for FailAtImpossible {
    fn before(&mut self, _point: PublishPoint, _destination: &std::path::Path) -> io::Result<()> {
        Ok(())
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
#[test]
fn every_pre_rename_failure_leaves_no_bundle_or_owned_stage()
-> Result<(), Box<dyn std::error::Error>> {
    for point in [
        PublishPoint::CreateStage,
        PublishPoint::CreateSnapshot,
        PublishPoint::WriteSnapshot,
        PublishPoint::SyncSnapshot,
        PublishPoint::CreateReceipt,
        PublishPoint::WriteReceipt,
        PublishPoint::SyncReceipt,
        PublishPoint::SyncStageDirectory,
        PublishPoint::RenameBundle,
    ] {
        let directory = tempfile::tempdir()?;
        let destination = directory.path().join("bundle");
        assert!(publish_with(&destination, b"a", b"b", &mut FailAt(point)).is_err());
        assert!(!destination.exists());
        assert!(
            stages(directory.path())?.is_empty(),
            "leaked stage at {point:?}"
        );
    }
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
#[test]
fn parent_sync_failure_reports_published_but_uncertain() -> Result<(), Box<dyn std::error::Error>> {
    let directory = tempfile::tempdir()?;
    let destination = directory.path().join("bundle");
    assert_eq!(
        publish_with(
            &destination,
            b"snapshot",
            b"receipt",
            &mut FailAt(PublishPoint::SyncParentDirectory),
        ),
        Err(PublishError::PublishedDurabilityUncertain)
    );
    assert_eq!(fs::read(destination.join(SNAPSHOT_NAME))?, b"snapshot");
    assert_eq!(fs::read(destination.join(RECEIPT_NAME))?, b"receipt");
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
struct CreateCompetitor;

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
impl PublishHook for CreateCompetitor {
    fn before(&mut self, point: PublishPoint, destination: &std::path::Path) -> io::Result<()> {
        if point == PublishPoint::RenameBundle {
            fs::create_dir(destination)?;
            fs::write(destination.join("competitor"), b"keep")?;
        }
        Ok(())
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
#[test]
fn rename_collision_preserves_the_competitor_and_cleans_the_stage()
-> Result<(), Box<dyn std::error::Error>> {
    let directory = tempfile::tempdir()?;
    let destination = directory.path().join("bundle");
    assert_eq!(
        publish_with(&destination, b"ours", b"ours", &mut CreateCompetitor),
        Err(PublishError::RenameCollision)
    );
    assert_eq!(fs::read(destination.join("competitor"))?, b"keep");
    assert!(!destination.join(SNAPSHOT_NAME).exists());
    assert!(stages(directory.path())?.is_empty());
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
#[test]
fn existing_files_directories_and_hard_links_are_never_overwritten()
-> Result<(), Box<dyn std::error::Error>> {
    for kind in 0..3 {
        let directory = tempfile::tempdir()?;
        let destination = directory.path().join("bundle");
        let original = directory.path().join("original");
        match kind {
            0 => fs::write(&destination, b"file")?,
            1 => fs::create_dir(&destination)?,
            _ => {
                fs::write(&original, b"linked")?;
                fs::hard_link(&original, &destination)?;
            }
        }
        assert_eq!(
            publish_with(&destination, b"new", b"new", &mut FailAtImpossible),
            Err(PublishError::DestinationExists)
        );
        if kind == 2 {
            assert_eq!(fs::read(original)?, b"linked");
        }
    }
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
#[test]
fn lexical_alias_to_an_existing_input_cannot_bypass_identity_rejection()
-> Result<(), Box<dyn std::error::Error>> {
    let directory = tempfile::tempdir()?;
    let component = directory.path().join("component");
    fs::create_dir(&component)?;
    let input = directory.path().join("input.mdb");
    fs::write(&input, b"original input")?;
    let aliased_destination = component.join("..").join("input.mdb");
    assert_eq!(
        publish_with(
            &aliased_destination,
            b"snapshot",
            b"receipt",
            &mut FailAtImpossible,
        ),
        Err(PublishError::DestinationExists)
    );
    assert_eq!(fs::read(input)?, b"original input");
    Ok(())
}

#[test]
fn platform_contract_is_explicit_and_fail_closed_when_unsupported() {
    #[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
    {
        assert_eq!(super::platform_preflight(), Ok(()));
        for (error, code) in [
            (PublishError::InvalidDestination, "invalid_output_bundle"),
            (PublishError::DestinationExists, "output_bundle_exists"),
            (PublishError::StageFailed, "output_bundle_stage_failed"),
            (PublishError::SnapshotFailed, "snapshot_output_failed"),
            (PublishError::ReceiptFailed, "coverage_output_failed"),
            (
                PublishError::StageSyncFailed,
                "output_bundle_stage_sync_failed",
            ),
            (PublishError::RenameFailed, "output_bundle_publish_failed"),
            (PublishError::RenameCollision, "output_bundle_exists"),
            (PublishError::CleanupFailed, "output_bundle_cleanup_failed"),
            (
                PublishError::CleanupUncertain,
                "output_bundle_cleanup_uncertain",
            ),
            (
                PublishError::PublishedCleanupUncertain,
                "output_bundle_published_cleanup_uncertain",
            ),
            (
                PublishError::PublishedDurabilityUncertain,
                "output_bundle_published_durability_uncertain",
            ),
        ] {
            assert_eq!(crate::publication_error_code(error), code);
        }
    }
    #[cfg(not(any(target_os = "linux", target_vendor = "apple", windows)))]
    {
        assert_eq!(
            super::platform_preflight(),
            Err(PublishError::UnsupportedPlatform)
        );
        assert_eq!(
            crate::publication_error_code(PublishError::UnsupportedPlatform),
            "atomic_bundle_unsupported"
        );
    }
}

#[cfg(windows)]
struct AssertArtifactHandlesDenyWrite {
    observed: bool,
}

#[cfg(windows)]
impl PublishHook for AssertArtifactHandlesDenyWrite {
    fn before(&mut self, point: PublishPoint, destination: &std::path::Path) -> io::Result<()> {
        if point != PublishPoint::RenameBundle {
            return Ok(());
        }
        let parent = destination
            .parent()
            .ok_or_else(|| io::Error::other("destination parent is missing"))?;
        let stage = stages(parent)?
            .into_iter()
            .next()
            .ok_or_else(|| io::Error::other("owned stage is missing"))?;
        let bundle = stage.join("bundle");
        self.observed = [SNAPSHOT_NAME, RECEIPT_NAME].into_iter().all(|leaf| {
            fs::OpenOptions::new()
                .write(true)
                .open(bundle.join(leaf))
                .is_err()
        });
        if self.observed {
            Ok(())
        } else {
            Err(io::Error::other(
                "retained artifact handle allowed mutation",
            ))
        }
    }
}

#[cfg(windows)]
#[test]
fn windows_retains_non_writable_artifact_handles_through_staging()
-> Result<(), Box<dyn std::error::Error>> {
    let directory = tempfile::tempdir()?;
    let destination = directory.path().join("bundle");
    let mut hook = AssertArtifactHandlesDenyWrite { observed: false };
    publish_with(&destination, b"snapshot", b"receipt", &mut hook)?;
    assert!(hook.observed);
    assert_eq!(fs::read(destination.join(SNAPSHOT_NAME))?, b"snapshot");
    assert_eq!(fs::read(destination.join(RECEIPT_NAME))?, b"receipt");
    Ok(())
}

#[cfg(windows)]
#[test]
fn windows_live_and_dangling_symlink_destinations_are_rejected()
-> Result<(), Box<dyn std::error::Error>> {
    use std::os::windows::fs::symlink_file;

    for live in [false, true] {
        let directory = tempfile::tempdir()?;
        let target = directory.path().join("target");
        if live {
            fs::write(&target, b"target")?;
        }
        let destination = directory.path().join("bundle");
        if let Err(error) = symlink_file(&target, &destination) {
            if error.raw_os_error() == Some(1314) {
                return Ok(());
            }
            return Err(error.into());
        }
        assert_eq!(
            publish_with(&destination, b"new", b"new", &mut FailAtImpossible),
            Err(PublishError::DestinationExists)
        );
        assert!(fs::symlink_metadata(&destination)?.file_type().is_symlink());
    }
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
#[test]
fn native_directory_identity_distinguishes_a_replacement_entry()
-> Result<(), Box<dyn std::error::Error>> {
    let directory = tempfile::tempdir()?;
    let entry = directory.path().join("entry");
    let displaced = directory.path().join("displaced");
    fs::create_dir(&entry)?;
    let parent = fs::File::open(directory.path())?;
    let held = fs::File::open(&entry)?;
    let identity = super::directory_identity(&held)?;

    assert!(super::directory_identity_matches(
        &parent,
        std::ffi::OsStr::new("entry"),
        &identity,
    )?);
    fs::rename(&entry, &displaced)?;
    fs::create_dir(&entry)?;
    assert!(!super::directory_identity_matches(
        &parent,
        std::ffi::OsStr::new("entry"),
        &identity,
    )?);
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
#[test]
fn live_and_dangling_symlink_destinations_are_rejected_without_following()
-> Result<(), Box<dyn std::error::Error>> {
    use std::os::unix::fs::symlink;

    for live in [false, true] {
        let directory = tempfile::tempdir()?;
        let target = directory.path().join("target");
        if live {
            fs::create_dir(&target)?;
        }
        let destination = directory.path().join("bundle");
        symlink(&target, &destination)?;
        assert_eq!(
            publish_with(&destination, b"new", b"new", &mut FailAtImpossible),
            Err(PublishError::DestinationExists)
        );
        assert!(fs::symlink_metadata(&destination)?.file_type().is_symlink());
    }
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple", windows))]
#[test]
fn lexical_parent_alias_is_resolved_before_staging_and_publication()
-> Result<(), Box<dyn std::error::Error>> {
    let directory = tempfile::tempdir()?;
    let alias_component = directory.path().join("alias-component");
    fs::create_dir(&alias_component)?;
    let destination = alias_component.join("..").join("bundle");
    publish_with(&destination, b"snapshot", b"receipt", &mut FailAtImpossible)?;
    assert_eq!(
        fs::read(directory.path().join("bundle").join(SNAPSHOT_NAME))?,
        b"snapshot"
    );
    assert!(stages(directory.path())?.is_empty());
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
struct ReplaceParentAtCreateSnapshot {
    moved_parent: PathBuf,
    fail_after_replacement: bool,
    replacement_stage: Option<PathBuf>,
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
impl PublishHook for ReplaceParentAtCreateSnapshot {
    fn before(&mut self, point: PublishPoint, destination: &std::path::Path) -> io::Result<()> {
        if point == PublishPoint::CreateSnapshot {
            let parent = destination
                .parent()
                .ok_or_else(|| io::Error::other("destination parent is missing"))?;
            fs::rename(parent, &self.moved_parent)?;
            fs::create_dir(parent)?;
            let owned_stage = stages(&self.moved_parent)?
                .into_iter()
                .next()
                .ok_or_else(|| io::Error::other("owned stage is missing"))?;
            let replacement_stage = parent.join(
                owned_stage
                    .file_name()
                    .ok_or_else(|| io::Error::other("owned stage leaf is missing"))?,
            );
            fs::create_dir(&replacement_stage)?;
            fs::write(replacement_stage.join("competitor"), b"keep")?;
            self.replacement_stage = Some(replacement_stage);
        }
        if self.fail_after_replacement && point == PublishPoint::WriteSnapshot {
            return Err(io::Error::other("injected post-replacement failure"));
        }
        Ok(())
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
#[test]
fn parent_replacement_cannot_redirect_artifacts_publication_or_cleanup()
-> Result<(), Box<dyn std::error::Error>> {
    for fail_after_replacement in [false, true] {
        let directory = tempfile::tempdir()?;
        let parent = directory.path().join("parent");
        let moved_parent = directory.path().join("pinned-parent");
        fs::create_dir(&parent)?;
        let destination = parent.join("bundle");
        let mut hook = ReplaceParentAtCreateSnapshot {
            moved_parent: moved_parent.clone(),
            fail_after_replacement,
            replacement_stage: None,
        };

        let result = publish_with(&destination, b"snapshot", b"receipt", &mut hook);
        let replacement_stage = hook
            .replacement_stage
            .ok_or("replacement stage was not created")?;
        assert_eq!(fs::read(replacement_stage.join("competitor"))?, b"keep");
        assert!(!replacement_stage.join(SNAPSHOT_NAME).exists());
        assert!(!replacement_stage.join(RECEIPT_NAME).exists());
        assert!(!destination.exists());

        if fail_after_replacement {
            assert_eq!(result, Err(PublishError::SnapshotFailed));
            assert!(!moved_parent.join("bundle").exists());
            assert!(stages(&moved_parent)?.is_empty());
        } else {
            assert_eq!(result, Ok(()));
            assert_eq!(
                fs::read(moved_parent.join("bundle").join(SNAPSHOT_NAME))?,
                b"snapshot"
            );
            assert_eq!(
                fs::read(moved_parent.join("bundle").join(RECEIPT_NAME))?,
                b"receipt"
            );
            assert!(stages(&moved_parent)?.is_empty());
        }
    }
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
struct SwapStageAtRename {
    moved_stage: PathBuf,
    fail_after_swap: bool,
    replacement_stage: Option<PathBuf>,
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
impl PublishHook for SwapStageAtRename {
    fn before(&mut self, point: PublishPoint, destination: &std::path::Path) -> io::Result<()> {
        if point != PublishPoint::RenameBundle {
            return Ok(());
        }
        let parent = destination
            .parent()
            .ok_or_else(|| io::Error::other("destination parent is missing"))?;
        let owned_stage = stages(parent)?
            .into_iter()
            .next()
            .ok_or_else(|| io::Error::other("owned stage is missing"))?;
        let stage_leaf = owned_stage
            .file_name()
            .ok_or_else(|| io::Error::other("owned stage leaf is missing"))?
            .to_owned();
        fs::rename(&owned_stage, &self.moved_stage)?;

        let replacement_stage = parent.join(stage_leaf);
        fs::create_dir(&replacement_stage)?;
        fs::write(replacement_stage.join(SNAPSHOT_NAME), b"forged snapshot")?;
        fs::write(replacement_stage.join(RECEIPT_NAME), b"forged receipt")?;
        self.replacement_stage = Some(replacement_stage);

        if self.fail_after_swap {
            Err(io::Error::other("injected post-swap failure"))
        } else {
            Ok(())
        }
    }
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
fn assert_stage_swap_preserved(
    replacement_stage: &std::path::Path,
    moved_stage: &std::path::Path,
) -> Result<(), Box<dyn std::error::Error>> {
    assert_eq!(
        fs::read(replacement_stage.join(SNAPSHOT_NAME))?,
        b"forged snapshot"
    );
    assert_eq!(
        fs::read(replacement_stage.join(RECEIPT_NAME))?,
        b"forged receipt"
    );
    assert_eq!(fs::read_dir(moved_stage)?.count(), 0);
    Ok(())
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
#[test]
fn stage_entry_swap_cannot_publish_forged_replacement_bytes()
-> Result<(), Box<dyn std::error::Error>> {
    let directory = tempfile::tempdir()?;
    let destination = directory.path().join("bundle");
    let moved_stage = directory.path().join("displaced-owned-stage");
    let mut hook = SwapStageAtRename {
        moved_stage: moved_stage.clone(),
        fail_after_swap: false,
        replacement_stage: None,
    };

    assert_eq!(
        publish_with(
            &destination,
            b"genuine snapshot",
            b"genuine receipt",
            &mut hook
        ),
        Err(PublishError::PublishedCleanupUncertain)
    );
    assert_eq!(
        fs::read(destination.join(SNAPSHOT_NAME))?,
        b"genuine snapshot"
    );
    assert_eq!(
        fs::read(destination.join(RECEIPT_NAME))?,
        b"genuine receipt"
    );
    let replacement_stage = hook
        .replacement_stage
        .ok_or("replacement stage was not created")?;
    assert_stage_swap_preserved(&replacement_stage, &moved_stage)
}

#[cfg(any(target_os = "linux", target_vendor = "apple"))]
#[test]
fn stage_entry_swap_failure_cleans_only_the_owned_bundle() -> Result<(), Box<dyn std::error::Error>>
{
    let directory = tempfile::tempdir()?;
    let destination = directory.path().join("bundle");
    let moved_stage = directory.path().join("displaced-owned-stage");
    let mut hook = SwapStageAtRename {
        moved_stage: moved_stage.clone(),
        fail_after_swap: true,
        replacement_stage: None,
    };

    assert_eq!(
        publish_with(
            &destination,
            b"genuine snapshot",
            b"genuine receipt",
            &mut hook
        ),
        Err(PublishError::CleanupUncertain)
    );
    assert!(!destination.exists());
    let replacement_stage = hook
        .replacement_stage
        .ok_or("replacement stage was not created")?;
    assert_stage_swap_preserved(&replacement_stage, &moved_stage)
}
