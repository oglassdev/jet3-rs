//! EXP-0057/0077/0234/0254 allocation roles and EXP-0060/0061 live storage.
use std::fmt;

use crate::mutation_map::MapBits;
use crate::resource::reserve;
use crate::{
    AllocationTraversalError, ColumnOrdinal, DatabaseReader, Error, MapRowLocator, PAGE_BYTES,
    PageNumber, ReadAt, ResourceBudget, RowDirectoryError, RowError, RowLocator, TableDefinition,
    TableDefinitionError, UpdateError, VisitedPages,
};

/// An allocation or physical-storage consistency failure during read-only validation.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum StorageValidationError {
    /// A map could not be decoded by the shared allocation reader.
    Map {
        /// Physical map row being read.
        locator: MapRowLocator,
        /// Shared storage-reader error; no update was attempted.
        source: AllocationTraversalError,
    },
    /// A catalogued definition could not be read for its allocation inventory.
    Definition {
        /// Catalogued root page.
        root: PageNumber,
        /// Definition-reader error.
        source: TableDefinitionError,
    },
    /// A page has inconsistent ownership or allocation state.
    Page {
        /// Conflicting page.
        page: PageNumber,
        /// Consistency rule that failed.
        detail: &'static str,
    },
    /// A shared reader rejected storage reached through an allocation map.
    MapInvariant {
        /// Map locating the affected storage; the exact corrupt row may be unknown.
        locator: MapRowLocator,
        /// Consistency rule that failed.
        detail: &'static str,
    },
    /// A Memo/OLE fragment is missing, shared, or owned by the wrong column.
    Fragment {
        /// Referenced physical fragment.
        locator: RowLocator,
        /// Column whose payload is being checked.
        column: ColumnOrdinal,
        /// Consistency rule that failed.
        detail: &'static str,
    },
    /// Reading or classifying a payload page failed.
    Read(crate::DatabasePageError),
    /// The shared physical row/directory validator rejected storage.
    Rows(RowError),
    /// A physical row directory is inconsistent.
    Directory(RowDirectoryError),
    /// The caller's resource budget rejected the operation.
    Resource(Error),
}

impl fmt::Display for StorageValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "storage validation failed: {self:?}")
    }
}

impl std::error::Error for StorageValidationError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Map { source, .. } => Some(source),
            Self::Read(source) => Some(source),
            Self::Rows(source) => Some(source),
            Self::Directory(source) => Some(source),
            Self::Definition { source, .. } => Some(source),
            Self::Resource(source) => Some(source),
            Self::Page { .. } | Self::MapInvariant { .. } | Self::Fragment { .. } => None,
        }
    }
}

fn conflict(page: PageNumber, detail: &'static str) -> StorageValidationError {
    StorageValidationError::Page { page, detail }
}

pub(super) fn shared(source: UpdateError, locator: MapRowLocator) -> StorageValidationError {
    match source {
        UpdateError::Resource(source) => StorageValidationError::Resource(source),
        UpdateError::Definition(TableDefinitionError::Page(source)) => {
            StorageValidationError::Read(source)
        }
        UpdateError::Definition(source) => StorageValidationError::Definition {
            root: locator.page(),
            source,
        },
        UpdateError::Rows(source) => StorageValidationError::Rows(source),
        UpdateError::Directory(source) => StorageValidationError::Directory(source),
        UpdateError::UsageMap(source) => StorageValidationError::Map {
            locator,
            source: AllocationTraversalError::UsageMap(source),
        },
        UpdateError::Allocation(source) => StorageValidationError::Map {
            locator,
            source: AllocationTraversalError::AllocationMap(source),
        },
        UpdateError::NotFound(detail)
        | UpdateError::Mismatch(detail)
        | UpdateError::Unsupported(detail) => {
            StorageValidationError::MapInvariant { locator, detail }
        }
        // The shared map and row-graph readers do not perform file publication,
        // value encoding, catalog discovery, or relationship mutation.
        _ => StorageValidationError::MapInvariant {
            locator,
            detail: "unexpected shared storage reader failure",
        },
    }
}

fn map<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    locator: MapRowLocator,
    budget: &mut ResourceBudget,
) -> Result<MapBits, StorageValidationError> {
    MapBits::load(database, locator, budget).map_err(|source| shared(source, locator))
}

fn members(
    map: &MapBits,
    count: u64,
    budget: &mut ResourceBudget,
) -> Result<Vec<PageNumber>, StorageValidationError> {
    map.existing_pages(count, false, budget)
        .map_err(|source| shared(source, map.locator))
}

pub(super) struct AllocationState {
    global: MapBits,
    content: VisitedPages,
    metadata: VisitedPages,
    exclusive: VisitedPages,
    maps: Vec<MapRowLocator>,
}

impl AllocationState {
    pub fn new<S: ReadAt>(
        database: &mut DatabaseReader<S>,
        budget: &mut ResourceBudget,
    ) -> Result<Self, StorageValidationError> {
        let global = map(
            database,
            crate::mutation_map_write::global_locator(),
            budget,
        )?;
        let geometry = database.geometry();
        let mut state = Self {
            global,
            content: VisitedPages::new(geometry, budget)
                .map_err(StorageValidationError::Resource)?,
            metadata: VisitedPages::new(geometry, budget)
                .map_err(StorageValidationError::Resource)?,
            exclusive: VisitedPages::new(geometry, budget)
                .map_err(StorageValidationError::Resource)?,
            maps: Vec::new(),
        };
        state.metadata_page(PageNumber::new(0), true)?;
        // Global free bits beyond EOF describe future space (EXP-0254).
        let global = map(database, state.global.locator, budget)?;
        state.register(&global, budget)?;
        Ok(state)
    }

    fn allocated(&self, page: PageNumber) -> Result<(), StorageValidationError> {
        if self
            .global
            .contains(page)
            .map_err(|source| shared(source, self.global.locator))?
        {
            return Err(conflict(page, "owned or metadata page is globally free"));
        }
        Ok(())
    }

    fn metadata_page(
        &mut self,
        page: PageNumber,
        exclusive: bool,
    ) -> Result<(), StorageValidationError> {
        self.allocated(page)?;
        if self.content.contains(page)
            || self.exclusive.contains(page)
            || (exclusive && self.metadata.contains(page))
        {
            return Err(conflict(page, "shared definition, bitmap or content page"));
        }
        self.metadata
            .insert(page)
            .map_err(StorageValidationError::Resource)?;
        if exclusive {
            self.exclusive
                .insert(page)
                .map_err(StorageValidationError::Resource)?;
        }
        Ok(())
    }

    fn register(
        &mut self,
        map: &MapBits,
        budget: &mut ResourceBudget,
    ) -> Result<(), StorageValidationError> {
        budget
            .charge_work_units(self.maps.len() as u64 + map.spans.len() as u64 + 1)
            .map_err(StorageValidationError::Resource)?;
        if self.maps.contains(&map.locator) {
            return Err(conflict(
                map.locator.page(),
                "allocation map shared between roles",
            ));
        }
        reserve(&mut self.maps, 1, budget).map_err(StorageValidationError::Resource)?;
        self.maps.push(map.locator);
        self.metadata_page(map.locator.page(), false)?;
        for span in &map.spans {
            if span.page != map.locator.page() {
                self.metadata_page(span.page, true)?;
            }
        }
        Ok(())
    }

    fn owned<S: ReadAt>(
        &mut self,
        database: &mut DatabaseReader<S>,
        locator: MapRowLocator,
        available: Option<MapRowLocator>,
        budget: &mut ResourceBudget,
    ) -> Result<(), StorageValidationError> {
        let owned = map(database, locator, budget)?;
        self.register(&owned, budget)?;
        for page in members(&owned, database.geometry().page_count(), budget)? {
            self.allocated(page)?;
            if self.metadata.contains(page)
                || self
                    .content
                    .insert(page)
                    .map_err(StorageValidationError::Resource)?
            {
                return Err(conflict(page, "page is owned by incompatible roles"));
            }
        }
        if let Some(locator) = available {
            let available = map(database, locator, budget)?;
            self.register(&available, budget)?;
            for page in members(&available, database.geometry().page_count(), budget)? {
                budget
                    .charge_work_units(u64::from(owned.spans.len().max(1).ilog2()) + 1)
                    .map_err(StorageValidationError::Resource)?;
                if !owned
                    .contains(page)
                    .map_err(|source| shared(source, owned.locator))?
                {
                    return Err(conflict(page, "available page is not owned"));
                }
            }
        }
        Ok(())
    }

    pub fn table<S: ReadAt>(
        &mut self,
        database: &mut DatabaseReader<S>,
        definition: &TableDefinition,
        budget: &mut ResourceBudget,
    ) -> Result<(), StorageValidationError> {
        budget
            .charge_items(definition.pages().len() as u64)
            .map_err(StorageValidationError::Resource)?;
        for &page in definition.pages() {
            self.metadata_page(page, true)?;
        }
        self.owned(
            database,
            definition.maps().owned(),
            Some(definition.maps().available()),
            budget,
        )?;
        for index in definition.physical_indexes() {
            self.owned(
                database,
                MapRowLocator::new(index.usage_map().page(), index.usage_map().row()),
                None,
                budget,
            )?;
        }
        for group in definition.long_value_maps() {
            self.owned(database, group.owned(), Some(group.available()), budget)?;
        }
        Ok(())
    }
}

struct FragmentPage {
    page: PageNumber,
    column: ColumnOrdinal,
    seen: [u64; 4],
}

pub(super) struct PayloadInventory(Vec<FragmentPage>);

impl PayloadInventory {
    pub fn new<S: ReadAt>(
        database: &mut DatabaseReader<S>,
        definition: &TableDefinition,
        budget: &mut ResourceBudget,
    ) -> Result<Self, StorageValidationError> {
        let mut pages = Vec::new();
        for group in definition.long_value_maps() {
            let owned = map(database, group.owned(), budget)?;
            for page in members(&owned, database.geometry().page_count(), budget)? {
                reserve(&mut pages, 1, budget).map_err(StorageValidationError::Resource)?;
                pages.push(FragmentPage {
                    page,
                    column: group.column(),
                    seen: [0; 4],
                });
            }
        }
        budget
            .charge_work_units(
                (pages.len() as u64).saturating_mul(u64::from(pages.len().max(1).ilog2()) + 1),
            )
            .map_err(StorageValidationError::Resource)?;
        pages.sort_unstable_by_key(|page| page.page);
        Ok(Self(pages))
    }

    pub fn reached(
        &mut self,
        locator: RowLocator,
        column: ColumnOrdinal,
        budget: &mut ResourceBudget,
    ) -> Result<(), StorageValidationError> {
        budget
            .charge_work_units(u64::from(self.0.len().max(1).ilog2()) + 1)
            .map_err(StorageValidationError::Resource)?;
        let invalid = |detail| StorageValidationError::Fragment {
            locator,
            column,
            detail,
        };
        let position = self
            .0
            .binary_search_by_key(&locator.page(), |page| page.page)
            .map_err(|_| invalid("fragment page is not owned by a payload column"))?;
        let page = &mut self.0[position];
        let word = usize::from(locator.slot()) / 64;
        let bit = 1 << (locator.slot() % 64);
        if page.column != column {
            return Err(invalid("fragment belongs to another column"));
        }
        if page.seen[word] & bit != 0 {
            return Err(invalid("payload fragment has multiple references"));
        }
        page.seen[word] |= bit;
        Ok(())
    }

    pub fn finish<S: ReadAt>(
        self,
        database: &mut DatabaseReader<S>,
        budget: &mut ResourceBudget,
    ) -> Result<(), StorageValidationError> {
        budget
            .charge_work_units(self.0.len() as u64)
            .map_err(StorageValidationError::Resource)?;
        for page in self.0 {
            if live_fragments(database, page.page, budget)? != page.seen {
                return Err(conflict(page.page, "unreferenced live payload fragment"));
            }
        }
        Ok(())
    }
}

fn live_fragments<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    page: PageNumber,
    budget: &mut ResourceBudget,
) -> Result<[u64; 4], StorageValidationError> {
    let mut image = [0; PAGE_BYTES];
    let classified = database
        .read_classified_page(page, &mut image, budget)
        .map_err(StorageValidationError::Read)?;
    if classified.kind() != crate::PageKind::Data {
        return Err(conflict(page, "owned payload page is not a data page"));
    }
    let directory = crate::row_directory::RowDirectory::validate_long_value(page, &image, budget)
        .map_err(StorageValidationError::Directory)?;
    let mut live = [0; 4];
    budget
        .charge_items(u64::from(directory.row_count()))
        .map_err(StorageValidationError::Resource)?;
    for slot in 0..directory.row_count() {
        let entry = directory
            .entry(&image, slot as u8)
            .map_err(StorageValidationError::Directory)?;
        if entry.hidden() && entry.overflow() && entry.range().is_empty() {
            continue;
        }
        if entry.hidden() || entry.overflow() || entry.range().is_empty() {
            return Err(conflict(page, "invalid live payload slot"));
        }
        live[usize::from(slot) / 64] |= 1 << (slot % 64);
    }
    if live == [0; 4] {
        return Err(conflict(page, "owned payload page has no live fragments"));
    }
    Ok(live)
}

pub(super) fn index<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    definition: &crate::PhysicalIndexDefinition,
    tree: &crate::IndexTree,
    budget: &mut ResourceBudget,
) -> Result<(), StorageValidationError> {
    let locator = MapRowLocator::new(definition.usage_map().page(), definition.usage_map().row());
    let owned = map(database, locator, budget)?;
    budget
        .charge_work_units(
            (tree.nodes().len() as u64)
                .saturating_mul(u64::from(owned.spans.len().max(1).ilog2()) + 1),
        )
        .map_err(StorageValidationError::Resource)?;
    for node in tree.nodes() {
        if !owned
            .contains(node.page())
            .map_err(|source| shared(source, locator))?
        {
            return Err(conflict(
                node.page(),
                "index node is not owned by its index",
            ));
        }
    }
    Ok(())
}
