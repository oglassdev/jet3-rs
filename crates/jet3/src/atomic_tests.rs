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
fn every_injected_prepublication_failure_preserves_original_and_cleans_private_copy() -> TestResult
{
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
        let result = atomic_update(&target, &mut budget, replace_contents, validate_replacement);
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
