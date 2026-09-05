use super::*;

const ID_FIELD: [IndexColumnSpec<'static>; 1] = [field(0, IndexDirection::Ascending)];

fn one_index(kind: IndexKind) -> [IndexSpec<'static>; 1] {
    [IndexSpec {
        name: b"ById",
        fields: &ID_FIELD,
        kind,
    }]
}

fn tree(path: &std::path::Path) -> Result<crate::IndexTree, Box<dyn std::error::Error>> {
    let mut budget = budget();
    let mut database = DatabaseReader::open(path, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    Ok(database.index_tree(&definition, 0, &mut budget)?)
}

#[test]
fn ascending_long_keys_sort_signed_extremes_and_retain_row_locators() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = one_index(IndexKind::Primary);
    let table = TableSpec {
        name: b"Items",
        columns: &[ID],
        indexes: &indexes,
    };
    let rows: &[&[RowValue<'_>]] = &[
        &[RowValue::Long(i32::MAX)],
        &[RowValue::Long(i32::MIN)],
        &[RowValue::Long(0)],
        &[RowValue::Long(-1)],
    ];
    create_database_with_rows(directory.target(), &table, rows, &mut budget())?;
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
        let directory = TestDirectory::create()?;
        let indexes = one_index(kind);
        let table = TableSpec {
            name: b"Items",
            columns: &[ID],
            indexes: &indexes,
        };
        let result = create_database_with_rows(directory.target(), &table, rows, &mut budget());
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
                Err(CreateDatabaseError::Compose(
                    ComposeError::DuplicateInitialIndexKey { value: 2 }
                ))
            ));
            assert!(directory.entries()?.is_empty());
        }
    }
    Ok(())
}

#[test]
fn indexed_payload_rows_can_reference_multiple_data_pages() -> TestResult {
    let directory = TestDirectory::create()?;
    let columns = [
        ID,
        ColumnSpec::new(b"Payload", ColumnType::Text { max_len: nz(255) }),
    ];
    let indexes = one_index(IndexKind::Unique);
    let table = TableSpec {
        name: b"Items",
        columns: &columns,
        indexes: &indexes,
    };
    let text = [b'x'; 255];
    let values = (0..20)
        .map(|value| [RowValue::Long(19 - value), RowValue::Text(&text)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_rows(directory.target(), &table, &rows, &mut budget())?;
    for (key, entry) in tree(&directory.target())?.entries().iter().enumerate() {
        let ordinal = 19 - key;
        assert_eq!(
            entry.row(),
            crate::RowLocator::new(
                PageNumber::new(24 + ordinal as u64 / 7),
                (ordinal % 7) as u8
            )
        );
    }
    Ok(())
}

#[test]
fn leaf_capacity_spills_into_a_branch_root() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = one_index(IndexKind::Primary);
    let table = TableSpec {
        name: b"Items",
        columns: &[ID],
        indexes: &indexes,
    };
    let values = (0..201)
        .map(|value| [RowValue::Long(value)])
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_rows(directory.target(), &table, &rows[..200], &mut budget())?;
    let original = fs::read(directory.target())?;
    assert_eq!(tree(&directory.target())?.entries().len(), 200);
    assert_eq!(
        &original[23 * crate::PAGE_BYTES + 2..23 * crate::PAGE_BYTES + 4],
        &[0, 0]
    );
    assert!(matches!(
        create_database_with_rows(directory.target(), &table, &rows, &mut budget()),
        Err(CreateDatabaseError::Publish(_))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    let directory = TestDirectory::create()?;
    create_database_with_rows(directory.target(), &table, &rows, &mut budget())?;
    let expanded = tree(&directory.target())?;
    assert_eq!(expanded.entries().len(), 201);
    assert_eq!(expanded.nodes().len(), 3);
    assert_eq!(expanded.nodes()[0].depth(), 1);
    assert_eq!(fs::read(directory.target())?[23 * crate::PAGE_BYTES], 3);
    Ok(())
}

#[test]
fn required_null_keys_and_unsupported_key_schemas_fail_before_publication() -> TestResult {
    let directory = TestDirectory::create()?;
    for kind in [
        IndexKind::Primary,
        IndexKind::Ordinary.with_null_policy(crate::IndexNullPolicy::Required),
    ] {
        let indexes = one_index(kind);
        let table = TableSpec {
            name: b"Items",
            columns: &[ID],
            indexes: &indexes,
        };
        assert!(matches!(
            create_database_with_rows(
                directory.target(),
                &table,
                &[&[RowValue::Null]],
                &mut budget()
            ),
            Err(CreateDatabaseError::Compose(
                ComposeError::NullInitialIndexKey { row: 0 }
            ))
        ));
    }
    let multiple = [
        one_index(IndexKind::Ordinary)[0],
        IndexSpec {
            name: b"Other",
            ..one_index(IndexKind::Ordinary)[0]
        },
    ];
    let composite = [IndexSpec {
        fields: &[
            field(0, IndexDirection::Ascending),
            field(1, IndexDirection::Ascending),
        ],
        ..one_index(IndexKind::Ordinary)[0]
    }];
    let text = [IndexSpec {
        fields: &[IndexColumnSpec::ascending(b"Code")],
        ..one_index(IndexKind::Ordinary)[0]
    }];
    for indexes in [&multiple[..], &composite, &text] {
        let table = TableSpec {
            name: b"Items",
            columns: &[ID, CODE],
            indexes,
        };
        assert!(matches!(
            create_database_with_rows(directory.target(), &table, &[], &mut budget()),
            Err(CreateDatabaseError::Compose(
                ComposeError::UnsupportedInitialIndexSchema
            ))
        ));
    }
    assert!(directory.entries()?.is_empty());
    Ok(())
}

#[test]
fn candidate_check_rejects_index_owner_and_key_corruption() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = one_index(IndexKind::Primary);
    let table = TableSpec {
        name: b"Items",
        columns: &[ID],
        indexes: &indexes,
    };
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::Long(1)], &[RowValue::Long(2)]];
    create_database_with_rows(directory.target(), &table, rows, &mut budget())?;
    let original = fs::read(directory.target())?;
    let mut changed = original.clone();
    changed[23 * crate::PAGE_BYTES + 4] = 19;
    fs::write(directory.target(), &changed)?;
    assert!(matches!(
        super::super::super::check_initial_rows(&directory.target(), &table, rows, &mut budget()),
        Err(CandidateCheckError::Index(_))
    ));
    let mut changed = original;
    changed[23 * crate::PAGE_BYTES + 248 + 4] = 0;
    fs::write(directory.target(), &changed)?;
    assert!(matches!(
        super::super::super::check_initial_rows(&directory.target(), &table, rows, &mut budget()),
        Err(CandidateCheckError::Mismatch {
            detail: "initial index entries"
        })
    ));
    Ok(())
}

#[test]
fn index_storage_budget_and_empty_index_are_handled() -> TestResult {
    let directory = TestDirectory::create()?;
    let indexes = one_index(IndexKind::Primary);
    let table = TableSpec {
        name: b"Items",
        columns: &[ID],
        indexes: &indexes,
    };
    create_database_with_rows(directory.target(), &table, &[], &mut budget())?;
    assert!(tree(&directory.target())?.entries().is_empty());
    let original = fs::read(directory.target())?;
    let mut limited = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(crate::ByteCount::new(8)),
    );
    assert!(matches!(
        create_database_with_rows(
            directory.target(),
            &table,
            &[&[RowValue::Long(1)]],
            &mut limited
        ),
        Err(CreateDatabaseError::Compose(ComposeError::Encoding(
            crate::Error::ResourceLimitExceeded {
                kind: crate::ResourceLimitKind::AllocationBytes,
                ..
            }
        )))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    Ok(())
}

#[path = "create_composite_index_tests.rs"]
mod composite;

#[path = "create_multi_level_index_tests.rs"]
mod multi_level;
