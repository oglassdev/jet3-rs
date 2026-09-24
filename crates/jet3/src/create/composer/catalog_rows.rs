//! Existing EXP-0073/0087 system rows and EXP-0208 catalog properties.

use super::*;
use crate::PAGE_BYTES;

pub(super) const OBJECT_LAYOUT: [RowColumnLayout; 17] = [
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
pub(super) const ACE_LAYOUT: [RowColumnLayout; 4] = [
    fixed(ColumnPhysicalType::Long, 0, 4),
    variable(ColumnPhysicalType::Binary, 0, 255),
    fixed(ColumnPhysicalType::Long, 4, 4),
    fixed(ColumnPhysicalType::Boolean, 8, 1),
];
pub(super) const fn fixed(kind: ColumnPhysicalType, offset: u16, size: u16) -> RowColumnLayout {
    RowColumnLayout::new(kind, ColumnStorageClass::Fixed { offset }, size)
}
pub(super) const fn variable(kind: ColumnPhysicalType, index: u16, size: u16) -> RowColumnLayout {
    RowColumnLayout::new(kind, ColumnStorageClass::Variable { index }, size)
}

#[derive(Clone, Copy)]
pub(super) struct CatalogSeed<'a> {
    pub(super) id: i32,
    pub(super) parent: i32,
    pub(super) name: &'a [u8],
    pub(super) kind: i16,
    pub(super) owner: &'static [u8],
    pub(super) flags: i32,
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
pub(super) fn catalog_seeds<'a>(
    creates: &'a [PlannedCreate<'a>],
    extra: Option<CatalogSeed<'a>>,
) -> impl Iterator<Item = CatalogSeed<'a>> + 'a {
    CATALOG_SEEDS
        .into_iter()
        .chain(creates.iter().map(PlannedCreate::catalog_seed))
        .chain(extra)
}

/// Builds catalog rows with optional EXP-0208 Memo properties.
/// Other rows retain the EXP-0091 null-LvProp form.
pub(super) fn objects_data_page(
    creates: &[PlannedCreate<'_>],
    extra: Option<CatalogSeed<'_>>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let mut builder = DataPageBuilder::new(PageNumber::new(MSYS_OBJECTS_ROOT), budget)?;
    let mut row = [0_u8; PAGE_BYTES];
    for seed in catalog_seeds(creates, extra) {
        budget.charge_work_units(creates.len() as u64)?;
        let header = creates
            .iter()
            .find(|create| create.catalog_seed().id == seed.id)
            .map(PlannedCreate::property_header)
            .transpose()?
            .flatten();
        let length = encode_catalog_row(seed, header.as_ref(), &mut row, budget)?;
        builder.append_row(&row[..length], budget)?;
    }
    finish_data_builder(builder, budget)
}

pub(super) fn encode_catalog_row(
    seed: CatalogSeed<'_>,
    property: Option<&[u8; 12]>,
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<usize, ComposeError> {
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
        property.map_or(RowValue::Null, |header| RowValue::LongValue(header)),
        RowValue::Null,
        RowValue::Null,
    ];
    Ok(encode_row(&OBJECT_LAYOUT, &values, output, budget)?.get() as usize)
}

#[derive(Clone, Copy)]
pub(super) struct AceSeed {
    pub(super) object: i32,
    pub(super) sid: &'static [u8],
    pub(super) acm: i32,
    pub(super) inheritable: bool,
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
pub(super) const fn ace(object: i32, sid: &'static [u8], acm: i32, inheritable: bool) -> AceSeed {
    AceSeed {
        object,
        sid,
        acm,
        inheritable,
    }
}

/// Returns the access-control rows the composed image holds, in stored order.
pub(super) fn ace_seeds<'a>(
    creates: &'a [PlannedCreate<'a>],
    extra: &'a [AceSeed],
) -> impl Iterator<Item = AceSeed> + 'a {
    ACE_SEEDS
        .into_iter()
        .chain(creates.iter().flat_map(PlannedCreate::ace_seeds))
        .chain(extra.iter().copied())
}

pub(super) fn aces_data_page(
    creates: &[PlannedCreate<'_>],
    extra: &[AceSeed],
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let mut builder = DataPageBuilder::new(PageNumber::new(MSYS_ACES_ROOT), budget)?;
    let mut row = [0_u8; 64];
    for seed in ace_seeds(creates, extra) {
        let length = encode_ace_row(seed, &mut row, budget)?;
        builder.append_row(&row[..length], budget)?;
    }
    finish_data_builder(builder, budget)
}

pub(super) fn encode_ace_row(
    seed: AceSeed,
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<usize, ComposeError> {
    let values = [
        RowValue::Long(seed.object),
        RowValue::Binary(seed.sid),
        RowValue::Long(seed.acm),
        RowValue::Boolean(seed.inheritable),
    ];
    Ok(encode_row(&ACE_LAYOUT, &values, output, budget)?.get() as usize)
}
