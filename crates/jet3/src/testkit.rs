//! Shared helpers for the parser's unit tests.

use std::fs;
use std::io;
use std::ops::Deref;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::{
    ColumnSpec, DatabaseReader, DatabaseSpec, IndexColumnSpec, IndexKind, IndexSpec, PAGE_BYTES,
    ResourceBudget, ResourceLimits, TableRows, TableSpec, TableValidation, TextCodePage,
    ValidationReport, WriteError, create_database,
};

pub(crate) type TestResult<T = ()> = Result<T, Box<dyn std::error::Error>>;

/// A budget with the default resource limits.
pub(crate) fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

/// A table without table-level validation.
pub(crate) const fn table<'a>(
    name: &'a [u8],
    columns: &'a [ColumnSpec<'a>],
    indexes: &'a [IndexSpec<'a>],
) -> TableSpec<'a> {
    TableSpec {
        name,
        columns,
        indexes,
        validation: TableValidation::NONE,
    }
}

pub(crate) const fn index<'a>(
    name: &'a [u8],
    fields: &'a [IndexColumnSpec<'a>],
    kind: IndexKind,
) -> IndexSpec<'a> {
    IndexSpec { name, fields, kind }
}

/// Creates `tables` without relationships under a default budget.
pub(crate) fn create(path: impl AsRef<Path>, tables: &[TableRows<'_>]) -> Result<(), WriteError> {
    create_spec(
        path,
        &DatabaseSpec {
            tables,
            ..DatabaseSpec::default()
        },
    )
}

/// Runs [`create_database`] under a default budget.
pub(crate) fn create_spec(
    path: impl AsRef<Path>,
    spec: &DatabaseSpec<'_>,
) -> Result<(), WriteError> {
    create_database(path, spec, &mut budget())
}

/// Opens `path` and runs the full structural validation.
pub(crate) fn validate_file(path: impl AsRef<Path>) -> TestResult<ValidationReport> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    Ok(db.validate(TextCodePage::Windows1252, &mut work)?)
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

    /// The conventional database path inside this directory.
    pub(crate) fn target(&self) -> PathBuf {
        self.0.join("created.mdb")
    }

    pub(crate) fn is_empty(&self) -> io::Result<bool> {
        Ok(fs::read_dir(&self.0)?.next().is_none())
    }

    /// Sorted file names in this directory.
    pub(crate) fn entries(&self) -> io::Result<Vec<String>> {
        let mut names = fs::read_dir(&self.0)?
            .map(|entry| Ok(entry?.file_name().to_string_lossy().into_owned()))
            .collect::<io::Result<Vec<_>>>()?;
        names.sort();
        Ok(names)
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
