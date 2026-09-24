//! Shared helpers for the parser's unit tests.

use std::fs;
use std::io;
use std::ops::Deref;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::{PAGE_BYTES, ResourceBudget, ResourceLimits};

/// A budget with the default resource limits.
pub(crate) fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

/// A fresh directory under the system temporary directory, removed on drop.
#[derive(Debug)]
pub(crate) struct TempDir(PathBuf);

impl TempDir {
    pub(crate) fn new(label: &str) -> io::Result<Self> {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "jet3-{label}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path)?;
        Ok(Self(path))
    }
}

impl Deref for TempDir {
    type Target = Path;

    fn deref(&self) -> &Path {
        &self.0
    }
}

impl AsRef<Path> for TempDir {
    fn as_ref(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

/// Writes the page-zero bytes every test image shares after the signature.
pub(crate) fn write_jet3_header_fields(page: &mut [u8]) {
    page[0x41] = 0x4e;
    page[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
}

/// A zeroed image of `page_count` pages with a Jet 3 page zero.
pub(crate) fn database_image(page_count: usize) -> Vec<u8> {
    let mut bytes = vec![0_u8; page_count * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    write_jet3_header_fields(&mut bytes);
    bytes
}
