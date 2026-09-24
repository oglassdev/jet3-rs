use super::table_tests::*;
use crate::testkit::TestResult;
use crate::{
    ColumnPhysicalType, ColumnStorageClass, IndexDefinitionError, IndexDefinitionKind,
    LongValueMapError, PageNumber, UsageMapError,
    definition::table::{TableDefinitionError, TableDefinitionKind},
};

fn long_value_columns() -> Vec<ColumnSpec> {
    vec![
        (4, 3, 0, 4, b"Id".to_vec()),
        (12, 2, 0, 0, b"Note".to_vec()),
        (11, 2, 0, 0, b"Ole".to_vec()),
    ]
}

fn long_value_definition(suffix: &[u8]) -> Vec<u8> {
    build_definition(USER_MARKER, &long_value_columns(), &[], &[], suffix)
}

fn ordinary_logical(physical_index: u32, class: u8) -> [u8; 20] {
    let mut logical = [0_u8; 20];
    logical[..4].copy_from_slice(&physical_index.to_le_bytes());
    logical[4..8].copy_from_slice(&physical_index.to_le_bytes());
    logical[9..13].copy_from_slice(&u32::MAX.to_le_bytes());
    logical[17..19].copy_from_slice(&[4, 4]);
    logical[19] = class;
    logical
}

/// An `MSysACEs`-shaped system definition plus one Memo column, with the
/// `EXP-0073` primary over unique-only flags and one `0x02`-flagged index.
fn system_definition() -> Vec<u8> {
    system_definition_with_first_flags(1)
}

fn system_definition_with_first_flags(first_flags: u8) -> Vec<u8> {
    let columns: Vec<ColumnSpec> = vec![
        (4, 0x13, 0, 4, b"ObjectId".to_vec()),
        (9, 0x32, 0, 255, b"SID".to_vec()),
        (12, 0x12, 0, 0, b"Lv".to_vec()),
        (1, 0x13, 0, 1, b"FInheritable".to_vec()),
    ];
    let mut relationship_like = physical_index(2);
    relationship_like[..2].copy_from_slice(&1_u16.to_le_bytes());
    build_definition(
        SYSTEM_MARKER,
        &columns,
        &[physical_index(first_flags), relationship_like],
        &[
            (ordinary_logical(0, 1), b"ObjectId"),
            (ordinary_logical(1, 0), b"SID"),
        ],
        &group(2, 2, 3, MAP_PAGE as u8),
    )
}

#[test]
fn decodes_system_definition_under_exp_0073_relaxations() -> TestResult {
    let definition = decode_logical(&system_definition())?;
    assert_eq!(definition.kind(), TableDefinitionKind::System);
    assert_eq!(definition.raw_header()[20], SYSTEM_MARKER);
    let columns = definition.columns();
    assert_eq!(columns.len(), 4);
    assert_eq!(
        columns[0].storage(),
        ColumnStorageClass::Fixed { offset: 0 }
    );
    assert_eq!(columns[0].raw_class_flags(), 0x13);
    assert_eq!(columns[0].sourced_constant(), 0);
    assert_eq!(columns[0].raw_record()[5..7], [0, 0]);
    assert_eq!(
        columns[1].storage(),
        ColumnStorageClass::Variable { index: 0 }
    );
    assert_eq!(columns[1].raw_class_flags(), 0x32);
    assert_eq!(
        columns[2].storage(),
        ColumnStorageClass::Variable { index: 1 }
    );
    assert_eq!(columns[3].physical_type(), ColumnPhysicalType::Boolean);
    assert!(!columns[0].auto_increment());

    assert_eq!(definition.indexes()[0].kind(), IndexDefinitionKind::Primary);
    assert_eq!(definition.physical_indexes()[0].raw_flags(), 1);
    assert_eq!(definition.physical_indexes()[1].raw_flags(), 2);
    assert!(!definition.physical_indexes()[1].unique());
    assert!(!definition.physical_indexes()[1].required());

    assert!(matches!(
        decode_logical(&system_definition_with_first_flags(3)),
        Err(TableDefinitionError::Index(
            IndexDefinitionError::UnsupportedPhysicalFlags { raw: 3, .. }
        ))
    ));

    let mut user_flags = primary_definition();
    user_flags[PHYSICAL_OFFSET + 38] = 2;
    assert!(matches!(
        decode_logical(&user_flags),
        Err(TableDefinitionError::Index(
            IndexDefinitionError::InvalidPrimaryFlags { raw: 2, .. }
        ))
    ));

    let maps = definition.long_value_maps();
    assert_eq!(maps.len(), 1);
    assert_eq!(maps[0].column().get(), 2);
    assert_eq!(maps[0].owned().page(), PageNumber::new(MAP_PAGE as u64));
    assert_eq!(maps[0].owned().row(), 2);
    assert_eq!(maps[0].available().row(), 3);
    assert_eq!(maps[0].raw_group(), &group(2, 2, 3, MAP_PAGE as u8));
    assert_eq!(definition.raw_suffix(), &group(2, 2, 3, MAP_PAGE as u8));
    Ok(())
}

#[test]
fn rejects_column_constants_of_the_other_definition_kind() {
    let system = system_definition();
    let system_column = |ordinal: usize| COLUMN_ONLY_OFFSET + 16 + ordinal * 18;

    let mut constant = system.clone();
    constant[system_column(0) + 7] = 1;
    assert!(matches!(
        decode_logical(&constant),
        Err(TableDefinitionError::InvalidColumnConstant { ordinal: 0, raw: 1 })
    ));

    let mut repeat = system.clone();
    repeat[system_column(1) + 5] = 1;
    assert!(matches!(
        decode_logical(&repeat),
        Err(TableDefinitionError::InvalidColumnOrdinal {
            record: 1,
            repeated: 1,
            ..
        })
    ));

    for class in [3, 7, 0x17, 0x33] {
        let mut user_class = system.clone();
        user_class[system_column(0) + 13] = class;
        assert!(matches!(
            decode_logical(&user_class),
            Err(TableDefinitionError::UnsupportedColumnClass { ordinal: 0, .. })
        ));
    }

    let mut user = column_only_definition();
    user[COLUMN_ONLY_OFFSET + 13] = 0x13;
    assert!(matches!(
        decode_logical(&user),
        Err(TableDefinitionError::UnsupportedColumnClass {
            ordinal: 0,
            raw: 0x13,
            ..
        })
    ));
}

#[test]
fn decodes_long_value_maps_in_stored_order_and_rejects_corruption() -> TestResult {
    let page = MAP_PAGE as u8;
    let mut suffix = Vec::new();
    suffix.extend_from_slice(&group(2, 0, 1, page));
    suffix.extend_from_slice(&group(1, 2, 3, page));
    let definition = decode_logical(&long_value_definition(&suffix))?;
    let maps = definition.long_value_maps();
    assert_eq!(maps.len(), 2);
    assert_eq!(maps[0].column().get(), 2);
    assert_eq!(maps[1].column().get(), 1);
    assert_eq!(maps[1].owned().row(), 2);
    assert_eq!(maps[1].available().row(), 3);

    let map_error = |suffix: &[u8]| match decode_logical(&long_value_definition(suffix)) {
        Err(TableDefinitionError::LongValueMap(error)) => Some(error),
        _ => None,
    };
    assert!(matches!(
        map_error(&suffix[..19]),
        Some(LongValueMapError::InvalidSuffixLength { length: 19 })
    ));
    assert!(matches!(
        map_error(&group(1, 0, 1, page)),
        Some(LongValueMapError::MissingColumn { ordinal: 2 })
    ));
    let mut both = Vec::new();
    both.extend_from_slice(&group(1, 0, 1, page));
    both.extend_from_slice(&group(1, 0, 1, page));
    assert!(matches!(
        map_error(&both),
        Some(LongValueMapError::DuplicateColumn { ordinal: 1 })
    ));
    let mut out_of_range = suffix.clone();
    out_of_range[..2].copy_from_slice(&5_u16.to_le_bytes());
    assert!(matches!(
        map_error(&out_of_range),
        Some(LongValueMapError::InvalidColumnOrdinal {
            group: 0,
            ordinal: 5,
            column_count: 3,
        })
    ));
    let mut scalar = suffix.clone();
    scalar[..2].copy_from_slice(&0_u16.to_le_bytes());
    assert!(matches!(
        map_error(&scalar),
        Some(LongValueMapError::NotLongValueColumn {
            ordinal: 0,
            physical_type: ColumnPhysicalType::Long,
        })
    ));
    let mut zero_page = suffix.clone();
    zero_page[3] = 0;
    assert!(matches!(
        map_error(&zero_page),
        Some(LongValueMapError::InvalidReference {
            role: "owned",
            source: None,
            ..
        })
    ));
    let mut beyond = suffix.clone();
    beyond[7] = 99;
    assert!(matches!(
        map_error(&beyond),
        Some(LongValueMapError::InvalidReference {
            role: "available",
            source: Some(_),
            ..
        })
    ));
    let mut wrong_kind = suffix.clone();
    wrong_kind[3] = INDEX_ROOT as u8;
    assert!(matches!(
        map_error(&wrong_kind),
        Some(LongValueMapError::InvalidMapRow {
            ordinal: 2,
            role: "owned",
            source: UsageMapError::ExpectedDataPage { .. },
            ..
        })
    ));
    let mut missing_row = suffix;
    missing_row[16] = 9;
    assert!(matches!(
        map_error(&missing_row),
        Some(LongValueMapError::InvalidMapRow {
            ordinal: 1,
            role: "available",
            source: UsageMapError::RowOutOfBounds { row: 9, .. },
            ..
        })
    ));
    Ok(())
}

#[test]
fn ordinary_logical_aliases_share_one_physical_index() -> TestResult {
    let first = ordinary_logical(0, 0);
    let mut second = first;
    second[..4].copy_from_slice(&1_u32.to_le_bytes());
    let aliases = |second: [u8; 20]| {
        build_definition(
            USER_MARKER,
            &[(4, 3, 0, 4, b"Id".to_vec())],
            &[physical_index(0)],
            &[(first, b"FkA"), (second, b"FkB")],
            &[],
        )
    };
    let definition = decode_logical(&aliases(second))?;
    assert_eq!(definition.physical_indexes().len(), 1);
    assert_eq!(definition.indexes().len(), 2);
    for (index, expected) in definition.indexes().iter().zip([first, second]) {
        assert_eq!(index.physical_index(), 0);
        assert_eq!(index.kind(), IndexDefinitionKind::Ordinary);
        assert_eq!(index.raw_record(), &expected);
    }
    second[4..8].copy_from_slice(&1_u32.to_le_bytes());
    assert!(matches!(
        decode_logical(&aliases(second)),
        Err(TableDefinitionError::Index(
            IndexDefinitionError::InvalidPhysicalIndexOrdinal {
                logical_index: 1,
                ordinal: 1,
                physical_count: 1,
            }
        ))
    ));
    Ok(())
}
