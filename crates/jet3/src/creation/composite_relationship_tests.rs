//! EXP-0290 ordered composite fields and partial-null key matching.
use super::*;

const PAIR: &[IndexColumnSpec<'static>] = &[
    IndexColumnSpec {
        column: ColumnRef::Ordinal(1),
        direction: IndexDirection::Ascending,
    },
    IndexColumnSpec {
        column: ColumnRef::Ordinal(2),
        direction: IndexDirection::Ascending,
    },
];
const FIELDS: &[RelationshipField<'static>] = &[
    RelationshipField {
        parent: ColumnRef::Ordinal(1),
        child: ColumnRef::Ordinal(1),
    },
    RelationshipField {
        parent: ColumnRef::Ordinal(2),
        child: ColumnRef::Ordinal(2),
    },
];
const PARENT_INDEXES: &[IndexSpec<'static>] = &[
    INDEXES[0],
    IndexSpec {
        name: b"Pair",
        fields: PAIR,
        kind: IndexKind::Unique,
    },
];
fn schema() -> [TableSpec<'static>; 2] {
    [
        TableSpec {
            indexes: PARENT_INDEXES,
            ..TABLES[0]
        },
        TABLES[1],
    ]
}
fn edge() -> RelationshipSpec<'static> {
    RelationshipSpec {
        name: b"Pair",
        parent: TableRef::Ordinal(0),
        child: TableRef::Ordinal(1),
        fields: FIELDS,
    }
}

#[test]
fn composite_relationship_creation_checks_full_tuple_and_catalog_inventory() -> TestResult {
    let directory = Directory::new()?;
    let tables = schema();
    let parent = [
        &[
            RowValue::Long(1),
            RowValue::Long(11),
            RowValue::Long(111),
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(2),
            RowValue::Long(11),
            RowValue::Null,
            RowValue::Null,
        ][..],
    ];
    let child = [
        &[
            RowValue::Long(10),
            RowValue::Long(11),
            RowValue::Long(111),
            RowValue::Memo(b"first"),
        ][..],
        &[
            RowValue::Long(11),
            RowValue::Long(11),
            RowValue::Null,
            RowValue::Null,
        ][..],
    ];
    let requests = [
        TableRows {
            table: tables[0],
            rows: &parent,
        },
        TableRows {
            table: tables[1],
            rows: &child,
        },
    ];
    create_database_with_relationships_and_rows(
        directory.target(),
        &requests,
        &[edge()],
        &mut budget(),
    )?;
    let mut database = DatabaseReader::open(directory.target(), &mut budget())?;
    let report = database.validate(TextCodePage::Windows1252, &mut budget())?;
    assert_eq!(report.relationship_catalog_rows, 2);
    assert_eq!(report.relationships_with_verified_keys, 1);
    assert!(report.relationship_inventory_checked);
    for key in [RowValue::Long(999), RowValue::Null] {
        let orphan = [&[RowValue::Long(12), key, RowValue::Long(111), RowValue::Null][..]];
        let missing = directory.0.join(if matches!(key, RowValue::Null) {
            "partial.mdb"
        } else {
            "orphan.mdb"
        });
        let requests = [
            requests[0],
            TableRows {
                table: tables[1],
                rows: &orphan,
            },
        ];
        assert!(matches!(
            create_database_with_relationships_and_rows(
                &missing,
                &requests,
                &[edge()],
                &mut budget()
            ),
            Err(CreateDatabaseError::Compose(
                ComposeError::OrphanInitialScalarRelationshipKey { row: 0 }
            ))
        ));
        assert!(!missing.exists());
    }
    Ok(())
}

#[test]
fn composite_relationship_requires_aligned_unique_fields_and_distinct_components() -> TestResult {
    let directory = Directory::new()?;
    let tables = schema();
    for fields in [
        &[FIELDS[1], FIELDS[0]][..],
        &[FIELDS[0], FIELDS[0]][..],
        &[][..],
        &[FIELDS[0]; 11][..],
    ] {
        let invalid = RelationshipSpec { fields, ..edge() };
        assert!(matches!(
            create_database_with_relationships(
                directory.target(),
                &tables,
                &[invalid],
                &mut budget()
            ),
            Err(CreateDatabaseError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
        assert!(!directory.target().exists());
    }
    Ok(())
}

#[test]
fn self_relationship_creation_refuses_identical_keys_but_admits_partial_overlap() -> TestResult {
    let directory = Directory::new()?;
    let tables = schema();
    let scalar = [RelationshipField {
        parent: ColumnRef::Name(b"Id"),
        child: ColumnRef::Ordinal(0),
    }];
    for fields in [&scalar[..], FIELDS] {
        let relation = RelationshipSpec {
            name: b"SelfRelation",
            parent: TableRef::Name(b"Alpha"),
            child: TableRef::Ordinal(0),
            fields,
        };
        assert!(matches!(
            create_database_with_relationships(
                directory.target(),
                &tables[..1],
                &[relation],
                &mut budget()
            ),
            Err(CreateDatabaseError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
        assert!(!directory.target().exists());
    }
    let partial = [
        FIELDS[0],
        RelationshipField {
            parent: ColumnRef::Ordinal(2),
            child: ColumnRef::Name(b"Id"),
        },
    ];
    create_database_with_relationships(
        directory.target(),
        &tables[..1],
        &[RelationshipSpec {
            name: b"PartialSelf",
            parent: TableRef::Ordinal(0),
            child: TableRef::Name(b"Alpha"),
            fields: &partial,
        }],
        &mut budget(),
    )?;
    let mut database = DatabaseReader::open(directory.target(), &mut budget())?;
    let report = database.validate(TextCodePage::Windows1252, &mut budget())?;
    assert_eq!(report.relationships_with_verified_keys, 1);
    Ok(())
}

fn locate(
    path: &Path,
    table: &[u8],
    id: i32,
) -> Result<crate::RowLocator, Box<dyn std::error::Error>> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let definition = crate::update::indexed_writable_table(&mut db, table, &mut work)?;
    let mut rows = db.rows(&definition, &mut work)?;
    while let Some(mut row) = rows.next_row()? {
        if matches!(row.value(crate::ColumnOrdinal::new(0), TextCodePage::Windows1252)?.ok_or("missing Id")?.kind(), crate::ValueKind::Long(value) if *value == id)
        {
            return Ok(row.locator());
        }
    }
    Err("missing row".into())
}

#[test]
fn referenced_parent_payload_field_edit_does_not_assign_its_composite_key() -> TestResult {
    let directory = Directory::new()?;
    let path = directory.target();
    let mut columns = COLUMNS.to_vec();
    columns[3] = ColumnSpec::new(
        b"Body",
        ColumnType::Text {
            max_len: std::num::NonZeroU8::new(255).ok_or("width")?,
        },
    );
    let mut tables = schema();
    tables[0].columns = &columns;
    let parent = [
        RowValue::Long(1),
        RowValue::Long(11),
        RowValue::Long(111),
        RowValue::Text(b"before"),
    ];
    let child = [
        RowValue::Long(10),
        RowValue::Long(11),
        RowValue::Long(111),
        RowValue::Null,
    ];
    create_database_with_relationships_and_rows(
        &path,
        &[
            TableRows {
                table: tables[0],
                rows: &[&parent],
            },
            TableRows {
                table: tables[1],
                rows: &[&child],
            },
        ],
        &[edge()],
        &mut budget(),
    )?;
    let row = locate(&path, b"Alpha", 1)?;
    let original = fs::read(&path)?;
    let mut replacement = parent;
    replacement[3] = RowValue::Text(&[b'x'; 255]);
    assert!(matches!(
        crate::update_row(
            &path,
            crate::RowUpdate {
                table: b"Alpha",
                row,
                values: &replacement,
            },
            &mut budget()
        ),
        Err(crate::UpdateError::ScalarRelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(&path)?, original);
    crate::update_field(
        &path,
        crate::FieldUpdate {
            table: b"Alpha",
            row,
            column: crate::ColumnOrdinal::new(3),
            value: replacement[3],
        },
        &mut budget(),
    )?;
    let mut work = budget();
    let mut db = DatabaseReader::open(&path, &mut work)?;
    let table = crate::update::indexed_writable_table(&mut db, b"Alpha", &mut work)?;
    {
        let mut rows = db.rows(&table, &mut work)?;
        let mut actual = rows.next_row()?.ok_or("row")?;
        for (ordinal, expected) in replacement.iter().enumerate() {
            assert_eq!(
                crate::numeric_row_values::read_column(
                    &mut actual,
                    crate::ColumnOrdinal::new(ordinal as u16)
                )?,
                *expected
            );
        }
    }
    db.validate(TextCodePage::Windows1252, &mut work)?;
    Ok(())
}

#[test]
fn composite_mutations_protect_assigned_parent_rows_and_admit_all_null_children() -> TestResult {
    let directory = Directory::new()?;
    let path = directory.target();
    let tables = schema();
    let parent = [
        &[
            RowValue::Long(1),
            RowValue::Long(11),
            RowValue::Long(111),
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(2),
            RowValue::Long(11),
            RowValue::Null,
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(3),
            RowValue::Long(11),
            RowValue::Null,
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(4),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ][..],
    ];
    let child = [
        &[
            RowValue::Long(10),
            RowValue::Long(11),
            RowValue::Long(111),
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(11),
            RowValue::Long(11),
            RowValue::Null,
            RowValue::Null,
        ][..],
        &[
            RowValue::Long(12),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ][..],
    ];
    create_database_with_relationships_and_rows(
        &path,
        &[
            TableRows {
                table: tables[0],
                rows: &parent,
            },
            TableRows {
                table: tables[1],
                rows: &child,
            },
        ],
        &[edge()],
        &mut budget(),
    )?;
    let original = fs::read(&path)?;
    let delete = |table: &[u8], id| -> TestResult {
        crate::delete_row(
            &path,
            crate::RowDelete {
                table,
                row: locate(&path, table, id)?,
            },
            &mut budget(),
        )?;
        Ok(())
    };
    assert!(delete(b"Alpha", 2).is_err());
    assert_eq!(fs::read(&path)?, original);
    for (id, value) in [(1, RowValue::Long(11)), (2, RowValue::Long(999))] {
        let error = crate::update_field(
            &path,
            crate::FieldUpdate {
                table: b"Alpha",
                row: locate(&path, b"Alpha", id)?,
                column: crate::ColumnOrdinal::new(1),
                value,
            },
            &mut budget(),
        )
        .err()
        .ok_or("referenced parent assignment accepted")?;
        assert!(matches!(
            error,
            crate::UpdateError::ScalarRelationshipConstraint { .. }
        ));
        assert_eq!(fs::read(&path)?, original);
    }
    let error = crate::insert_row(
        &path,
        b"Bravo",
        &[
            RowValue::Long(13),
            RowValue::Long(999),
            RowValue::Null,
            RowValue::Null,
        ],
        &mut budget(),
    )
    .err()
    .ok_or("partial orphan accepted")?;
    assert!(matches!(
        error,
        crate::UpdateError::ScalarRelationshipConstraint { .. }
    ));
    assert_eq!(fs::read(&path)?, original);
    assert!(delete(b"Alpha", 4).is_err());
    assert_eq!(fs::read(&path)?, original);
    delete(b"Bravo", 12)?;
    delete(b"Alpha", 4)?;
    crate::insert_row(
        &path,
        b"Bravo",
        &[
            RowValue::Long(13),
            RowValue::Null,
            RowValue::Null,
            RowValue::Null,
        ],
        &mut budget(),
    )?;
    let mut db = DatabaseReader::open(&path, &mut budget())?;
    db.validate(TextCodePage::Windows1252, &mut budget())?;
    Ok(())
}
