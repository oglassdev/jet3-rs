use super::super::{compose_table_database, global_map_page};
use super::*;

const IDX_TRI_COLUMNS: [ColumnSpec<'static>; 3] = [
    ColumnSpec::new(b"Id", ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4),
    ColumnSpec::new(
        b"Code",
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        4,
    ),
    ColumnSpec::new(
        b"Sequence",
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        4,
    ),
];
const IDX_TRI_INDEXES: [IndexSpec<'static>; 3] = [
    IndexSpec {
        name: b"ZPrimary",
        fields: &[IndexFieldSpec {
            column: 0,
            direction: IndexDirection::Ascending,
        }],
        kind: IndexKind::Primary,
    },
    IndexSpec {
        name: b"MUniqueX",
        fields: &[IndexFieldSpec {
            column: 1,
            direction: IndexDirection::Descending,
        }],
        kind: IndexKind::Unique,
    },
    IndexSpec {
        name: b"ASecondx",
        fields: &[IndexFieldSpec {
            column: 2,
            direction: IndexDirection::Ascending,
        }],
        kind: IndexKind::Ordinary,
    },
];
const IDX_TRI: TableSpec<'static> = TableSpec {
    name: b"IdxTri",
    columns: &IDX_TRI_COLUMNS,
    indexes: &IDX_TRI_INDEXES,
};
const WIDE_FIELD_COUNT: usize = 70;

fn indexed_candidate_bytes() -> Result<Vec<u8>, BootstrapComposeError> {
    let mut budget = compose_budget();
    let plan = compose_table_database(&IDX_TRI, &mut budget)?;
    Ok(plan
        .pages()
        .iter()
        .flat_map(|page| page.image().as_bytes().iter().copied())
        .collect())
}

fn wide_candidate_bytes() -> CandidateResult<Vec<u8>> {
    let names = (0..WIDE_FIELD_COUNT)
        .map(|ordinal| format!("F{ordinal:03}AAAAAA").into_bytes())
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4))
        .collect::<Vec<_>>();
    let base = TableSpec {
        name: b"ContOneX",
        columns: &columns[..40],
        indexes: &[],
    };
    let mut budget = compose_budget();
    let plan = compose_table_database(&base, &mut budget)?;
    let mut pages = plan
        .pages()
        .iter()
        .map(|page| *page.image().as_bytes())
        .collect::<Vec<_>>();
    assert_eq!(pages.len(), 23);
    pages[1] = global_map_page(24, &mut budget)?.into_bytes();
    let mut logical = [0_u8; 2 * crate::PAGE_BYTES];
    let length = crate::encode_table_definition(
        &crate::TableDefinitionSpec {
            kind: TableDefinitionKind::User,
            columns: &columns,
            system_column_classes: &[],
            physical_indexes: &[],
            indexes: &[],
            owned_map: MapRowLocator::new(PageNumber::new(ALPHA_MAP_PAGE), 0),
            available_map: MapRowLocator::new(PageNumber::new(ALPHA_MAP_PAGE), 1),
            row_count: 0,
            long_value_maps: &[],
        },
        &mut logical,
        &mut budget,
    )?
    .get() as usize;
    assert_eq!(length, 2075);
    let mut root = [0_u8; crate::PAGE_BYTES];
    root.copy_from_slice(&logical[..crate::PAGE_BYTES]);
    root[4..8].copy_from_slice(&23_u32.to_le_bytes());
    let mut continuation = [0_u8; crate::PAGE_BYTES];
    continuation[..4].copy_from_slice(&logical[..4]);
    continuation[8..8 + length - crate::PAGE_BYTES]
        .copy_from_slice(&logical[crate::PAGE_BYTES..length]);
    pages[20] = root;
    pages.push(continuation);
    Ok(pages.concat())
}

type CandidateResult<T> = Result<T, Box<dyn std::error::Error>>;

fn assert_shared_candidate(bytes: &[u8], table_name: &[u8]) -> TestResult {
    let mut budget = read_budget(bytes.len());
    let source = SliceSource::new(bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let objects = database.table_definition(PageNumber::new(2), &mut budget)?;
    let mut rows = database.rows(&objects, &mut budget)?;
    for _ in 0..8 {
        rows.next_row()?.ok_or("missing system catalog row")?;
    }
    let mut row = rows.next_row()?.ok_or("missing user catalog row")?;
    assert!(matches!(
        row.value(ColumnOrdinal::new(2), TextCodePage::Windows1252)?
            .ok_or("missing table name")?
            .kind(),
        ValueKind::Text(name) if name.raw_bytes() == table_name
    ));
    assert!(matches!(
        row.value(ColumnOrdinal::new(14), TextCodePage::Windows1252)?
            .ok_or("missing table LvProp")?
            .kind(),
        ValueKind::Null
    ));
    assert!(!inline_map_bit(bytes, 1, 0, 22)?);
    assert_eq!(
        &bytes[22 * crate::PAGE_BYTES..22 * crate::PAGE_BYTES + 10],
        b"\x01\x01\xf6\x07LVAL\0\0"
    );
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    assert!(
        database
            .rows(&definition, &mut budget)?
            .next_row()?
            .is_none()
    );
    Ok(())
}

#[test]
fn the_schema_candidates_decode_to_their_preregistered_shapes() -> TestResult {
    assert_shared_candidate(&bytes(true)?, b"Alpha")?;
    let indexed = indexed_candidate_bytes()?;
    assert_shared_candidate(&indexed, b"IdxTri")?;
    assert_eq!(indexed.len(), 26 * crate::PAGE_BYTES);
    let mut budget = read_budget(indexed.len());
    let source = SliceSource::new(&indexed, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    assert_eq!(definition.columns().len(), 3);
    assert_eq!(
        definition
            .indexes()
            .iter()
            .map(|index| (
                index.name().raw_bytes(),
                index.physical_index(),
                index.kind()
            ))
            .collect::<Vec<_>>(),
        [
            (
                b"ASecondx".as_slice(),
                2,
                crate::IndexDefinitionKind::Ordinary
            ),
            (
                b"MUniqueX".as_slice(),
                1,
                crate::IndexDefinitionKind::Ordinary
            ),
            (
                b"ZPrimary".as_slice(),
                0,
                crate::IndexDefinitionKind::Primary
            ),
        ]
    );
    assert_eq!(
        definition
            .physical_indexes()
            .iter()
            .map(|index| (index.root().get(), index.raw_flags()))
            .collect::<Vec<_>>(),
        [(23, 0x09), (24, 0x01), (25, 0x00)]
    );
    assert_eq!(
        definition
            .physical_indexes()
            .iter()
            .map(|index| {
                index
                    .fields()
                    .iter()
                    .map(|field| (field.column().get(), field.direction()))
                    .collect::<Vec<_>>()
            })
            .collect::<Vec<_>>(),
        [
            vec![(0, IndexDirection::Ascending)],
            vec![(1, IndexDirection::Descending)],
            vec![(2, IndexDirection::Ascending)],
        ]
    );

    let wide = wide_candidate_bytes()?;
    assert_shared_candidate(&wide, b"ContOneX")?;
    assert_eq!(wide.len(), 24 * crate::PAGE_BYTES);
    assert!(!inline_map_bit(&wide, 1, 0, 23)?);
    assert!(inline_map_bit(&wide, 1, 0, 24)?);
    assert_eq!(
        &wide[23 * crate::PAGE_BYTES..23 * crate::PAGE_BYTES + 8],
        b"\x02\x01\x56\x43\0\0\0\0"
    );
    let mut budget = read_budget(wide.len());
    let source = SliceSource::new(&wide, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    assert_eq!(definition.logical_length(), 2075);
    assert_eq!(definition.columns().len(), WIDE_FIELD_COUNT);
    assert_eq!(definition.columns()[69].name().raw_bytes(), b"F069AAAAAA");
    assert_eq!(definition.maps().owned().page(), PageNumber::new(21));
    let mut catalog = database.catalog(&mut budget)?;
    let mut seen = false;
    while let Some(record) = catalog.next_record()? {
        if record.name().raw_bytes() == b"ContOneX" {
            seen = true;
            assert_eq!(record.table_definition(), Some(PageNumber::new(20)));
        }
    }
    assert!(seen);
    Ok(())
}

#[test]
#[ignore = "writes private deterministic candidates for the preregistered issue #178 experiment"]
fn export_lvprop_schema_candidates() -> TestResult {
    use std::path::PathBuf;

    let root = PathBuf::from(
        std::env::var_os("JET3_LVPROP_SCHEMAS_CANDIDATE_DIR")
            .ok_or("JET3_LVPROP_SCHEMAS_CANDIDATE_DIR is required")?,
    );
    export_candidate_set(
        &root,
        [
            ("lvprop-schemas-alpha.mdb", bytes(true)?),
            ("lvprop-schemas-indexed.mdb", indexed_candidate_bytes()?),
            ("lvprop-schemas-wide.mdb", wide_candidate_bytes()?),
        ],
    )
}
