use super::*;

type PlanResult = Result<(), TableSchemaPlanError>;

const ID: PlannedColumn<'static> = PlannedColumn {
    name: b"Id",
    physical_type: ColumnPhysicalType::Long,
    storage: ColumnStorageKind::Fixed,
    size: 4,
};
const LABEL: PlannedColumn<'static> = PlannedColumn {
    name: b"Label",
    physical_type: ColumnPhysicalType::Text,
    storage: ColumnStorageKind::Variable,
    size: 30,
};
const NAME: PlannedColumn<'static> = PlannedColumn {
    name: b"Name",
    physical_type: ColumnPhysicalType::Text,
    storage: ColumnStorageKind::Variable,
    size: 50,
};
const NOTE: PlannedColumn<'static> = PlannedColumn {
    name: b"Note",
    physical_type: ColumnPhysicalType::Memo,
    storage: ColumnStorageKind::Variable,
    size: 0,
};

fn spec<'a>(
    name: &'a [u8],
    columns: &'a [PlannedColumn<'a>],
    indexes: &'a [PlannedIndex<'a>],
) -> TableSchemaSpec<'a> {
    TableSchemaSpec {
        name,
        columns,
        indexes,
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
        fields: &[0],
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
        fields: &[0],
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
        Err(TableSchemaPlanError::TableName(
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
        Err(TableSchemaPlanError::TableName(
            CatalogNameKeyError::EmptyName
        ))
    );
}

#[test]
fn a_table_without_columns_is_refused() {
    assert_eq!(
        plan_table_schema(&spec(b"Empty", &[], &[]), 20),
        Err(TableSchemaPlanError::NoColumns)
    );
}

#[test]
fn a_column_count_one_above_the_limit_is_refused() {
    let columns = vec![ID; MAX_COLUMNS + 1];
    assert_eq!(
        plan_table_schema(&spec(b"Wide", &columns, &[]), 20),
        Err(TableSchemaPlanError::TooManyColumns {
            count: MAX_COLUMNS + 1,
            limit: MAX_COLUMNS,
        })
    );
    // The duplicate names inside a limit-sized spec must still be caught.
    let at_limit = vec![ID; MAX_COLUMNS];
    assert_eq!(
        plan_table_schema(&spec(b"Wide", &at_limit, &[]), 20),
        Err(TableSchemaPlanError::DuplicateColumnName {
            first: 0,
            second: 1,
        })
    );
}

#[test]
fn a_repeated_column_name_is_refused() {
    let columns = [ID, LABEL, ID];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &[]), 20),
        Err(TableSchemaPlanError::DuplicateColumnName {
            first: 0,
            second: 2,
        })
    );
}

#[test]
fn an_empty_column_name_is_refused() {
    let columns = [PlannedColumn { name: b"", ..ID }];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &[]), 20),
        Err(TableSchemaPlanError::EmptyColumnName { ordinal: 0 })
    );
}

#[test]
fn more_indexes_than_any_create_carried_are_refused() {
    // EXP-0087 observed only zero or one index per create, so a two-index
    // ordering has no evidence behind it.
    let columns = [ID, LABEL];
    let indexes = [
        PlannedIndex {
            name: b"ById",
            fields: &[0],
            kind: PlannedIndexKind::Primary,
        },
        PlannedIndex {
            name: b"ByLabel",
            fields: &[1],
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
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20),
        Err(TableSchemaPlanError::IndexWithoutFields { index: 0 })
    );
}

#[test]
fn an_index_naming_an_undeclared_column_is_refused() {
    let columns = [ID];
    let indexes = [PlannedIndex {
        name: b"ById",
        fields: &[1],
        kind: PlannedIndexKind::Ordinary,
    }];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20),
        Err(TableSchemaPlanError::IndexFieldOutOfRange {
            index: 0,
            column: 1,
        })
    );
}

#[test]
fn an_index_naming_one_column_twice_is_refused() {
    let columns = [ID, LABEL];
    let indexes = [PlannedIndex {
        name: b"ById",
        fields: &[0, 1, 0],
        kind: PlannedIndexKind::Ordinary,
    }];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20),
        Err(TableSchemaPlanError::DuplicateIndexField {
            index: 0,
            column: 0,
        })
    );
}

#[test]
fn an_index_field_count_one_above_the_limit_is_refused() {
    let columns = vec![ID; MAX_INDEX_FIELDS + 1];
    let fields = (0..=MAX_INDEX_FIELDS as u16).collect::<Vec<_>>();
    let indexes = [PlannedIndex {
        name: b"Wide",
        fields: &fields,
        kind: PlannedIndexKind::Ordinary,
    }];
    // Duplicate column names would mask the field-count check, so name them apart.
    let named = columns
        .iter()
        .enumerate()
        .map(|(ordinal, column)| PlannedColumn {
            name: COLUMN_NAMES[ordinal],
            ..*column
        })
        .collect::<Vec<_>>();
    assert_eq!(
        plan_table_schema(&spec(b"Wide", &named, &indexes), 20),
        Err(TableSchemaPlanError::TooManyIndexFields {
            index: 0,
            count: MAX_INDEX_FIELDS + 1,
            limit: MAX_INDEX_FIELDS,
        })
    );
}

const COLUMN_NAMES: [&[u8]; MAX_INDEX_FIELDS + 1] = [
    b"C0", b"C1", b"C2", b"C3", b"C4", b"C5", b"C6", b"C7", b"C8", b"C9", b"CA",
];

#[test]
fn an_empty_index_name_is_refused() {
    let columns = [ID];
    let indexes = [PlannedIndex {
        name: b"",
        fields: &[0],
        kind: PlannedIndexKind::Ordinary,
    }];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20),
        Err(TableSchemaPlanError::EmptyIndexName { index: 0 })
    );
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
fn the_highest_representable_first_page_is_accepted() -> PlanResult {
    let plan = plan_table_schema(&spec(b"Beta", &[ID], &[]), i32::MAX as u64)?;
    assert_eq!(plan.object_id(), i32::MAX);
    Ok(())
}
