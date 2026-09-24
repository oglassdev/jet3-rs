//! Windows publication uses standard-library rename/hard-link operations.
//! Handle identity uses GetFileInformationByHandle through a safe Rust wrapper.

pub(super) mod path_identity {
    use std::fs::{self, File};
    use std::io;
    use std::path::Path;

    #[derive(Clone, Copy, PartialEq, Eq)]
    pub(crate) struct FileIdentity {
        volume: u64,
        index: u64,
    }

    pub(crate) fn from_open_file(file: &File) -> io::Result<FileIdentity> {
        let information = winapi_util::file::information(file)?;
        Ok(FileIdentity {
            volume: information.volume_serial_number(),
            index: information.file_index(),
        })
    }

    pub(crate) fn from_path(path: &Path) -> io::Result<FileIdentity> {
        if !fs::symlink_metadata(path)?.file_type().is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "private publication path is no longer a regular file",
            ));
        }
        from_open_file(&File::open(path)?)
    }
}

pub(super) mod platform_publish {
    use std::fs;
    use std::io;
    use std::path::Path;

    pub(crate) const fn ensure_supported() -> io::Result<()> {
        Ok(())
    }

    pub(crate) fn replace(private: &Path, target: &Path) -> io::Result<()> {
        fs::rename(private, target)
    }

    pub(crate) fn link_new(private: &Path, target: &Path) -> io::Result<()> {
        fs::hard_link(private, target)
    }

    // Windows has no directory-flush equivalent provided by this implementation.
    pub(crate) const fn sync_directory(_parent: &Path) -> io::Result<()> {
        Ok(())
    }
}
