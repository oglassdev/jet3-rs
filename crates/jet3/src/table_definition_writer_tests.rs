use super::{
    TableDefinitionSpec, TableDefinitionWriteError, encode_table_definition, table_definition_len,
};
use crate::column_definition_writer::nz;
use crate::{
    ByteCount, ColumnPhysicalType, ColumnSpec, ColumnStorageClass, ColumnStorageKind, ColumnType,
    DatabaseReader, Error, IndexDefinitionKind, IndexDirection, IndexFieldSpec, JET3_PAGE_SIZE,
    LogicalIndexKindSpec, LogicalIndexSpec, LongValueMapSpec, MapRowLocator, PageNumber,
    PhysicalIndexFlagsSpec, PhysicalIndexSpec, ReadLimits, RelationshipSide, ResourceBudget,
    ResourceLimitKind, ResourceLimits, SliceSource, SystemColumnClassSpec, TableDefinition,
    TableDefinitionKind,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const ROOT: u64 = 1;
const MAP_PAGE: u64 = 2;
const INDEX_ROOT: u64 = 3;
const RELATED_ROOT: u64 = 5;

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn all_type_columns() -> Vec<ColumnSpec<'static>> {
    vec![
        ColumnSpec::new(b"Id", ColumnType::AutoIncrement),
        ColumnSpec::new(b"Flag", ColumnType::Boolean),
        ColumnSpec::new(b"Small", ColumnType::Byte),
        ColumnSpec::new(b"Short", ColumnType::Integer),
        ColumnSpec::new(b"Money", ColumnType::Currency),
        ColumnSpec::new(b"Ratio", ColumnType::Single),
        ColumnSpec::new(b"Precise", ColumnType::Double),
        ColumnSpec::new(b"When", ColumnType::DateTime),
        ColumnSpec::new(b"Blob", ColumnType::Binary { max_len: nz(16) }),
        ColumnSpec::new(b"Caf\xe9", ColumnType::Text { max_len: nz(50) }),
        ColumnSpec::new(b"Code", ColumnType::FixedText { len: nz(3) }),
        ColumnSpec::new(b"Ole", ColumnType::LongBinary),
        ColumnSpec::new(b"Notes", ColumnType::Memo),
        ColumnSpec::new(b"Rid", ColumnType::Guid),
    ]
}

fn physical(fields: &[IndexFieldSpec], unique: bool, required: bool) -> PhysicalIndexSpec<'_> {
    PhysicalIndexSpec {
        fields,
        usage_map_page: PageNumber::new(MAP_PAGE),
        usage_map_row: 2,
        root: PageNumber::new(INDEX_ROOT),
        flags: match (unique, required) {
            (false, false) => PhysicalIndexFlagsSpec::Ordinary,
            (true, false) => PhysicalIndexFlagsSpec::Unique,
            (false, true) => PhysicalIndexFlagsSpec::Required,
            (true, true) => PhysicalIndexFlagsSpec::UniqueRequired,
        },
        entry_count: 0,
    }
}

fn spec<'a>(
    columns: &'a [ColumnSpec<'a>],
    physical_indexes: &'a [PhysicalIndexSpec<'a>],
    indexes: &'a [LogicalIndexSpec<'a>],
) -> TableDefinitionSpec<'a> {
    let long_value_maps = if columns.iter().any(|column| {
        matches!(
            column.physical_type(),
            ColumnPhysicalType::Memo | ColumnPhysicalType::LongBinary
        )
    }) {
        &LONG_VALUE_MAPS[..]
    } else {
        &[]
    };
    TableDefinitionSpec {
        kind: TableDefinitionKind::User,
        columns,
        system_column_classes: &[],
        physical_indexes,
        indexes,
        owned_map: MapRowLocator::new(PageNumber::new(MAP_PAGE), 0),
        available_map: MapRowLocator::new(PageNumber::new(MAP_PAGE), 1),
        row_count: 0,
        long_value_maps,
    }
}

/// One long-value map group each for `Ole` (ordinal 11) and `Notes` (12).
const LONG_VALUE_SUFFIX: [u8; 20] = [
    11,
    0,
    2,
    MAP_PAGE as u8,
    0,
    0,
    3,
    MAP_PAGE as u8,
    0,
    0,
    12,
    0,
    2,
    MAP_PAGE as u8,
    0,
    0,
    3,
    MAP_PAGE as u8,
    0,
    0,
];

const LONG_VALUE_MAPS: [LongValueMapSpec; 2] = [
    LongValueMapSpec {
        column: 11,
        owned: MapRowLocator::new(PageNumber::new(MAP_PAGE), 2),
        available: MapRowLocator::new(PageNumber::new(MAP_PAGE), 3),
    },
    LongValueMapSpec {
        column: 12,
        owned: MapRowLocator::new(PageNumber::new(MAP_PAGE), 2),
        available: MapRowLocator::new(PageNumber::new(MAP_PAGE), 3),
    },
];

fn decode(logical: &[u8]) -> Result<TableDefinition, Box<dyn std::error::Error>> {
    let mut bytes = vec![0_u8; 6 * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    let root = ROOT as usize * PAGE_BYTES;
    bytes[root..root + logical.len()].copy_from_slice(logical);
    let map = &mut bytes[MAP_PAGE as usize * PAGE_BYTES..(MAP_PAGE as usize + 1) * PAGE_BYTES];
    map[0] = 1;
    map[8..10].copy_from_slice(&4_u16.to_le_bytes());
    for row in 0..4 {
        let start = (PAGE_BYTES - 8 * (row + 1)) as u16;
        map[10 + 2 * row..12 + 2 * row].copy_from_slice(&start.to_le_bytes());
    }
    bytes[INDEX_ROOT as usize * PAGE_BYTES] = 4;
    bytes[RELATED_ROOT as usize * PAGE_BYTES] = 2;
    let mut budget = ResourceBudget::new(ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    )));
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    Ok(database.table_definition(PageNumber::new(ROOT), &mut budget)?)
}

#[test]
fn round_trips_every_column_type_and_index_kind() -> Result<(), Box<dyn std::error::Error>> {
    let columns = all_type_columns();
    let primary_fields = [IndexFieldSpec {
        column: 0,
        direction: IndexDirection::Ascending,
    }];
    let composite_fields = [
        IndexFieldSpec {
            column: 10,
            direction: IndexDirection::Descending,
        },
        IndexFieldSpec {
            column: 3,
            direction: IndexDirection::Ascending,
        },
    ];
    let physical_indexes = [
        physical(&primary_fields, true, true),
        physical(&composite_fields, false, false),
    ];
    let indexes = [
        LogicalIndexSpec {
            name: b"CodeSeq",
            physical_index: 1,
            kind: LogicalIndexKindSpec::Ordinary,
        },
        LogicalIndexSpec {
            name: b"PrimaryKey",
            physical_index: 0,
            kind: LogicalIndexKindSpec::Primary,
        },
        LogicalIndexSpec {
            name: b".rB",
            physical_index: 0,
            kind: LogicalIndexKindSpec::Relationship {
                side: RelationshipSide::PrimaryTable,
                related_table: PageNumber::new(RELATED_ROOT),
                raw_selector: 1,
                relation_ordinal: 0,
                cascade_updates: true,
                cascade_deletes: false,
            },
        },
    ];
    let spec = spec(&columns, &physical_indexes, &indexes);
    let mut output = vec![0xa5_u8; PAGE_BYTES];
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let length = encode_table_definition(&spec, &mut output, &mut budget)?;
    assert_eq!(length.get() as usize, table_definition_len(&spec)?);
    let decoded = decode(&output[..length.get() as usize])?;

    assert_eq!(decoded.logical_length(), length.get() as u32);
    assert_eq!(decoded.maps().owned().page(), PageNumber::new(MAP_PAGE));
    assert_eq!(decoded.maps().available().row(), 1);
    assert_eq!(decoded.raw_suffix(), &LONG_VALUE_SUFFIX);
    assert_eq!(decoded.long_value_maps().len(), 2);
    assert_eq!(decoded.columns().len(), columns.len());
    for (column, expected) in decoded.columns().iter().zip(&columns) {
        assert_eq!(column.name().raw_bytes(), expected.name());
        assert_eq!(column.physical_type(), expected.physical_type());
        assert_eq!(column.size(), expected.size());
        assert_eq!(
            matches!(column.storage(), ColumnStorageClass::Fixed { .. }),
            expected.storage() == ColumnStorageKind::Fixed
        );
    }
    assert!(decoded.columns()[0].auto_increment());
    assert_eq!(
        decoded.columns()[1].storage(),
        ColumnStorageClass::Fixed { offset: 4 }
    );
    assert_eq!(
        decoded.columns()[2].storage(),
        ColumnStorageClass::Fixed { offset: 4 }
    );
    assert_eq!(
        decoded.columns()[13].storage(),
        ColumnStorageClass::Fixed { offset: 38 }
    );
    assert_eq!(
        decoded.columns()[12].storage(),
        ColumnStorageClass::Variable { index: 3 }
    );

    let composite = &decoded.physical_indexes()[1];
    assert_eq!(composite.fields().len(), 2);
    assert_eq!(composite.fields()[0].column().get(), 10);
    assert_eq!(
        composite.fields()[0].direction(),
        IndexDirection::Descending
    );
    assert_eq!(composite.fields()[1].direction(), IndexDirection::Ascending);
    assert!(!composite.unique());
    assert_eq!(composite.usage_map().row(), 2);
    assert_eq!(composite.root(), PageNumber::new(INDEX_ROOT));
    assert_eq!(decoded.physical_indexes()[0].raw_flags(), 0x09);

    assert_eq!(decoded.indexes()[0].kind(), IndexDefinitionKind::Ordinary);
    assert_eq!(decoded.indexes()[0].physical_index(), 1);
    assert_eq!(decoded.indexes()[1].kind(), IndexDefinitionKind::Primary);
    assert_eq!(decoded.indexes()[1].name().raw_bytes(), b"PrimaryKey");
    let IndexDefinitionKind::Relationship(relation) = decoded.indexes()[2].kind() else {
        return Err("expected relationship".into());
    };
    assert_eq!(relation.side(), RelationshipSide::PrimaryTable);
    assert_eq!(relation.related_table(), PageNumber::new(RELATED_ROOT));
    assert_eq!(relation.raw_selector(), 1);
    assert!(relation.cascade_updates());
    assert!(!relation.cascade_deletes());
    Ok(())
}

#[test]
fn round_trips_typed_system_marker_columns_flags_counts_and_maps() -> TestResult {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Payload", ColumnType::LongBinary),
    ];
    let fields = [IndexFieldSpec {
        column: 0,
        direction: IndexDirection::Ascending,
    }];
    let physical = [PhysicalIndexSpec {
        fields: &fields,
        usage_map_page: PageNumber::new(MAP_PAGE),
        usage_map_row: 2,
        root: PageNumber::new(INDEX_ROOT),
        flags: PhysicalIndexFlagsSpec::Unique,
        entry_count: 7,
    }];
    let logical = [LogicalIndexSpec {
        name: b"Id",
        physical_index: 0,
        kind: LogicalIndexKindSpec::Primary,
    }];
    let maps = [LongValueMapSpec {
        column: 1,
        owned: MapRowLocator::new(PageNumber::new(MAP_PAGE), 2),
        available: MapRowLocator::new(PageNumber::new(MAP_PAGE), 3),
    }];
    let system = TableDefinitionSpec {
        kind: TableDefinitionKind::System,
        columns: &columns,
        system_column_classes: &[
            SystemColumnClassSpec::Fixed,
            SystemColumnClassSpec::Variable,
        ],
        physical_indexes: &physical,
        indexes: &logical,
        owned_map: MapRowLocator::new(PageNumber::new(MAP_PAGE), 0),
        available_map: MapRowLocator::new(PageNumber::new(MAP_PAGE), 1),
        row_count: 9,
        long_value_maps: &maps,
    };
    let mut output = [0_u8; PAGE_BYTES];
    let length = encode_table_definition(&system, &mut output, &mut budget())?;
    assert_eq!(output[12..16], 9_u32.to_le_bytes());
    assert_eq!(output[20], 0x53);
    assert_eq!(output[47..51], 7_u32.to_le_bytes());

    let decoded = decode(&output[..length.get() as usize])?;
    assert_eq!(decoded.kind(), TableDefinitionKind::System);
    assert_eq!(decoded.columns()[0].raw_class_flags(), 0x13);
    assert_eq!(decoded.columns()[1].raw_class_flags(), 0x12);
    assert_eq!(decoded.columns()[1].raw_record()[5..9], [0, 0, 0, 0]);
    assert_eq!(decoded.physical_indexes()[0].raw_flags(), 0x01);
    assert_eq!(
        decoded.physical_indexes()[0].sourced_prefix()[4..8],
        7_u32.to_le_bytes()
    );
    assert_eq!(decoded.long_value_maps()[0].column().get(), 1);

    let missing = TableDefinitionSpec {
        system_column_classes: &[],
        ..system
    };
    assert!(matches!(
        encode_table_definition(&missing, &mut [0; PAGE_BYTES], &mut budget()),
        Err(TableDefinitionWriteError::InvalidSystemColumnClassCount { .. })
    ));
    let invalid = TableDefinitionSpec {
        system_column_classes: &[SystemColumnClassSpec::Fixed, SystemColumnClassSpec::Binary],
        ..system
    };
    assert!(matches!(
        encode_table_definition(&invalid, &mut [0; PAGE_BYTES], &mut budget()),
        Err(TableDefinitionWriteError::InvalidSystemColumnClass { .. })
    ));
    Ok(())
}

#[test]
fn rejects_cross_kind_flags_and_incomplete_typed_long_value_maps() {
    let columns = [ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let mut missing = spec(&columns, &[], &[]);
    missing.long_value_maps = &[];
    assert_eq!(
        table_definition_len(&missing).and_then(|length| {
            encode_table_definition(&missing, &mut vec![0; length], &mut budget()).map(|_| length)
        }),
        Err(TableDefinitionWriteError::MissingLongValueMap { column: 0 })
    );
    let duplicate_maps = [LongValueMapSpec {
        column: 0,
        owned: MapRowLocator::new(PageNumber::new(MAP_PAGE), 0),
        available: MapRowLocator::new(PageNumber::new(MAP_PAGE), 1),
    }; 2];
    missing.long_value_maps = &duplicate_maps;
    assert!(matches!(
        encode_table_definition(&missing, &mut [0; PAGE_BYTES], &mut budget()),
        Err(TableDefinitionWriteError::TooManyLongValueMaps {
            count: 2,
            maximum: 1,
        })
    ));

    let scalar_columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let fields = [IndexFieldSpec {
        column: 0,
        direction: IndexDirection::Ascending,
    }];
    let physical = [PhysicalIndexSpec {
        fields: &fields,
        usage_map_page: PageNumber::new(MAP_PAGE),
        usage_map_row: 0,
        root: PageNumber::new(INDEX_ROOT),
        flags: PhysicalIndexFlagsSpec::SystemUninterpreted,
        entry_count: 0,
    }];
    let logical = [LogicalIndexSpec {
        name: b"Id",
        physical_index: 0,
        kind: LogicalIndexKindSpec::Ordinary,
    }];
    let user = TableDefinitionSpec {
        kind: TableDefinitionKind::User,
        columns: &scalar_columns,
        system_column_classes: &[],
        physical_indexes: &physical,
        indexes: &logical,
        owned_map: MapRowLocator::new(PageNumber::new(MAP_PAGE), 0),
        available_map: MapRowLocator::new(PageNumber::new(MAP_PAGE), 1),
        row_count: 0,
        long_value_maps: &[],
    };
    assert!(matches!(
        encode_table_definition(&user, &mut [0; PAGE_BYTES], &mut budget()),
        Err(TableDefinitionWriteError::InvalidPhysicalFlags {
            physical_index: 0,
            kind: TableDefinitionKind::User,
            flags: PhysicalIndexFlagsSpec::SystemUninterpreted,
        })
    ));
}

#[test]
fn rejects_structural_errors_before_writing() {
    let mut output = vec![0_u8; PAGE_BYTES];
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let long_name = [b'x'; 256];
    let cases: Vec<(Vec<ColumnSpec<'_>>, TableDefinitionWriteError)> = vec![
        (
            vec![ColumnSpec::new(b"A", ColumnType::Long); 256],
            TableDefinitionWriteError::TooManyColumns {
                count: 256,
                maximum: 255,
            },
        ),
        (
            vec![ColumnSpec::new(&long_name, ColumnType::Long)],
            TableDefinitionWriteError::NameTooLong {
                role: "column",
                ordinal: 0,
                length: 256,
                maximum: 255,
            },
        ),
        (
            vec![
                ColumnSpec::new(b"A", ColumnType::Long),
                ColumnSpec::new(b"A", ColumnType::Long),
            ],
            TableDefinitionWriteError::DuplicateName {
                role: "column",
                ordinal: 1,
            },
        ),
    ];
    for (columns, expected) in cases {
        if matches!(&expected, TableDefinitionWriteError::TooManyColumns { .. }) {
            assert_eq!(
                table_definition_len(&spec(&columns, &[], &[])),
                Err(expected.clone())
            );
        }
        assert_eq!(
            encode_table_definition(&spec(&columns, &[], &[]), &mut output, &mut budget),
            Err(expected)
        );
    }

    let names: [&[u8]; 9] = [b"A", b"B", b"C", b"D", b"E", b"F", b"G", b"H", b"I"];
    let oversized_columns: Vec<_> = names
        .into_iter()
        .map(|name| ColumnSpec::new(name, ColumnType::FixedText { len: nz(255) }))
        .collect();
    assert_eq!(
        encode_table_definition(
            &spec(&oversized_columns, &[], &[]),
            &mut output,
            &mut budget
        ),
        Err(TableDefinitionWriteError::RowLayoutTooLarge {
            minimum: 2_298,
            maximum: PAGE_BYTES - 12,
        })
    );

    let columns = [ColumnSpec::new(b"Notes", ColumnType::Memo)];
    let fields = [IndexFieldSpec {
        column: 0,
        direction: IndexDirection::Ascending,
    }];
    let physical_indexes = [physical(&fields, false, false)];
    assert_eq!(
        encode_table_definition(
            &spec(&columns, &physical_indexes, &[]),
            &mut output,
            &mut budget
        ),
        Err(TableDefinitionWriteError::UnsupportedKeyColumn {
            physical_index: 0,
            ordinal: 0,
            physical_type: ColumnPhysicalType::Memo,
        })
    );

    let columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let mut invalid_map = spec(&columns, &[], &[]);
    invalid_map.owned_map = MapRowLocator::new(PageNumber::new(0), 0);
    assert_eq!(
        encode_table_definition(&invalid_map, &mut output, &mut budget),
        Err(TableDefinitionWriteError::InvalidMapReference {
            role: "owned",
            page: PageNumber::new(0),
        })
    );
    invalid_map.owned_map = MapRowLocator::new(PageNumber::new(0x0100_0000), 0);
    assert_eq!(
        encode_table_definition(&invalid_map, &mut output, &mut budget),
        Err(TableDefinitionWriteError::InvalidMapReference {
            role: "owned",
            page: PageNumber::new(0x0100_0000),
        })
    );
    assert!(output.iter().all(|byte| *byte == 0));

    let columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let fields = [IndexFieldSpec {
        column: 0,
        direction: IndexDirection::Ascending,
    }];
    let physical_indexes = [physical(&fields, false, false)];
    let unreferenced = spec(&columns, &physical_indexes, &[]);
    assert_eq!(
        encode_table_definition(&unreferenced, &mut output, &mut budget),
        Err(TableDefinitionWriteError::UnreferencedPhysicalIndex { physical_index: 0 })
    );
    let primary = [LogicalIndexSpec {
        name: b"PK",
        physical_index: 0,
        kind: LogicalIndexKindSpec::Primary,
    }];
    assert_eq!(
        encode_table_definition(
            &spec(&columns, &physical_indexes, &primary),
            &mut output,
            &mut budget
        ),
        Err(TableDefinitionWriteError::InvalidPrimaryFlags {
            logical_index: 0,
            raw: 0,
        })
    );
}

#[test]
fn rejects_small_output_and_exhausted_budget() -> Result<(), Box<dyn std::error::Error>> {
    let columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let spec = spec(&columns, &[], &[]);
    let needed = table_definition_len(&spec)?;
    let mut output = vec![0_u8; needed];
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    assert_eq!(
        encode_table_definition(&spec, &mut output[..needed - 1], &mut budget),
        Err(TableDefinitionWriteError::OutputTooSmall {
            needed,
            available: needed - 1,
        })
    );
    let mut exhausted =
        ResourceBudget::new(ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(3)));
    assert_eq!(
        encode_table_definition(&spec, &mut output, &mut exhausted),
        Err(TableDefinitionWriteError::Resource(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::EncodedBytes,
                requested: 4,
                maximum: 3,
            }
        ))
    );
    let error = TableDefinitionWriteError::DuplicatePrimaryIndex;
    assert!(
        error
            .to_string()
            .contains("table definition encoding failed")
    );
    assert!(encode_table_definition(&spec, &mut output, &mut budget).is_ok());
    assert!(decode(&output).is_ok());
    Ok(())
}
