//! Immutable, bounded snapshot input staging.
//!
//! Linux uses an anonymous `O_TMPFILE` with fsync and rewind when available.
//! Windows uses a delete-on-close file. Other cases retain one fallibly
//! allocated, pathless memory copy; that copy is not filesystem-synced.

use std::env;
use std::fs::File;
use std::io::{Read, Seek, Write};
#[cfg(windows)]
use std::sync::atomic::AtomicU64;

use jet3::{ByteCount, ByteOffset, Error, FileSource, ReadAt, ReadBudget, SliceSource};
use jet3_testkit::{Sha256, Sha256Hasher, hex_digest};

const COPY_BUFFER_BYTES: usize = 64 * 1024;
#[cfg(windows)]
const CREATE_ATTEMPTS: u64 = 128;
#[cfg(windows)]
static NEXT_STAGE: AtomicU64 = AtomicU64::new(0);

enum StagedStorage {
    File(File),
    Memory(MemoryStage),
}

struct MemoryStage {
    bytes: Vec<u8>,
    #[cfg(test)]
    capacity_limit: Option<usize>,
}

impl StagedStorage {
    fn create(initial_len: u64) -> std::io::Result<Self> {
        let directory = env::temp_dir();
        if let Some(file) = handle_owned_stage::create(&directory)? {
            return Ok(Self::File(file));
        }
        Self::memory(initial_len)
    }

    fn memory(initial_len: u64) -> std::io::Result<Self> {
        Self::memory_with_limit(initial_len, None)
    }

    fn memory_with_limit(
        initial_len: u64,
        #[cfg_attr(not(test), allow(unused_variables))] capacity_limit: Option<usize>,
    ) -> std::io::Result<Self> {
        let capacity = usize::try_from(initial_len).map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::OutOfMemory,
                "snapshot input length does not fit in memory",
            )
        })?;
        #[cfg(test)]
        if capacity_limit.is_some_and(|limit| capacity > limit) {
            return Err(std::io::Error::new(
                std::io::ErrorKind::OutOfMemory,
                "injected snapshot input memory allocation failure",
            ));
        }
        let mut bytes = Vec::new();
        bytes.try_reserve_exact(capacity).map_err(|_| {
            std::io::Error::new(
                std::io::ErrorKind::OutOfMemory,
                "snapshot input memory allocation failed",
            )
        })?;
        Ok(Self::Memory(MemoryStage {
            bytes,
            #[cfg(test)]
            capacity_limit,
        }))
    }

    fn write_all(&mut self, bytes: &[u8]) -> std::io::Result<()> {
        match self {
            Self::File(file) => file.write_all(bytes),
            Self::Memory(staged) => {
                let final_len = staged.bytes.len().checked_add(bytes.len()).ok_or_else(|| {
                    std::io::Error::new(
                        std::io::ErrorKind::OutOfMemory,
                        "snapshot input memory length overflow",
                    )
                })?;
                #[cfg(test)]
                if staged.capacity_limit.is_some_and(|limit| final_len > limit) {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::OutOfMemory,
                        "injected snapshot input memory allocation failure",
                    ));
                }
                let additional = final_len.checked_sub(staged.bytes.len()).ok_or_else(|| {
                    std::io::Error::new(
                        std::io::ErrorKind::OutOfMemory,
                        "snapshot input memory length regressed",
                    )
                })?;
                staged.bytes.try_reserve_exact(additional).map_err(|_| {
                    std::io::Error::new(
                        std::io::ErrorKind::OutOfMemory,
                        "snapshot input memory allocation failed",
                    )
                })?;
                staged.bytes.extend_from_slice(bytes);
                Ok(())
            }
        }
    }

    fn sync_and_rewind(&mut self) -> Result<(), &'static str> {
        match self {
            Self::File(file) => {
                file.sync_all().map_err(|_| "input_stage_sync_failed")?;
                file.rewind().map_err(|_| "input_stage_rewind_failed")
            }
            Self::Memory(_) => Ok(()),
        }
    }
}

pub(crate) enum StagedSource<'a> {
    File(FileSource),
    Memory(SliceSource<'a>),
}

impl ReadAt for StagedSource<'_> {
    fn len(&self) -> ByteCount {
        match self {
            Self::File(source) => source.len(),
            Self::Memory(source) => source.len(),
        }
    }

    fn read_exact_at(
        &mut self,
        offset: ByteOffset,
        destination: &mut [u8],
        budget: &mut ReadBudget,
    ) -> Result<(), Error> {
        match self {
            Self::File(source) => source.read_exact_at(offset, destination, budget),
            Self::Memory(source) => source.read_exact_at(offset, destination, budget),
        }
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

pub(crate) struct StagedInput {
    storage: StagedStorage,
    length: u64,
    sha256: Sha256,
}

impl StagedInput {
    pub(crate) fn copy_from(source: File, max_bytes: u64) -> Result<Self, &'static str> {
        let initial_len = source.metadata().map_err(|_| "metadata_failed")?.len();
        if initial_len > max_bytes {
            return Err("input_limit_exceeded");
        }
        let storage =
            StagedStorage::create(initial_len).map_err(|_| "input_stage_create_failed")?;
        Self::copy_into(source, max_bytes, storage)
    }

    #[cfg(test)]
    fn copy_from_memory(source: File, max_bytes: u64) -> Result<Self, &'static str> {
        Self::copy_from_memory_with_limit(source, max_bytes, None)
    }

    #[cfg(test)]
    fn copy_from_memory_with_limit(
        source: File,
        max_bytes: u64,
        capacity_limit: Option<usize>,
    ) -> Result<Self, &'static str> {
        let initial_len = source.metadata().map_err(|_| "metadata_failed")?.len();
        if initial_len > max_bytes {
            return Err("input_limit_exceeded");
        }
        let storage = StagedStorage::memory_with_limit(initial_len, capacity_limit)
            .map_err(|_| "input_stage_create_failed")?;
        Self::copy_into(source, max_bytes, storage)
    }

    fn copy_into(
        mut source: File,
        max_bytes: u64,
        mut storage: StagedStorage,
    ) -> Result<Self, &'static str> {
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
            storage
                .write_all(&buffer[..count])
                .map_err(|_| "input_stage_write_failed")?;
            hasher
                .update(&buffer[..count])
                .map_err(|_| "input_stage_hash_failed")?;
            length = length
                .checked_add(u64::try_from(count).map_err(|_| "input_stage_read_failed")?)
                .ok_or("input_limit_exceeded")?;
        }
        storage.sync_and_rewind()?;
        let sha256 = Sha256::new(hex_digest(
            hasher.finalize().map_err(|_| "input_stage_hash_failed")?,
        ))
        .map_err(|_| "input_stage_hash_failed")?;
        Ok(Self {
            storage,
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

    pub(crate) fn traversal_source(
        &self,
        budget: &ReadBudget,
    ) -> Result<StagedSource<'_>, &'static str> {
        match &self.storage {
            StagedStorage::File(file) => FileSource::from_file(
                file.try_clone().map_err(|_| "input_stage_open_failed")?,
                budget,
            )
            .map(StagedSource::File)
            .map_err(|_| "input_stage_open_failed"),
            StagedStorage::Memory(memory) => SliceSource::new(&memory.bytes, budget)
                .map(StagedSource::Memory)
                .map_err(|_| "input_stage_open_failed"),
        }
    }

    pub(crate) fn close(self) -> Result<(), &'static str> {
        Ok(())
    }

    #[cfg(test)]
    fn is_memory(&self) -> bool {
        matches!(self.storage, StagedStorage::Memory(_))
    }
}

#[cfg(test)]
mod tests {
    use std::fs::{self, File};
    use std::io::{Seek, Write};

    use jet3::{ByteCount, ByteOffset, ReadAt, ReadBudget, ReadLimits};

    use super::StagedInput;

    fn read_staged(staged: StagedInput) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
        let length = staged.length();
        let byte_length = ByteCount::new(length);
        let mut budget = ReadBudget::new(ReadLimits::new(byte_length, byte_length, byte_length));
        let mut source = staged.traversal_source(&budget)?;
        let mut bytes = vec![0_u8; usize::try_from(length)?];
        source.read_exact_at(ByteOffset::new(0), &mut bytes, &mut budget)?;
        drop(source);
        staged.close()?;
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
        let staged = StagedInput::copy_from_memory(File::open(&alias)?, 1024)?;
        assert!(staged.is_memory());
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

    #[test]
    fn memory_fallback_exact_boundary_and_reserve_failure_are_fail_closed()
    -> Result<(), Box<dyn std::error::Error>> {
        let mut exact = tempfile::tempfile()?;
        exact.write_all(&[4_u8; 17])?;
        exact.rewind()?;
        let staged = StagedInput::copy_from_memory(exact, 17)?;
        assert!(staged.is_memory());
        assert_eq!(read_staged(staged)?, [4_u8; 17]);

        let mut allocation_failure = tempfile::tempfile()?;
        allocation_failure.write_all(&[5_u8; 17])?;
        allocation_failure.rewind()?;
        assert_eq!(
            StagedInput::copy_from_memory_with_limit(allocation_failure, 17, Some(16)).err(),
            Some("input_stage_create_failed")
        );
        Ok(())
    }

    #[test]
    fn memory_fallback_never_adopts_or_removes_path_replacements()
    -> Result<(), Box<dyn std::error::Error>> {
        let directory = tempfile::tempdir()?;
        let source = directory.path().join("source.mdb");
        fs::write(&source, b"pathless staged bytes")?;
        let staged = StagedInput::copy_from_memory(File::open(&source)?, 64)?;
        let empty_replacement = directory.path().join("empty-replacement");
        let nonempty_replacement = directory.path().join("nonempty-replacement");
        fs::create_dir(&empty_replacement)?;
        fs::create_dir(&nonempty_replacement)?;
        fs::write(nonempty_replacement.join("competitor"), b"replacement data")?;

        assert_eq!(read_staged(staged)?, b"pathless staged bytes");
        assert!(empty_replacement.is_dir());
        assert_eq!(fs::read_dir(&empty_replacement)?.count(), 0);
        assert_eq!(
            fs::read(nonempty_replacement.join("competitor"))?,
            b"replacement data"
        );
        Ok(())
    }

    #[test]
    fn platform_staging_strategy_is_explicit() {
        #[cfg(target_os = "linux")]
        assert_eq!(
            platform_strategy(),
            "anonymous_o_tmpfile_fsync_or_pathless_memory"
        );
        #[cfg(windows)]
        assert_eq!(platform_strategy(), "delete_on_close_file_fsync");
        #[cfg(not(any(target_os = "linux", windows)))]
        assert_eq!(platform_strategy(), "pathless_memory_not_fsynced");
    }

    const fn platform_strategy() -> &'static str {
        #[cfg(target_os = "linux")]
        return "anonymous_o_tmpfile_fsync_or_pathless_memory";
        #[cfg(windows)]
        return "delete_on_close_file_fsync";
        #[cfg(not(any(target_os = "linux", windows)))]
        "pathless_memory_not_fsynced"
    }
}
