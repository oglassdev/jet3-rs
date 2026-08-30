use super::{
    TableDefinitionSpec, TableDefinitionWriteError, encode_table_definition, table_definition_len,
};
use crate::{
    ByteCount, ColumnPhysicalType, ColumnSpec, ColumnStorageClass, ColumnStorageKind,
    DatabaseReader, Error, IndexDefinitionKind, IndexDirection, IndexFieldSpec, JET3_PAGE_SIZE,
    LogicalIndexKindSpec, LogicalIndexSpec, MapRowLocator, PageNumber, PhysicalIndexSpec,
    ReadLimits, RelationshipSide, ResourceBudget, ResourceLimitKind, ResourceLimits, SliceSource,
    TableDefinition,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const ROOT: u64 = 1;
const MAP_PAGE: u64 = 2;
const INDEX_ROOT: u64 = 3;
const RELATED_ROOT: u64 = 5;

fn all_type_columns() -> Vec<ColumnSpec<'static>> {
    use ColumnPhysicalType as T;
    use ColumnStorageKind::{Fixed, Variable};
    vec![
        ColumnSpec::new(b"Id", T::Long, Fixed, 4).with_auto_increment(),
        ColumnSpec::new(b"Flag", T::Boolean, Fixed, 1),
        ColumnSpec::new(b"Small", T::Byte, Fixed, 1),
        ColumnSpec::new(b"Short", T::Integer, Fixed, 2),
        ColumnSpec::new(b"Money", T::Currency, Fixed, 8),
        ColumnSpec::new(b"Ratio", T::Single, Fixed, 4),
        ColumnSpec::new(b"Precise", T::Double, Fixed, 8),
        ColumnSpec::new(b"When", T::DateTime, Fixed, 8),
        ColumnSpec::new(b"Blob", T::Binary, Variable, 16),
        ColumnSpec::new(b"Caf\xe9", T::Text, Variable, 50),
        ColumnSpec::new(b"Code", T::Text, Fixed, 3),
        ColumnSpec::new(b"Ole", T::LongBinary, Variable, 0),
        ColumnSpec::new(b"Notes", T::Memo, Variable, 0),
        ColumnSpec::new(b"Rid", T::Guid, Fixed, 16),
    ]
}

fn physical(fields: &[IndexFieldSpec], unique: bool, required: bool) -> PhysicalIndexSpec<'_> {
    PhysicalIndexSpec {
        fields,
        usage_map_page: PageNumber::new(MAP_PAGE),
        usage_map_row: 2,
        root: PageNumber::new(INDEX_ROOT),
        unique,
        required,
    }
}

fn spec<'a>(
    columns: &'a [ColumnSpec<'a>],
    physical_indexes: &'a [PhysicalIndexSpec<'a>],
    indexes: &'a [LogicalIndexSpec<'a>],
) -> TableDefinitionSpec<'a> {
    TableDefinitionSpec {
        columns,
        physical_indexes,
        indexes,
        owned_map: MapRowLocator::new(PageNumber::new(MAP_PAGE), 0),
        available_map: MapRowLocator::new(PageNumber::new(MAP_PAGE), 1),
        raw_suffix: &[7, 7],
    }
}

fn decode(logical: &[u8]) -> Result<TableDefinition, Box<dyn std::error::Error>> {
    let mut bytes = vec![0_u8; 6 * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    let root = ROOT as usize * PAGE_BYTES;
    bytes[root..root + logical.len()].copy_from_slice(logical);
    bytes[MAP_PAGE as usize * PAGE_BYTES] = 1;
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
    assert_eq!(decoded.raw_suffix(), &[7, 7]);
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
fn rejects_structural_errors_before_writing() {
    let mut output = vec![0_u8; PAGE_BYTES];
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let long_name = [b'x'; 256];
    let cases: Vec<(Vec<ColumnSpec<'_>>, TableDefinitionWriteError)> = vec![
        (
            vec![ColumnSpec::new(b"A", ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4); 256],
            TableDefinitionWriteError::TooManyColumns {
                count: 256,
                maximum: 255,
            },
        ),
        (
            vec![ColumnSpec::new(
                &long_name,
                ColumnPhysicalType::Long,
                ColumnStorageKind::Fixed,
                4,
            )],
            TableDefinitionWriteError::NameTooLong {
                role: "column",
                ordinal: 0,
                length: 256,
                maximum: 255,
            },
        ),
        (
            vec![
                ColumnSpec::new(b"A", ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4),
                ColumnSpec::new(b"A", ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4),
            ],
            TableDefinitionWriteError::DuplicateName {
                role: "column",
                ordinal: 1,
            },
        ),
        (
            vec![ColumnSpec::new(
                b"A",
                ColumnPhysicalType::Memo,
                ColumnStorageKind::Fixed,
                0,
            )],
            TableDefinitionWriteError::UnsupportedColumnClass {
                ordinal: 0,
                physical_type: ColumnPhysicalType::Memo,
                storage: ColumnStorageKind::Fixed,
            },
        ),
        (
            vec![ColumnSpec::new(
                b"A",
                ColumnPhysicalType::Guid,
                ColumnStorageKind::Fixed,
                8,
            )],
            TableDefinitionWriteError::UnsupportedColumnSize {
                ordinal: 0,
                physical_type: ColumnPhysicalType::Guid,
                size: 8,
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
        .map(|name| {
            ColumnSpec::new(
                name,
                ColumnPhysicalType::Text,
                ColumnStorageKind::Fixed,
                255,
            )
        })
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

    let columns = [ColumnSpec::new(
        b"Notes",
        ColumnPhysicalType::Memo,
        ColumnStorageKind::Variable,
        0,
    )];
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

    let columns = [ColumnSpec::new(
        b"Id",
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        4,
    )];
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

    let columns = [ColumnSpec::new(
        b"Id",
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        4,
    )];
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
    let columns = [ColumnSpec::new(
        b"Id",
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        4,
    )];
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
