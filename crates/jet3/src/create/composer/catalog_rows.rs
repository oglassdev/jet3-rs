//! Existing EXP-0073/0087 system rows and EXP-0208 catalog properties.

use super::*;
use crate::{PAGE_BYTES, catalog::system_rows::values};

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

const CATALOG_SEEDS: [ObjectRow<'static>; 8] = [
    ObjectRow {
        id: TABLES_ID,
        parent: ROOT_CONTAINER_ID,
        name: b"Tables",
        kind: 3,
        owner: OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    ObjectRow {
        id: DATABASES_ID,
        parent: ROOT_CONTAINER_ID,
        name: b"Databases",
        kind: 3,
        owner: OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    ObjectRow {
        id: RELATIONSHIPS_ID,
        parent: ROOT_CONTAINER_ID,
        name: b"Relationships",
        kind: 3,
        owner: OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    ObjectRow {
        id: MSYS_DB_ID,
        parent: DATABASES_ID,
        name: b"MSysDb",
        kind: 2,
        owner: OWNER_0301,
        flags: SYSTEM_FLAGS,
    },
    ObjectRow {
        id: MSYS_OBJECTS_ROOT as i32,
        parent: TABLES_ID,
        name: b"MSysObjects",
        kind: 1,
        owner: OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    ObjectRow {
        id: MSYS_ACES_ROOT as i32,
        parent: TABLES_ID,
        name: b"MSysACEs",
        kind: 1,
        owner: OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    ObjectRow {
        id: MSYS_QUERIES_ROOT as i32,
        parent: TABLES_ID,
        name: b"MSysQueries",
        kind: 1,
        owner: OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
    ObjectRow {
        id: MSYS_RELATIONSHIPS_ROOT as i32,
        parent: TABLES_ID,
        name: b"MSysRelationships",
        kind: 1,
        owner: OWNER_0203,
        flags: SYSTEM_FLAGS,
    },
];

/// Returns the catalog rows the composed image holds, in stored row order.
pub(super) fn catalog_seeds<'a>(
    creates: &'a [PlannedCreate<'a>],
    extra: Option<ObjectRow<'a>>,
) -> impl Iterator<Item = ObjectRow<'a>> + 'a {
    CATALOG_SEEDS
        .into_iter()
        .chain(creates.iter().map(PlannedCreate::catalog_seed))
        .chain(extra)
}

/// Builds catalog rows with optional EXP-0208 Memo properties.
/// Other rows retain the EXP-0091 null-LvProp form.
pub(super) fn objects_data_page(
    creates: &[PlannedCreate<'_>],
    extra: Option<ObjectRow<'_>>,
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
    seed: ObjectRow<'_>,
    property: Option<&[u8; 12]>,
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<usize, ComposeError> {
    let [id, parent, name, kind, created, updated, owner, flags] = values(seed.columns());
    let values = [
        id,
        parent,
        name,
        kind,
        created,
        updated,
        owner,
        flags,
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

const ACE_SEEDS: [AceRow; 16] = [
    AceRow::new(2, OWNER_0301, 393216, false),
    AceRow::new(3, OWNER_0301, 393216, false),
    AceRow::new(4, OWNER_0301, 393216, false),
    AceRow::new(5, OWNER_0301, 917504, false),
    AceRow::new(TABLES_ID, SID_0204, 983294, true),
    AceRow::new(TABLES_ID, OWNER_0301, 393217, false),
    AceRow::new(RELATIONSHIPS_ID, SID_0204, 983294, true),
    AceRow::new(RELATIONSHIPS_ID, OWNER_0301, 393217, false),
    AceRow::new(DATABASES_ID, OWNER_0301, 393216, false),
    AceRow::new(MSYS_DB_ID, OWNER_0301, 393230, false),
    AceRow::new(MSYS_DB_ID, SID_0201, 14, false),
    AceRow::new(4, SID_0201, 20, false),
    AceRow::new(5, SID_0201, 20, false),
    AceRow::new(2, SID_0201, 20, false),
    AceRow::new(TABLES_ID, SID_0201, 1048319, true),
    AceRow::new(RELATIONSHIPS_ID, SID_0201, 1048575, true),
];

/// Returns the access-control rows the composed image holds, in stored order.
pub(super) fn ace_seeds<'a>(
    creates: &'a [PlannedCreate<'a>],
    extra: &'a [AceRow],
) -> impl Iterator<Item = AceRow> + 'a {
    ACE_SEEDS
        .into_iter()
        .chain(creates.iter().flat_map(PlannedCreate::ace_seeds))
        .chain(extra.iter().copied())
}

pub(super) fn aces_data_page(
    creates: &[PlannedCreate<'_>],
    extra: &[AceRow],
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
    seed: AceRow,
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<usize, ComposeError> {
    Ok(encode_row(&ACE_LAYOUT, &values(seed.columns()), output, budget)?.get() as usize)
}
