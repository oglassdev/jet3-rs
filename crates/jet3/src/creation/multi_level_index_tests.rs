use super::*;
use crate::{IndexNodeKind, TableRows, create_database_with_table_rows};

#[test]
fn branch_fanout_builds_another_level_and_preserves_complete_separators() -> TestResult {
    let indexes = one_index(IndexKind::Primary);
    let table = TableSpec {
        name: b"Items",
        columns: &[ID],
        indexes: &indexes,
    };
    let values = (0..27801)
        .rev()
        .map(|id| [RowValue::Long(id)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    for (count, nodes, depth) in [(27800, 140, 2), (27801, 143, 3)] {
        let directory = TestDirectory::create()?;
        create_database_with_rows(directory.target(), &table, &rows[..count], &mut budget())?;
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
fn composite_duplicates_cross_leaves_and_later_tables_keep_roots_and_locators() -> TestResult {
    let indexes = [IndexSpec {
        fields: &[
            field(1, IndexDirection::Descending),
            field(0, IndexDirection::Ascending),
        ],
        ..one_index(IndexKind::Ordinary)[0]
    }];
    let table = TableSpec {
        name: b"First",
        columns: &[ID, SEQUENCE],
        indexes: &indexes,
    };
    let values = (0..12929)
        .map(|position| {
            [
                RowValue::Long(position / 400),
                RowValue::Long(position / 800),
            ]
        })
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    let requests = [
        TableRows { table, rows: &rows },
        TableRows {
            table: TableSpec {
                name: b"Later",
                ..table
            },
            rows: &rows[..401],
        },
    ];
    let directory = TestDirectory::create()?;
    create_database_with_table_rows(directory.target(), &requests, &mut budget())?;
    let bytes = fs::read(directory.target())?;
    let mut reader = DatabaseReader::open(directory.target(), &mut budget())?;
    let first = reader.table_definition(PageNumber::new(20), &mut budget())?;
    let first_tree = reader.index_tree(&first, 0, &mut budget())?;
    assert_eq!(
        first_tree.nodes().iter().map(|node| node.depth()).max(),
        Some(3)
    );
    assert_eq!(first_tree.entries().len(), rows.len());
    let later_root = first_tree
        .nodes()
        .iter()
        .map(|node| node.page().get())
        .max()
        .ok_or("missing nodes")?
        + 1;
    let later = reader.table_definition(PageNumber::new(later_root), &mut budget())?;
    let later_tree = reader.index_tree(&later, 0, &mut budget())?;
    assert_eq!(later_tree.entries().len(), 401);
    for node in later_tree.nodes() {
        assert!(map_bit(&bytes, later_root + 1, 2, node.page().get())?);
    }
    for entry in later_tree.entries() {
        assert!(entry.row().page().get() > later_root + 2);
        assert!(map_bit(
            &bytes,
            later_root + 1,
            0,
            entry.row().page().get()
        )?);
    }
    let distinct = u32::from_le_bytes(
        bytes[20 * crate::PAGE_BYTES + 47..20 * crate::PAGE_BYTES + 51].try_into()?,
    );
    assert_eq!(distinct, 33);
    assert!(
        first_tree
            .entries()
            .windows(2)
            .all(|pair| pair[0].key().raw_bytes() <= pair[1].key().raw_bytes())
    );
    Ok(())
}

#[test]
fn branched_corruption_and_index_map_overflow_preserve_publication() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = one_index(IndexKind::Ordinary);
    let table = TableSpec {
        name: b"Items",
        columns: &[ID],
        indexes: &indexes,
    };
    let value = [RowValue::Long(1)];
    let rows = vec![value.as_slice(); 401];
    create_database_with_rows(directory.target(), &table, &rows, &mut budget())?;
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
            crate::creation::api::check_initial_rows(
                &directory.target(),
                &table,
                &rows,
                &mut budget()
            )
            .is_err()
        );
    }
    fs::write(directory.target(), &original)?;
    let huge = vec![value.as_slice(); 204800];
    assert!(matches!(
        create_database_with_rows(directory.target(), &table, &huge, &mut budget()),
        Err(CreateDatabaseError::Compose(ComposeError::UsageMap(_)))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(100));
    assert!(create_database_with_rows(directory.target(), &table, &rows, &mut limited).is_err());
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}

#[test]
fn data_and_all_index_levels_share_the_exact_inline_map_limit() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = one_index(IndexKind::Ordinary);
    let table = TableSpec {
        name: b"Items",
        columns: &[ID],
        indexes: &indexes,
    };
    let value = [RowValue::Long(1)];
    let rows = vec![value.as_slice(); 111253];
    create_database_with_rows(directory.target(), &table, &rows[..111252], &mut budget())?;
    let original = fs::read(directory.target())?;
    assert_eq!(original.len(), 1024 * crate::PAGE_BYTES);
    assert!(matches!(
        create_database_with_rows(directory.target(), &table, &rows, &mut budget()),
        Err(CreateDatabaseError::Compose(ComposeError::UsageMap(_)))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}

#[test]
fn generated_keys_keep_their_counter_and_locators_across_index_leaves() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = one_index(IndexKind::Primary);
    let table = TableSpec {
        name: b"Items",
        columns: &[ColumnSpec::new(b"Id", ColumnType::AutoIncrement)],
        indexes: &indexes,
    };
    let value = [RowValue::AutoIncrement];
    let rows = vec![value.as_slice(); 401];
    create_database_with_rows(directory.target(), &table, &rows, &mut budget())?;
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
