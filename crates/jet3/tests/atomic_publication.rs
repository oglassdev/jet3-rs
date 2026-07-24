#![cfg(unix)]

use std::fs;
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, mpsc};
use std::time::Duration;

use jet3::{PublishStage, ReadLimits, ResourceBudget, ResourceLimits, atomic_update_with_hook};

const CONTENT_BYTES: usize = 256 * 1024;
const CHANNEL_TIMEOUT: Duration = Duration::from_secs(10);
const MAX_OBSERVER_READS: usize = 1_000_000;
const ORIGINAL: u8 = 0x35;
const REPLACEMENT: u8 = 0xca;
static NEXT_TEST_DIRECTORY: AtomicU64 = AtomicU64::new(0);

type TestResult = Result<(), Box<dyn std::error::Error + Send + Sync>>;

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
            "jet3-atomic-publication-{}-{sequence}",
            std::process::id()
        ));
        fs::create_dir(&path)?;
        Ok(Self { path })
    }

    fn target(&self) -> PathBuf {
        self.path.join("target.bin")
    }

    fn private_entries(&self) -> Result<usize, std::io::Error> {
        Ok(self.private_paths()?.len())
    }

    fn private_paths(&self) -> Result<Vec<PathBuf>, std::io::Error> {
        let mut paths = Vec::new();
        for entry in fs::read_dir(&self.path)? {
            let entry = entry?;
            if entry
                .file_name()
                .to_string_lossy()
                .contains(".jet3-private-")
            {
                paths.push(entry.path());
            }
        }
        Ok(paths)
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _cleanup_result = fs::remove_dir_all(&self.path);
    }
}

fn operation_budget() -> ResourceBudget {
    let limits =
        ResourceLimits::new(ReadLimits::default()).with_max_total_work_units(CONTENT_BYTES as u64);
    ResourceBudget::new(limits)
}

fn write_replacement(file: &mut fs::File) -> Result<(), std::io::Error> {
    file.set_len(0)?;
    file.seek(SeekFrom::Start(0))?;
    file.write_all(&vec![REPLACEMENT; CONTENT_BYTES])
}

fn validate_replacement(path: &Path) -> Result<(), TestFailure> {
    let bytes = fs::read(path).map_err(|_| TestFailure("validation read failed"))?;
    if bytes == vec![REPLACEMENT; CONTENT_BYTES] {
        Ok(())
    } else {
        Err(TestFailure("replacement did not validate"))
    }
}

#[derive(Debug)]
enum Observation {
    OriginalSeen,
    ReplacementSeen,
    InvalidBytes,
    ReadFailed,
    ReadLimitExceeded,
}

#[test]
fn concurrent_observer_sees_only_original_or_validated_replacement() -> TestResult {
    let directory = TestDirectory::create()?;
    let target = directory.target();
    fs::write(&target, vec![ORIGINAL; CONTENT_BYTES])?;

    let (start_sender, start_receiver) = mpsc::sync_channel(0);
    let (observation_sender, observation_receiver) = mpsc::channel();
    let stop = Arc::new(AtomicBool::new(false));
    let observer_stop = Arc::clone(&stop);
    let observer_target = target.clone();
    let observer = std::thread::spawn(move || {
        let expected_original = vec![ORIGINAL; CONTENT_BYTES];
        let expected_replacement = vec![REPLACEMENT; CONTENT_BYTES];
        if start_receiver.recv_timeout(CHANNEL_TIMEOUT).is_err() {
            return;
        }
        let mut saw_original = false;
        for _ in 0..MAX_OBSERVER_READS {
            if observer_stop.load(Ordering::Acquire) {
                return;
            }
            let observation = match fs::read(&observer_target) {
                Ok(bytes) if bytes == expected_original => {
                    if saw_original {
                        continue;
                    }
                    saw_original = true;
                    Observation::OriginalSeen
                }
                Ok(bytes) if bytes == expected_replacement => Observation::ReplacementSeen,
                Ok(_) => Observation::InvalidBytes,
                Err(_) => Observation::ReadFailed,
            };
            let finished = matches!(
                observation,
                Observation::ReplacementSeen | Observation::InvalidBytes | Observation::ReadFailed
            );
            if observation_sender.send(observation).is_err() || finished {
                return;
            }
        }
        let _send_result = observation_sender.send(Observation::ReadLimitExceeded);
    });

    let mut budget = operation_budget();
    let result = atomic_update_with_hook(
        &target,
        &mut budget,
        write_replacement,
        validate_replacement,
        |stage| {
            if stage == PublishStage::Publish {
                start_sender
                    .send(())
                    .map_err(|_| TestFailure("observer did not start"))?;
                match observation_receiver.recv_timeout(CHANNEL_TIMEOUT) {
                    Ok(Observation::OriginalSeen) => {}
                    Ok(_) => return Err(TestFailure("observer did not see original first")),
                    Err(_) => return Err(TestFailure("observer timed out before publication")),
                }
            }
            if stage == PublishStage::DirectorySync {
                match observation_receiver.recv_timeout(CHANNEL_TIMEOUT) {
                    Ok(Observation::ReplacementSeen) => {}
                    Ok(Observation::InvalidBytes) => {
                        return Err(TestFailure("observer saw partial or unknown bytes"));
                    }
                    Ok(Observation::ReadFailed) => {
                        return Err(TestFailure("target disappeared during publication"));
                    }
                    Ok(Observation::ReadLimitExceeded) => {
                        return Err(TestFailure("observer exceeded its bounded read limit"));
                    }
                    Ok(Observation::OriginalSeen) => {
                        return Err(TestFailure("observer reported original twice"));
                    }
                    Err(_) => return Err(TestFailure("observer timed out after publication")),
                }
            }
            Ok(())
        },
    );
    stop.store(true, Ordering::Release);
    observer
        .join()
        .map_err(|_| TestFailure("observer thread panicked"))?;
    result?;

    assert_eq!(fs::read(&target)?, vec![REPLACEMENT; CONTENT_BYTES]);
    assert_eq!(directory.private_entries()?, 0);
    Ok(())
}

#[test]
fn every_stage_fault_reports_a_whole_file_and_cleans_private_copy() -> TestResult {
    let stages = [
        PublishStage::PrivateCopyCreation,
        PublishStage::Copy,
        PublishStage::Mutation,
        PublishStage::Metadata,
        PublishStage::Validation,
        PublishStage::FileSync,
        PublishStage::PrePublish,
        PublishStage::Publish,
        PublishStage::DirectorySync,
    ];

    for fault in stages {
        let directory = TestDirectory::create()?;
        let target = directory.target();
        fs::write(&target, vec![ORIGINAL; CONTENT_BYTES])?;
        set_distinct_permissions(&target)?;
        let original_permissions = fs::metadata(&target)?.permissions();
        let mut budget = operation_budget();

        let error = atomic_update_with_hook(
            &target,
            &mut budget,
            write_replacement,
            validate_replacement,
            |stage| {
                if stage == fault {
                    Err(TestFailure("injected stage fault"))
                } else {
                    Ok(())
                }
            },
        )
        .err()
        .ok_or(TestFailure("injected fault unexpectedly succeeded"))?;

        assert_eq!(error.stage(), fault);
        let expected_byte = if fault == PublishStage::DirectorySync {
            assert!(error.replacement_published());
            REPLACEMENT
        } else {
            assert!(!error.replacement_published());
            ORIGINAL
        };
        assert_eq!(fs::read(&target)?, vec![expected_byte; CONTENT_BYTES]);
        assert_permissions_equal(&target, &original_permissions)?;
        assert_eq!(directory.private_entries()?, 0);
    }

    let directory = TestDirectory::create()?;
    let target = directory.target();
    fs::write(&target, vec![ORIGINAL; CONTENT_BYTES])?;
    set_distinct_permissions(&target)?;
    let original_permissions = fs::metadata(&target)?.permissions();
    let mut budget = operation_budget();
    let cleanup_error = atomic_update_with_hook(
        &target,
        &mut budget,
        write_replacement,
        validate_replacement,
        |stage| match stage {
            PublishStage::Validation => Err(TestFailure("injected validation fault")),
            PublishStage::Cleanup => Err(TestFailure("injected cleanup fault")),
            _ => Ok(()),
        },
    )
    .err()
    .ok_or(TestFailure("cleanup fault unexpectedly succeeded"))?;
    assert_eq!(cleanup_error.stage(), PublishStage::Validation);
    assert!(cleanup_error.cleanup_error().is_some());
    assert_eq!(fs::read(&target)?, vec![ORIGINAL; CONTENT_BYTES]);
    assert_permissions_equal(&target, &original_permissions)?;
    assert_eq!(directory.private_entries()?, 0);
    Ok(())
}

#[test]
fn validator_path_substitution_is_rejected_without_publishing_or_deleting_it() -> TestResult {
    let directory = TestDirectory::create()?;
    let target = directory.target();
    let substitute = directory.path.join("substitute.bin");
    fs::write(&target, vec![ORIGINAL; CONTENT_BYTES])?;
    fs::write(&substitute, vec![0x91; CONTENT_BYTES])?;
    let original = fs::read(&target)?;
    let mut budget = operation_budget();

    let error = atomic_update_with_hook(
        &target,
        &mut budget,
        write_replacement,
        |private_path| {
            validate_replacement(private_path)?;
            fs::rename(&substitute, private_path)
                .map_err(|_| TestFailure("private-path substitution failed"))
        },
        |_| Ok::<(), TestFailure>(()),
    )
    .err()
    .ok_or(TestFailure("substituted private path was published"))?;

    assert_eq!(error.stage(), PublishStage::Publish);
    assert!(!error.replacement_published());
    assert_eq!(fs::read(&target)?, original);
    let cleanup_error = error.cleanup_error().ok_or(TestFailure(
        "identity-safe cleanup refusal was not reported",
    ))?;
    assert!(cleanup_error.to_string().contains("no longer identifies"));

    let private_paths = directory.private_paths()?;
    assert_eq!(private_paths.len(), 1);
    assert_eq!(fs::read(&private_paths[0])?, vec![0x91; CONTENT_BYTES]);
    Ok(())
}

#[cfg(unix)]
fn set_distinct_permissions(path: &Path) -> Result<(), std::io::Error> {
    use std::os::unix::fs::PermissionsExt;

    fs::set_permissions(path, fs::Permissions::from_mode(0o640))
}

#[cfg(not(unix))]
fn set_distinct_permissions(_path: &Path) -> Result<(), std::io::Error> {
    Ok(())
}

fn assert_permissions_equal(path: &Path, expected: &fs::Permissions) -> Result<(), std::io::Error> {
    let actual = fs::metadata(path)?.permissions();
    assert_eq!(actual.readonly(), expected.readonly());
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        assert_eq!(actual.mode() & 0o777, expected.mode() & 0o777);
    }
    Ok(())
}
