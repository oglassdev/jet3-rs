//! Immutable, bounded snapshot input staging.

use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use jet3_testkit::{Sha256, Sha256Hasher, hex_digest};

const COPY_BUFFER_BYTES: usize = 64 * 1024;
const CREATE_ATTEMPTS: u64 = 128;
static NEXT_STAGE: AtomicU64 = AtomicU64::new(0);

struct OwnedTemporaryFile {
    file: Option<File>,
    named: Option<NamedStage>,
}

struct NamedStage {
    path: PathBuf,
    identity: path_identity::FileIdentity,
}

impl OwnedTemporaryFile {
    fn create() -> std::io::Result<Self> {
        let directory = env::temp_dir();
        if let Some(file) = handle_owned_stage::create(&directory)? {
            return Ok(Self {
                file: Some(file),
                named: None,
            });
        }
        let mut staged = Self::create_named_in(&directory)?;
        match staged.unlink_named_while_open() {
            Ok(()) => Ok(staged),
            Err(error) if named_stage_must_remain(&error) => Ok(staged),
            Err(error) => Err(error),
        }
    }

    fn create_named_in(directory: &Path) -> std::io::Result<Self> {
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
                    let identity = path_identity::from_open_file(&file)?;
                    return Ok(Self {
                        file: Some(file),
                        named: Some(NamedStage { path, identity }),
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

    fn close(mut self) -> std::io::Result<()> {
        self.cleanup_named_path()
    }

    fn verify_named_path(&self) -> std::io::Result<()> {
        let Some(named) = self.named.as_ref() else {
            return Ok(());
        };
        let actual = path_identity::from_path(&named.path)?;
        if actual == named.identity {
            Ok(())
        } else {
            Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "snapshot input stage path no longer identifies the owned file",
            ))
        }
    }

    fn unlink_named_while_open(&mut self) -> std::io::Result<()> {
        self.verify_named_path()?;
        let Some(named) = self.named.as_ref() else {
            return Ok(());
        };
        fs::remove_file(&named.path)?;
        self.named = None;
        Ok(())
    }

    fn cleanup_named_path(&mut self) -> std::io::Result<()> {
        if self.named.is_none() {
            return Ok(());
        }
        self.verify_named_path()?;
        drop(self.file.take());
        self.verify_named_path()?;
        let named = self.named.as_ref().ok_or_else(|| {
            std::io::Error::other("snapshot input named stage unexpectedly disappeared")
        })?;
        fs::remove_file(&named.path)?;
        self.named = None;
        Ok(())
    }
}

#[cfg(windows)]
fn named_stage_must_remain(error: &std::io::Error) -> bool {
    error.kind() == std::io::ErrorKind::PermissionDenied
}

#[cfg(not(windows))]
const fn named_stage_must_remain(_error: &std::io::Error) -> bool {
    false
}

impl Drop for OwnedTemporaryFile {
    fn drop(&mut self) {
        if self.named.is_some() {
            let _ = self.cleanup_named_path();
        }
        drop(self.file.take());
    }
}

#[cfg(target_os = "linux")]
mod handle_owned_stage {
    use std::fs::File;
    use std::io;
    use std::path::Path;

    use rustix::fs::{CWD, Mode, OFlags, openat};
    use rustix::io::Errno;

    pub(super) fn create(directory: &Path) -> io::Result<Option<File>> {
        let flags = OFlags::RDWR | OFlags::CLOEXEC | OFlags::TMPFILE;
        let mode = Mode::RUSR | Mode::WUSR;
        match openat(CWD, directory, flags, mode) {
            Ok(file) => Ok(Some(File::from(file))),
            Err(Errno::INVAL | Errno::ISDIR | Errno::NOENT | Errno::NOTSUP) => Ok(None),
            Err(error) => Err(error.into()),
        }
    }
}

#[cfg(windows)]
mod handle_owned_stage {
    use std::fs::{File, OpenOptions};
    use std::io;
    use std::os::windows::fs::OpenOptionsExt;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::Ordering;
    use std::time::{SystemTime, UNIX_EPOCH};

    use super::{CREATE_ATTEMPTS, NEXT_STAGE};

    const FILE_FLAG_DELETE_ON_CLOSE: u32 = 0x0400_0000;

    pub(super) fn create(directory: &Path) -> io::Result<Option<File>> {
        let (file, _path) = create_with_path(directory)?;
        Ok(Some(file))
    }

    fn create_with_path(directory: &Path) -> io::Result<(File, PathBuf)> {
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
            options
                .read(true)
                .write(true)
                .create_new(true)
                .share_mode(0)
                .custom_flags(FILE_FLAG_DELETE_ON_CLOSE);
            match options.open(&path) {
                Ok(file) => return Ok((file, path)),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        }
        Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "could not create a unique delete-on-close snapshot input stage",
        ))
    }
}

#[cfg(not(any(target_os = "linux", windows)))]
mod handle_owned_stage {
    use std::fs::File;
    use std::io;
    use std::path::Path;

    pub(super) const fn create(_directory: &Path) -> io::Result<Option<File>> {
        Ok(None)
    }
}

#[cfg(unix)]
mod path_identity {
    use std::fs::{self, File};
    use std::io;
    use std::os::unix::fs::MetadataExt;
    use std::path::Path;

    #[derive(Clone, Copy, PartialEq, Eq)]
    pub(super) struct FileIdentity {
        device: u64,
        inode: u64,
    }

    pub(super) fn from_open_file(file: &File) -> io::Result<FileIdentity> {
        Ok(from_metadata(&file.metadata()?))
    }

    pub(super) fn from_path(path: &Path) -> io::Result<FileIdentity> {
        Ok(from_metadata(&fs::symlink_metadata(path)?))
    }

    fn from_metadata(metadata: &fs::Metadata) -> FileIdentity {
        FileIdentity {
            device: metadata.dev(),
            inode: metadata.ino(),
        }
    }
}

#[cfg(not(unix))]
mod path_identity {
    use std::fs::File;
    use std::io;
    use std::path::Path;

    #[derive(Clone, Copy, PartialEq, Eq)]
    pub(super) struct FileIdentity;

    pub(super) fn from_open_file(_file: &File) -> io::Result<FileIdentity> {
        Err(unsupported())
    }

    pub(super) fn from_path(_path: &Path) -> io::Result<FileIdentity> {
        Err(unsupported())
    }

    fn unsupported() -> io::Error {
        io::Error::new(
            io::ErrorKind::Unsupported,
            "file identity is unavailable for the snapshot input stage",
        )
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

    pub(crate) fn close(self) -> Result<(), &'static str> {
        self.file
            .close()
            .map_err(|_| "input_stage_cleanup_uncertain")
    }
}

#[cfg(test)]
mod tests {
    use std::fs::{self, File};
    use std::io::{Read, Seek, SeekFrom, Write};

    #[cfg(unix)]
    use super::OwnedTemporaryFile;
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

    #[cfg(unix)]
    #[test]
    fn named_stage_cleanup_removes_only_the_owned_path() -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let staged = OwnedTemporaryFile::create_named_in(directory.path())?;
        let path = staged
            .named
            .as_ref()
            .ok_or("missing named stage")?
            .path
            .clone();
        assert!(path.exists());

        staged.close()?;

        assert!(!path.exists());
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn named_stage_replacement_survives_and_cleanup_reports_uncertainty()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let mut staged = OwnedTemporaryFile::create_named_in(directory.path())?;
        staged.file_mut()?.write_all(b"owned stage")?;
        let path = staged
            .named
            .as_ref()
            .ok_or("missing named stage")?
            .path
            .clone();
        let displaced = directory.path().join("displaced-stage");
        fs::rename(&path, &displaced)?;
        fs::write(&path, b"competitor")?;

        let error = match staged.close() {
            Ok(()) => return Err("replacement cleanup unexpectedly succeeded".into()),
            Err(error) => error,
        };

        assert_eq!(error.kind(), std::io::ErrorKind::InvalidData);
        assert_eq!(fs::read(&path)?, b"competitor");
        assert_eq!(fs::read(&displaced)?, b"owned stage");
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn named_stage_drop_cleans_up_after_an_early_failure() -> Result<(), Box<dyn std::error::Error>>
    {
        let directory = tempfile::tempdir()?;
        let path = {
            let mut staged = OwnedTemporaryFile::create_named_in(directory.path())?;
            staged.file_mut()?.write_all(b"partial stage")?;
            staged
                .named
                .as_ref()
                .ok_or("missing named stage")?
                .path
                .clone()
        };

        assert!(!path.exists());
        Ok(())
    }
}
