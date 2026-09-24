use super::api_tests::*;
use crate::WriteError;
use crate::testkit::create;
use crate::testkit::index;
use crate::testkit::table;
use crate::{
    ColumnSpec, ColumnType, ComposeError, DatabaseReader, DatabaseSpec, IndexDirection, IndexKind,
    IndexSpec, PageNumber, ResourceBudget, ResourceLimits, RowValue, TableRows,
    create::api::create_database,
};
use std::fs;

const COLUMNS: [ColumnSpec<'static>; 3] =
    [ID, SEQUENCE, ColumnSpec::new(b"Value", ColumnType::Long)];
const INDEXES: [IndexSpec<'static>; 3] = [
    index(
        b"ZPrimary",
        &[field(0, IndexDirection::Ascending)],
        IndexKind::Primary,
    ),
    index(
        b"ASequence",
        &[field(1, IndexDirection::Descending)],
        IndexKind::Ordinary,
    ),
    index(
        b"MValue",
        &[field(2, IndexDirection::Ascending)],
        IndexKind::Unique,
    ),
];

#[test]
fn six_tables_preserve_independent_index_roots_and_initial_rows() -> TestResult {
    for populated in [false, true] {
        let directory = TempDir::new("create")?;
        let names = (0..6).map(|n| format!("T{n:02}")).collect::<Vec<_>>();
        let tables = names
            .iter()
            .enumerate()
            .map(|(n, name)| table(name.as_bytes(), &COLUMNS, &INDEXES[..[3, 0, 1, 2, 3, 3][n]]))
            .collect::<Vec<_>>();
        let values = (0..17)
            .map(|n| {
                [
                    RowValue::Long(n),
                    RowValue::Long(n % 3),
                    RowValue::Long(n - 8),
                ]
            })
            .collect::<Vec<_>>();
        let rows = values
            .iter()
            .map(<[RowValue<'_>; 3]>::as_slice)
            .collect::<Vec<_>>();
        if populated {
            let requests = tables
                .iter()
                .map(|table| TableRows {
                    table: *table,
                    rows: &rows,
                })
                .collect::<Vec<_>>();
            create(directory.target(), &requests)?;
        } else {
            create(
                directory.target(),
                &tables
                    .iter()
                    .copied()
                    .map(TableRows::empty)
                    .collect::<Vec<_>>(),
            )?;
        }
        let mut budget = budget();
        let mut database = DatabaseReader::open(directory.target(), &mut budget)?;
        let mut root = 20;
        for (position, table) in tables.iter().enumerate() {
            let definition = database.table_definition(PageNumber::new(root), &mut budget)?;
            let mut cursor = database.rows(&definition, &mut budget)?;
            let mut count = 0;
            while cursor.next_row()?.is_some() {
                count += 1;
            }
            drop(cursor);
            assert_eq!(count, if populated { 17 } else { 0 });
            assert_eq!(definition.physical_indexes().len(), table.indexes.len());
            for (n, index) in definition.physical_indexes().iter().enumerate() {
                assert_eq!(
                    index.root(),
                    PageNumber::new(root + 2 + u64::from(position == 0) + n as u64)
                );
                assert_eq!(index.usage_map().page(), PageNumber::new(root + 1));
                assert_eq!(index.usage_map().row(), 2 + n as u8);
                let tree = database.index_tree(&definition, n as u16, &mut budget)?;
                assert_eq!(tree.entries().len(), if populated { 17 } else { 0 });
            }
            root +=
                2 + u64::from(position == 0) + table.indexes.len() as u64 + u64::from(populated);
        }
        assert_eq!(database.geometry().page_count(), root);
    }
    Ok(())
}

#[test]
fn creation_counter_overflow_is_refused_before_allocating_or_writing() -> TestResult {
    let directory = TempDir::new("create")?;
    let tables = vec![table(b"T", &[ID], &[]); 32640];
    let mut budget = budget();
    assert!(matches!(
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &tables
                    .iter()
                    .copied()
                    .map(TableRows::empty)
                    .collect::<Vec<_>>(),
                ..DatabaseSpec::default()
            },
            &mut budget
        ),
        Err(WriteError::Compose(ComposeError::TableCountOverflow {
            count: 32640,
            maximum: 32639
        }))
    ));
    assert_eq!(budget.allocation_bytes().get(), 0);
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn catalog_data_and_index_pages_grow_with_complete_row_locators() -> TestResult {
    use crate::{CatalogObjectClass, ColumnOrdinal, TextCodePage, ValueKind};
    use std::collections::BTreeSet;
    for (count, padding) in [
        (29, 0),
        (40, 0),
        (110, 0),
        (30, 45),
        (40, 61),
        (127, 45),
        (128, 0),
        (255, 0),
        (256, 0),
    ] {
        let directory = TempDir::new("create")?;
        let names = (0..count)
            .map(|n| format!("T{n:02}{}", "x".repeat(padding)))
            .collect::<Vec<_>>();
        let tables = names
            .iter()
            .map(|name| table(name.as_bytes(), &[ID], &[]))
            .collect::<Vec<_>>();
        create(
            directory.target(),
            &tables
                .iter()
                .copied()
                .map(TableRows::empty)
                .collect::<Vec<_>>(),
        )?;
        let mut budget = budget();
        let mut database = DatabaseReader::open(directory.target(), &mut budget)?;
        let mut users = Vec::new();
        {
            let mut catalog = database.catalog(&mut budget)?;
            while let Some(record) = catalog.next_record()? {
                if record.class() == CatalogObjectClass::User {
                    users.push(record.name().raw_bytes().to_vec());
                }
            }
        }
        assert_eq!(
            users,
            names
                .iter()
                .map(|n| n.as_bytes().to_vec())
                .collect::<Vec<_>>()
        );
        let raw = fs::read(directory.target())?;
        let objects = database.table_definition(PageNumber::new(2), &mut budget)?;
        let aces = database.table_definition(PageNumber::new(3), &mut budget)?;
        for (definition, expected_count) in [(&objects, 8 + count), (&aces, 16 + 2 * count)] {
            assert_eq!(definition.row_count() as usize, expected_count);
            let mut stored = Vec::new();
            {
                let mut rows = database.rows(definition, &mut budget)?;
                while let Some(mut row) = rows.next_row()? {
                    let locator = row.locator();
                    let value = row
                        .value(ColumnOrdinal::new(0), TextCodePage::Windows1252)?
                        .ok_or("missing system Id")?;
                    let ValueKind::Long(id) = value.kind() else {
                        return Err("system Id type".into());
                    };
                    stored.push((locator.page().get(), locator.slot(), *id));
                }
            }
            assert_eq!(stored.len(), expected_count);
            let pages = stored.iter().map(|r| r.0).collect::<BTreeSet<_>>();
            assert_eq!(
                pages.len() > 1,
                definition.root().get() == 2 || count >= 110
            );
            assert_eq!(catalog_map_pages(&raw, definition.maps().owned())?, pages);
            let available = catalog_map_pages(&raw, definition.maps().available())?;
            assert!(available.is_subset(&pages) && available.len() <= 1);
            if let Some(last) = available.last() {
                assert_eq!(Some(last), pages.last());
            }
            let numeric_ordinal = u16::from(definition.root().get() == 2);
            for ordinal in 0..definition.physical_indexes().len() as u16 {
                let tree = database.index_tree(definition, ordinal, &mut budget)?;
                let reference = definition.physical_indexes()[ordinal as usize].usage_map();
                assert_eq!(
                    catalog_map_pages(
                        &raw,
                        crate::MapRowLocator::new(reference.page(), reference.row())
                    )?,
                    tree.nodes().iter().map(|n| n.page().get()).collect()
                );
                let mut indexed = tree
                    .entries()
                    .iter()
                    .map(|entry| {
                        let id = if ordinal == numeric_ordinal {
                            let key = entry.key().raw_bytes();
                            let bytes: [u8; 4] =
                                key[1..5].try_into().map_err(|_| "numeric key width")?;
                            (u32::from_be_bytes(bytes) ^ 0x8000_0000) as i32
                        } else {
                            stored
                                .iter()
                                .find(|r| {
                                    r.0 == entry.row().page().get() && r.1 == entry.row().slot()
                                })
                                .ok_or("catalog index locator")?
                                .2
                        };
                        Ok((entry.row().page().get(), entry.row().slot(), id))
                    })
                    .collect::<Result<Vec<_>, Box<dyn std::error::Error>>>()?;
                indexed.sort_unstable();
                let mut expected = stored.clone();
                expected.sort_unstable();
                assert_eq!(indexed, expected);
            }
        }
        let raw = fs::read(directory.target())?;
        assert_eq!(
            raw[9 * crate::PAGE_BYTES] == 3,
            (padding >= 45 && count >= 30) || count >= 128
        );
        assert_eq!(raw[13 * crate::PAGE_BYTES] == 3, count >= 110);
        assert_eq!(
            u16::from_le_bytes(raw[1538..1540].try_into()?) as usize,
            0x0100 + 2 * count
        );
        if count == 256 {
            let mut work = ResourceBudget::new(ResourceLimits::default());
            let report = DatabaseReader::open(directory.target(), &mut work)?
                .validate(TextCodePage::Windows1252, &mut work)?;
            assert_eq!(report.user_tables, count as u64);
        }
    }
    Ok(())
}

#[test]
fn catalog_spill_extends_maps_past_inline_capacity() -> TestResult {
    let directory = TempDir::new("create")?;
    let names = (0..40).map(|n| format!("T{n:02}")).collect::<Vec<_>>();
    let payload = [b'x'; 200];
    let values = [RowValue::Binary(&payload); 8];
    let rows = [values.as_slice(); 26];
    let width = std::num::NonZeroU8::new(200).ok_or("binary width")?;
    let columns = [b"C0", b"C1", b"C2", b"C3", b"C4", b"C5", b"C6", b"C7"]
        .map(|name| ColumnSpec::new(name, ColumnType::Binary { max_len: width }));
    let mut requests = names
        .iter()
        .enumerate()
        .map(|(n, name)| TableRows {
            table: table(name.as_bytes(), &columns, &[]),
            rows: &rows[..if n == 39 { 25 } else { 23 }],
        })
        .collect::<Vec<_>>();
    create(directory.target(), &requests)?;
    assert_eq!(
        fs::metadata(directory.target())?.len(),
        1024 * crate::PAGE_BYTES as u64
    );
    requests[39].rows = &rows;
    let grown = directory.target().with_file_name("grown.mdb");
    create(&grown, &requests)?;
    assert!(fs::metadata(grown)?.len() > 1024 * crate::PAGE_BYTES as u64);
    Ok(())
}

fn catalog_map_pages(
    raw: &[u8],
    locator: crate::MapRowLocator,
) -> Result<std::collections::BTreeSet<u64>, Box<dyn std::error::Error>> {
    let start = locator.page().get() as usize * crate::PAGE_BYTES;
    let image: &[u8; crate::PAGE_BYTES] = raw[start..start + crate::PAGE_BYTES].try_into()?;
    let mut budget = budget();
    let page = crate::classify_page(locator.page(), image, &mut budget)?;
    let record = crate::locate_usage_map(page, locator, &mut budget)?;
    let bytes = record.raw();
    assert_eq!(&bytes[..5], &[0; 5]);
    Ok((0..1024)
        .filter(|p| bytes[5 + *p as usize / 8] & (1 << (*p % 8)) != 0)
        .collect())
}
