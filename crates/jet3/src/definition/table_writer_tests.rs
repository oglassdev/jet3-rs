use super::table_tests::decode_logical;
use super::table_writer::{
    TableDefinitionSpec, TableDefinitionWriteError, encode_table_definition, table_definition_len,
};
use crate::{
    ByteCount, ColumnPhysicalType, ColumnSpec, ColumnStorageClass, ColumnStorageKind, ColumnType,
    Error, IndexDefinitionKind, IndexDirection, IndexFieldSpec, LogicalIndexKindSpec,
    LogicalIndexSpec, LongValueMapSpec, MapRowLocator, PAGE_BYTES, PageNumber,
    PhysicalIndexFlagsSpec, PhysicalIndexSpec, RelationshipSide, ResourceBudget, ResourceLimitKind,
    ResourceLimits, SystemColumnClassSpec, TableDefinitionKind, definition::column_writer::nz,
};

use crate::testkit::{TestResult, budget};

const MAP_PAGE: u64 = 2;
const INDEX_ROOT: u64 = 3;
const RELATED_ROOT: u64 = 5;
const FIRST_ASCENDING: [IndexFieldSpec; 1] = [IndexFieldSpec {
    column: 0,
    direction: IndexDirection::Ascending,
}];

const fn map_row(row: u8) -> MapRowLocator {
    MapRowLocator::new(PageNumber::new(MAP_PAGE), row)
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

fn physical(fields: &[IndexFieldSpec], flags: PhysicalIndexFlagsSpec) -> PhysicalIndexSpec<'_> {
    PhysicalIndexSpec {
        fields,
        usage_map_page: PageNumber::new(MAP_PAGE),
        usage_map_row: 2,
        root: PageNumber::new(INDEX_ROOT),
        flags,
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
        owned_map: map_row(0),
        available_map: map_row(1),
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
        owned: map_row(2),
        available: map_row(3),
    },
    LongValueMapSpec {
        column: 12,
        owned: map_row(2),
        available: map_row(3),
    },
];

/// Encodes into a zeroed page with a default budget, leaving the page
/// untouched on refusal.
fn encode(spec: &TableDefinitionSpec<'_>) -> Result<Vec<u8>, TableDefinitionWriteError> {
    let mut output = vec![0_u8; PAGE_BYTES];
    let result = encode_into(spec, &mut output, &mut budget());
    if result.is_err() {
        assert!(output.iter().all(|byte| *byte == 0));
    }
    output.truncate(result?.get() as usize);
    Ok(output)
}

fn encode_into(
    spec: &TableDefinitionSpec<'_>,
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<ByteCount, TableDefinitionWriteError> {
    encode_table_definition(
        spec,
        output,
        crate::index::key::text::ENCODING_CONTEXT,
        budget,
    )
}

#[test]
fn round_trips_every_column_type_and_index_kind() -> TestResult {
    let columns = all_type_columns();
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
        physical(&FIRST_ASCENDING, PhysicalIndexFlagsSpec::UniqueRequired),
        physical(&composite_fields, PhysicalIndexFlagsSpec::Ordinary),
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
    let length = encode_into(&spec, &mut output, &mut budget())?.get() as usize;
    assert_eq!(length, table_definition_len(&spec)?);
    let decoded = decode_logical(&output[..length])?;

    assert_eq!(decoded.logical_length() as usize, length);
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
    for (ordinal, storage) in [
        (1, ColumnStorageClass::Fixed { offset: 0 }),
        (2, ColumnStorageClass::Fixed { offset: 4 }),
        (13, ColumnStorageClass::Fixed { offset: 38 }),
        (12, ColumnStorageClass::Variable { index: 3 }),
    ] {
        assert_eq!(decoded.columns()[ordinal].storage(), storage);
    }

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
    let physical = [PhysicalIndexSpec {
        entry_count: 7,
        ..physical(&FIRST_ASCENDING, PhysicalIndexFlagsSpec::Unique)
    }];
    let logical = [LogicalIndexSpec {
        name: b"Id",
        physical_index: 0,
        kind: LogicalIndexKindSpec::Primary,
    }];
    let maps = [LongValueMapSpec {
        column: 1,
        owned: map_row(2),
        available: map_row(3),
    }];
    let system = TableDefinitionSpec {
        kind: TableDefinitionKind::System,
        system_column_classes: &[
            SystemColumnClassSpec::Fixed,
            SystemColumnClassSpec::Variable,
        ],
        row_count: 9,
        long_value_maps: &maps,
        ..spec(&columns, &physical, &logical)
    };
    let output = encode(&system)?;
    assert_eq!(output[12..16], 9_u32.to_le_bytes());
    assert_eq!(output[20], 0x53);
    assert_eq!(output[47..51], 7_u32.to_le_bytes());

    let decoded = decode_logical(&output)?;
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

    assert!(matches!(
        encode(&TableDefinitionSpec {
            system_column_classes: &[],
            ..system
        }),
        Err(TableDefinitionWriteError::InvalidSystemColumnClassCount { .. })
    ));
    assert!(matches!(
        encode(&TableDefinitionSpec {
            system_column_classes: &[SystemColumnClassSpec::Fixed, SystemColumnClassSpec::Binary],
            ..system
        }),
        Err(TableDefinitionWriteError::InvalidSystemColumnClass { .. })
    ));
    Ok(())
}

#[test]
fn rejects_cross_kind_flags_and_incomplete_typed_long_value_maps() {
    let columns = [ColumnSpec::new(b"Payload", ColumnType::LongBinary)];
    let mut maps = spec(&columns, &[], &[]);
    maps.long_value_maps = &[];
    assert_eq!(
        encode(&maps),
        Err(TableDefinitionWriteError::MissingLongValueMap { column: 0 })
    );
    let duplicate_maps = [LongValueMapSpec {
        column: 0,
        owned: map_row(0),
        available: map_row(1),
    }; 2];
    maps.long_value_maps = &duplicate_maps;
    assert_eq!(
        encode(&maps),
        Err(TableDefinitionWriteError::TooManyLongValueMaps {
            count: 2,
            maximum: 1,
        })
    );

    let scalar_columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let physical = [physical(
        &FIRST_ASCENDING,
        PhysicalIndexFlagsSpec::SystemUninterpreted,
    )];
    let logical = [LogicalIndexSpec {
        name: b"Id",
        physical_index: 0,
        kind: LogicalIndexKindSpec::Ordinary,
    }];
    assert_eq!(
        encode(&spec(&scalar_columns, &physical, &logical)),
        Err(TableDefinitionWriteError::InvalidPhysicalFlags {
            physical_index: 0,
            kind: TableDefinitionKind::User,
            flags: PhysicalIndexFlagsSpec::SystemUninterpreted,
        })
    );
}

#[test]
fn rejects_structural_errors_before_writing() {
    let too_many = vec![ColumnSpec::new(b"A", ColumnType::Long); 256];
    let expected = TableDefinitionWriteError::TooManyColumns {
        count: 256,
        maximum: 255,
    };
    assert_eq!(
        table_definition_len(&spec(&too_many, &[], &[])),
        Err(expected.clone())
    );
    assert_eq!(encode(&spec(&too_many, &[], &[])), Err(expected));

    let long_name = [b'x'; 256];
    assert_eq!(
        encode(&spec(
            &[ColumnSpec::new(&long_name, ColumnType::Long)],
            &[],
            &[]
        )),
        Err(TableDefinitionWriteError::NameTooLong {
            role: "column",
            ordinal: 0,
            length: 256,
            maximum: 255,
        })
    );
    let duplicate = [
        ColumnSpec::new(b"A", ColumnType::Long),
        ColumnSpec::new(b"A", ColumnType::Long),
    ];
    assert_eq!(
        encode(&spec(&duplicate, &[], &[])),
        Err(TableDefinitionWriteError::DuplicateName {
            role: "column",
            ordinal: 1,
        })
    );

    let names: [&[u8]; 9] = [b"A", b"B", b"C", b"D", b"E", b"F", b"G", b"H", b"I"];
    let oversized_columns: Vec<_> = names
        .into_iter()
        .map(|name| ColumnSpec::new(name, ColumnType::FixedText { len: nz(255) }))
        .collect();
    assert_eq!(
        encode(&spec(&oversized_columns, &[], &[])),
        Err(TableDefinitionWriteError::RowLayoutTooLarge {
            minimum: 2_298,
            maximum: 2003,
        })
    );

    let memo = [ColumnSpec::new(b"Notes", ColumnType::Memo)];
    let ordinary = [physical(&FIRST_ASCENDING, PhysicalIndexFlagsSpec::Ordinary)];
    assert_eq!(
        encode(&spec(&memo, &ordinary, &[])),
        Err(TableDefinitionWriteError::UnsupportedKeyColumn {
            physical_index: 0,
            ordinal: 0,
            physical_type: ColumnPhysicalType::Memo,
        })
    );

    let columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let mut invalid_map = spec(&columns, &[], &[]);
    for page in [0, 0x0100_0000] {
        invalid_map.owned_map = MapRowLocator::new(PageNumber::new(page), 0);
        assert_eq!(
            encode(&invalid_map),
            Err(TableDefinitionWriteError::InvalidMapReference {
                role: "owned",
                page: PageNumber::new(page),
            })
        );
    }

    assert_eq!(
        encode(&spec(&columns, &ordinary, &[])),
        Err(TableDefinitionWriteError::UnreferencedPhysicalIndex { physical_index: 0 })
    );
    let primary = [LogicalIndexSpec {
        name: b"PK",
        physical_index: 0,
        kind: LogicalIndexKindSpec::Primary,
    }];
    assert_eq!(
        encode(&spec(&columns, &ordinary, &primary)),
        Err(TableDefinitionWriteError::InvalidPrimaryFlags {
            logical_index: 0,
            raw: 0,
        })
    );
}

#[test]
fn rejects_small_output_and_exhausted_budget() -> TestResult {
    let columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let spec = spec(&columns, &[], &[]);
    let needed = table_definition_len(&spec)?;
    let mut output = vec![0_u8; needed];
    assert_eq!(
        encode_into(&spec, &mut output[..needed - 1], &mut budget()),
        Err(TableDefinitionWriteError::OutputTooSmall {
            needed,
            available: needed - 1,
        })
    );
    let mut exhausted =
        ResourceBudget::new(ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(3)));
    assert_eq!(
        encode_into(&spec, &mut output, &mut exhausted),
        Err(TableDefinitionWriteError::Resource(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::EncodedBytes,
                requested: 4,
                maximum: 3,
            }
        ))
    );
    encode_into(&spec, &mut output, &mut budget())?;
    decode_logical(&output)?;
    Ok(())
}
