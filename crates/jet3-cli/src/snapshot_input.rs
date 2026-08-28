//! Immutable, bounded snapshot input staging.

use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use jet3_testkit::{Sha256, Sha256Hasher, hex_digest};

const COPY_BUFFER_BYTES: usize = 64 * 1024;
const CREATE_ATTEMPTS: u64 = 128;
static NEXT_STAGE: AtomicU64 = AtomicU64::new(0);

struct OwnedTemporaryFile {
    file: Option<File>,
    path: PathBuf,
}

impl OwnedTemporaryFile {
    fn create() -> std::io::Result<Self> {
        let directory = env::temp_dir();
        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        for attempt in 0..CREATE_ATTEMPTS {
            let sequence = NEXT_STAGE.fetch_add(1, Ordering::Relaxed);
            let path = directory.join(format!(
                ".jet3-snapshot-input-{}-{timestamp:032x}-{sequence:016x}-{attempt:02x}",
                std::process::id()
            ));
            let mut options = OpenOptions::new();
            options.read(true).write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            match options.open(&path) {
                Ok(file) => {
                    return Ok(Self {
                        file: Some(file),
                        path,
                    });
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        }
        Err(std::io::Error::new(
            std::io::ErrorKind::AlreadyExists,
            "could not create a unique snapshot input stage",
        ))
    }

    fn file_mut(&mut self) -> std::io::Result<&mut File> {
        self.file
            .as_mut()
            .ok_or_else(|| std::io::Error::other("snapshot input stage is closed"))
    }

    fn try_clone(&self) -> std::io::Result<File> {
        self.file
            .as_ref()
            .ok_or_else(|| std::io::Error::other("snapshot input stage is closed"))?
            .try_clone()
    }
}

impl Drop for OwnedTemporaryFile {
    fn drop(&mut self) {
        drop(self.file.take());
        let _ = fs::remove_file(&self.path);
    }
}

pub(crate) struct StagedInput {
    file: OwnedTemporaryFile,
    length: u64,
    sha256: Sha256,
}

impl StagedInput {
    pub(crate) fn copy_from(mut source: File, max_bytes: u64) -> Result<Self, &'static str> {
        if source.metadata().map_err(|_| "metadata_failed")?.len() > max_bytes {
            return Err("input_limit_exceeded");
        }
        let mut staged = OwnedTemporaryFile::create().map_err(|_| "input_stage_create_failed")?;
        let mut hasher = Sha256Hasher::new();
        let mut buffer = [0_u8; COPY_BUFFER_BYTES];
        let mut length = 0_u64;
        loop {
            let remaining = max_bytes
                .checked_sub(length)
                .ok_or("input_limit_exceeded")?;
            let allowed = usize::try_from(remaining.min(COPY_BUFFER_BYTES as u64))
                .map_err(|_| "input_stage_read_failed")?;
            if allowed == 0 {
                let mut one_more = [0_u8; 1];
                if source
                    .read(&mut one_more)
                    .map_err(|_| "input_stage_read_failed")?
                    != 0
                {
                    return Err("input_limit_exceeded");
                }
                break;
            }
            let count = source
                .read(&mut buffer[..allowed])
                .map_err(|_| "input_stage_read_failed")?;
            if count == 0 {
                break;
            }
            staged
                .file_mut()
                .map_err(|_| "input_stage_write_failed")?
                .write_all(&buffer[..count])
                .map_err(|_| "input_stage_write_failed")?;
            hasher
                .update(&buffer[..count])
                .map_err(|_| "input_stage_hash_failed")?;
            length = length
                .checked_add(u64::try_from(count).map_err(|_| "input_stage_read_failed")?)
                .ok_or("input_limit_exceeded")?;
        }
        staged
            .file_mut()
            .map_err(|_| "input_stage_sync_failed")?
            .sync_all()
            .map_err(|_| "input_stage_sync_failed")?;
        staged
            .file_mut()
            .map_err(|_| "input_stage_rewind_failed")?
            .rewind()
            .map_err(|_| "input_stage_rewind_failed")?;
        let sha256 = Sha256::new(hex_digest(
            hasher.finalize().map_err(|_| "input_stage_hash_failed")?,
        ))
        .map_err(|_| "input_stage_hash_failed")?;
        Ok(Self {
            file: staged,
            length,
            sha256,
        })
    }

    pub(crate) const fn length(&self) -> u64 {
        self.length
    }

    pub(crate) fn sha256(&self) -> Sha256 {
        self.sha256.clone()
    }

    pub(crate) fn traversal_file(&self) -> Result<File, &'static str> {
        self.file.try_clone().map_err(|_| "input_stage_open_failed")
    }
}

#[cfg(test)]
mod tests {
    use std::fs::{self, File};
    use std::io::{Read, Seek, SeekFrom, Write};

    use super::StagedInput;

    fn read_staged(staged: StagedInput) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
        let mut file = staged.traversal_file()?;
        file.seek(SeekFrom::Start(0))?;
        let mut bytes = Vec::new();
        file.read_to_end(&mut bytes)?;
        Ok(bytes)
    }

    #[test]
    fn exact_boundary_is_accepted_and_one_over_is_rejected()
    -> Result<(), Box<dyn std::error::Error>> {
        let mut exact = tempfile::tempfile()?;
        exact.write_all(&[7_u8; 17])?;
        exact.rewind()?;
        let staged = StagedInput::copy_from(exact, 17)?;
        assert_eq!(staged.length(), 17);
        assert_eq!(read_staged(staged)?, [7_u8; 17]);

        let mut oversized = tempfile::tempfile()?;
        oversized.write_all(&[9_u8; 18])?;
        oversized.rewind()?;
        assert_eq!(
            StagedInput::copy_from(oversized, 17).err(),
            Some("input_limit_exceeded")
        );
        Ok(())
    }

    #[test]
    fn source_mutation_truncation_and_replacement_cannot_change_staged_bytes()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let source = directory.path().join("source.mdb");
        let alias = directory.path().join("source-alias.mdb");
        let original = b"immutable staged content";
        fs::write(&source, original)?;
        fs::hard_link(&source, &alias)?;
        let staged = StagedInput::copy_from(File::open(&alias)?, 1024)?;
        assert_eq!(
            staged.sha256().as_str(),
            "0ad036d72d4f0059a2fde9496dcea264bae3694aa06ff8d373aee89ae687fb12"
        );

        fs::write(&source, b"x")?;
        fs::remove_file(&source)?;
        fs::write(&source, b"replacement")?;
        assert_eq!(read_staged(staged)?, original);
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn changing_a_symlink_after_staging_cannot_redirect_the_private_copy()
    -> Result<(), Box<dyn std::error::Error>> {
        use std::os::unix::fs::symlink;

        let directory = tempfile::tempdir()?;
        let first = directory.path().join("first.mdb");
        let second = directory.path().join("second.mdb");
        let alias = directory.path().join("alias.mdb");
        fs::write(&first, b"first")?;
        fs::write(&second, b"second")?;
        symlink(&first, &alias)?;
        let staged = StagedInput::copy_from(File::open(&alias)?, 16)?;
        fs::remove_file(&alias)?;
        symlink(&second, &alias)?;
        assert_eq!(read_staged(staged)?, b"first");
        Ok(())
    }
}
