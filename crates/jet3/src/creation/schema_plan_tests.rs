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
    match plan_table_schema(spec, 20, true, &mut budget()) {
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
    let plan = plan_table_schema(&spec(b"Beta", &columns, &[]), 23, true, &mut budget())?;
    assert_eq!(plan.object_id(), 23);
    assert_eq!(plan.definition_root(), PageNumber::new(23));
    assert_eq!(plan.map_page(), PageNumber::new(24));
    assert_eq!(plan.property_page(), Some(PageNumber::new(25)));
    assert_eq!(plan.index_placements().count(), 0);
    assert_eq!(plan.appended_page_count(), 3);
    Ok(())
}

#[test]
fn later_text_properties_precede_index_roots() -> PlanResult {
    // EXP-0284: disabled empty values need explicit properties on later text tables.
    let columns = [ID, NAME, NOTE];
    let plan = plan_table_schema(&spec(b"Beta", &columns, &[]), 23, false, &mut budget())?;
    assert_eq!(plan.object_id(), 23);
    assert_eq!(plan.property_page(), Some(PageNumber::new(25)));
    assert_eq!(plan.appended_page_count(), 3);
    let indexes = [IndexSpec {
        name: b"PrimaryKey",
        fields: &[key(0)],
        kind: IndexKind::Primary,
    }];
    let plan = plan_table_schema(&spec(b"Gamma", &[ID], &indexes), 25, false, &mut budget())?;
    assert_eq!(plan.map_page(), PageNumber::new(26));
    assert_eq!(
        plan.index_placements().collect::<Vec<_>>(),
        [(PageNumber::new(27), 2)]
    );
    assert_eq!(plan.appended_page_count(), 3);
    Ok(())
}

#[test]
fn a_later_create_places_continuations_after_its_map() -> PlanResult {
    let names = names_of_definition_len(DEFINITION_ROOT_CAPACITY + 1);
    let columns = long_columns(&names);
    let plan = plan_table_schema(&spec(b"Wide", &columns, &[]), 23, false, &mut budget())?;
    assert_eq!(plan.continuation_page(), Some(PageNumber::new(25)));
    assert_eq!(plan.appended_page_count(), 3);
    Ok(())
}

#[test]
fn later_indexes_have_separate_roots_and_maps_in_physical_order() -> PlanResult {
    let columns = [ID, NAME];
    let indexes = [
        IndexSpec {
            name: b"ZPrimary",
            fields: &[key(0)],
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"MName",
            fields: &[key(1)],
            kind: IndexKind::Ordinary,
        },
        IndexSpec {
            name: b"AId",
            fields: &[key(0)],
            kind: IndexKind::Unique,
        },
    ];
    for count in 2..=3 {
        let plan = plan_table_schema(
            &spec(b"Beta", &columns, &indexes[..count]),
            23,
            false,
            &mut budget(),
        )?;
        assert_eq!(plan.property_page(), Some(PageNumber::new(25)));
        assert_eq!(plan.appended_page_count(), 3 + count as u64);
        assert_eq!(
            plan.index_placements().collect::<Vec<_>>(),
            (0..count)
                .map(|n| (PageNumber::new(26 + n as u64), 2 + n as u8))
                .collect::<Vec<_>>()
        );
    }
    Ok(())
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
    let plan = plan_table_schema(&spec(b"Three", &columns, &indexes), 28, true, &mut budget())?;
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
fn mixed_case_and_accented_index_names_are_accepted() -> PlanResult {
    let columns = [ID];
    let fields = [key(0)];
    let indexes = [b"ById".as_slice(), b"Z", b"a", b"\xc1", b"\xe6", b"B"].map(|name| IndexSpec {
        name,
        fields: &fields,
        kind: IndexKind::Ordinary,
    });
    plan_table_schema(
        &spec(b"T\xe2ble \xc6", &columns, &indexes),
        20,
        true,
        &mut budget(),
    )?;
    Ok(())
}

#[test]
fn an_undefined_column_or_index_name_byte_is_refused() {
    let columns = [ColumnSpec::new(b"Caf\x81", ColumnType::Long)];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &[]), 20, true, &mut budget()),
        Err(TableSchemaPlanError::NameByteUnestablished {
            role: "column",
            ordinal: 0,
            position: 3,
            byte: 0x81,
        })
    );
    let columns = [ID];
    let indexes = [IndexSpec {
        name: b"By\x81",
        fields: &[key(0)],
        kind: IndexKind::Ordinary,
    }];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20, true, &mut budget()),
        Err(TableSchemaPlanError::NameByteUnestablished {
            role: "logical index",
            ordinal: 0,
            position: 2,
            byte: 0x81,
        })
    );
}

#[test]
fn an_undefined_table_name_byte_is_refused() {
    let columns = [ID];
    assert_eq!(
        plan_table_schema(&spec(b"Caf\x81", &columns, &[]), 20, true, &mut budget()),
        Err(TableSchemaPlanError::TableNameKey(
            CatalogNameKeyError::UnmappedNameByte {
                position: 3,
                byte: 0x81,
            }
        ))
    );
}

#[test]
fn an_empty_table_name_is_refused() {
    let columns = [ID];
    assert_eq!(
        plan_table_schema(&spec(b"", &columns, &[]), 20, true, &mut budget()),
        Err(TableSchemaPlanError::TableNameKey(
            CatalogNameKeyError::EmptyName
        ))
    );
}

#[test]
fn creation_name_limits_cover_table_column_and_index_boundaries() {
    let columns = [ID];
    for (role, maximum) in [("table", 64), ("column", 64), ("logical index", 63)] {
        for length in [maximum, maximum + 1] {
            let name = vec![b'A'; length];
            let named_column = [ColumnSpec::new(&name, ColumnType::Long)];
            let indexes = [IndexSpec {
                name: &name,
                fields: &[key(0)],
                kind: IndexKind::Ordinary,
            }];
            let schema = match role {
                "table" => spec(&name, &columns, &[]),
                "column" => spec(b"Items", &named_column, &[]),
                _ => spec(b"Items", &columns, &indexes),
            };
            let result = plan_table_schema(&schema, 20, true, &mut budget());
            if length == maximum {
                assert!(result.is_ok(), "{result:?}");
            } else {
                assert_eq!(
                    result,
                    Err(TableSchemaPlanError::NameTooLong {
                        role,
                        length,
                        maximum
                    })
                );
            }
        }
    }
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
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20, true, &mut budget()),
        Err(TableSchemaPlanError::NameTooLong {
            role: "logical index",
            length: 256,
            maximum: 63,
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
    let named = plan_table_schema(&spec(b"Beta", &columns, &by_name), 20, true, &mut budget())?;
    let ordinal = plan_table_schema(
        &spec(b"Beta", &columns, &by_ordinal),
        20,
        true,
        &mut budget(),
    )?;
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
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20, true, &mut budget()),
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
        plan_table_schema(&spec(b"Empty", &[], &[]), 20, true, &mut budget()),
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
fn indexes_beyond_the_native_limit_are_refused() {
    let columns = [ID];
    let fields = [key(0)];
    let indexes = vec![
        IndexSpec {
            name: b"ById",
            fields: &fields,
            kind: IndexKind::Ordinary
        };
        MAX_OBSERVED_INDEXES + 1
    ];
    assert_eq!(
        plan_table_schema(&spec(b"Beta", &columns, &indexes), 20, true, &mut budget()),
        Err(TableSchemaPlanError::UnobservedIndexCount {
            count: MAX_OBSERVED_INDEXES + 1,
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
        plan_table_schema(&spec(b"Beta", &columns, &[]), first, true, &mut budget()),
        Err(TableSchemaPlanError::PageOverflow { first, needed: 3 })
    );
}

#[test]
fn a_map_page_no_usage_map_locator_could_name_is_refused() -> PlanResult {
    // Usage-map locators hold a three-byte page, so the map page bounds the
    // run well below the signed Id range.
    let columns = [ID];
    let highest = MAX_MAP_PAGE - 1;
    let plan = plan_table_schema(&spec(b"Beta", &columns, &[]), highest, true, &mut budget())?;
    assert_eq!(plan.map_page(), PageNumber::new(MAX_MAP_PAGE));
    assert_eq!(
        plan_table_schema(
            &spec(b"Beta", &columns, &[]),
            highest + 1,
            true,
            &mut budget()
        ),
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
    // 2,040; EXP-0247 retains an empty terminal page at exact boundaries.
    for (length, expected) in [
        (2047, 0),
        (2048, 1),
        (2049, 1),
        (4088, 2),
        (4089, 2),
        (6128, 3),
        (6129, 3),
    ] {
        assert_eq!(continuation_count(length), expected, "length {length}");
    }
}

#[test]
fn a_definition_shorter_than_its_root_page_needs_no_continuation() -> PlanResult {
    let names = names_of_definition_len(DEFINITION_ROOT_CAPACITY - 1);
    let columns = long_columns(&names);
    let plan = plan_table_schema(&spec(b"Wide", &columns, &[]), 20, true, &mut budget())?;
    assert_eq!(plan.appended_page_count(), 3);
    Ok(())
}

#[test]
fn a_definition_needing_one_continuation_places_it_after_the_property_page() -> PlanResult {
    // EXP-0107: the accepted ContOneX image appended its single continuation
    // at page 23, directly after the LvProp page, with no index roots.
    let names = names_of_definition_len(DEFINITION_ROOT_CAPACITY + 1);
    let columns = long_columns(&names);
    let plan = plan_table_schema(&spec(b"Wide", &columns, &[]), 20, true, &mut budget())?;
    assert_eq!(plan.definition_len(), DEFINITION_ROOT_CAPACITY + 1);
    assert_eq!(plan.property_page(), Some(PageNumber::new(22)));
    assert_eq!(plan.continuation_page(), Some(PageNumber::new(23)));
    assert_eq!(plan.appended_page_count(), 4);
    let full = names_of_definition_len(DEFINITION_ROOT_CAPACITY + CONTINUATION_CAPACITY);
    let columns = long_columns(&full);
    assert_eq!(
        plan_table_schema(&spec(b"Wide", &columns, &[]), 20, true, &mut budget())?
            .appended_page_count(),
        5
    );
    Ok(())
}

#[test]
fn definition_chains_precede_index_roots_on_first_and_later_tables() -> PlanResult {
    for length in [2048, 2049, 4088, 4089, 6128, 6129] {
        // Index metadata is included in the encoded length, so test placement
        // against the measured total rather than the column-only target.
        let names = names_of_definition_len(length);
        let columns = long_columns(&names);
        let indexes = [
            IndexSpec {
                name: b"A",
                fields: &[key(0)],
                kind: IndexKind::Primary,
            },
            IndexSpec {
                name: b"B",
                fields: &[key(1)],
                kind: IndexKind::Ordinary,
            },
            IndexSpec {
                name: b"C",
                fields: &[key(2)],
                kind: IndexKind::Unique,
            },
        ];
        for first in [false, true] {
            for count in [0, 3] {
                let plan = plan_table_schema(
                    &spec(b"Wide", &columns, &indexes[..count]),
                    20,
                    first,
                    &mut budget(),
                )?;
                let fixed = 2 + u64::from(first);
                let continuation_count = continuation_count(plan.definition_len()) as u64;
                assert_eq!(
                    plan.appended_page_count(),
                    fixed + continuation_count + count as u64
                );
                let expected = (0..count)
                    .map(|n| {
                        (
                            PageNumber::new(20 + fixed + continuation_count + n as u64),
                            2 + n as u8,
                        )
                    })
                    .collect::<Vec<_>>();
                assert_eq!(plan.index_placements().collect::<Vec<_>>(), expected);
            }
        }
    }
    Ok(())
}

fn budget() -> crate::ResourceBudget {
    crate::ResourceBudget::new(crate::ResourceLimits::default())
}

#[test]
fn schema_name_comparisons_charge_work_before_scanning() {
    let columns = [
        ColumnSpec::new(b"\xc6", ColumnType::Long),
        ColumnSpec::new(b"AE", ColumnType::Long),
    ];
    let mut limited =
        crate::ResourceBudget::new(crate::ResourceLimits::default().with_max_total_work_units(0));
    assert!(matches!(
        plan_table_schema(&spec(b"Items", &columns, &[]), 20, true, &mut limited),
        Err(TableSchemaPlanError::Resource(
            crate::Error::ResourceLimitExceeded { .. }
        ))
    ));
    assert_eq!(limited.total_work_units(), 0);
}
