use super::*;
use crate::PhysicalIndexFlagsSpec;

use crate::column_definition_writer::nz;
use crate::physical_index_definition::KEY_SLOT_COUNT;
use crate::{
    ColumnPhysicalType, ColumnRef, ColumnType, IndexColumnSpec, IndexDirection,
    LogicalIndexKindSpec,
};

type PlanResult = Result<(), TableSchemaPlanError>;

const ID: ColumnSpec<'static> = ColumnSpec::new(b"Id", ColumnType::Long);
const LABEL: ColumnSpec<'static> = ColumnSpec::new(b"Label", ColumnType::Text { max_len: nz(30) });
const NAME: ColumnSpec<'static> = ColumnSpec::new(b"Name", ColumnType::Text { max_len: nz(50) });
const NOTE: ColumnSpec<'static> = ColumnSpec::new(b"Note", ColumnType::Memo);

const fn key(column: u16) -> IndexColumnSpec<'static> {
    IndexColumnSpec {
        column: ColumnRef::Ordinal(column),
        direction: IndexDirection::Ascending,
    }
}

fn spec<'a>(
    name: &'a [u8],
    columns: &'a [ColumnSpec<'a>],
    indexes: &'a [IndexSpec<'a>],
) -> TableSpec<'a> {
    TableSpec {
        name,
        columns,
        indexes,
    }
}

/// Returns the definition error planning `spec` produced, if it produced one.
fn definition_error(spec: &TableSpec<'_>) -> Option<TableDefinitionWriteError> {
    match plan_table_schema(spec, 20, true) {
        Err(TableSchemaPlanError::Definition(error)) => Some(error),
        _ => None,
    }
}

/// Fixed Long columns whose definition encodes to exactly `target` bytes.
fn names_of_definition_len(target: usize) -> Vec<Vec<u8>> {
    // Header 43 + terminator 2, then 18 record bytes + 1 length byte + 5
    // name bytes per column; the last name absorbs the remainder.
    let count = (target - 45) / 24;
    let remainder = target - 45 - 24 * count;
    let mut names = (0..count)
        .map(|ordinal| format!("C{ordinal:04}").into_bytes())
        .collect::<Vec<_>>();
    if let Some(last) = names.last_mut() {
        last.extend(std::iter::repeat_n(b'x', remainder));
    }
    names
}

fn long_columns(names: &[Vec<u8>]) -> Vec<ColumnSpec<'_>> {
    names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnType::Long))
        .collect()
}

#[test]
fn a_table_without_an_index_appends_a_root_a_map_page_and_a_property_page() -> PlanResult {
    // EXP-0093: three appended pages, Id equal to the root page.
    let columns = [ID, NAME, NOTE];
    let plan = plan_table_schema(&spec(b"Beta", &columns, &[]), 23, true)?;
    assert_eq!(plan.object_id(), 23);
    assert_eq!(plan.definition_root(), PageNumber::new(23));
    assert_eq!(plan.map_page(), PageNumber::new(24));
    assert_eq!(plan.property_page(), Some(PageNumber::new(25)));
    assert_eq!(plan.index_placements().count(), 0);
    assert_eq!(plan.appended_page_count(), 3);
    Ok(())
}

#[test]
fn a_later_create_appends_no_property_page() -> PlanResult {
    // EXP-0087: Beta, the second create, appended only its root and map page;
    // Gamma, with one index, appended root, map page, then the index root.
    let columns = [ID, NAME, NOTE];
    let plan = plan_table_schema(&spec(b"Beta", &columns, &[]), 23, false)?;
    assert_eq!(plan.object_id(), 23);
    assert_eq!(plan.property_page(), None);
    assert_eq!(plan.appended_page_count(), 2);
    let indexes = [IndexSpec {
        name: b"PrimaryKey",
        fields: &[key(0)],
        kind: IndexKind::Primary,
    }];
    let plan = plan_table_schema(&spec(b"Gamma", &[ID], &indexes), 25, false)?;
    assert_eq!(plan.map_page(), PageNumber::new(26));
    assert_eq!(
        plan.index_placements().collect::<Vec<_>>(),
        [(PageNumber::new(27), 2)]
    );
    assert_eq!(plan.appended_page_count(), 3);
    Ok(())
}

#[test]
fn a_later_create_with_two_indexes_or_a_continuation_is_refused() {
    // EXP-0087 observed later creates with at most one index and no
    // continuation; EXP-0093 and EXP-0107 observed wider layouts only on a
    // first create.
    let columns = [ID, NAME];
    let indexes = [
        IndexSpec {
            name: b"PrimaryKey",
            fields: &[key(0)],
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"ByName",
            fields: &[key(1)],
            kind: IndexKind::Ordinary,
        },
    ];
    assert!(plan_table_schema(&spec(b"Beta", &columns, &indexes), 23, true).is_ok());
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 23, false),
        Err(TableSchemaPlanError::UnobservedLaterCreateIndexCount {
            count: 2,
            observed: MAX_OBSERVED_LATER_CREATE_INDEXES,
        })
    );
    let names = names_of_definition_len(DEFINITION_ROOT_CAPACITY + 1);
    let columns = long_columns(&names);
    assert!(plan_table_schema(&spec(b"Wide", &columns, &[]), 23, true).is_ok());
    assert_eq!(
        plan_table_schema(&spec(b"Wide", &columns, &[]), 23, false),
        Err(TableSchemaPlanError::UnobservedLaterCreateContinuation {
            length: DEFINITION_ROOT_CAPACITY + 1,
            continuations: 1,
        })
    );
}

#[test]
fn index_roots_follow_the_property_page_in_physical_order() -> PlanResult {
    // EXP-0093's `three` arm: one root and one map row per physical ordinal.
    let columns = [ID, LABEL, NAME];
    let indexes = [
        IndexSpec {
            name: b"ZPrimary",
            fields: &[key(0)],
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"MUniqueX",
            fields: &[key(1)],
            kind: IndexKind::Unique,
        },
        IndexSpec {
            name: b"ASecondx",
            fields: &[key(2)],
            kind: IndexKind::Ordinary,
        },
    ];
    let plan = plan_table_schema(&spec(b"Three", &columns, &indexes), 28, true)?;
    assert_eq!(plan.property_page(), Some(PageNumber::new(30)));
    assert_eq!(
        plan.index_placements().collect::<Vec<_>>(),
        [
            (PageNumber::new(31), 2),
            (PageNumber::new(32), 3),
            (PageNumber::new(33), 4),
        ]
    );
    assert_eq!(plan.appended_page_count(), 6);
    assert_eq!(logical_index_order(&indexes), [2, 1, 0]);
    Ok(())
}

#[test]
fn index_kinds_map_to_the_observed_flag_classes() {
    // EXP-0093: primary 0x09, unique non-primary 0x01, ordinary 0x00.
    assert_eq!(
        IndexKind::Primary.flags(),
        PhysicalIndexFlagsSpec::UniqueRequired
    );
    assert_eq!(IndexKind::Unique.flags(), PhysicalIndexFlagsSpec::Unique);
    assert_eq!(
        IndexKind::Ordinary.flags(),
        PhysicalIndexFlagsSpec::Ordinary
    );
    assert_eq!(
        IndexKind::Unique.logical_kind(),
        LogicalIndexKindSpec::Ordinary
    );
}

#[test]
fn index_names_whose_order_depends_on_case_folding_are_refused() {
    // Byte order puts "Banana" first; case-folded order puts "apple" first.
    let columns = [ID, LABEL];
    let indexes = [
        IndexSpec {
            name: b"apple",
            fields: &[key(0)],
            kind: IndexKind::Ordinary,
        },
        IndexSpec {
            name: b"Banana",
            fields: &[key(1)],
            kind: IndexKind::Ordinary,
        },
    ];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20, true),
        Err(TableSchemaPlanError::UnderdeterminedIndexNameOrder {
            first: 0,
            second: 1
        })
    );
}

#[test]
fn a_name_byte_above_the_established_range_is_refused() {
    let columns = [ColumnSpec::new(b"Caf\xe9", ColumnType::Long)];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &[]), 20, true),
        Err(TableSchemaPlanError::NameByteUnestablished {
            role: "column",
            ordinal: 0,
            position: 3,
            byte: 0xe9,
        })
    );
    let columns = [ID];
    let indexes = [IndexSpec {
        name: b"By\x80",
        fields: &[key(0)],
        kind: IndexKind::Ordinary,
    }];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20, true),
        Err(TableSchemaPlanError::NameByteUnestablished {
            role: "logical index",
            ordinal: 0,
            position: 2,
            byte: 0x80,
        })
    );
}

#[test]
fn a_table_name_byte_without_an_established_weight_is_refused() {
    let columns = [ID];
    assert_eq!(
        plan_table_schema(&spec(b"Caf\xe9", &columns, &[]), 20, true),
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
        plan_table_schema(&spec(b"", &columns, &[]), 20, true),
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
    assert!(plan_table_schema(&spec(&longest, &columns, &[]), 20, true).is_ok());
    let overlong = vec![b'A'; 225];
    assert!(matches!(
        plan_table_schema(&spec(&overlong, &columns, &[]), 20, true),
        Err(TableSchemaPlanError::TableNameRow(
            CatalogRecordWriteError::NameTooLong { length: 225, .. }
        ))
    ));
}

#[test]
fn a_column_name_too_long_for_the_definition_is_refused() {
    let overlong = vec![b'A'; 256];
    let columns = [ColumnSpec::new(&overlong, ColumnType::Long)];
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
    let indexes = [IndexSpec {
        name: &overlong,
        fields: &[key(0)],
        kind: IndexKind::Ordinary,
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
fn index_columns_named_by_name_resolve_to_the_same_ordinals() -> PlanResult {
    let columns = [ID, LABEL, NAME];
    let by_name = [IndexSpec {
        name: b"ByLabel",
        fields: &[
            IndexColumnSpec::descending(b"Label"),
            IndexColumnSpec::ascending(b"Id"),
        ],
        kind: IndexKind::Ordinary,
    }];
    let by_ordinal = [IndexSpec {
        name: b"ByLabel",
        fields: &[IndexColumnSpec::descending(1), key(0)],
        kind: IndexKind::Ordinary,
    }];
    let named = plan_table_schema(&spec(b"Beta", &columns, &by_name), 20, true)?;
    let ordinal = plan_table_schema(&spec(b"Beta", &columns, &by_ordinal), 20, true)?;
    assert_eq!(named, ordinal);
    assert_eq!(
        named.index_fields().collect::<Vec<_>>(),
        [&[
            IndexFieldSpec {
                column: 1,
                direction: IndexDirection::Descending,
            },
            IndexFieldSpec {
                column: 0,
                direction: IndexDirection::Ascending,
            },
        ][..]]
    );
    Ok(())
}

#[test]
fn an_index_column_name_the_table_lacks_is_refused() {
    let columns = [ID, NAME];
    let indexes = [IndexSpec {
        name: b"ByLabel",
        fields: &[
            IndexColumnSpec::ascending(b"Id"),
            IndexColumnSpec::ascending(b"Label"),
        ],
        kind: IndexKind::Ordinary,
    }];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20, true),
        Err(TableSchemaPlanError::UnknownIndexColumn { index: 0, field: 1 })
    );
}

#[test]
fn a_fixed_area_no_row_slot_could_hold_is_refused() {
    // Each column is a fixed Text(255); enough of them overrun the row slot.
    let names = (0..MANY_COLUMNS)
        .map(|ordinal| format!("C{ordinal:03}").into_bytes())
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnType::FixedText { len: nz(255) }))
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
        plan_table_schema(&spec(b"Empty", &[], &[]), 20, true),
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
    let columns = [ColumnSpec::new(b"", ColumnType::Long)];
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
    // EXP-0093 observed at most three indexes per create.
    let columns = [ID, LABEL, NAME];
    let fields = [key(0), key(1), key(2), key(0)];
    let indexes = fields
        .iter()
        .zip([b"A".as_slice(), b"B", b"C", b"D"])
        .map(|(field, name)| IndexSpec {
            name,
            fields: std::slice::from_ref(field),
            kind: IndexKind::Ordinary,
        })
        .collect::<Vec<_>>();
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20, true),
        Err(TableSchemaPlanError::UnobservedIndexCount {
            count: 4,
            observed: MAX_OBSERVED_INDEXES,
        })
    );
}

#[test]
fn an_index_naming_no_columns_is_refused() {
    let columns = [ID];
    let indexes = [IndexSpec {
        name: b"ById",
        fields: &[],
        kind: IndexKind::Ordinary,
    }];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &indexes)),
        Some(TableDefinitionWriteError::EmptyPhysicalIndex { physical_index: 0 })
    ));
}

#[test]
fn an_index_naming_an_undeclared_column_is_refused() {
    let columns = [ID];
    let indexes = [IndexSpec {
        name: b"ById",
        fields: &[key(1)],
        kind: IndexKind::Ordinary,
    }];
    assert!(matches!(
        definition_error(&spec(b"Beta", &columns, &indexes)),
        Some(TableDefinitionWriteError::InvalidKeyColumn { ordinal: 1, .. })
    ));
}

#[test]
fn an_index_over_a_memo_column_is_refused() {
    let columns = [NOTE];
    let indexes = [IndexSpec {
        name: b"ByNote",
        fields: &[key(0)],
        kind: IndexKind::Ordinary,
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
    let indexes = [IndexSpec {
        name: b"ById",
        fields: &[key(0), key(1), key(0)],
        kind: IndexKind::Ordinary,
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
        .map(|name| ColumnSpec::new(name, ColumnType::Long))
        .collect::<Vec<_>>();
    // An unknown name past the limit must not be reached: the count is
    // refused before any key is resolved or stored.
    let mut fields = (0..=KEY_SLOT_COUNT as u16).map(key).collect::<Vec<_>>();
    fields.push(IndexColumnSpec::ascending(b"Missing"));
    let indexes = [IndexSpec {
        name: b"Wide",
        fields: &fields,
        kind: IndexKind::Ordinary,
    }];
    assert!(matches!(
        definition_error(&spec(b"Wide", &columns, &indexes)),
        Some(TableDefinitionWriteError::TooManyKeyFields { count, .. })
            if count == KEY_SLOT_COUNT + 2
    ));
}

#[test]
fn a_first_page_above_the_signed_id_range_is_refused() {
    // EXP-0087 observed the object Id equal to the definition root page, and
    // MSysObjects.Id is a signed Long, so the run must stay inside that range.
    let columns = [ID];
    let first = i32::MAX as u64 + 1;
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &[]), first, true),
        Err(TableSchemaPlanError::PageOverflow { first, needed: 3 })
    );
}

#[test]
fn a_map_page_no_usage_map_locator_could_name_is_refused() -> PlanResult {
    // Usage-map locators hold a three-byte page, so the map page bounds the
    // run well below the signed Id range.
    let columns = [ID];
    let highest = MAX_MAP_PAGE - 1;
    let plan = plan_table_schema(&spec(b"Beta", &columns, &[]), highest, true)?;
    assert_eq!(plan.map_page(), PageNumber::new(MAX_MAP_PAGE));
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &[]), highest + 1, true),
        Err(TableSchemaPlanError::MapPageNotAddressable {
            page: MAX_MAP_PAGE + 1,
            maximum: MAX_MAP_PAGE,
        })
    );
    Ok(())
}

#[test]
fn continuation_counts_follow_the_established_capacities() {
    // EXP-0105: the root holds 2,048 logical bytes and each continuation
    // 2,040, so the counts change one byte above each capacity.
    for (length, expected) in [
        (2048, 0),
        (2049, 1),
        (4088, 1),
        (4089, 2),
        (6128, 2),
        (6129, 3),
    ] {
        assert_eq!(continuation_count(length), expected, "length {length}");
    }
}

#[test]
fn a_definition_that_exactly_fills_its_root_page_needs_no_continuation() -> PlanResult {
    let names = names_of_definition_len(DEFINITION_ROOT_CAPACITY);
    let columns = long_columns(&names);
    let plan = plan_table_schema(&spec(b"Wide", &columns, &[]), 20, true)?;
    assert_eq!(plan.appended_page_count(), 3);
    Ok(())
}

#[test]
fn a_definition_needing_one_continuation_places_it_after_the_property_page() -> PlanResult {
    // EXP-0107: the accepted ContOneX image appended its single continuation
    // at page 23, directly after the LvProp page, with no index roots.
    let names = names_of_definition_len(DEFINITION_ROOT_CAPACITY + 1);
    let columns = long_columns(&names);
    let plan = plan_table_schema(&spec(b"Wide", &columns, &[]), 20, true)?;
    assert_eq!(plan.definition_len(), DEFINITION_ROOT_CAPACITY + 1);
    assert_eq!(plan.property_page(), Some(PageNumber::new(22)));
    assert_eq!(plan.continuation_page(), Some(PageNumber::new(23)));
    assert_eq!(plan.appended_page_count(), 4);
    let full = names_of_definition_len(DEFINITION_ROOT_CAPACITY + CONTINUATION_CAPACITY);
    let columns = long_columns(&full);
    assert_eq!(
        plan_table_schema(&spec(b"Wide", &columns, &[]), 20, true)?.appended_page_count(),
        4
    );
    Ok(())
}

#[test]
fn a_definition_needing_two_continuations_is_refused() {
    // EXP-0105 observed two-continuation chains only under the provider's own
    // allocation, so the refusal starts one byte above the continuation.
    let length = DEFINITION_ROOT_CAPACITY + CONTINUATION_CAPACITY + 1;
    let names = names_of_definition_len(length);
    let columns = long_columns(&names);
    assert_eq!(
        plan_table_schema(&spec(b"Wide", &columns, &[]), 20, true),
        Err(TableSchemaPlanError::ContinuationPlacementUnestablished {
            length,
            continuations: 2,
        })
    );
}

#[test]
fn a_continuation_beside_an_index_is_refused() {
    // No observed create carried both, so the order of the continuation and
    // the index roots is unestablished.
    let names = names_of_definition_len(DEFINITION_ROOT_CAPACITY + 1);
    let columns = long_columns(&names);
    let indexes = [IndexSpec {
        name: b"ByFirst",
        fields: &[key(0)],
        kind: IndexKind::Ordinary,
    }];
    assert_eq!(
        plan_table_schema(&spec(b"Wide", &columns, &indexes), 20, true),
        Err(TableSchemaPlanError::UnobservedContinuationIndexLayout {
            continuations: 1,
            indexes: 1,
        })
    );
}
