use super::*;

#[test]
fn graph_selects_later_unique_parent_and_preserves_declared_foreign_indexes() -> TestResult {
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Key", ColumnType::Long),
    ];
    let id_fields = [IndexColumnSpec::ascending(0)];
    let key_fields = [IndexColumnSpec::ascending(1)];
    let descending = [IndexColumnSpec::descending(1)];
    for parent_kind in [IndexKind::Primary, IndexKind::Unique] {
        let parent_indexes = [
            IndexSpec {
                name: b"ById",
                fields: &id_fields,
                kind: IndexKind::Ordinary,
            },
            IndexSpec {
                name: b"ParentKey",
                fields: &key_fields,
                kind: parent_kind,
            },
        ];
        for mode in 0..5 {
            let primary = IndexSpec {
                name: b"ById",
                fields: &id_fields,
                kind: IndexKind::Primary,
            };
            let foreign = IndexSpec {
                name: b"ExistingForeign",
                fields: if mode == 3 { &descending } else { &key_fields },
                kind: match mode {
                    2 => IndexKind::Unique,
                    4 => IndexKind::Ordinary.with_null_policy(crate::IndexNullPolicy::Required),
                    _ => IndexKind::Ordinary,
                },
            };
            let child_indexes = if mode == 0 {
                [foreign, primary]
            } else {
                [primary, foreign]
            };
            let parent = TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Parent",
                columns: &columns,
                indexes: &parent_indexes,
            };
            let child = TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Child",
                columns: &columns,
                indexes: &child_indexes,
            };
            let parent_rows: &[&[RowValue<'_>]] = &[
                &[RowValue::Long(100), RowValue::Long(1)],
                &[RowValue::Long(200), RowValue::Long(2)],
            ];
            let child_rows: &[&[RowValue<'_>]] = &[&[RowValue::Long(10), RowValue::Long(1)]];
            let requests = [
                TableRows {
                    table: parent,
                    rows: parent_rows,
                },
                TableRows {
                    table: child,
                    rows: child_rows,
                },
            ];
            let mut edge = relation(b"Relation", 0, 1, 1);
            edge.fields = &[RelationshipField {
                parent: ColumnRef::Ordinal(1),
                child: ColumnRef::Ordinal(1),
            }];
            let directory = Directory::new()?;
            create_database_with_relationships_and_rows(
                directory.target(),
                &requests,
                &[edge],
                &mut budget(),
            )?;
            let parent_locator = {
                let mut work = budget();
                let mut db = DatabaseReader::open(directory.target(), &mut work)?;
                let pd = crate::update::indexed_writable_table(&mut db, parent.name, &mut work)?;
                let cd = crate::update::indexed_writable_table(&mut db, child.name, &mut work)?;
                let pr = pd.relationships().next().ok_or("parent relationship")?;
                let cr = cd.relationships().next().ok_or("child relationship")?;
                assert_eq!(pr.physical_index(), 1);
                assert_eq!(cr.physical_index(), if mode < 2 { mode } else { 2 });
                assert_eq!(cd.physical_indexes().len(), if mode < 2 { 2 } else { 3 });
                assert_eq!(cd.indexes().len(), 3);
                assert!(
                    cd.indexes()
                        .iter()
                        .any(|index| index.name().raw_bytes() == foreign.name)
                );
                let mut rows = db.rows(&pd, &mut work)?;
                rows.next_row()?.ok_or("parent row")?.locator()
            };
            crate::insert_row(
                directory.target(),
                child.name,
                &[RowValue::Long(11), RowValue::Long(2)],
                &mut budget(),
            )?;
            let before = fs::read(directory.target())?;
            assert!(matches!(
                crate::insert_row(
                    directory.target(),
                    child.name,
                    &[RowValue::Long(12), RowValue::Long(99)],
                    &mut budget()
                ),
                Err(crate::UpdateError::RelationshipConstraint { value: 99, .. })
            ));
            assert!(matches!(
                crate::delete_row(
                    directory.target(),
                    crate::RowDelete {
                        table: parent.name,
                        row: parent_locator
                    },
                    &mut budget()
                ),
                Err(crate::UpdateError::RelationshipConstraint { value: 1, .. })
            ));
            assert_eq!(fs::read(directory.target())?, before);
            let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
            assert_eq!(
                db.validate(TextCodePage::Windows1252, &mut budget())?
                    .relationships_with_verified_keys,
                1
            );
        }
    }
    Ok(())
}

#[test]
fn relationship_alias_consumes_a_logical_index_slot_when_reusing_a_tree() -> TestResult {
    let names = (0..32)
        .map(|n| format!("Index{n:02}").into_bytes())
        .collect::<Vec<_>>();
    let id = [IndexColumnSpec::ascending(0)];
    let fk = [IndexColumnSpec::ascending(1)];
    for reuse in [false, true] {
        let indexes = names
            .iter()
            .enumerate()
            .map(|(n, name)| IndexSpec {
                name,
                fields: if reuse && n == 1 { &fk } else { &id },
                kind: IndexKind::Ordinary,
            })
            .collect::<Vec<_>>();
        for count in [31, 32] {
            let directory = Directory::new()?;
            let child = TableSpec {
                indexes: &indexes[..count],
                ..TABLES[1]
            };
            let result = create_database_with_relationships(
                directory.target(),
                &[TABLES[0], child],
                &[relation(b"Relation", 0, 1, 1)],
                &mut budget(),
            );
            if count == 32 {
                assert!(matches!(
                    result,
                    Err(CreateDatabaseError::Compose(ComposeError::Schema(
                        crate::TableSchemaPlanError::UnobservedIndexCount {
                            count: 33,
                            observed: 32
                        }
                    )))
                ));
                assert!(!directory.target().exists());
            } else {
                result?;
                let mut work = budget();
                let mut db = DatabaseReader::open(directory.target(), &mut work)?;
                let definition =
                    crate::update::indexed_writable_table(&mut db, child.name, &mut work)?;
                assert_eq!(definition.indexes().len(), 32);
                assert_eq!(
                    definition.physical_indexes().len(),
                    if reuse { 31 } else { 32 }
                );
            }
        }
    }
    Ok(())
}

#[test]
fn graph_parent_selection_follows_logical_name_order_before_primary_status() -> TestResult {
    for primary_first in [false, true] {
        let unique = IndexSpec {
            name: b"AUnique",
            kind: IndexKind::Unique,
            ..INDEXES[0]
        };
        let primary = IndexSpec {
            name: b"ZPrimary",
            ..INDEXES[0]
        };
        let indexes = if primary_first {
            [primary, unique]
        } else {
            [unique, primary]
        };
        let parent = TableSpec {
            indexes: &indexes,
            ..TABLES[0]
        };
        let directory = Directory::new()?;
        create_database_with_relationships(
            directory.target(),
            &[parent, TABLES[1]],
            &[relation(b"Relation", 0, 1, 1)],
            &mut budget(),
        )?;
        let mut work = budget();
        let mut db = DatabaseReader::open(directory.target(), &mut work)?;
        let definition = crate::update::indexed_writable_table(&mut db, parent.name, &mut work)?;
        assert_eq!(
            definition
                .relationships()
                .next()
                .ok_or("parent relationship")?
                .physical_index(),
            u16::from(primary_first)
        );
    }
    Ok(())
}

#[test]
fn graph_nullable_unique_parent_allows_duplicate_nulls_and_null_foreign_keys() -> TestResult {
    let indexes = [IndexSpec {
        kind: IndexKind::Unique,
        ..INDEXES[0]
    }];
    let parent = TableSpec {
        indexes: &indexes,
        ..TABLES[0]
    };
    let null: &[RowValue<'_>] = &[
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
    ];
    let keyed: &[RowValue<'_>] = &[
        RowValue::Long(1),
        RowValue::Null,
        RowValue::Null,
        RowValue::Null,
    ];
    let directory = Directory::new()?;
    create_database_with_relationships_and_rows(
        directory.target(),
        &[
            TableRows {
                table: parent,
                rows: &[keyed, null, null],
            },
            TableRows {
                table: TABLES[1],
                rows: &[keyed],
            },
        ],
        &[relation(b"Relation", 0, 1, 1)],
        &mut budget(),
    )?;
    crate::insert_row(
        directory.target(),
        TABLES[1].name,
        &[
            RowValue::Long(2),
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Null,
        ],
        &mut budget(),
    )?;
    let before = fs::read(directory.target())?;
    assert!(matches!(
        crate::insert_row(
            directory.target(),
            TABLES[1].name,
            &[
                RowValue::Long(3),
                RowValue::Long(99),
                RowValue::Null,
                RowValue::Null
            ],
            &mut budget()
        ),
        Err(crate::UpdateError::RelationshipConstraint { value: 99, .. })
    ));
    assert_eq!(fs::read(directory.target())?, before);
    let mut db = DatabaseReader::open(directory.target(), &mut budget())?;
    assert_eq!(
        db.validate(TextCodePage::Windows1252, &mut budget())?
            .relationships_with_verified_keys,
        1
    );
    Ok(())
}

#[test]
fn graph_parent_hidden_names_cross_the_native_nibble_boundary() -> TestResult {
    let names = (0..32)
        .map(|n| format!("Index{n:02}").into_bytes())
        .collect::<Vec<_>>();
    let fields = [IndexColumnSpec::ascending(0)];
    let indexes = names
        .iter()
        .map(|name| IndexSpec {
            name,
            fields: &fields,
            kind: IndexKind::Unique,
        })
        .collect::<Vec<_>>();
    for (count, hidden) in [
        (15, ".rP"),
        (16, ".rAB"),
        (24, ".rIB"),
        (25, ".rJB"),
        (26, ".rKB"),
        (31, ".rPB"),
        (32, ""),
    ] {
        let directory = Directory::new()?;
        let parent = TableSpec {
            indexes: &indexes[..count],
            ..TABLES[0]
        };
        let result = create_database_with_relationships(
            directory.target(),
            &[parent, TABLES[1]],
            &[relation(b"Relation", 0, 1, 1)],
            &mut budget(),
        );
        if count == 32 {
            assert!(matches!(
                result,
                Err(CreateDatabaseError::Compose(ComposeError::Schema(
                    crate::TableSchemaPlanError::UnobservedIndexCount {
                        count: 33,
                        observed: 32
                    }
                )))
            ));
            assert!(!directory.target().exists());
        } else {
            result?;
            let mut work = budget();
            let mut db = DatabaseReader::open(directory.target(), &mut work)?;
            let definition =
                crate::update::indexed_writable_table(&mut db, parent.name, &mut work)?;
            let relation = definition
                .relationships()
                .next()
                .ok_or("parent relationship")?;
            assert_eq!(relation.name().raw_bytes(), hidden.as_bytes());
            assert_eq!(relation.raw_selector() as usize, count);
            assert_eq!(definition.indexes().len(), count + 1);
        }
    }
    Ok(())
}
