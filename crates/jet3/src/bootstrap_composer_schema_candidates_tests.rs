use super::super::{compose_database, compose_table_database, global_map_page};
use super::*;
use crate::column_definition_writer::nz;
use crate::{ColumnRef, IndexColumnSpec};

const IDX_TRI_COLUMNS: [ColumnSpec<'static>; 3] = [
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"Code", ColumnType::Long),
    ColumnSpec::new(b"Sequence", ColumnType::Long),
];
const IDX_TRI_INDEXES: [IndexSpec<'static>; 3] = [
    IndexSpec {
        name: b"ZPrimary",
        fields: &[IndexColumnSpec {
            column: ColumnRef::Ordinal(0),
            direction: IndexDirection::Ascending,
        }],
        kind: IndexKind::Primary,
    },
    IndexSpec {
        name: b"MUniqueX",
        fields: &[IndexColumnSpec {
            column: ColumnRef::Ordinal(1),
            direction: IndexDirection::Descending,
        }],
        kind: IndexKind::Unique,
    },
    IndexSpec {
        name: b"ASecondx",
        fields: &[IndexColumnSpec {
            column: ColumnRef::Ordinal(2),
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

fn indexed_candidate_bytes() -> Result<Vec<u8>, ComposeError> {
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
        .map(|name| ColumnSpec::new(name, ColumnType::Long))
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
fn the_composer_reproduces_the_accepted_cont_one_x_candidate() -> TestResult {
    // EXP-0107 accepted the exact hand-assembled ContOneX image; the general
    // composer must produce the same 49,152 bytes for the same schema.
    let names = (0..WIDE_FIELD_COUNT)
        .map(|ordinal| format!("F{ordinal:03}AAAAAA").into_bytes())
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnType::Long))
        .collect::<Vec<_>>();
    let mut budget = compose_budget();
    let composed = compose_table_database(
        &TableSpec {
            name: b"ContOneX",
            columns: &columns,
            indexes: &[],
        },
        &mut budget,
    )?
    .pages()
    .iter()
    .flat_map(|page| page.image().as_bytes().iter().copied())
    .collect::<Vec<u8>>();
    let candidate = wide_candidate_bytes()?;
    assert_eq!(composed.len(), 24 * crate::PAGE_BYTES);
    assert!(
        composed == candidate,
        "composed image differs from the accepted candidate"
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

/// The exact `EXP-0087` create sequence: Alpha, Beta, Gamma, then Delta.
const QUAD_ALPHA_COLUMNS: [ColumnSpec<'static>; 1] = [ColumnSpec::new(b"Id", ColumnType::Long)];
const QUAD_BETA_COLUMNS: [ColumnSpec<'static>; 3] = [
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"Name", ColumnType::Text { max_len: nz(50) }),
    ColumnSpec::new(b"Note", ColumnType::Memo),
];
const QUAD_DELTA_COLUMNS: [ColumnSpec<'static>; 1] = [ColumnSpec::new(
    b"Label",
    ColumnType::Text { max_len: nz(30) },
)];
const QUAD_GAMMA_INDEXES: [IndexSpec<'static>; 1] = [IndexSpec {
    name: b"PrimaryKey",
    fields: &[IndexColumnSpec {
        column: ColumnRef::Ordinal(0),
        direction: IndexDirection::Ascending,
    }],
    kind: IndexKind::Primary,
}];
const QUAD_DELTA_INDEXES: [IndexSpec<'static>; 1] = [IndexSpec {
    name: b"ByLabel",
    fields: &[IndexColumnSpec {
        column: ColumnRef::Ordinal(0),
        direction: IndexDirection::Ascending,
    }],
    kind: IndexKind::Ordinary,
}];
const QUAD_TABLES: [TableSpec<'static>; 4] = [
    TableSpec {
        name: b"Alpha",
        columns: &QUAD_ALPHA_COLUMNS,
        indexes: &[],
    },
    TableSpec {
        name: b"Beta",
        columns: &QUAD_BETA_COLUMNS,
        indexes: &[],
    },
    TableSpec {
        name: b"Gamma",
        columns: &QUAD_ALPHA_COLUMNS,
        indexes: &QUAD_GAMMA_INDEXES,
    },
    TableSpec {
        name: b"Delta",
        columns: &QUAD_DELTA_COLUMNS,
        indexes: &QUAD_DELTA_INDEXES,
    },
];

fn quad_candidate_bytes() -> Result<Vec<u8>, ComposeError> {
    let mut budget = compose_budget();
    let plan = compose_database(&QUAD_TABLES, &mut budget)?;
    Ok(plan
        .pages()
        .iter()
        .flat_map(|page| page.image().as_bytes().iter().copied())
        .collect())
}

#[test]
fn the_quad_candidate_decodes_to_its_preregistered_shape() -> TestResult {
    // EXP-0087: 31 pages after the four creates, page-zero byte 1538 at 8,
    // and only the first create carrying a long-value page.
    let quad = quad_candidate_bytes()?;
    assert_eq!(quad.len(), 31 * crate::PAGE_BYTES);
    assert_eq!(quad[1538], 8);
    assert_eq!(
        &quad[22 * crate::PAGE_BYTES..22 * crate::PAGE_BYTES + 10],
        b"\x01\x01\xf6\x07LVAL\0\0"
    );
    let mut budget = read_budget(quad.len());
    let source = SliceSource::new(&quad, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut user_tables = Vec::new();
    {
        let mut catalog = database.catalog(&mut budget)?;
        while let Some(record) = catalog.next_record()? {
            if record.class() == CatalogObjectClass::User {
                user_tables.push((
                    record.name().raw_bytes().to_vec(),
                    record.table_definition(),
                ));
            }
        }
    }
    assert_eq!(
        user_tables,
        [
            (b"Alpha".to_vec(), Some(PageNumber::new(20))),
            (b"Beta".to_vec(), Some(PageNumber::new(23))),
            (b"Gamma".to_vec(), Some(PageNumber::new(25))),
            (b"Delta".to_vec(), Some(PageNumber::new(28))),
        ]
    );
    let objects = database.table_definition(PageNumber::new(2), &mut budget)?;
    let mut rows = database.rows(&objects, &mut budget)?;
    let mut null_properties = 0;
    while let Some(mut row) = rows.next_row()? {
        if matches!(
            row.value(ColumnOrdinal::new(14), TextCodePage::Windows1252)?
                .ok_or("missing LvProp")?
                .kind(),
            ValueKind::Null
        ) {
            null_properties += 1;
        }
    }
    assert_eq!(null_properties, 8 + 4);
    for (root, columns, indexes) in [(20, 1, 0), (23, 3, 0), (25, 1, 1), (28, 1, 1)] {
        let definition = database.table_definition(PageNumber::new(root), &mut budget)?;
        assert_eq!(definition.columns().len(), columns, "root {root}");
        assert_eq!(definition.physical_indexes().len(), indexes, "root {root}");
        assert!(
            database
                .rows(&definition, &mut budget)?
                .next_row()?
                .is_none()
        );
    }
    let gamma = database.table_definition(PageNumber::new(25), &mut budget)?;
    assert_eq!(gamma.physical_indexes()[0].root(), PageNumber::new(27));
    let delta = database.table_definition(PageNumber::new(28), &mut budget)?;
    assert_eq!(delta.physical_indexes()[0].root(), PageNumber::new(30));
    Ok(())
}

#[test]
#[ignore = "writes the private deterministic candidate for the preregistered issue #100 multi-table experiment"]
fn export_multi_table_candidate() -> TestResult {
    use std::path::PathBuf;

    let root = PathBuf::from(
        std::env::var_os("JET3_MULTI_TABLE_CANDIDATE_DIR")
            .ok_or("JET3_MULTI_TABLE_CANDIDATE_DIR is required")?,
    );
    export_candidate_set(&root, [("multi-table-quad.mdb", quad_candidate_bytes()?)])
}
