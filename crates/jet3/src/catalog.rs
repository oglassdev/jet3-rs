//! Bounded, streaming Jet 3 catalog discovery and reading.
//!
//! `EXP-0058` supplies the self-identifying catalog-root rule and minimum
//! object record fields. Owned-page traversal remains delegated to
//! `EXP-0057`; this layer does not traverse indexes or table rows.

use std::fmt;
use std::mem::size_of;

use crate::catalog_record::{CatalogPageDirectory, CatalogRecordView, decode_catalog_record};
use crate::{
    AllocationTraversalError, ByteCount, CatalogObjectClass, CatalogObjectId, CatalogObjectKind,
    CatalogRecord, CatalogRecordError, DatabasePageError, DatabaseReader, Error, JET3_PAGE_SIZE,
    OwnedPages, PageKind, PageNumber, ReadAt, ResourceBudget, VisitedPages,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const CATALOG_SELF_NAME: &[u8] = b"MSysObjects";

/// A structured failure while discovering or streaming the catalog.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum CatalogError {
    /// Reading or classifying a root-candidate page failed.
    Page(DatabasePageError),
    /// Traversing a candidate or selected root's owned pages failed.
    Allocation(AllocationTraversalError),
    /// A catalog data-page directory or object record is malformed.
    Record(CatalogRecordError),
    /// An owned page is not a data page.
    UnexpectedOwnedPageKind { page: PageNumber, actual: PageKind },
    /// No self-identifying catalog root was found.
    RootNotFound,
    /// More than one self-identifying catalog root was found.
    DuplicateRoot {
        first: PageNumber,
        duplicate: PageNumber,
    },
    /// An active catalog identifier occurred more than once.
    DuplicateObjectId { id: CatalogObjectId },
    /// A table identifier is outside the captured page range.
    InvalidTableDefinitionReference {
        id: CatalogObjectId,
        page: PageNumber,
        source: Error,
    },
    /// A table identifier names a page not classified as a table definition.
    UnexpectedTableDefinitionReference {
        id: CatalogObjectId,
        page: PageNumber,
    },
    /// Resource policy rejected discovery or cursor state.
    Resource(Error),
}

impl fmt::Display for CatalogError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Page(source) => write!(formatter, "catalog page access failed: {source}"),
            Self::Allocation(source) => write!(formatter, "catalog allocation failed: {source}"),
            Self::Record(source) => write!(formatter, "catalog record failed: {source}"),
            Self::UnexpectedOwnedPageKind { page, actual } => write!(
                formatter,
                "catalog-owned page {} must be data, found {actual:?}",
                page.get()
            ),
            Self::RootNotFound => formatter.write_str("no self-identifying catalog root found"),
            Self::DuplicateRoot { first, duplicate } => write!(
                formatter,
                "catalog roots {} and {} both self-identify",
                first.get(),
                duplicate.get()
            ),
            Self::DuplicateObjectId { id } => {
                write!(
                    formatter,
                    "catalog object identifier {} is duplicated",
                    id.get()
                )
            }
            Self::InvalidTableDefinitionReference { id, page, source } => write!(
                formatter,
                "catalog table identifier {} names invalid page {}: {source}",
                id.get(),
                page.get()
            ),
            Self::UnexpectedTableDefinitionReference { id, page } => write!(
                formatter,
                "catalog table identifier {} names non-TDEF page {}",
                id.get(),
                page.get()
            ),
            Self::Resource(source) => write!(formatter, "catalog rejected: {source}"),
        }
    }
}

impl std::error::Error for CatalogError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Page(source) => Some(source),
            Self::Allocation(source) => Some(source),
            Self::Record(source) => Some(source),
            Self::InvalidTableDefinitionReference { source, .. } | Self::Resource(source) => {
                Some(source)
            }
            _ => None,
        }
    }
}

/// Fallible, forward-only stream of immutable catalog object records.
#[derive(Debug)]
pub struct CatalogCursor<'operation, S> {
    root: PageNumber,
    owned: OwnedPages<'operation, S>,
    table_definitions: VisitedPages,
    page: [u8; PAGE_BYTES],
    directory: Option<CatalogPageDirectory>,
    identifiers: Vec<CatalogObjectId>,
    failed: bool,
}

impl<'operation, S: ReadAt> CatalogCursor<'operation, S> {
    pub(crate) fn new(
        database: &'operation mut DatabaseReader<S>,
        budget: &'operation mut ResourceBudget,
    ) -> Result<Self, CatalogError> {
        let (root, table_definitions) = discover_catalog(database, budget)?;
        let owned = database
            .owned_pages(root, budget)
            .map_err(CatalogError::Allocation)?;
        Ok(Self {
            root,
            owned,
            table_definitions,
            page: [0_u8; PAGE_BYTES],
            directory: None,
            identifiers: Vec::new(),
            failed: false,
        })
    }

    /// Returns the dynamically discovered catalog table-definition root.
    #[must_use]
    pub const fn root(&self) -> PageNumber {
        self.root
    }

    /// Returns the next active catalog record.
    ///
    /// Any error exhausts the cursor. Repeated calls after exhaustion perform
    /// no reads, allocation, or work charges.
    pub fn next_record(&mut self) -> Result<Option<CatalogRecord>, CatalogError> {
        if self.failed {
            return Ok(None);
        }
        let result = self.next_record_inner();
        if result.is_err() {
            self.failed = true;
        }
        result
    }

    fn next_record_inner(&mut self) -> Result<Option<CatalogRecord>, CatalogError> {
        loop {
            if let Some(directory) = &mut self.directory {
                if let Some(row) = directory
                    .next_active(&self.page)
                    .map_err(CatalogError::Record)?
                {
                    let view = decode_catalog_record(row, self.owned.budget_mut())
                        .map_err(CatalogError::Record)?;
                    return finish_record(
                        view,
                        &mut self.identifiers,
                        &self.table_definitions,
                        &mut self.owned,
                    )
                    .map(Some);
                }
                self.directory = None;
            }

            let Some((page, kind)) = self
                .owned
                .next_classified_page_into(&mut self.page)
                .map_err(CatalogError::Allocation)?
            else {
                return Ok(None);
            };
            if kind != PageKind::Data {
                return Err(CatalogError::UnexpectedOwnedPageKind { page, actual: kind });
            }
            self.directory = Some(
                CatalogPageDirectory::validate(&self.page, self.owned.budget_mut())
                    .map_err(CatalogError::Record)?,
            );
        }
    }
}

fn finish_record<S: ReadAt>(
    view: CatalogRecordView<'_>,
    identifiers: &mut Vec<CatalogObjectId>,
    table_definitions: &VisitedPages,
    owned: &mut OwnedPages<'_, S>,
) -> Result<CatalogRecord, CatalogError> {
    let id = view.id();
    let comparisons = u64::try_from(identifiers.len()).map_err(|_| {
        CatalogError::Resource(Error::IntegerConversion {
            value: identifiers.len() as u128,
            target: "u64",
        })
    })?;
    owned
        .budget_mut()
        .charge_work_units(comparisons)
        .map_err(CatalogError::Resource)?;
    if identifiers.contains(&id) {
        return Err(CatalogError::DuplicateObjectId { id });
    }

    let table_definition = if view.kind() == CatalogObjectKind::Table {
        let page = PageNumber::new(u64::from(id.get()));
        owned
            .geometry()
            .validate_reference(page)
            .map_err(|source| CatalogError::InvalidTableDefinitionReference { id, page, source })?;
        if !table_definitions.contains(page) {
            return Err(CatalogError::UnexpectedTableDefinitionReference { id, page });
        }
        Some(page)
    } else {
        None
    };

    owned
        .budget_mut()
        .charge_allocation(ByteCount::new(size_of::<CatalogObjectId>() as u64))
        .map_err(CatalogError::Resource)?;
    identifiers.try_reserve_exact(1).map_err(|_| {
        CatalogError::Resource(Error::Io {
            operation: "reserve catalog identifiers",
            kind: std::io::ErrorKind::OutOfMemory,
        })
    })?;
    let record = view
        .into_owned(table_definition, owned.budget_mut())
        .map_err(CatalogError::Resource)?;
    identifiers.push(id);
    Ok(record)
}

fn discover_catalog<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    budget: &mut ResourceBudget,
) -> Result<(PageNumber, VisitedPages), CatalogError> {
    let geometry = database.geometry();
    budget
        .charge_items(geometry.page_count())
        .map_err(CatalogError::Resource)?;
    let mut table_definitions =
        VisitedPages::new(geometry, budget).map_err(CatalogError::Resource)?;
    let mut root = None;
    let mut page_bytes = [0_u8; PAGE_BYTES];
    for raw_page in 1..geometry.page_count() {
        let page = PageNumber::new(raw_page);
        let classified = database
            .read_classified_page(page, &mut page_bytes, budget)
            .map_err(CatalogError::Page)?;
        if classified.kind() != PageKind::TableDefinition {
            continue;
        }
        table_definitions
            .insert(page)
            .map_err(CatalogError::Resource)?;
        if candidate_self_identifies(database, page, budget)? {
            if let Some(first) = root {
                return Err(CatalogError::DuplicateRoot {
                    first,
                    duplicate: page,
                });
            }
            root = Some(page);
        }
    }
    root.map(|root| (root, table_definitions))
        .ok_or(CatalogError::RootNotFound)
}

fn candidate_self_identifies<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    candidate: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<bool, CatalogError> {
    let mut owned = database
        .owned_pages(candidate, budget)
        .map_err(CatalogError::Allocation)?;
    let mut page_bytes = [0_u8; PAGE_BYTES];
    while let Some((page, kind)) = owned
        .next_classified_page_into(&mut page_bytes)
        .map_err(CatalogError::Allocation)?
    {
        if kind != PageKind::Data {
            return Err(CatalogError::UnexpectedOwnedPageKind { page, actual: kind });
        }
        let mut directory = CatalogPageDirectory::validate(&page_bytes, owned.budget_mut())
            .map_err(CatalogError::Record)?;
        while let Some(row) = directory
            .next_active(&page_bytes)
            .map_err(CatalogError::Record)?
        {
            let Ok(record) = decode_catalog_record(row, owned.budget_mut()) else {
                continue;
            };
            if record.kind() == CatalogObjectKind::Table
                && record.class() == CatalogObjectClass::System
                && u64::from(record.id().get()) == candidate.get()
                && record.name_bytes() == CATALOG_SELF_NAME
            {
                return Ok(true);
            }
        }
    }
    Ok(false)
}

#[cfg(test)]
#[path = "catalog_tests.rs"]
mod tests;
