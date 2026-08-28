use std::fs;
use std::io;
use std::path::PathBuf;

use super::{PublishError, PublishHook, PublishPoint, RECEIPT_NAME, SNAPSHOT_NAME, publish_with};

struct FailAt(PublishPoint);

impl PublishHook for FailAt {
    fn before(&mut self, point: PublishPoint, _destination: &std::path::Path) -> io::Result<()> {
        if point == self.0 {
            Err(io::Error::other("injected publication failure"))
        } else {
            Ok(())
        }
    }
}

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

struct FailAtImpossible;

impl PublishHook for FailAtImpossible {
    fn before(&mut self, _point: PublishPoint, _destination: &std::path::Path) -> io::Result<()> {
        Ok(())
    }
}

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

struct CreateCompetitor;

impl PublishHook for CreateCompetitor {
    fn before(&mut self, point: PublishPoint, destination: &std::path::Path) -> io::Result<()> {
        if point == PublishPoint::RenameBundle {
            fs::create_dir(destination)?;
            fs::write(destination.join("competitor"), b"keep")?;
        }
        Ok(())
    }
}

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
    let result = super::platform_preflight();
    if cfg!(any(target_os = "linux", target_vendor = "apple")) {
        assert_eq!(result, Ok(()));
    } else {
        assert_eq!(result, Err(PublishError::UnsupportedPlatform));
    }
}

#[cfg(unix)]
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
