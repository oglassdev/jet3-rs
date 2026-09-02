//! Crate-private composition of the observed fresh Jet 3 bootstrap images.
//!
//! This connects the checked definition, row, usage-map, page, append, and
//! whole-file primitives. It deliberately exposes no creation or I/O API and
//! makes no DAO-compatibility claim. Page roles and the empty-to-`Alpha`
//! transition come from `EXP-0073`; the fixed page-zero transition byte comes
//! from `EXP-0069` and `EXP-0071`; long-value map groups come from `EXP-0077`;
//! fixed composite keys and the opaque `Alpha.LvProp` value come from
//! `EXP-0079`; the fixed opaque page-zero candidate hypothesis is
//! preregistered by `EXP-0084`.

#![allow(
    dead_code,
    reason = "crate-private writer slice awaiting DAO validation"
)]

use std::fmt;

use crate::catalog_name_key::{CatalogNameKeyError, encode_catalog_name_key};
use crate::long_value_writer::LongValueWriteError;
use crate::page_append_plan::EMPTY_DATABASE_PAGE_COUNT;
use crate::table_schema_plan::{TableSchemaPlanError, TableSchemaSpec};
use crate::whole_file_plan::{WholeFileImagePlan, WholeFilePlanError};
use crate::{
    ByteCount, ColumnPhysicalType, ColumnSpec, ColumnStorageClass, ColumnStorageKind,
    DataPageBuilder, Error, IndexDirection, IndexFieldSpec, InlineUsageMapEncoder,
    LogicalIndexKindSpec, LogicalIndexSpec, LongValueMapSpec, MapRowLocator, PAGE_BYTES, PageImage,
    PageImageError, PageKind, PageNumber, PageOffset, PhysicalIndexFlagsSpec, PhysicalIndexSpec,
    ResourceBudget, RowColumnLayout, RowValue, RowWriteError, SystemColumnClassSpec,
    TableDefinitionKind, TableDefinitionSpec, TableDefinitionWriteError, UsageMapWriteError,
    encode_row, encode_table_definition,
};

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
const INDEX_KEY_CAPACITY: usize = 64;
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
// EXP-0079 records this fixed value losslessly; its property grammar remains
// uninterpreted.
const ALPHA_LVPROP_PAYLOAD: &[u8] =
    b"KKD\x00\x10\x00\x00\x00\x80\x00\x08\x00Required\x17\x00\x00\x00\x01\x00\x08\x00\x00\x00\x02\x00Id\x09\x00\x01\x01\x00\x00\x01\x00\x00";

const TABLES_ID: i32 = 0x0f00_0001;
const DATABASES_ID: i32 = 0x0f00_0002;
const RELATIONSHIPS_ID: i32 = 0x0f00_0003;
const ROOT_CONTAINER_ID: i32 = 0x0f00_0000;
const MSYS_DB_ID: i32 = 0x1000_0000;

#[path = "bootstrap_compose_error.rs"]
mod error;
pub(crate) use error::BootstrapComposeError;

/// Composes the deterministic 20-page empty image established by `EXP-0073`.
pub(crate) fn compose_empty_database(
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, BootstrapComposeError> {
    let images = compose_existing_pages(None, budget)?;
    WholeFileImagePlan::from_existing_pages(images, budget).map_err(Into::into)
}

/// Composes the fixed empty-to-`Alpha(Id Long)` transition from `EXP-0073`,
/// carrying the `LvProp` payload `EXP-0079` recorded for it.
pub(crate) fn compose_alpha_database(
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, BootstrapComposeError> {
    const COLUMNS: [ColumnSpec<'static>; 1] = [ColumnSpec::new(
        b"Id",
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        4,
    )];
    compose_table_database(
        TableCreate {
            spec: &TableSchemaSpec {
                name: b"Alpha",
                columns: &COLUMNS,
                indexes: &[],
            },
            properties: ALPHA_LVPROP_PAYLOAD,
        },
        budget,
    )
}

/// Composes the empty database plus one created user table, following the
/// `EXP-0087` create observations.
pub(crate) fn compose_table_database(
    create: TableCreate<'_>,
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, BootstrapComposeError> {
    let planned = PlannedCreate::new(create)?;
    let images = compose_existing_pages(Some(&planned), budget)?;
    let mut plan = WholeFileImagePlan::from_existing_pages(images, budget)?;
    planned.append_pages(&mut plan, budget)?;
    Ok(plan)
}

fn compose_existing_pages(
    create: Option<&PlannedCreate<'_>>,
    budget: &mut ResourceBudget,
) -> Result<[PageImage; EMPTY_DATABASE_PAGE_COUNT as usize], BootstrapComposeError> {
    let object_count = if create.is_some() { 9 } else { 8 };
    let ace_count = if create.is_some() { 18 } else { 16 };
    let page_count = create.map_or(EMPTY_DATABASE_PAGE_COUNT, PlannedCreate::page_count);
    Ok([
        header_page(create.is_some(), budget)?,
        global_map_page(page_count, budget)?,
        msys_objects_definition(object_count, budget)?,
        msys_aces_definition(ace_count, object_count, budget)?,
        msys_queries_definition(budget)?,
        msys_relationships_definition(budget)?,
        objects_map_page(create, budget)?,
        single_map_page(&[], budget)?,
        single_map_page(&[OBJECTS_PARENT_NAME_ROOT], budget)?,
        objects_parent_name_index(create, budget)?,
        single_map_page(&[OBJECTS_ID_ROOT], budget)?,
        objects_id_index(create, budget)?,
        shared_map_page(budget)?,
        aces_index(create, budget)?,
        empty_index_page(MSYS_QUERIES_ROOT, budget)?,
        empty_index_page(MSYS_RELATIONSHIPS_ROOT, budget)?,
        empty_index_page(MSYS_RELATIONSHIPS_ROOT, budget)?,
        empty_index_page(MSYS_RELATIONSHIPS_ROOT, budget)?,
        objects_data_page(create, budget)?,
        aces_data_page(create, budget)?,
    ])
}

fn header_page(
    created: bool,
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
    let mut image = PageImage::new(PageKind::DatabaseDefinition);
    image.write_at(PageOffset::new(1), &[1], budget)?;
    image.write_at(PageOffset::new(4), b"Standard Jet DB", budget)?;
    image.write_at(PageOffset::new(24), &DATABASE_HEADER_FIXED_OPAQUE, budget)?;
    let mut commit_state = [0_u8; 512];
    commit_state[0] = 1;
    for offset in (1..commit_state.len()).step_by(2) {
        commit_state[offset] = 1;
    }
    // EXP-0087: byte 1538 advanced by 2 per create from 0; no rule beyond that.
    commit_state[2] = if created { 2 } else { 0 };
    image.write_at(PageOffset::new(1536), &commit_state, budget)?;
    Ok(image)
}

fn global_map(
    used_pages: u64,
    budget: &mut ResourceBudget,
) -> Result<InlineUsageMapEncoder, BootstrapComposeError> {
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
) -> Result<PageImage, BootstrapComposeError> {
    let map = global_map(used_pages, budget)?;
    let mut row = [0_u8; 133];
    map.encode_into(&mut row, budget)?;
    data_page(GLOBAL_MAP_PAGE, &[&row, &[0; 133]], budget)
}

fn inline_map_row(
    pages: &[u64],
    budget: &mut ResourceBudget,
) -> Result<[u8; 133], BootstrapComposeError> {
    let mut map =
        InlineUsageMapEncoder::new(PageNumber::new(0), ByteCount::new(MAP_BITMAP_BYTES), budget)?;
    for &page in pages {
        map.set_page(PageNumber::new(page))?;
    }
    let mut row = [0_u8; 133];
    map.encode_into(&mut row, budget)?;
    Ok(row)
}

fn single_map_page(
    pages: &[u64],
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
    let row = inline_map_row(pages, budget)?;
    data_page(HEADER_PAGE, &[&row], budget)
}

fn objects_map_page(
    create: Option<&PlannedCreate<'_>>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
    let owned = inline_map_row(&[MSYS_OBJECTS_DATA_PAGE], budget)?;
    let available = inline_map_row(&[MSYS_OBJECTS_DATA_PAGE], budget)?;
    let empty = inline_map_row(&[], budget)?;
    let long_value_pages = create.map(PlannedCreate::long_value_page);
    let lvprop = inline_map_row(long_value_pages.as_slice(), budget)?;
    let rows: [&[u8]; 15] = [
        &owned, &available, &empty, &empty, &empty, &empty, &empty, &empty, &empty, &empty,
        &lvprop, &lvprop, &empty, &empty, &empty,
    ];
    data_page(HEADER_PAGE, &rows, budget)
}

fn shared_map_page(budget: &mut ResourceBudget) -> Result<PageImage, BootstrapComposeError> {
    let ace_owned = inline_map_row(&[MSYS_ACES_DATA_PAGE], budget)?;
    let ace_available = inline_map_row(&[MSYS_ACES_DATA_PAGE], budget)?;
    let ace_index = inline_map_row(&[ACES_OBJECT_ID_ROOT], budget)?;
    let empty = inline_map_row(&[], budget)?;
    let query_index = inline_map_row(&[QUERIES_INDEX_ROOT], budget)?;
    let relation_name = inline_map_row(&[RELATIONSHIPS_NAME_ROOT], budget)?;
    let relation_object = inline_map_row(&[RELATIONSHIPS_OBJECT_ROOT], budget)?;
    let relation_referenced = inline_map_row(&[RELATIONSHIPS_REFERENCED_ROOT], budget)?;
    let rows: [&[u8]; 13] = [
        &ace_owned,
        &ace_available,
        &ace_index,
        &empty,
        &empty,
        &empty,
        &empty,
        &query_index,
        &empty,
        &empty,
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
) -> Result<PageImage, BootstrapComposeError> {
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
) -> Result<PageImage, BootstrapComposeError> {
    let mut bytes = [0_u8; PAGE_BYTES];
    encode_table_definition(spec, &mut bytes, budget)?;
    Ok(PageImage::from_bytes(bytes))
}

#[path = "bootstrap_system_definitions.rs"]
mod definitions;
use definitions::{
    msys_aces_definition, msys_objects_definition, msys_queries_definition,
    msys_relationships_definition,
};

#[path = "bootstrap_table_create.rs"]
mod table_create;
use table_create::PlannedCreate;
pub(crate) use table_create::TableCreate;

const OBJECT_LAYOUT: [RowColumnLayout; 17] = [
    fixed(ColumnPhysicalType::Long, 0, 4),
    fixed(ColumnPhysicalType::Long, 4, 4),
    variable(ColumnPhysicalType::Text, 0, 255),
    fixed(ColumnPhysicalType::Integer, 8, 2),
    fixed(ColumnPhysicalType::DateTime, 10, 8),
    fixed(ColumnPhysicalType::DateTime, 18, 8),
    variable(ColumnPhysicalType::Binary, 1, 255),
    fixed(ColumnPhysicalType::Long, 26, 4),
    variable(ColumnPhysicalType::Memo, 2, 0),
    variable(ColumnPhysicalType::Memo, 3, 0),
    variable(ColumnPhysicalType::Text, 4, 255),
    variable(ColumnPhysicalType::Binary, 5, 255),
    variable(ColumnPhysicalType::LongBinary, 6, 0),
    variable(ColumnPhysicalType::LongBinary, 7, 0),
    variable(ColumnPhysicalType::LongBinary, 8, 0),
    variable(ColumnPhysicalType::LongBinary, 9, 0),
    variable(ColumnPhysicalType::LongBinary, 10, 0),
];
const ACE_LAYOUT: [RowColumnLayout; 4] = [
    fixed(ColumnPhysicalType::Long, 0, 4),
    variable(ColumnPhysicalType::Binary, 0, 255),
    fixed(ColumnPhysicalType::Long, 4, 4),
    fixed(ColumnPhysicalType::Boolean, 8, 1),
];
const fn fixed(kind: ColumnPhysicalType, offset: u16, size: u16) -> RowColumnLayout {
    RowColumnLayout::new(kind, ColumnStorageClass::Fixed { offset }, size)
}
const fn variable(kind: ColumnPhysicalType, index: u16, size: u16) -> RowColumnLayout {
    RowColumnLayout::new(kind, ColumnStorageClass::Variable { index }, size)
}

#[derive(Clone, Copy)]
struct CatalogSeed<'a> {
    id: i32,
    parent: i32,
    name: &'a [u8],
    kind: i16,
    owner: &'static [u8],
    flags: i32,
}
// EXP-0058: system objects carry flags `0x80000000`.
const SYSTEM_FLAGS: i32 = i32::MIN;
const CATALOG_SEEDS: [CatalogSeed<'static>; 8] = [
    CatalogSeed {
        id: TABLES_ID,
        parent: ROOT_CONTAINER_ID,
        name: b"Tables",
        kind: 3,
        owner: CATALOG_OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    CatalogSeed {
        id: DATABASES_ID,
        parent: ROOT_CONTAINER_ID,
        name: b"Databases",
        kind: 3,
        owner: CATALOG_OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    CatalogSeed {
        id: RELATIONSHIPS_ID,
        parent: ROOT_CONTAINER_ID,
        name: b"Relationships",
        kind: 3,
        owner: CATALOG_OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    CatalogSeed {
        id: MSYS_DB_ID,
        parent: DATABASES_ID,
        name: b"MSysDb",
        kind: 2,
        owner: CATALOG_OWNER_0301,
        flags: SYSTEM_FLAGS,
    },
    CatalogSeed {
        id: MSYS_OBJECTS_ROOT as i32,
        parent: TABLES_ID,
        name: b"MSysObjects",
        kind: 1,
        owner: CATALOG_OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    CatalogSeed {
        id: MSYS_ACES_ROOT as i32,
        parent: TABLES_ID,
        name: b"MSysACEs",
        kind: 1,
        owner: CATALOG_OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    CatalogSeed {
        id: MSYS_QUERIES_ROOT as i32,
        parent: TABLES_ID,
        name: b"MSysQueries",
        kind: 1,
        owner: CATALOG_OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    CatalogSeed {
        id: MSYS_RELATIONSHIPS_ROOT as i32,
        parent: TABLES_ID,
        name: b"MSysRelationships",
        kind: 1,
        owner: CATALOG_OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
];

/// Returns the catalog rows the composed image holds, in stored row order.
fn catalog_seeds<'a>(create: Option<&PlannedCreate<'a>>) -> impl Iterator<Item = CatalogSeed<'a>> {
    CATALOG_SEEDS
        .into_iter()
        .chain(create.map(PlannedCreate::catalog_seed))
}

fn objects_data_page(
    create: Option<&PlannedCreate<'_>>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
    let mut builder = DataPageBuilder::new(PageNumber::new(MSYS_OBJECTS_ROOT), budget)?;
    let mut row = [0_u8; PAGE_BYTES];
    let created_id = create.map(PlannedCreate::object_id);
    for seed in catalog_seeds(create) {
        let lvprop = match create {
            Some(planned) if Some(seed.id) == created_id => Some(planned.long_value_header()?),
            _ => None,
        };
        let length = encode_catalog_row(seed, lvprop, &mut row, budget)?;
        builder.append_row(&row[..length], budget)?;
    }
    finish_data_builder(builder, budget)
}

fn encode_catalog_row(
    seed: CatalogSeed<'_>,
    lvprop: Option<[u8; 12]>,
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<usize, BootstrapComposeError> {
    let lvprop_value = lvprop
        .as_ref()
        .map_or(RowValue::Null, |bytes| RowValue::LongValue(bytes));
    let values = [
        RowValue::Long(seed.id),
        RowValue::Long(seed.parent),
        RowValue::Text(seed.name),
        RowValue::Integer(seed.kind),
        RowValue::DateTime { days: 0.0 },
        RowValue::DateTime { days: 0.0 },
        RowValue::Binary(seed.owner),
        RowValue::Long(seed.flags),
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        lvprop_value,
        RowValue::Null,
        RowValue::Null,
    ];
    Ok(encode_row(&OBJECT_LAYOUT, &values, output, budget)?.get() as usize)
}

#[derive(Clone, Copy)]
struct AceSeed {
    object: i32,
    sid: &'static [u8],
    acm: i32,
    inheritable: bool,
}
const ACE_SEEDS: [AceSeed; 16] = [
    ace(2, b"\x03\x01", 393216, false),
    ace(3, b"\x03\x01", 393216, false),
    ace(4, b"\x03\x01", 393216, false),
    ace(5, b"\x03\x01", 917504, false),
    ace(TABLES_ID, b"\x02\x04", 983294, true),
    ace(TABLES_ID, b"\x03\x01", 393217, false),
    ace(RELATIONSHIPS_ID, b"\x02\x04", 983294, true),
    ace(RELATIONSHIPS_ID, b"\x03\x01", 393217, false),
    ace(DATABASES_ID, b"\x03\x01", 393216, false),
    ace(MSYS_DB_ID, b"\x03\x01", 393230, false),
    ace(MSYS_DB_ID, b"\x02\x01", 14, false),
    ace(4, b"\x02\x01", 20, false),
    ace(5, b"\x02\x01", 20, false),
    ace(2, b"\x02\x01", 20, false),
    ace(TABLES_ID, b"\x02\x01", 1048319, true),
    ace(RELATIONSHIPS_ID, b"\x02\x01", 1048575, true),
];
const fn ace(object: i32, sid: &'static [u8], acm: i32, inheritable: bool) -> AceSeed {
    AceSeed {
        object,
        sid,
        acm,
        inheritable,
    }
}

/// Returns the access-control rows the composed image holds, in stored order.
fn ace_seeds(create: Option<&PlannedCreate<'_>>) -> impl Iterator<Item = AceSeed> {
    ACE_SEEDS
        .into_iter()
        .chain(create.map(PlannedCreate::ace_seeds).into_iter().flatten())
}

fn aces_data_page(
    create: Option<&PlannedCreate<'_>>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
    let mut builder = DataPageBuilder::new(PageNumber::new(MSYS_ACES_ROOT), budget)?;
    let mut row = [0_u8; 64];
    for seed in ace_seeds(create) {
        let values = [
            RowValue::Long(seed.object),
            RowValue::Binary(seed.sid),
            RowValue::Long(seed.acm),
            RowValue::Boolean(seed.inheritable),
        ];
        let length = encode_row(&ACE_LAYOUT, &values, &mut row, budget)?.get() as usize;
        builder.append_row(&row[..length], budget)?;
    }
    finish_data_builder(builder, budget)
}

fn finish_data_builder(
    builder: DataPageBuilder,
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
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
    key: [u8; INDEX_KEY_CAPACITY],
    len: usize,
    row: u8,
}
impl OwnedIndexEntry {
    const EMPTY: Self = Self {
        key: [0; INDEX_KEY_CAPACITY],
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
    fn name(parent: i32, name: &[u8], row: u8) -> Result<Self, BootstrapComposeError> {
        let mut entry = Self::EMPTY;
        entry.len = encode_catalog_name_key(parent, name, &mut entry.key)?;
        entry.row = row;
        Ok(entry)
    }
}

fn objects_parent_name_index(
    create: Option<&PlannedCreate<'_>>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
    let mut entries = [OwnedIndexEntry::EMPTY; 9];
    let count = if create.is_some() { 9 } else { 8 };
    for (row, seed) in catalog_seeds(create).enumerate() {
        entries[row] = OwnedIndexEntry::name(seed.parent, seed.name, row as u8)?;
    }
    sort_index_entries(&mut entries[..count]);
    index_page(
        MSYS_OBJECTS_ROOT,
        MSYS_OBJECTS_DATA_PAGE,
        &entries[..count],
        budget,
    )
}
fn objects_id_index(
    create: Option<&PlannedCreate<'_>>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
    let mut entries = [OwnedIndexEntry::EMPTY; 9];
    let count = if create.is_some() { 9 } else { 8 };
    for (row, seed) in catalog_seeds(create).enumerate() {
        entries[row] = OwnedIndexEntry::long(seed.id, row as u8);
    }
    sort_index_entries(&mut entries[..count]);
    index_page(
        MSYS_OBJECTS_ROOT,
        MSYS_OBJECTS_DATA_PAGE,
        &entries[..count],
        budget,
    )
}
fn aces_index(
    create: Option<&PlannedCreate<'_>>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
    let mut entries = [OwnedIndexEntry::EMPTY; 18];
    let mut count = 0;
    for (row, seed) in ace_seeds(create).enumerate() {
        entries[row] = OwnedIndexEntry::long(seed.object, row as u8);
        count = row + 1;
    }
    sort_index_entries(&mut entries[..count]);
    index_page(
        MSYS_ACES_ROOT,
        MSYS_ACES_DATA_PAGE,
        &entries[..count],
        budget,
    )
}

fn sort_index_entries(entries: &mut [OwnedIndexEntry]) {
    entries.sort_unstable_by(|left, right| {
        left.key[..left.len]
            .cmp(&right.key[..right.len])
            .then(left.row.cmp(&right.row))
    });
}

fn empty_index_page(
    owner: u64,
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
    index_page(owner, 0, &[], budget)
}

fn index_page(
    owner: u64,
    row_page: u64,
    entries: &[OwnedIndexEntry],
    budget: &mut ResourceBudget,
) -> Result<PageImage, BootstrapComposeError> {
    let needed = entries
        .iter()
        .try_fold(0_usize, |total, entry| total.checked_add(entry.len + 4))
        .ok_or(Error::Arithmetic {
            operation: "size bootstrap index entries",
        })?;
    if needed > INDEX_ENTRY_AREA_LEN {
        return Err(BootstrapComposeError::IndexPageFull {
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
#[path = "bootstrap_composer_tests.rs"]
mod tests;
