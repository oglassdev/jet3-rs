//! Crate-private composition of the observed fresh Jet 3 bootstrap images.
//!
//! This connects the checked definition, row, usage-map, page, append, and
//! whole-file primitives. It deliberately exposes no creation or I/O API and
//! makes no DAO-compatibility claim. Page roles and the empty-to-`Alpha`
//! transition come from `EXP-0073`; the fixed page-zero transition byte comes
//! from `EXP-0069` and `EXP-0071`; long-value map groups come from `EXP-0077`;
//! fixed composite keys come from `EXP-0079`; the fixed opaque page-zero
//! candidate hypothesis is preregistered by `EXP-0084`; the null catalog
//! `LvProp` with a retained empty long-value page is the `EXP-0091` accepted
//! construction for `Alpha(Id Long)` only; first-create page order comes
//! from `EXP-0093`. Any composed image other than the `Alpha` transition is
//! an unvalidated candidate until a DAO differential accepts it.

use std::fmt;

use crate::catalog_name_key::{CatalogNameKeyError, catalog_names_equal, encode_catalog_name_key};
use crate::creation::schema_plan::{TableSchemaPlanError, TableSpec};
use crate::page_append_plan::EMPTY_DATABASE_PAGE_COUNT;
use crate::whole_file_plan::{WholeFileImagePlan, WholeFilePlanError};
use crate::{
    ByteCount, ColumnPhysicalType, ColumnSpec, ColumnStorageClass, ColumnType, DataPageBuilder,
    Error, IndexDirection, InlineUsageMapEncoder, LogicalIndexKindSpec, LogicalIndexSpec,
    LongValueMapSpec, MapRowLocator, PAGE_BYTES, PageImage, PageImageError, PageKind, PageNumber,
    PageOffset, PhysicalIndexFlagsSpec, PhysicalIndexSpec, ResourceBudget, RowColumnLayout,
    RowValue, RowWriteError, SystemColumnClassSpec, TableDefinitionKind, TableDefinitionSpec,
    TableDefinitionWriteError, UsageMapWriteError, encode_row, encode_table_definition,
};

mod allocation_maps;
use allocation_maps::AllocationMaps;

const HEADER_PAGE: u64 = 0;
const GLOBAL_MAP_PAGE: u64 = 1;
const MSYS_OBJECTS_ROOT: u64 = 2;
const MSYS_ACES_ROOT: u64 = 3;
const MSYS_QUERIES_ROOT: u64 = 4;
const MSYS_RELATIONSHIPS_ROOT: u64 = 5;
const MSYS_OBJECTS_MAP_PAGE: u64 = 6;
const LV_EXTRA_MAP_PAGE: u64 = 7;
const OBJECTS_PARENT_NAME_MAP_PAGE: u64 = 8;
const OBJECTS_PARENT_NAME_ROOT: u64 = 9;
const OBJECTS_ID_MAP_PAGE: u64 = 10;
const OBJECTS_ID_ROOT: u64 = 11;
const SHARED_MAP_PAGE: u64 = 12;
const ACES_OBJECT_ID_ROOT: u64 = 13;
const QUERIES_INDEX_ROOT: u64 = 14;
const RELATIONSHIPS_NAME_ROOT: u64 = 15;
const RELATIONSHIPS_OBJECT_ROOT: u64 = 16;
const RELATIONSHIPS_REFERENCED_ROOT: u64 = 17;
const MSYS_OBJECTS_DATA_PAGE: u64 = 18;
const MSYS_ACES_DATA_PAGE: u64 = 19;

const GLOBAL_BITMAP_BYTES: u64 = 128;
const MAP_BITMAP_BYTES: u64 = 128;
const INDEX_ENTRY_AREA_OFFSET: usize = 248;
const INDEX_ENTRY_AREA_LEN: usize = PAGE_BYTES - INDEX_ENTRY_AREA_OFFSET;
const INDEX_BOUNDARY_BITMAP_OFFSET: usize = 22;
const CATALOG_KEY_CAPACITY: usize = crate::catalog_name_key::MAX_CREATION_KEY_BYTES;
// EXP-0084 preregisters only these fixed per-row candidate values; their SID
// meanings are not generalized.
const CATALOG_OWNER_0203: &[u8] = b"\x02\x03";
const CATALOG_OWNER_0301: &[u8] = b"\x03\x01";
// EXP-0084 preregisters this fixed bootstrap hypothesis. Its fields remain
// uninterpreted and no general page-zero grammar is inferred.
const DATABASE_HEADER_FIXED_OPAQUE: [u8; 126] = [
    0xb5, 0x6e, 0x03, 0x62, 0x60, 0x09, 0xc2, 0x55, 0xe9, 0xa9, 0x67, 0x72, 0x40, 0x3f, 0x00, 0x9c,
    0x7e, 0x9f, 0x90, 0xff, 0x85, 0x9a, 0x31, 0xc5, 0x79, 0xba, 0xed, 0x30, 0xbc, 0xdf, 0xcc, 0x9d,
    0x63, 0xd9, 0xed, 0xc7, 0x9f, 0x46, 0xfb, 0x8a, 0xbc, 0x4e, 0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44,
    0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6, 0x8a, 0x60, 0x54, 0x94, 0x7b, 0x36, 0xbc, 0x54,
    0xdf, 0xb1, 0x77, 0xf4, 0x13, 0x43, 0xcf, 0xaf, 0xb1, 0x33, 0x34, 0x61, 0x79, 0x5b, 0x92, 0xb5,
    0x7c, 0x2a, 0x05, 0xf1, 0x7c, 0x99, 0x01, 0x1b, 0x98, 0xfd, 0x12, 0x4f, 0x4a, 0x94, 0x6c, 0x3e,
    0x60, 0x26, 0x5f, 0x95, 0xf8, 0xd0, 0x89, 0x24, 0x85, 0x67, 0xc6, 0x1f, 0x27, 0x44, 0xd2, 0xee,
    0xcf, 0x65, 0xed, 0xff, 0x07, 0xc7, 0x46, 0xa1, 0x78, 0x16, 0x0c, 0xed, 0xe9, 0x2d,
];
#[cfg(test)]
const ALPHA_COLUMNS: [ColumnSpec<'static>; 1] = [ColumnSpec::new(b"Id", ColumnType::Long)];
#[cfg(test)]
const ALPHA_SPEC: TableSpec<'static> = TableSpec {
    validation: crate::TableValidation::NONE,
    name: b"Alpha",
    columns: &ALPHA_COLUMNS,
    indexes: &[],
};

/// `EXP-0073`: catalog and access-control rows of the empty database.
const SYSTEM_OBJECT_COUNT: usize = 8;
const SYSTEM_ACE_COUNT: usize = 16;

const TABLES_ID: i32 = 0x0f00_0001;
const DATABASES_ID: i32 = 0x0f00_0002;
const RELATIONSHIPS_ID: i32 = 0x0f00_0003;
const ROOT_CONTAINER_ID: i32 = 0x0f00_0000;
const MSYS_DB_ID: i32 = 0x1000_0000;

#[path = "compose_error.rs"]
mod error;
pub use error::ComposeError;

/// Composes the deterministic 20-page empty image established by `EXP-0073`.
#[cfg(test)]
pub(crate) fn compose_empty_database(
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    let catalog = CatalogPages::new(&[], budget)?;
    let images = compose_existing_pages(&[], &catalog, &mut AllocationMaps::inline_only(), budget)?;
    WholeFileImagePlan::from_existing_pages(images, budget).map_err(Into::into)
}

/// Composes the empty-to-`Alpha(Id Long)` transition from `EXP-0073` in the
/// null-`LvProp` form `EXP-0091` accepted.
#[cfg(test)]
pub(crate) fn compose_alpha_database(
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    compose_table_database(&ALPHA_SPEC, budget)
}

/// Composes the empty database plus one created user table.
#[cfg(test)]
pub(crate) fn compose_table_database(
    spec: &TableSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    compose_database(std::slice::from_ref(spec), budget)
}

/// Composes the empty database plus the given user tables created in order.
///
/// EXP-0087/0093 supply sequential table pages and independent index maps;
/// EXP-0222/0241 extend the catalog within inline-map capacities.
/// Only the first table carries a retained bootstrap LvProp page.
pub(crate) fn compose_database(
    specs: &[TableSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    let mut creates = reserve_creates(specs.len(), budget)?;
    let mut next_page = EMPTY_DATABASE_PAGE_COUNT;
    for (position, spec) in specs.iter().enumerate() {
        budget.charge_items(1)?;
        budget.charge_work_units((position as u64).saturating_mul(512))?;
        if let Some(first) = specs[..position]
            .iter()
            .position(|earlier| catalog_names_equal(earlier.name, spec.name))
        {
            return Err(ComposeError::DuplicateTableName {
                first,
                second: position,
            });
        }
        let planned = PlannedCreate::new(spec, next_page, position == 0, budget)?;
        next_page = planned.page_count();
        creates.push(planned);
    }
    compose_planned_creates(&creates, budget)
}

fn compose_planned_creates(
    creates: &[PlannedCreate<'_>],
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    let catalog = CatalogPages::new(creates, budget)?;
    let mut maps = AllocationMaps::new(catalog.page_count());
    let images = compose_existing_pages(creates, &catalog, &mut maps, budget)?;
    let mut plan = WholeFileImagePlan::from_existing_pages(images, budget)?;
    for planned in creates {
        planned.append_pages(&mut plan, &mut maps, budget)?;
    }
    catalog.append(&mut plan, budget)?;
    maps.finish(&mut plan, budget)?;
    Ok(plan)
}

/// Resolves precisely the same column placements as the definition encoder.
pub(crate) fn initial_row_layout(
    spec: &TableSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<Vec<RowColumnLayout>, ComposeError> {
    budget.charge_allocation(ByteCount::new(
        (spec.columns.len() * std::mem::size_of::<RowColumnLayout>()) as u64,
    ))?;
    let mut layout = Vec::new();
    layout
        .try_reserve_exact(spec.columns.len())
        .map_err(|_| Error::Io {
            operation: "reserve initial row layout",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
    let mut fixed = 0;
    let mut variable = 0;
    for (ordinal, column) in (0_u16..).zip(spec.columns) {
        let resolved = crate::column_definition_writer::resolve_column(
            ordinal,
            column,
            TableDefinitionKind::User,
            None,
            &mut fixed,
            &mut variable,
        )?;
        layout.push(RowColumnLayout::new(
            column.physical_type(),
            resolved.storage,
            column.size(),
        ));
    }
    Ok(layout)
}

fn compose_existing_pages(
    creates: &[PlannedCreate<'_>],
    catalog: &CatalogPages,
    maps: &mut AllocationMaps,
    budget: &mut ResourceBudget,
) -> Result<[PageImage; EMPTY_DATABASE_PAGE_COUNT as usize], ComposeError> {
    compose_existing_pages_with_relationships(
        creates,
        catalog,
        RelationshipMaps::single(&[]),
        maps,
        budget,
    )
}

fn compose_existing_pages_with_relationships(
    creates: &[PlannedCreate<'_>],
    catalog: &CatalogPages,
    relationships: RelationshipMaps<'_>,
    maps: &mut AllocationMaps,
    budget: &mut ResourceBudget,
) -> Result<[PageImage; EMPTY_DATABASE_PAGE_COUNT as usize], ComposeError> {
    let object_count = catalog.object_count()?;
    let ace_count = catalog.ace_count()?;
    Ok([
        header_page(creates.len(), budget)?,
        global_map_page(EMPTY_DATABASE_PAGE_COUNT, budget)?,
        msys_objects_definition(object_count, budget)?,
        msys_aces_definition(ace_count, object_count, budget)?,
        msys_queries_definition(budget)?,
        msys_relationships_definition(0, [0; 3], budget)?,
        catalog.objects_map(creates, maps, budget)?,
        single_map_page(&[], budget)?,
        catalog.names_map(maps, budget)?,
        catalog.names_root()?,
        catalog.ids_map(maps, budget)?,
        catalog.ids_root()?,
        catalog.shared_map(relationships, maps, budget)?,
        catalog.aces_root()?,
        empty_index_page(MSYS_QUERIES_ROOT, budget)?,
        empty_index_page(MSYS_RELATIONSHIPS_ROOT, budget)?,
        empty_index_page(MSYS_RELATIONSHIPS_ROOT, budget)?,
        empty_index_page(MSYS_RELATIONSHIPS_ROOT, budget)?,
        catalog.objects_data()?,
        catalog.aces_data()?,
    ])
}

fn header_page(table_count: usize, budget: &mut ResourceBudget) -> Result<PageImage, ComposeError> {
    let mut image = PageImage::new(PageKind::DatabaseDefinition);
    image.write_at(PageOffset::new(1), &[1], budget)?;
    image.write_at(PageOffset::new(4), b"Standard Jet DB", budget)?;
    image.write_at(PageOffset::new(24), &DATABASE_HEADER_FIXED_OPAQUE, budget)?;
    let mut commit_state = [0_u8; 512];
    commit_state[0] = 1;
    for offset in (1..commit_state.len()).step_by(2) {
        commit_state[offset] = 1;
    }
    // EXP-0249: preserve the sourced 16-bit carry for the creation history.
    commit_state[2..4].copy_from_slice(&creation_counter(table_count)?.to_le_bytes());
    image.write_at(PageOffset::new(1536), &commit_state, budget)?;
    Ok(image)
}

fn global_map(
    used_pages: u64,
    budget: &mut ResourceBudget,
) -> Result<InlineUsageMapEncoder, ComposeError> {
    let mut map = InlineUsageMapEncoder::new(
        PageNumber::new(0),
        ByteCount::new(GLOBAL_BITMAP_BYTES),
        budget,
    )?;
    for page in used_pages..map.page_count() {
        map.set_page(PageNumber::new(page))?;
    }
    Ok(map)
}

fn global_map_page(
    used_pages: u64,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let map = global_map(used_pages, budget)?;
    let mut row = [0_u8; 133];
    map.encode_into(&mut row, budget)?;
    data_page(GLOBAL_MAP_PAGE, &[&row, &[0; 133]], budget)
}

fn inline_map_row(pages: &[u64], budget: &mut ResourceBudget) -> Result<[u8; 133], ComposeError> {
    let mut map =
        InlineUsageMapEncoder::new(PageNumber::new(0), ByteCount::new(MAP_BITMAP_BYTES), budget)?;
    for &page in pages {
        map.set_page(PageNumber::new(page))?;
    }
    let mut row = [0_u8; 133];
    map.encode_into(&mut row, budget)?;
    Ok(row)
}

fn single_map_page(pages: &[u64], budget: &mut ResourceBudget) -> Result<PageImage, ComposeError> {
    let row = inline_map_row(pages, budget)?;
    data_page(HEADER_PAGE, &[&row], budget)
}

fn objects_map_page(
    creates: &[PlannedCreate<'_>],
    owned: &[u64],
    available: &[u64],
    maps: &mut AllocationMaps,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let owned = maps.row(owned.iter().copied(), budget)?;
    let available = maps.row(available.iter().copied(), budget)?;
    let empty = inline_map_row(&[], budget)?;
    let long_value_pages = creates
        .iter()
        .flat_map(|create| create.property_pages(false));
    let lvprop = maps.row(long_value_pages, budget)?;
    let lvprop_available = maps.row(
        creates
            .iter()
            .flat_map(|create| create.property_pages(true)),
        budget,
    )?;
    let rows: [&[u8]; 15] = [
        &owned,
        &available,
        &empty,
        &empty,
        &empty,
        &empty,
        &empty,
        &empty,
        &empty,
        &empty,
        &lvprop,
        &lvprop_available,
        &empty,
        &empty,
        &empty,
    ];
    data_page(HEADER_PAGE, &rows, budget)
}

fn shared_map_page_with_aces(
    relationships: RelationshipMaps<'_>,
    ace_owned: &[u64],
    ace_available: &[u64],
    ace_index: &[u64],
    maps: &mut AllocationMaps,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let ace_owned = maps.row(ace_owned.iter().copied(), budget)?;
    let ace_available = maps.row(ace_available.iter().copied(), budget)?;
    let ace_index = maps.row(ace_index.iter().copied(), budget)?;
    let empty = inline_map_row(&[], budget)?;
    let query_index = inline_map_row(&[QUERIES_INDEX_ROOT], budget)?;
    let relation_data = maps.row(relationships.owned.iter().copied(), budget)?;
    let relation_available = maps.row(relationships.available.iter().copied(), budget)?;
    let relation_name = maps.row(relationships.indexes[0].iter().copied(), budget)?;
    let relation_object = maps.row(relationships.indexes[1].iter().copied(), budget)?;
    let relation_referenced = maps.row(relationships.indexes[2].iter().copied(), budget)?;
    let rows: [&[u8]; 13] = [
        &ace_owned,
        &ace_available,
        &ace_index,
        &empty,
        &empty,
        &empty,
        &empty,
        &query_index,
        &relation_data,
        &relation_available,
        &relation_name,
        &relation_object,
        &relation_referenced,
    ];
    data_page(HEADER_PAGE, &rows, budget)
}

fn data_page(
    owner: u64,
    rows: &[&[u8]],
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let mut builder = DataPageBuilder::new(PageNumber::new(owner), budget)?;
    for row in rows {
        builder.append_row(row, budget)?;
    }
    let free = u16::try_from(builder.free_bytes().get()).map_err(|_| Error::IntegerConversion {
        value: u128::from(builder.free_bytes().get()),
        target: "u16",
    })?;
    let mut image = builder.finish();
    let [low, high] = free.to_le_bytes();
    image.write_at(PageOffset::new(1), &[1, low, high], budget)?;
    Ok(image)
}

fn definition_page(
    spec: &TableDefinitionSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let mut bytes = [0_u8; PAGE_BYTES];
    encode_table_definition(spec, &mut bytes, budget)?;
    Ok(PageImage::from_bytes(bytes))
}

#[path = "system_definitions.rs"]
mod definitions;
use definitions::{
    msys_aces_definition, msys_objects_definition, msys_queries_definition,
    msys_relationships_definition,
};

#[path = "initial_long_values.rs"]
mod initial_long_values;
use initial_long_values::InitialLongValues;
pub(crate) use initial_long_values::{encode_initial_row, initial_payload_start};

#[path = "initial_index.rs"]
mod initial_index;
pub(crate) use initial_index::InitialLongIndex;

#[path = "table_create.rs"]
mod table_create;
pub(crate) use table_create::compose_database_with_table_rows;
use table_create::{PlannedCreate, creation_counter, reserve_creates};

mod catalog_rows;
use catalog_rows::*;

mod catalog_pages;
use catalog_pages::CatalogPages;

fn finish_data_builder(
    builder: DataPageBuilder,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let free = u16::try_from(builder.free_bytes().get()).map_err(|_| Error::IntegerConversion {
        value: u128::from(builder.free_bytes().get()),
        target: "u16",
    })?;
    let mut image = builder.finish();
    let [low, high] = free.to_le_bytes();
    image.write_at(PageOffset::new(1), &[1, low, high], budget)?;
    Ok(image)
}

#[derive(Clone, Copy)]
struct OwnedIndexEntry {
    key: [u8; CATALOG_KEY_CAPACITY],
    len: usize,
    row: u8,
}
impl OwnedIndexEntry {
    const EMPTY: Self = Self {
        key: [0; CATALOG_KEY_CAPACITY],
        len: 0,
        row: 0,
    };
    fn long(value: i32, row: u8) -> Self {
        let mut entry = Self::EMPTY;
        entry.key[0] = 0x7f;
        let mut raw = value.to_be_bytes();
        raw[0] ^= 0x80;
        entry.key[1..5].copy_from_slice(&raw);
        entry.len = 5;
        entry.row = row;
        entry
    }
    fn name(parent: i32, name: &[u8], row: u8) -> Result<Self, ComposeError> {
        let mut entry = Self::EMPTY;
        entry.len = encode_catalog_name_key(parent, name, &mut entry.key)?;
        entry.row = row;
        Ok(entry)
    }
}

fn objects_parent_name_index(
    creates: &[PlannedCreate<'_>],
    extra: Option<CatalogSeed<'_>>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let mut entries = catalog_seeds(creates, extra)
        .enumerate()
        .map(|(row, seed)| OwnedIndexEntry::name(seed.parent, seed.name, catalog_row_number(row)?))
        .collect::<Result<Vec<_>, ComposeError>>()?;
    sort_index_entries(&mut entries);
    index_page(MSYS_OBJECTS_ROOT, MSYS_OBJECTS_DATA_PAGE, &entries, budget)
}
fn objects_id_index(
    creates: &[PlannedCreate<'_>],
    extra: Option<CatalogSeed<'_>>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let mut entries = catalog_seeds(creates, extra)
        .enumerate()
        .map(|(row, seed)| Ok(OwnedIndexEntry::long(seed.id, catalog_row_number(row)?)))
        .collect::<Result<Vec<_>, ComposeError>>()?;
    sort_index_entries(&mut entries);
    index_page(MSYS_OBJECTS_ROOT, MSYS_OBJECTS_DATA_PAGE, &entries, budget)
}
fn aces_index(
    creates: &[PlannedCreate<'_>],
    extra: &[AceSeed],
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let mut entries = ace_seeds(creates, extra)
        .enumerate()
        .map(|(row, seed)| Ok(OwnedIndexEntry::long(seed.object, catalog_row_number(row)?)))
        .collect::<Result<Vec<_>, ComposeError>>()?;
    sort_index_entries(&mut entries);
    index_page(MSYS_ACES_ROOT, MSYS_ACES_DATA_PAGE, &entries, budget)
}

fn catalog_row_number(row: usize) -> Result<u8, ComposeError> {
    u8::try_from(row).map_err(|_| {
        Error::IntegerConversion {
            value: row as u128,
            target: "u8",
        }
        .into()
    })
}

fn sort_index_entries(entries: &mut [OwnedIndexEntry]) {
    entries.sort_unstable_by(|left, right| {
        left.key[..left.len]
            .cmp(&right.key[..right.len])
            .then(left.row.cmp(&right.row))
    });
}

fn empty_index_page(owner: u64, budget: &mut ResourceBudget) -> Result<PageImage, ComposeError> {
    index_page(owner, 0, &[], budget)
}

fn index_page(
    owner: u64,
    row_page: u64,
    entries: &[OwnedIndexEntry],
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let needed = entries
        .iter()
        .try_fold(0_usize, |total, entry| total.checked_add(entry.len + 4))
        .ok_or(Error::Arithmetic {
            operation: "size bootstrap index entries",
        })?;
    if needed > INDEX_ENTRY_AREA_LEN {
        return Err(ComposeError::IndexPageFull {
            needed,
            available: INDEX_ENTRY_AREA_LEN,
        });
    }
    let mut bytes = [0_u8; PAGE_BYTES];
    bytes[0] = 4;
    bytes[1] = 1;
    bytes[2..4].copy_from_slice(&(INDEX_ENTRY_AREA_LEN as u16 - needed as u16).to_le_bytes());
    bytes[4..8].copy_from_slice(&(owner as u32).to_le_bytes());
    let mut end = 0_usize;
    for entry in entries {
        let start = INDEX_ENTRY_AREA_OFFSET + end;
        bytes[start..start + entry.len].copy_from_slice(&entry.key[..entry.len]);
        let trailer = start + entry.len;
        let page = row_page as u32;
        bytes[trailer..trailer + 3].copy_from_slice(&page.to_be_bytes()[1..]);
        bytes[trailer + 3] = entry.row;
        end += entry.len + 4;
        bytes[INDEX_BOUNDARY_BITMAP_OFFSET + end / 8] |= 1 << (end % 8);
    }
    let mut image = PageImage::new(PageKind::LeafIndex);
    image.write_at(PageOffset::new(0), &bytes, budget)?;
    Ok(image)
}

#[cfg(test)]
#[path = "composer_tests.rs"]
mod tests;

#[path = "relationship_candidate.rs"]
mod relationship_candidate;
mod relationship_graph;
mod relationship_pages;
pub(crate) use relationship_graph::{GraphImage, compose_relationship_graph};
use relationship_pages::{RelationshipMaps, RelationshipPages};

pub(crate) use relationship_candidate::{compose_relationship, compose_relationship_with_rows};

#[path = "autoincrement.rs"]
mod autoincrement;
pub(crate) use autoincrement::InitialAutoIncrement;

#[cfg(all(test, any(unix, windows)))]
mod relationship_graph_mutation_tests;

pub(crate) fn table_count_limit(count: usize) -> Result<(), ComposeError> {
    creation_counter(count).map(|_| ())
}
