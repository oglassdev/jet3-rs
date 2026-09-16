//! EXP-0241 catalog data/index maps reuse ordinary rows and EXP-0062 trees.
//! Compact packing and placement after user pages are creation policy.

use super::*;
use crate::numeric_index_pages::{IndexRecord, NumericIndexPages, TreeBuildError};

const PAGE_LIMIT: u64 = allocation_maps::PAGE_LIMIT;

pub(super) struct CatalogData {
    pub(super) images: Vec<(u64, PageImage)>,
    pub(super) owned: Vec<u64>,
    pub(super) available: Vec<u64>,
    pub(super) locators: Vec<(u64, u8)>,
}

impl CatalogData {
    pub(super) fn build<T>(
        owner: u64,
        first_page: u64,
        seeds: impl Iterator<Item = T>,
        next_page: &mut u64,
        budget: &mut ResourceBudget,
        mut encode: impl FnMut(T, &mut [u8], &mut ResourceBudget) -> Result<usize, ComposeError>,
    ) -> Result<Self, ComposeError> {
        let mut result = Self {
            images: Vec::new(),
            owned: Vec::new(),
            available: Vec::new(),
            locators: Vec::new(),
        };
        let mut builder = DataPageBuilder::new(PageNumber::new(owner), budget)?;
        let mut page = first_page;
        let mut slot = 0;
        let mut row = [0_u8; PAGE_BYTES];
        let mut smallest = PAGE_BYTES;
        for seed in seeds {
            budget.charge_items(1)?;
            let length = encode(seed, &mut row, budget)?;
            smallest = smallest.min(length + 2);
            if builder.free_bytes().get() < (length + 2) as u64 {
                push(&mut result.owned, page, budget)?;
                push(
                    &mut result.images,
                    (page, finish_data_builder(builder, budget)?),
                    budget,
                )?;
                page = allocate_page(next_page)?;
                builder = DataPageBuilder::new(PageNumber::new(owner), budget)?;
                slot = 0;
            }
            builder.append_row(&row[..length], budget)?;
            push(
                &mut result.locators,
                (page, catalog_row_number(slot)?),
                budget,
            )?;
            slot += 1;
        }
        // The final page is the append candidate. A fully packed page cannot
        // accept even the smallest row in this catalog's admitted inventory.
        if builder.free_bytes().get() >= smallest as u64 {
            push(&mut result.available, page, budget)?;
        }
        push(&mut result.owned, page, budget)?;
        push(
            &mut result.images,
            (page, finish_data_builder(builder, budget)?),
            budget,
        )?;
        Ok(result)
    }

    fn first(&self) -> Result<PageImage, ComposeError> {
        self.images
            .first()
            .map(|(_, image)| image.clone())
            .ok_or(ComposeError::CatalogLayout {
                detail: "missing initial data page",
            })
    }
}

struct CatalogRecord {
    bytes: [u8; CATALOG_KEY_CAPACITY + 4],
    length: usize,
}

impl IndexRecord for CatalogRecord {
    fn record(&self) -> &[u8] {
        &self.bytes[..self.length]
    }
}

impl CatalogRecord {
    fn new(entry: OwnedIndexEntry, locator: (u64, u8)) -> Result<Self, ComposeError> {
        if locator.0 >= PAGE_LIMIT {
            return Err(ComposeError::CatalogPageLimit {
                maximum: PAGE_LIMIT,
            });
        }
        let mut bytes = [0; CATALOG_KEY_CAPACITY + 4];
        bytes[..entry.len].copy_from_slice(&entry.key[..entry.len]);
        bytes[entry.len..entry.len + 3].copy_from_slice(&(locator.0 as u32).to_be_bytes()[1..]);
        bytes[entry.len + 3] = locator.1;
        Ok(Self {
            bytes,
            length: entry.len + 4,
        })
    }
}

pub(super) struct CatalogIndex {
    pub(super) images: Vec<(u64, PageImage)>,
    pub(super) owned: Vec<u64>,
    pub(super) distinct_count: u32,
}

impl CatalogIndex {
    pub(super) fn build(
        owner: u64,
        root: u64,
        locators: &[(u64, u8)],
        keys: impl Iterator<Item = Result<OwnedIndexEntry, ComposeError>>,
        next_page: &mut u64,
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        let mut entries = Vec::new();
        for (position, key) in keys.enumerate() {
            let locator = locators
                .get(position)
                .copied()
                .ok_or(ComposeError::CatalogLayout {
                    detail: "catalog key has no row locator",
                })?;
            push(&mut entries, CatalogRecord::new(key?, locator)?, budget)?;
        }
        if entries.len() != locators.len() {
            return Err(ComposeError::CatalogLayout {
                detail: "catalog row has no key",
            });
        }
        budget.charge_work_units((entries.len() as u64).saturating_mul(entries.len() as u64))?;
        entries.sort_unstable_by(|left, right| left.record().cmp(right.record()));
        let distinct_count = usize::from(!entries.is_empty())
            + entries
                .windows(2)
                .filter(|pair| {
                    pair[0].bytes[..pair[0].length - 4] != pair[1].bytes[..pair[1].length - 4]
                })
                .count();
        let tree =
            NumericIndexPages::new(&entries, PAGE_LIMIT as usize, budget).map_err(tree_error)?;
        let mut result = Self {
            images: Vec::new(),
            owned: Vec::new(),
            distinct_count: u32::try_from(distinct_count).map_err(|_| {
                ComposeError::CatalogLayout {
                    detail: "catalog distinct key count",
                }
            })?,
        };
        for position in 0..tree.len() {
            let page = if position + 1 == tree.len() {
                root
            } else {
                allocate_page(next_page)?
            };
            push(&mut result.owned, page, budget)?;
        }
        for (position, &page) in result.owned.iter().enumerate() {
            let image = tree
                .image(
                    position,
                    &entries,
                    |n| result.owned.get(n).copied().map(PageNumber::new),
                    PageNumber::new(owner),
                    &[0; PAGE_BYTES],
                    budget,
                )
                .map_err(tree_error)?;
            push(&mut result.images, (page, image), budget)?;
        }
        Ok(result)
    }

    pub(super) fn root(&self) -> Result<PageImage, ComposeError> {
        self.images
            .last()
            .map(|(_, image)| image.clone())
            .ok_or(ComposeError::CatalogLayout {
                detail: "missing catalog index root",
            })
    }
}

pub(super) struct CatalogPages {
    objects: CatalogData,
    aces: CatalogData,
    names: CatalogIndex,
    ids: CatalogIndex,
    ace_ids: CatalogIndex,
    page_count: u64,
}

impl CatalogPages {
    pub(super) fn new(
        creates: &[PlannedCreate<'_>],
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        Self::new_with_extras(creates, &[], &[], budget)
    }

    pub(super) fn new_with_extras(
        creates: &[PlannedCreate<'_>],
        extra_objects: &[CatalogSeed<'_>],
        extra_aces: &[AceSeed],
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        let mut next_page = creates
            .last()
            .map_or(EMPTY_DATABASE_PAGE_COUNT, PlannedCreate::page_count);
        let objects = CatalogData::build(
            MSYS_OBJECTS_ROOT,
            MSYS_OBJECTS_DATA_PAGE,
            catalog_seeds(creates, None).chain(extra_objects.iter().copied()),
            &mut next_page,
            budget,
            |seed, output, budget| {
                budget.charge_work_units(creates.len() as u64)?;
                let property = creates
                    .iter()
                    .find(|c| c.catalog_seed().id == seed.id)
                    .map(PlannedCreate::property_header)
                    .transpose()?
                    .flatten();
                encode_catalog_row(seed, property.as_ref(), output, budget)
            },
        )?;
        let aces = CatalogData::build(
            MSYS_ACES_ROOT,
            MSYS_ACES_DATA_PAGE,
            ace_seeds(creates, extra_aces),
            &mut next_page,
            budget,
            encode_ace_row,
        )?;
        let names = CatalogIndex::build(
            MSYS_OBJECTS_ROOT,
            OBJECTS_PARENT_NAME_ROOT,
            &objects.locators,
            catalog_seeds(creates, None)
                .chain(extra_objects.iter().copied())
                .map(|seed| OwnedIndexEntry::name(seed.parent, seed.name, 0)),
            &mut next_page,
            budget,
        )?;
        let ids = CatalogIndex::build(
            MSYS_OBJECTS_ROOT,
            OBJECTS_ID_ROOT,
            &objects.locators,
            catalog_seeds(creates, None)
                .chain(extra_objects.iter().copied())
                .map(|seed| Ok(OwnedIndexEntry::long(seed.id, 0))),
            &mut next_page,
            budget,
        )?;
        let ace_ids = CatalogIndex::build(
            MSYS_ACES_ROOT,
            ACES_OBJECT_ID_ROOT,
            &aces.locators,
            ace_seeds(creates, extra_aces).map(|seed| Ok(OwnedIndexEntry::long(seed.object, 0))),
            &mut next_page,
            budget,
        )?;
        Ok(Self {
            objects,
            aces,
            names,
            ids,
            ace_ids,
            page_count: next_page,
        })
    }

    pub(super) fn object_count(&self) -> Result<u32, ComposeError> {
        u32::try_from(self.objects.locators.len()).map_err(|_| ComposeError::CatalogLayout {
            detail: "catalog object count",
        })
    }
    pub(super) fn ace_count(&self) -> Result<u32, ComposeError> {
        u32::try_from(self.aces.locators.len()).map_err(|_| ComposeError::CatalogLayout {
            detail: "catalog access-control count",
        })
    }

    pub(super) fn page_count(&self) -> u64 {
        self.page_count
    }
    pub(super) fn objects_data(&self) -> Result<PageImage, ComposeError> {
        self.objects.first()
    }
    pub(super) fn aces_data(&self) -> Result<PageImage, ComposeError> {
        self.aces.first()
    }
    pub(super) fn names_root(&self) -> Result<PageImage, ComposeError> {
        self.names.root()
    }
    pub(super) fn ids_root(&self) -> Result<PageImage, ComposeError> {
        self.ids.root()
    }
    pub(super) fn aces_root(&self) -> Result<PageImage, ComposeError> {
        self.ace_ids.root()
    }
    pub(super) fn names_map(
        &self,
        maps: &mut AllocationMaps,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        data_page(
            HEADER_PAGE,
            &[&maps.row(self.names.owned.iter().copied(), budget)?],
            budget,
        )
    }
    pub(super) fn ids_map(
        &self,
        maps: &mut AllocationMaps,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        data_page(
            HEADER_PAGE,
            &[&maps.row(self.ids.owned.iter().copied(), budget)?],
            budget,
        )
    }
    pub(super) fn objects_map(
        &self,
        creates: &[PlannedCreate<'_>],
        maps: &mut AllocationMaps,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        objects_map_page(
            creates,
            &self.objects.owned,
            &self.objects.available,
            maps,
            budget,
        )
    }
    pub(super) fn shared_map(
        &self,
        relationships: RelationshipMaps<'_>,
        maps: &mut AllocationMaps,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        shared_map_page_with_aces(
            relationships,
            &self.aces.owned,
            &self.aces.available,
            &self.ace_ids.owned,
            maps,
            budget,
        )
    }

    pub(super) fn append(
        self,
        plan: &mut WholeFileImagePlan,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let images = self
            .objects
            .images
            .into_iter()
            .chain(self.aces.images)
            .chain(self.names.images)
            .chain(self.ids.images)
            .chain(self.ace_ids.images);
        for (number, image) in images {
            if number < EMPTY_DATABASE_PAGE_COUNT {
                continue;
            }
            if number != plan.page_count() {
                return Err(ComposeError::CatalogLayout {
                    detail: "catalog append page order",
                });
            }
            plan.append_image(image, budget)?;
        }
        Ok(())
    }
}

fn allocate_page(next_page: &mut u64) -> Result<u64, ComposeError> {
    if *next_page >= PAGE_LIMIT {
        return Err(ComposeError::CatalogPageLimit {
            maximum: PAGE_LIMIT,
        });
    }
    let page = *next_page;
    *next_page += 1;
    Ok(page)
}

fn push<T>(values: &mut Vec<T>, value: T, budget: &mut ResourceBudget) -> Result<(), ComposeError> {
    if values.len() == values.capacity() {
        budget.charge_allocation(ByteCount::new(size_of::<T>() as u64))?;
        values.try_reserve_exact(1).map_err(|_| Error::Io {
            operation: "reserve catalog creation storage",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
    }
    values.push(value);
    Ok(())
}

fn tree_error(error: TreeBuildError) -> ComposeError {
    match error {
        TreeBuildError::Encoding(error) => error.into(),
        TreeBuildError::Layout(detail) => ComposeError::CatalogLayout { detail },
        TreeBuildError::NodeLimit { .. } => ComposeError::CatalogPageLimit {
            maximum: PAGE_LIMIT,
        },
    }
}
