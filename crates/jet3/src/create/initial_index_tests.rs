use super::initial_rows_tests::*;
use crate::WriteError;
use crate::testkit::create;
use crate::testkit::{index, table};
use crate::{
    ColumnSpec, ColumnType, ComposeError, DatabaseReader, DatabaseSpec, IndexColumnSpec,
    IndexDirection, IndexKind, IndexNodeKind, IndexSpec, PageNumber, ResourceBudget,
    ResourceLimits, RowValue, TableRows, TableSpec,
    create::{api_tests::*, check::ImageCheckError},
    create_database,
    definition::column_writer::nz,
};
use std::fs;

pub(super) const ID_FIELD: [IndexColumnSpec<'static>; 1] = [field(0, IndexDirection::Ascending)];

pub(super) fn one_index(kind: IndexKind) -> [IndexSpec<'static>; 1] {
    [index(b"ById", &ID_FIELD, kind)]
}

pub(super) fn tree(path: &std::path::Path) -> Result<crate::IndexTree, Box<dyn std::error::Error>> {
    let mut budget = budget();
    let mut database = DatabaseReader::open(path, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    Ok(database.index_tree(&definition, 0, &mut budget)?)
}

#[test]
fn ascending_long_keys_sort_signed_extremes_and_retain_row_locators() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = one_index(IndexKind::Primary);
    let table = table(b"Items", &[ID], &indexes);
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(i32::MAX)],
        &[RowValue::Long(i32::MIN)],
        &[RowValue::Long(0)],
        &[RowValue::Long(-1)],
    ];
    create(directory.target(), &[TableRows { table, rows }])?;
    let index = tree(&directory.target())?;
    let expected = [
        ([0x7f, 0, 0, 0, 0], 1),
        ([0x7f, 0x7f, 0xff, 0xff, 0xff], 3),
        ([0x7f, 0x80, 0, 0, 0], 2),
        ([0x7f, 0xff, 0xff, 0xff, 0xff], 0),
    ];
    for (entry, (key, slot)) in index.entries().iter().zip(expected) {
        assert_eq!(entry.key().raw_bytes(), key);
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(PageNumber::new(24), slot)
        );
    }
    let bytes = fs::read(directory.target())?;
    assert_eq!(
        &bytes[20 * crate::PAGE_BYTES + 47..20 * crate::PAGE_BYTES + 51],
        &4_u32.to_le_bytes()
    );
    assert!(map_bit(&bytes, 21, 2, 23)?);
    assert!(!map_bit(&bytes, 21, 0, 23)?);
    assert!(map_bit(&bytes, 21, 0, 24)?);
    Ok(())
}

#[test]
fn duplicate_keys_are_distinct_counted_for_ordinary_and_rejected_for_unique() -> TestResult {
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(2)],
        &[RowValue::Long(1)],
        &[RowValue::Long(2)],
    ];
    for kind in [IndexKind::Primary, IndexKind::Unique, IndexKind::Ordinary] {
        let directory = TempDir::new("create")?;
        let indexes = one_index(kind);
        let table = table(b"Items", &[ID], &indexes);
        let result = create(directory.target(), &[TableRows { table, rows }]);
        if kind == IndexKind::Ordinary {
            result?;
            let bytes = fs::read(directory.target())?;
            assert_eq!(
                &bytes[20 * crate::PAGE_BYTES + 47..20 * crate::PAGE_BYTES + 51],
                &2_u32.to_le_bytes()
            );
            let index = tree(&directory.target())?;
            assert_eq!(
                index
                    .entries()
                    .iter()
                    .map(|entry| entry.row().slot())
                    .collect::<Vec<_>>(),
                [1, 0, 2]
            );
        } else {
            assert!(matches!(
                result,
                Err(WriteError::Compose(
                    ComposeError::DuplicateInitialIndexKey { value: 2 }
                ))
            ));
            assert!(directory.entries()?.is_empty());
        }
    }
    Ok(())
}

#[test]
fn leaf_capacity_spills_into_a_branch_root() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = one_index(IndexKind::Primary);
    let table = table(b"Items", &[ID], &indexes);
    let values = (0..201)
        .map(|value| [RowValue::Long(value)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create(
        directory.target(),
        &[TableRows {
            table,
            rows: &rows[..200],
        }],
    )?;
    let original = fs::read(directory.target())?;
    assert_eq!(tree(&directory.target())?.entries().len(), 200);
    assert_eq!(
        &original[23 * crate::PAGE_BYTES + 2..23 * crate::PAGE_BYTES + 4],
        &[0, 0]
    );
    let grown = directory.join("grown.mdb");
    create(&grown, &[TableRows { table, rows: &rows }])?;
    let expanded = tree(&grown)?;
    assert_eq!(expanded.entries().len(), 201);
    assert_eq!(expanded.nodes().len(), 3);
    assert_eq!(expanded.nodes()[0].depth(), 1);
    assert_eq!(fs::read(grown)?[23 * crate::PAGE_BYTES], 3);
    Ok(())
}

#[test]
fn candidate_check_rejects_index_owner_and_key_corruption() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = one_index(IndexKind::Primary);
    let table = table(b"Items", &[ID], &indexes);
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::Long(1)], &[RowValue::Long(2)]];
    create(directory.target(), &[TableRows { table, rows }])?;
    let original = fs::read(directory.target())?;
    let mut changed = original.clone();
    changed[23 * crate::PAGE_BYTES + 4] = 19;
    fs::write(directory.target(), &changed)?;
    assert!(matches!(
        super::check::check_initial_rows(&directory.target(), &table, rows, &mut budget()),
        Err(ImageCheckError::Index(_))
    ));
    let mut changed = original;
    changed[23 * crate::PAGE_BYTES + 248 + 4] = 0;
    fs::write(directory.target(), &changed)?;
    assert!(matches!(
        super::check::check_initial_rows(&directory.target(), &table, rows, &mut budget()),
        Err(ImageCheckError::Mismatch {
            detail: "initial index entries"
        })
    ));
    Ok(())
}

#[test]
fn descending_signed_boundaries_encode_and_sort_with_original_locators() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = [IndexSpec {
        fields: &[field(0, IndexDirection::Descending)],
        ..one_index(IndexKind::Unique)[0]
    }];
    let table = table(b"Items", &[ID], &indexes);
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(i32::MIN)],
        &[RowValue::Long(-1)],
        &[RowValue::Long(0)],
        &[RowValue::Long(i32::MAX)],
    ];
    create(directory.target(), &[TableRows { table, rows }])?;
    let index = tree(&directory.target())?;
    for (entry, (key, slot)) in index.entries().iter().zip([
        ([0x80, 0, 0, 0, 0], 3),
        ([0x80, 0x7f, 0xff, 0xff, 0xff], 2),
        ([0x80, 0x80, 0, 0, 0], 1),
        ([0x80, 0xff, 0xff, 0xff, 0xff], 0),
    ]) {
        assert_eq!(entry.key().raw_bytes(), key);
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(PageNumber::new(24), slot)
        );
    }
    assert!(matches!(
        create(
            directory.target(),
            &[TableRows {
                table,
                rows: &[rows[0], rows[0]]
            }]
        ),
        Err(WriteError::Compose(
            ComposeError::DuplicateInitialIndexKey { value: i32::MIN }
        ))
    ));
    Ok(())
}

#[test]
fn mixed_components_respect_declared_order_and_count_complete_duplicate_keys() -> TestResult {
    for directions in [
        [IndexDirection::Ascending, IndexDirection::Descending],
        [IndexDirection::Descending, IndexDirection::Ascending],
    ] {
        let directory = TempDir::new("create")?;
        let indexes = [IndexSpec {
            fields: &[field(1, directions[0]), field(0, directions[1])],
            ..one_index(IndexKind::Ordinary)[0]
        }];
        let table = table(b"Items", &[ID, SEQUENCE], &indexes);
        let rows: &[&[RowValue<'_>]] = &[
            &[RowValue::Long(i32::MIN), RowValue::Long(i32::MAX)],
            &[RowValue::Long(i32::MAX), RowValue::Long(i32::MIN)],
            &[RowValue::Long(0), RowValue::Long(-1)],
            &[RowValue::Long(1), RowValue::Long(-1)],
            &[RowValue::Long(0), RowValue::Long(-1)],
        ];
        create(directory.target(), &[TableRows { table, rows }])?;
        let index = tree(&directory.target())?;
        let (slots, first_key) = if directions[0] == IndexDirection::Ascending {
            ([1, 3, 2, 4, 0], [0x7f, 0, 0, 0, 0, 0x80, 0, 0, 0, 0])
        } else {
            ([0, 2, 4, 3, 1], [0x80, 0, 0, 0, 0, 0x7f, 0, 0, 0, 0])
        };
        assert_eq!(index.entries()[0].key().raw_bytes(), first_key);
        assert_eq!(
            index
                .entries()
                .iter()
                .map(|entry| entry.row().slot())
                .collect::<Vec<_>>(),
            slots
        );
        let bytes = fs::read(directory.target())?;
        assert_eq!(
            &bytes[20 * crate::PAGE_BYTES + 47..20 * crate::PAGE_BYTES + 51],
            &4_u32.to_le_bytes()
        );
        for kind in [IndexKind::Primary, IndexKind::Unique] {
            let indexes = [IndexSpec { kind, ..indexes[0] }];
            let table = TableSpec {
                indexes: &indexes,
                ..table
            };
            assert!(matches!(
                create(directory.target(), &[TableRows { table, rows }]),
                Err(WriteError::Compose(
                    ComposeError::DuplicateInitialCompositeIndexKey { values: [-1, 0] }
                ))
            ));
            assert_eq!(fs::read(directory.target())?, bytes);
        }
    }
    Ok(())
}

#[test]
fn composite_capacity_and_multiple_row_pages_preserve_locators_and_destination() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = [IndexSpec {
        fields: &[
            field(0, IndexDirection::Ascending),
            field(1, IndexDirection::Descending),
        ],
        ..one_index(IndexKind::Unique)[0]
    }];
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &[
            ID,
            SEQUENCE,
            ColumnSpec::new(b"Payload", ColumnType::Text { max_len: nz(255) }),
        ],
        indexes: &indexes,
    };
    let payload = [b'x'; 255];
    let values = (0..129)
        .map(|value| {
            [
                RowValue::Long(value),
                RowValue::Long(-value),
                RowValue::Text(&payload),
            ]
        })
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create(
        directory.target(),
        &[TableRows {
            table,
            rows: &rows[..128],
        }],
    )?;
    let index = tree(&directory.target())?;
    assert_eq!(index.entries().len(), 128);
    for (ordinal, entry) in index.entries().iter().enumerate() {
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(
                PageNumber::new(24 + ordinal as u64 / 7),
                (ordinal % 7) as u8
            )
        );
    }
    let original = fs::read(directory.target())?;
    assert_eq!(
        &original[23 * crate::PAGE_BYTES + 2..23 * crate::PAGE_BYTES + 4],
        &8_u16.to_le_bytes()
    );
    let expanded = TempDir::new("create")?;
    create(expanded.target(), &[TableRows { table, rows: &rows }])?;
    assert_eq!(tree(&expanded.target())?.nodes().len(), 3);
    assert_eq!(tree(&expanded.target())?.entries().len(), 129);
    let mut changed = original;
    changed[23 * crate::PAGE_BYTES + 248 + 9] ^= 1;
    fs::write(directory.target(), changed)?;
    assert!(
        crate::create::check::check_initial_rows(
            &directory.target(),
            &table,
            &rows[..128],
            &mut budget()
        )
        .is_err()
    );
    Ok(())
}

#[test]
fn branch_fanout_builds_another_level_and_preserves_complete_separators() -> TestResult {
    let indexes = one_index(IndexKind::Primary);
    let table = table(b"Items", &[ID], &indexes);
    let values = (0..27801)
        .rev()
        .map(|id| [RowValue::Long(id)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    for (count, nodes, depth) in [(27800, 140, 2), (27801, 143, 3)] {
        let directory = TempDir::new("create")?;
        create(
            directory.target(),
            &[TableRows {
                table,
                rows: &rows[..count],
            }],
        )?;
        let index = tree(&directory.target())?;
        assert_eq!(index.entries().len(), count);
        assert_eq!(index.nodes().len(), nodes);
        assert_eq!(
            index.nodes().iter().map(|node| node.depth()).max(),
            Some(depth)
        );
        let bytes = fs::read(directory.target())?;
        for node in index.nodes() {
            assert!(map_bit(&bytes, 21, 2, node.page().get())?);
            assert!(!map_bit(&bytes, 21, 0, node.page().get())?);
            if node.kind() != IndexNodeKind::Intermediate {
                continue;
            }
            let offset = node.page().get() as usize * crate::PAGE_BYTES;
            let raw = &bytes[offset..offset + crate::PAGE_BYTES];
            let used = 1800 - usize::from(u16::from_le_bytes(raw[2..4].try_into()?));
            for separator in raw[248..248 + used].chunks_exact(13) {
                let child_page = u32::from_be_bytes(separator[9..13].try_into()?) as usize;
                let child =
                    &bytes[child_page * crate::PAGE_BYTES..(child_page + 1) * crate::PAGE_BYTES];
                // Follow rightmost children to the referenced subtree's maximum leaf entry.
                let mut leaf = child;
                while leaf[0] == 3 {
                    let page = u32::from_le_bytes(leaf[16..20].try_into()?) as usize;
                    leaf = &bytes[page * crate::PAGE_BYTES..(page + 1) * crate::PAGE_BYTES];
                }
                let end = 2048 - usize::from(u16::from_le_bytes(leaf[2..4].try_into()?));
                assert_eq!(&separator[..9], &leaf[end - 9..end]);
            }
        }
    }
    Ok(())
}

#[test]
fn branched_corruption_and_resource_limits_preserve_publication() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = one_index(IndexKind::Ordinary);
    let table = table(b"Items", &[ID], &indexes);
    let value = [RowValue::Long(1)];
    let rows = vec![value.as_slice(); 401];
    create(directory.target(), &[TableRows { table, rows: &rows }])?;
    let original = fs::read(directory.target())?;
    let index = tree(&directory.target())?;
    let leaf = index
        .nodes()
        .iter()
        .find(|node| node.kind() == IndexNodeKind::Leaf)
        .ok_or("missing leaf")?
        .page()
        .get() as usize;
    for offset in [
        23 * crate::PAGE_BYTES + 16,
        23 * crate::PAGE_BYTES + 248 + 12,
        leaf * crate::PAGE_BYTES + 12,
    ] {
        let mut changed = original.clone();
        changed[offset] = 0;
        fs::write(directory.target(), changed)?;
        assert!(
            crate::create::check::check_initial_rows(
                &directory.target(),
                &table,
                &rows,
                &mut budget()
            )
            .is_err()
        );
    }
    fs::write(directory.target(), &original)?;
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(100));
    assert!(
        create_database(
            directory.target(),
            &DatabaseSpec {
                tables: &[TableRows { table, rows: &rows }],
                ..DatabaseSpec::default()
            },
            &mut limited
        )
        .is_err()
    );
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}

#[test]
fn generated_keys_keep_their_counter_and_locators_across_index_leaves() -> TestResult {
    let directory = TempDir::new("create")?;
    let indexes = one_index(IndexKind::Primary);
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &[ColumnSpec::new(b"Id", ColumnType::AutoIncrement)],
        indexes: &indexes,
    };
    let value = [RowValue::AutoIncrement];
    let rows = vec![value.as_slice(); 401];
    create(directory.target(), &[TableRows { table, rows: &rows }])?;
    let bytes = fs::read(directory.target())?;
    assert_eq!(
        &bytes[20 * crate::PAGE_BYTES + 16..20 * crate::PAGE_BYTES + 20],
        &401_i32.to_le_bytes()
    );
    let index = tree(&directory.target())?;
    assert_eq!(index.nodes().len(), 4);
    for (ordinal, entry) in index.entries().iter().enumerate() {
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(
                PageNumber::new(24 + ordinal as u64 / 254),
                (ordinal % 254) as u8
            )
        );
    }
    Ok(())
}
