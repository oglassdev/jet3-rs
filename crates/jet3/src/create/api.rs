//! Creation of a fresh Jet 3 database holding user tables.
//!
//! [`create_database`] composes the complete image in memory, writes every
//! page in physical order to a private file beside the destination, reopens
//! that file through the ordinary reader to check its structure against the
//! request, and only then publishes it. A destination that already exists is
//! left untouched, and a destination that was absent stays absent after any
//! pre-publication failure.
//!
//! The structural reopen is a publication prerequisite, not compatibility
//! evidence. DAO observations in `docs/PROVENANCE.md` cover exact candidates;
//! they do not establish arbitrary schemas, values, or general compatibility.
//! Local and hosted differential results govern the support matrix.
use crate::{
    RelationshipSpec, ResourceBudget, RowValue, TableSpec, WriteError,
    create::{
        check::{check_image, check_initial_tables, check_long_value_written_pages},
        composer::{ComposeError, compose_database, compose_database_with_table_rows},
        page_append_plan::PlannedPage,
    },
    write::atomic::atomic_create,
};

use std::fs::File;
use std::io::{self, Write};
use std::path::Path;

/// A table schema and its initial rows.
#[derive(Debug, Clone, Copy)]
pub struct TableRows<'a> {
    /// The schema to create.
    pub table: TableSpec<'a>,
    /// Initial values, one slice per row in schema column order.
    pub rows: &'a [&'a [RowValue<'a>]],
}

impl<'a> TableRows<'a> {
    /// A table created without rows.
    #[must_use]
    pub const fn empty(table: TableSpec<'a>) -> Self {
        Self { table, rows: &[] }
    }
}

/// Everything [`create_database`] writes into a new database.
#[derive(Debug, Clone, Copy, Default)]
pub struct DatabaseSpec<'a> {
    /// Tables and their initial rows, created in order. An empty slice
    /// creates an empty database.
    pub tables: &'a [TableRows<'a>],
    /// Enforced relationships between `tables`.
    pub relationships: &'a [RelationshipSpec<'a>],
    /// Page layout used for `relationships`.
    pub relationship_layout: RelationshipLayout,
}

/// How [`create_database`] lays out relationships.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum RelationshipLayout {
    /// EXP-0273/0279 endpoint records. Relationships have one to ten scalar
    /// fields each; table order is independent of relationship direction;
    /// chains, multiple endpoints and self-references are admitted; missing
    /// parent or child indexes are generated.
    #[default]
    Graph,
    /// The EXP-0118/0122 construction: exactly one enforced, non-cascading
    /// Long relationship from the first table to the second. Page zero keeps
    /// the EXP-0114 transition byte, so the image differs from [`Self::Graph`]
    /// for the same request.
    SingleLong,
}

/// Creates the database described by `spec` at `path`.
///
/// Tables, their rows, indexes, AutoNumber state, Memo/OLE payloads and
/// relationships are composed in order, written privately, reopened and
/// checked, then published atomically. Every non-null foreign key must exist
/// in its parent's initial rows. `budget` bounds every step.
///
/// # Errors
///
/// Returns [`WriteError::Compose`] before writing anything when the
/// request is outside the supported scope, and [`WriteError::CreatePublish`]
/// when writing, checking or publication fails; an existing `path` is left
/// unchanged and reported as `AlreadyExists`. See `docs/plans/V1_SCOPE.md` for
/// the supported scope.
pub fn create_database(
    path: impl AsRef<Path>,
    spec: &DatabaseSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    let requests = spec.tables;
    let has_rows = requests.iter().any(|request| !request.rows.is_empty());
    match (spec.relationship_layout, spec.relationships) {
        (RelationshipLayout::Graph, []) if has_rows => create_table_rows(path, requests, budget),
        (RelationshipLayout::Graph, []) => {
            crate::create::composer::table_count_limit(requests.len())
                .map_err(WriteError::Compose)?;
            create_tables(path, &schemas(requests, budget)?, budget)
        }
        (RelationshipLayout::Graph, relationships) => {
            super::api_relationship_graph::create(path, requests, relationships, budget)
        }
        (RelationshipLayout::SingleLong, [relationship]) if has_rows => {
            super::api_relationship::create_with_rows(path, requests, relationship, budget)
        }
        (RelationshipLayout::SingleLong, [relationship]) => {
            // The composer refuses every table count other than two.
            let pair;
            let tables: &[TableSpec<'_>] = match requests {
                [parent, child] => {
                    pair = [parent.table, child.table];
                    &pair
                }
                _ => &[],
            };
            super::api_relationship::create(path, tables, relationship, budget)
        }
        (RelationshipLayout::SingleLong, _) => {
            Err(WriteError::Compose(ComposeError::UnsupportedRelationship {
                detail: "the single Long layout requires exactly one relationship",
            }))
        }
    }
}

fn schemas<'a>(
    requests: &[TableRows<'a>],
    budget: &mut ResourceBudget,
) -> Result<Vec<TableSpec<'a>>, WriteError> {
    let mut tables = Vec::new();
    crate::format::resource::reserve(&mut tables, requests.len(), budget)
        .map_err(|error| WriteError::Compose(error.into()))?;
    tables.extend(requests.iter().map(|request| request.table));
    Ok(tables)
}

fn create_tables(
    path: impl AsRef<Path>,
    tables: &[TableSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    let pages = compose_database(tables, budget)
        .map_err(WriteError::Compose)?
        .into_pages();
    let page_count = pages.len() as u64;
    budget
        .charge_work_units(page_count.saturating_mul(crate::PAGE_BYTES as u64))
        .map_err(|error| WriteError::Compose(ComposeError::Encoding(error)))?;
    atomic_create(
        path,
        |file| write_pages(file, &pages),
        |candidate| {
            check_long_value_written_pages(candidate, tables, &pages, budget)?;
            check_image(candidate, tables, page_count, budget)
        },
    )
    .map_err(WriteError::CreatePublish)
}

/// Tables, their LVAL pages and their row pages are placed sequentially in
/// input order within the map-reference and resource limits.
fn create_table_rows(
    path: impl AsRef<Path>,
    requests: &[TableRows<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    let pages = compose_database_with_table_rows(requests, budget)
        .map_err(WriteError::Compose)?
        .into_pages();
    budget
        .charge_allocation(crate::ByteCount::new(
            (requests.len() * std::mem::size_of::<TableSpec<'_>>()) as u64,
        ))
        .map_err(|error| WriteError::Compose(error.into()))?;
    let mut tables = Vec::new();
    tables.try_reserve_exact(requests.len()).map_err(|_| {
        WriteError::Compose(ComposeError::Encoding(crate::Error::Io {
            operation: "reserve initial table schemas",
            kind: io::ErrorKind::OutOfMemory,
        }))
    })?;
    tables.extend(requests.iter().map(|request| request.table));
    let page_count = pages.len() as u64;
    budget
        .charge_work_units(page_count.saturating_mul(crate::PAGE_BYTES as u64))
        .map_err(|error| WriteError::Compose(ComposeError::Encoding(error)))?;
    atomic_create(
        path,
        |file| write_pages(file, &pages),
        |candidate| {
            check_long_value_written_pages(candidate, &tables, &pages, budget)?;
            check_image(candidate, &tables, page_count, budget)?;
            check_initial_tables(candidate, &tables, requests, budget)
        },
    )
    .map_err(WriteError::CreatePublish)
}

/// Writes every page in physical order and sets the exact final length.
pub(super) fn write_pages(file: &mut File, pages: &[PlannedPage]) -> Result<(), io::Error> {
    for (slot, page) in pages.iter().enumerate() {
        if page.number().get() != slot as u64 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "planned pages are not in physical order",
            ));
        }
        file.write_all(page.image().as_bytes())?;
    }
    file.set_len(pages.len() as u64 * crate::PAGE_BYTES as u64)?;
    file.flush()
}
