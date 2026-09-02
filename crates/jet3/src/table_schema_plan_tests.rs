use super::*;

use crate::physical_index_definition::KEY_SLOT_COUNT;
use crate::{ColumnPhysicalType, ColumnStorageKind, IndexDirection};

type PlanResult = Result<(), TableSchemaPlanError>;

const ID: ColumnSpec<'static> =
    ColumnSpec::new(b"Id", ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4);
const LABEL: ColumnSpec<'static> = ColumnSpec::new(
    b"Label",
    ColumnPhysicalType::Text,
    ColumnStorageKind::Variable,
    30,
);
const NAME: ColumnSpec<'static> = ColumnSpec::new(
    b"Name",
    ColumnPhysicalType::Text,
    ColumnStorageKind::Variable,
    50,
);
const NOTE: ColumnSpec<'static> = ColumnSpec::new(
    b"Note",
    ColumnPhysicalType::Memo,
    ColumnStorageKind::Variable,
    0,
);

const fn key(column: u16) -> IndexFieldSpec {
    IndexFieldSpec {
        column,
        direction: IndexDirection::Ascending,
    }
}

fn spec<'a>(
    name: &'a [u8],
    columns: &'a [ColumnSpec<'a>],
    indexes: &'a [PlannedIndex<'a>],
) -> TableSchemaSpec<'a> {
    TableSchemaSpec {
        name,
        columns,
        indexes,
    }
}

/// Returns the definition error planning `spec` produced, if it produced one.
fn definition_error(spec: &TableSchemaSpec<'_>) -> Option<TableDefinitionWriteError> {
    match plan_table_schema(spec, 20) {
        Err(TableSchemaPlanError::Definition(error)) => Some(error),
        _ => None,
    }
}

#[test]
fn a_table_without_an_index_appends_a_definition_root_and_a_map_page() -> PlanResult {
    // EXP-0087's Beta create (Long Id, Text(50) Name, Memo Note): two
    // appended pages, Id equal to the root page.
    let columns = [ID, NAME, NOTE];
    let plan = plan_table_schema(&spec(b"Beta", &columns, &[]), 23)?;
    assert_eq!(plan.object_id(), 23);
    assert_eq!(plan.definition_root(), PageNumber::new(23));
    assert_eq!(plan.map_page(), PageNumber::new(24));
    assert_eq!(plan.index_root(), None);
    assert_eq!(plan.appended_page_count(), 2);
    Ok(())
}

#[test]
fn an_indexed_table_appends_its_index_root_after_the_map_page() -> PlanResult {
    // EXP-0087's Delta create: the index root follows the map-rows page.
    let columns = [LABEL];
    let indexes = [PlannedIndex {
        name: b"ByLabel",
        fields: &[key(0)],
        kind: PlannedIndexKind::Ordinary,
    }];
    let plan = plan_table_schema(&spec(b"Delta", &columns, &indexes), 28)?;
    assert_eq!(plan.object_id(), 28);
    assert_eq!(plan.definition_root(), PageNumber::new(28));
    assert_eq!(plan.map_page(), PageNumber::new(29));
    assert_eq!(plan.index_root(), Some(PageNumber::new(30)));
    assert_eq!(plan.appended_page_count(), 3);
    Ok(())
}

#[test]
fn a_primary_index_plans_the_same_pages_as_an_ordinary_one() -> PlanResult {
    // EXP-0087's Gamma create carried a primary index and appended three pages.
    let columns = [ID];
    let indexes = [PlannedIndex {
        name: b"PrimaryKey",
        fields: &[key(0)],
        kind: PlannedIndexKind::Primary,
    }];
    let plan = plan_table_schema(&spec(b"Gamma", &columns, &indexes), 25)?;
    assert_eq!(plan.object_id(), 25);
    assert_eq!(plan.index_root(), Some(PageNumber::new(27)));
    Ok(())
}

#[test]
fn a_table_name_byte_without_an_established_weight_is_refused() {
    let columns = [ID];
    assert_eq!(
        plan_table_schema(&spec(b"Caf\xe9", &columns, &[]), 20),
        Err(TableSchemaPlanError::TableNameKey(
            CatalogNameKeyError::UnmappedNameByte {
                position: 3,
                byte: 0xe9,
            }
        ))
    );
}

#[test]
fn an_empty_table_name_is_refused() {
    let columns = [ID];
    assert_eq!(
        plan_table_schema(&spec(b"", &columns, &[]), 20),
        Err(TableSchemaPlanError::TableNameKey(
            CatalogNameKeyError::EmptyName
        ))
    );
}

#[test]
fn a_table_name_too_long_for_a_catalog_row_is_refused() {
    // The MSysObjects row's one-byte name-end offset bounds the name; the
    // longest writable name must still plan.
    let columns = [ID];
    let longest = vec![b'A'; 224];
    assert!(plan_table_schema(&spec(&longest, &columns, &[]), 20).is_ok());
    let overlong = vec![b'A'; 225];
    assert!(matches!(
        plan_table_schema(&spec(&overlong, &columns, &[]), 20),
        Err(TableSchemaPlanError::TableNameRow(
            CatalogRecordWriteError::NameTooLong { length: 225, .. }
        ))
    ));
}

#[test]
fn a_column_name_too_long_for_the_definition_is_refused() {
    let overlong = vec![b'A'; 256];
    let columns = [ColumnSpec::new(
        &overlong,
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        4,
    )];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &[])),
        Some(TableDefinitionWriteError::NameTooLong {
            role: "column",
            length: 256,
            ..
        })
    ));
}

#[test]
fn an_index_name_too_long_for_the_definition_is_refused() {
    let overlong = vec![b'A'; 256];
    let columns = [ID];
    let indexes = [PlannedIndex {
        name: &overlong,
        fields: &[key(0)],
        kind: PlannedIndexKind::Ordinary,
    }];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &indexes)),
        Some(TableDefinitionWriteError::NameTooLong {
            role: "logical index",
            length: 256,
            ..
        })
    ));
}

#[test]
fn a_column_size_its_type_does_not_accept_is_refused() {
    let columns = [ColumnSpec::new(
        b"Id",
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        1,
    )];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &[])),
        Some(TableDefinitionWriteError::UnsupportedColumnSize {
            ordinal: 0,
            size: 1,
            ..
        })
    ));
}

#[test]
fn a_memo_column_in_fixed_storage_is_refused() {
    let columns = [ColumnSpec::new(
        b"Note",
        ColumnPhysicalType::Memo,
        ColumnStorageKind::Fixed,
        0,
    )];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &[])),
        Some(TableDefinitionWriteError::UnsupportedColumnClass { ordinal: 0, .. })
    ));
}

#[test]
fn a_fixed_area_no_row_slot_could_hold_is_refused() {
    // Each column is a fixed Text(255); enough of them overrun the row slot.
    let names = (0..MANY_COLUMNS)
        .map(|ordinal| format!("C{ordinal:03}").into_bytes())
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| {
            ColumnSpec::new(
                name,
                ColumnPhysicalType::Text,
                ColumnStorageKind::Fixed,
                255,
            )
        })
        .collect::<Vec<_>>();
    assert!(matches!(
        definition_error(&spec(b"Wide", &columns, &[])),
        Some(TableDefinitionWriteError::RowLayoutTooLarge { .. })
    ));
}

const MANY_COLUMNS: usize = 32;

#[test]
fn a_table_without_columns_is_refused() {
    assert_eq!(
        plan_table_schema(&spec(b"Empty", &[], &[]), 20),
        Err(TableSchemaPlanError::NoColumns)
    );
}

#[test]
fn a_column_count_one_above_the_limit_is_refused() {
    let columns = vec![ID; 256];
    assert!(matches!(
        definition_error(&spec(b"Wide", &columns, &[])),
        Some(TableDefinitionWriteError::TooManyColumns { count: 256, .. })
    ));
}

#[test]
fn a_repeated_column_name_is_refused() {
    let columns = [ID, LABEL, ID];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &[])),
        Some(TableDefinitionWriteError::DuplicateName {
            role: "column",
            ordinal: 2,
        })
    ));
}

#[test]
fn an_empty_column_name_is_refused() {
    let columns = [ColumnSpec::new(
        b"",
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        4,
    )];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &[])),
        Some(TableDefinitionWriteError::EmptyName {
            role: "column",
            ordinal: 0,
        })
    ));
}

#[test]
fn more_indexes_than_any_create_carried_are_refused() {
    // EXP-0087 observed only zero or one index per create, so a two-index
    // ordering has no evidence behind it.
    let columns = [ID, LABEL];
    let indexes = [
        PlannedIndex {
            name: b"ById",
            fields: &[key(0)],
            kind: PlannedIndexKind::Primary,
        },
        PlannedIndex {
            name: b"ByLabel",
            fields: &[key(1)],
            kind: PlannedIndexKind::Ordinary,
        },
    ];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20),
        Err(TableSchemaPlanError::UnobservedIndexCount {
            count: 2,
            observed: MAX_OBSERVED_INDEXES,
        })
    );
}

#[test]
fn an_index_naming_no_columns_is_refused() {
    let columns = [ID];
    let indexes = [PlannedIndex {
        name: b"ById",
        fields: &[],
        kind: PlannedIndexKind::Ordinary,
    }];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &indexes)),
        Some(TableDefinitionWriteError::EmptyPhysicalIndex { physical_index: 0 })
    ));
}

#[test]
fn an_index_naming_an_undeclared_column_is_refused() {
    let columns = [ID];
    let indexes = [PlannedIndex {
        name: b"ById",
        fields: &[key(1)],
        kind: PlannedIndexKind::Ordinary,
    }];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &indexes)),
        Some(TableDefinitionWriteError::InvalidKeyColumn { ordinal: 1, .. })
    ));
}

#[test]
fn an_index_over_a_memo_column_is_refused() {
    let columns = [NOTE];
    let indexes = [PlannedIndex {
        name: b"ByNote",
        fields: &[key(0)],
        kind: PlannedIndexKind::Ordinary,
    }];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &indexes)),
        Some(TableDefinitionWriteError::UnsupportedKeyColumn {
            ordinal: 0,
            physical_type: ColumnPhysicalType::Memo,
            ..
        })
    ));
}

#[test]
fn an_index_naming_one_column_twice_is_refused() {
    let columns = [ID, LABEL];
    let indexes = [PlannedIndex {
        name: b"ById",
        fields: &[key(0), key(1), key(0)],
        kind: PlannedIndexKind::Ordinary,
    }];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &indexes)),
        Some(TableDefinitionWriteError::DuplicateKeyColumn { ordinal: 0, .. })
    ));
}

#[test]
fn an_index_field_count_one_above_the_limit_is_refused() {
    let names = (0..=KEY_SLOT_COUNT)
        .map(|ordinal| format!("C{ordinal}").into_bytes())
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4))
        .collect::<Vec<_>>();
    let fields = (0..=KEY_SLOT_COUNT as u16).map(key).collect::<Vec<_>>();
    let indexes = [PlannedIndex {
        name: b"Wide",
        fields: &fields,
        kind: PlannedIndexKind::Ordinary,
    }];
    assert!(matches!(
        definition_error(&spec(b"Wide", &columns, &indexes)),
        Some(TableDefinitionWriteError::TooManyKeyFields { count, .. })
            if count == KEY_SLOT_COUNT + 1
    ));
}

#[test]
fn a_first_page_above_the_signed_id_range_is_refused() {
    // EXP-0087 observed the object Id equal to the definition root page, and
    // MSysObjects.Id is a signed Long, so the run must stay inside that range.
    let columns = [ID];
    let first = i32::MAX as u64 + 1;
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &[]), first),
        Err(TableSchemaPlanError::PageOverflow { first, needed: 2 })
    );
}

#[test]
fn a_map_page_no_usage_map_locator_could_name_is_refused() -> PlanResult {
    // Usage-map locators hold a three-byte page, so the map page bounds the
    // run well below the signed Id range.
    let columns = [ID];
    let highest = MAX_MAP_PAGE - 1;
    let plan = plan_table_schema(&spec(b"Beta", &columns, &[]), highest)?;
    assert_eq!(plan.map_page(), PageNumber::new(MAX_MAP_PAGE));
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &[]), highest + 1),
        Err(TableSchemaPlanError::MapPageNotAddressable {
            page: MAX_MAP_PAGE + 1,
            maximum: MAX_MAP_PAGE,
        })
    );
    Ok(())
}

#[test]
fn a_definition_too_long_for_its_root_page_is_refused() {
    // EXP-0087 saw no create append a continuation page, so where one would
    // land is unestablished. 100 fixed Long columns overrun the root page.
    let names = (0..100)
        .map(|ordinal| format!("Column{ordinal:03}").into_bytes())
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4))
        .collect::<Vec<_>>();
    assert!(matches!(
        plan_table_schema(&spec(b"Wide", &columns, &[]), 20),
        Err(TableSchemaPlanError::DefinitionNeedsContinuation { length, capacity })
            if length > capacity && capacity == DEFINITION_ROOT_CAPACITY
    ));
}

#[test]
fn a_definition_that_exactly_fills_its_root_page_is_accepted() -> PlanResult {
    // The refusal must start one byte above the root page, not below it.
    let names = (0..70)
        .map(|ordinal| format!("Column{ordinal:03}").into_bytes())
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4))
        .collect::<Vec<_>>();
    let length =
        definition_len(&columns, [].into_iter(), 0, 0).map_err(TableSchemaPlanError::Definition)?;
    assert!(length <= DEFINITION_ROOT_CAPACITY);
    plan_table_schema(&spec(b"Wide", &columns, &[]), 20)?;
    Ok(())
}
