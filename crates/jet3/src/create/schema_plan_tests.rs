use super::schema_plan::*;
use crate::ColumnSpec;
use crate::create::{IndexKind, IndexSpec};
use crate::testkit::{index, table};
use crate::{
    ColumnPhysicalType, ColumnRef, ColumnType, IndexColumnSpec, IndexDirection, IndexFieldSpec,
    LogicalIndexKindSpec, PageNumber, PhysicalIndexFlagsSpec, TableDefinitionWriteError,
    catalog::name_key::CatalogNameKeyError,
    create::TableSpec,
    definition::{column_writer::nz, header::KEY_SLOT_COUNT},
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
    table(name, columns, indexes)
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
fn invalid_schemas_are_refused_with_their_specific_error() {
    type Accepts = fn(&TableSchemaPlanError) -> bool;
    let id = [ID];
    let id_name = [ID, NAME];
    let id_label = [ID, LABEL];
    let repeated_column = [ID, LABEL, ID];
    let note = [NOTE];
    let undefined_byte = [ColumnSpec::new(b"Caf\x81", ColumnType::Long)];
    let empty_column = [ColumnSpec::new(b"", ColumnType::Long)];
    let fixed_names = (0..32)
        .map(|ordinal| format!("C{ordinal:03}").into_bytes())
        .collect::<Vec<_>>();
    let fixed = fixed_names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnType::FixedText { len: nz(255) }))
        .collect::<Vec<_>>();
    let too_many_columns = vec![ID; 256];
    let key_names = (0..=KEY_SLOT_COUNT)
        .map(|ordinal| format!("C{ordinal}").into_bytes())
        .collect::<Vec<_>>();
    let key_columns = long_columns(&key_names);
    // The unknown name past the limit is never resolved: the count is refused first.
    let mut wide_key = (0..=KEY_SLOT_COUNT as u16).map(key).collect::<Vec<_>>();
    wide_key.push(IndexColumnSpec::ascending(b"Missing"));
    let first = [key(0)];
    let second = [key(1)];
    let first_twice = [key(0), key(1), key(0)];
    let by_name = [
        IndexColumnSpec::ascending(b"Id"),
        IndexColumnSpec::ascending(b"Label"),
    ];
    let byte_index = [index(b"By\x81", &first, IndexKind::Ordinary)];
    let unknown_name = [index(b"ByLabel", &by_name, IndexKind::Ordinary)];
    let too_many_indexes =
        vec![index(b"ById", &first, IndexKind::Ordinary); MAX_OBSERVED_INDEXES + 1];
    let no_fields = [index(b"ById", &[], IndexKind::Ordinary)];
    let undeclared = [index(b"ById", &second, IndexKind::Ordinary)];
    let memo_key = [index(b"ByNote", &first, IndexKind::Ordinary)];
    let repeated_key = [index(b"ById", &first_twice, IndexKind::Ordinary)];
    let wide_index = [index(b"Wide", &wide_key, IndexKind::Ordinary)];
    let cases: [(&str, TableSpec<'_>, Accepts); 16] = [
        (
            "column name byte",
            spec(b"Beta", &undefined_byte, &[]),
            |e| {
                *e == TableSchemaPlanError::NameByteUnestablished {
                    role: "column",
                    ordinal: 0,
                    position: 3,
                    byte: 0x81,
                }
            },
        ),
        ("index name byte", spec(b"Beta", &id, &byte_index), |e| {
            *e == TableSchemaPlanError::NameByteUnestablished {
                role: "logical index",
                ordinal: 0,
                position: 2,
                byte: 0x81,
            }
        }),
        ("table name byte", spec(b"Caf\x81", &id, &[]), |e| {
            *e == TableSchemaPlanError::TableNameKey(CatalogNameKeyError::UnmappedNameByte {
                position: 3,
                byte: 0x81,
            })
        }),
        ("empty table name", spec(b"", &id, &[]), |e| {
            *e == TableSchemaPlanError::TableNameKey(CatalogNameKeyError::EmptyName)
        }),
        (
            "unknown index column",
            spec(b"Beta", &id_name, &unknown_name),
            |e| *e == TableSchemaPlanError::UnknownIndexColumn { index: 0, field: 1 },
        ),
        ("no columns", spec(b"Empty", &[], &[]), |e| {
            *e == TableSchemaPlanError::NoColumns
        }),
        (
            "too many indexes",
            spec(b"Beta", &id, &too_many_indexes),
            |e| {
                *e == TableSchemaPlanError::UnobservedIndexCount {
                    count: MAX_OBSERVED_INDEXES + 1,
                    observed: MAX_OBSERVED_INDEXES,
                }
            },
        ),
        ("fixed row area", spec(b"Wide", &fixed, &[]), |e| {
            matches!(
                e,
                TableSchemaPlanError::Definition(
                    TableDefinitionWriteError::RowLayoutTooLarge { .. }
                )
            )
        }),
        ("column count", spec(b"Wide", &too_many_columns, &[]), |e| {
            matches!(
                e,
                TableSchemaPlanError::Definition(TableDefinitionWriteError::TooManyColumns {
                    count: 256,
                    ..
                })
            )
        }),
        (
            "repeated column",
            spec(b"Beta", &repeated_column, &[]),
            |e| {
                matches!(
                    e,
                    TableSchemaPlanError::Definition(TableDefinitionWriteError::DuplicateName {
                        role: "column",
                        ordinal: 2,
                    })
                )
            },
        ),
        (
            "empty column name",
            spec(b"Beta", &empty_column, &[]),
            |e| {
                matches!(
                    e,
                    TableSchemaPlanError::Definition(TableDefinitionWriteError::EmptyName {
                        role: "column",
                        ordinal: 0,
                    })
                )
            },
        ),
        (
            "index without fields",
            spec(b"Beta", &id, &no_fields),
            |e| {
                matches!(
                    e,
                    TableSchemaPlanError::Definition(
                        TableDefinitionWriteError::EmptyPhysicalIndex { physical_index: 0 }
                    )
                )
            },
        ),
        (
            "undeclared key column",
            spec(b"Beta", &id, &undeclared),
            |e| {
                matches!(
                    e,
                    TableSchemaPlanError::Definition(TableDefinitionWriteError::InvalidKeyColumn {
                        ordinal: 1,
                        ..
                    })
                )
            },
        ),
        ("memo key column", spec(b"Beta", &note, &memo_key), |e| {
            matches!(
                e,
                TableSchemaPlanError::Definition(TableDefinitionWriteError::UnsupportedKeyColumn {
                    ordinal: 0,
                    physical_type: ColumnPhysicalType::Memo,
                    ..
                })
            )
        }),
        (
            "repeated key column",
            spec(b"Beta", &id_label, &repeated_key),
            |e| {
                matches!(
                    e,
                    TableSchemaPlanError::Definition(
                        TableDefinitionWriteError::DuplicateKeyColumn { ordinal: 0, .. }
                    )
                )
            },
        ),
        (
            "key field count",
            spec(b"Wide", &key_columns, &wide_index),
            |e| {
                matches!(
                    e,
                    TableSchemaPlanError::Definition(TableDefinitionWriteError::TooManyKeyFields {
                        count,
                        ..
                    }) if *count == KEY_SLOT_COUNT + 2
                )
            },
        ),
    ];
    for (label, schema, accepts) in cases {
        let result = plan_table_schema(&schema, 20, true, &mut budget());
        assert!(result.as_ref().is_err_and(accepts), "{label}: {result:?}");
    }
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
    for length in [2047, 2048, 2049, 4088, 4089, 6128, 6129] {
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
                    plan.continuation_page(),
                    (continuation_count > 0).then(|| PageNumber::new(20 + fixed))
                );
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

use crate::testkit::budget;

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

#[test]
fn property_payloads_above_the_native_single_page_limit_are_chained() -> PlanResult {
    // EXP-0300: DAO chains LvProp payloads above 1,776 bytes; fragments hold 2,032.
    let text = [b'd'; 2048];
    let len = |width: usize| -> Result<usize, TableSchemaPlanError> {
        let columns = [ID, NOTE.with_description(&text[..width])];
        crate::properties::column::CreationProperties::new(
            &columns,
            crate::TableValidation::NONE,
            crate::SortOrder::General,
            &mut budget(),
        )
        .ok()
        .flatten()
        .map(|properties| properties.len())
        .ok_or(TableSchemaPlanError::InvalidTextProperty {
            column: None,
            property: b"",
            detail: "test payload",
        })
    };
    let base = len(100)? - 100;
    for (payload, chained, pages) in [
        (1776, false, 1),
        (1777, true, 1),
        (2032, true, 1),
        (2033, true, 2),
    ] {
        let description = &text[..payload - base];
        let columns = [ID, NOTE.with_description(description)];
        assert_eq!(len(payload - base)?, payload);
        let plan = plan_table_schema(&spec(b"Props", &columns, &[]), 23, false, &mut budget())?;
        assert_eq!(plan.property_chained(), chained, "{payload}");
        assert_eq!(plan.property_page_count(), pages, "{payload}");
    }
    Ok(())
}
