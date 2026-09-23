//! EXP-0288 scalar endpoint compatibility and relationship key normalization.
use super::*;
use crate::{ColumnOrdinal, RowLocator, UpdateError, ValueKind};

const ID_KEY: &[IndexColumnSpec<'_>] = &[IndexColumnSpec {
    column: ColumnRef::Ordinal(0),
    direction: IndexDirection::Ascending,
}];
const VALUE_KEY: &[IndexColumnSpec<'_>] = &[IndexColumnSpec {
    column: ColumnRef::Ordinal(1),
    direction: IndexDirection::Ascending,
}];
const PARENT_INDEXES: &[IndexSpec<'_>] = &[
    IndexSpec {
        name: b"ById",
        fields: ID_KEY,
        kind: IndexKind::Primary,
    },
    IndexSpec {
        name: b"ByKey",
        fields: VALUE_KEY,
        kind: IndexKind::Unique,
    },
];

fn schema(
    parent: ColumnType,
    child: ColumnType,
    parent_rows: &[&[RowValue<'_>]],
    child_rows: &[&[RowValue<'_>]],
    path: &Path,
) -> Result<(), CreateDatabaseError> {
    create_database_with_relationships_and_rows(
        path,
        &[
            TableRows {
                table: TableSpec {
                    validation: crate::TableValidation::NONE,
                    name: b"Parent",
                    columns: &[
                        ColumnSpec::new(b"Id", ColumnType::Long),
                        ColumnSpec::new(b"Key", parent),
                    ],
                    indexes: PARENT_INDEXES,
                },
                rows: parent_rows,
            },
            TableRows {
                table: TableSpec {
                    validation: crate::TableValidation::NONE,
                    name: b"Child",
                    columns: &[
                        ColumnSpec::new(b"Id", ColumnType::Long),
                        ColumnSpec::new(b"Key", child),
                        ColumnSpec::new(b"Body", ColumnType::Memo),
                    ],
                    indexes: &PARENT_INDEXES[..1],
                },
                rows: child_rows,
            },
        ],
        &[RelationshipSpec {
            enforce: true,
            join: crate::RelationshipJoin::Inner,
            cascade_updates: false,
            cascade_deletes: false,
            name: b"ParentChild",
            parent: TableRef::Ordinal(0),
            child: TableRef::Ordinal(1),
            fields: &[RelationshipField {
                parent: ColumnRef::Ordinal(1),
                child: ColumnRef::Ordinal(1),
            }],
        }],
        &mut budget(),
    )
}

fn locate(path: &Path, table: &[u8], id: i32) -> Result<RowLocator, Box<dyn std::error::Error>> {
    let mut work = budget();
    let mut db = DatabaseReader::open(path, &mut work)?;
    let definition = crate::update::indexed_writable_table(&mut db, table, &mut work)?;
    let mut rows = db.rows(&definition, &mut work)?;
    while let Some(mut row) = rows.next_row()? {
        if matches!(row.value(ColumnOrdinal::new(0), TextCodePage::Windows1252)?.ok_or("missing Id")?.kind(), ValueKind::Long(value) if *value == id)
        {
            return Ok(row.locator());
        }
    }
    Err("missing row".into())
}

fn erase(path: &Path, table: &[u8], id: i32) -> TestResult {
    crate::delete_row(
        path,
        crate::RowDelete {
            table,
            row: locate(path, table, id)?,
        },
        &mut budget(),
    )?;
    Ok(())
}

fn verified(path: &Path) -> TestResult {
    let mut db = DatabaseReader::open(path, &mut budget())?;
    let report = db.validate(TextCodePage::Windows1252, &mut budget())?;
    assert_eq!(report.relationships_with_verified_keys, 1);
    Ok(())
}

#[test]
fn scalar_relationship_lifecycles_enforce_keys_and_preserve_refused_inputs() -> TestResult {
    let width = std::num::NonZeroU8::new(8).ok_or("width")?;
    for (kind, first, second) in [
        (
            ColumnType::Boolean,
            RowValue::Boolean(false),
            RowValue::Boolean(true),
        ),
        (ColumnType::Byte, RowValue::Byte(1), RowValue::Byte(2)),
        (
            ColumnType::Integer,
            RowValue::Integer(-1),
            RowValue::Integer(2),
        ),
        (ColumnType::Long, RowValue::Long(-1), RowValue::Long(2)),
        (
            ColumnType::Currency,
            RowValue::Currency { scaled: -12345 },
            RowValue::Currency { scaled: 20001 },
        ),
        (
            ColumnType::Single,
            RowValue::Single(-1.5),
            RowValue::Single(2.25),
        ),
        (
            ColumnType::Double,
            RowValue::Double(-1.5),
            RowValue::Double(2.25),
        ),
        (
            ColumnType::DateTime,
            RowValue::DateTime { days: 35000.5 },
            RowValue::DateTime { days: 35002.25 },
        ),
        (
            ColumnType::Text { max_len: width },
            RowValue::Text(b"first"),
            RowValue::Text(b"second"),
        ),
        (
            ColumnType::FixedText { len: width },
            RowValue::Text(b"first   "),
            RowValue::Text(b"second  "),
        ),
        (
            ColumnType::Binary { max_len: width },
            RowValue::Binary(b"\0\x81"),
            RowValue::Binary(b"\xff\0"),
        ),
        (
            ColumnType::Guid,
            RowValue::Guid([1; 16]),
            RowValue::Guid([2; 16]),
        ),
    ] {
        let directory = Directory::new()?;
        let path = directory.target();
        schema(
            kind,
            kind,
            &[&[RowValue::Long(1), first]],
            &[&[RowValue::Long(11), first, RowValue::Memo(b"old")]],
            &path,
        )?;
        verified(&path)?;
        let original = fs::read(&path)?;
        let orphan = crate::insert_row(
            &path,
            b"Child",
            &[RowValue::Long(12), second, RowValue::Null],
            &mut budget(),
        );
        assert!(
            matches!(
                orphan,
                Err(UpdateError::ScalarRelationshipConstraint { .. }
                    | UpdateError::RelationshipConstraint { .. })
            ),
            "{kind:?}: {orphan:?}"
        );
        assert_eq!(fs::read(&path)?, original);
        let referenced = crate::delete_row(
            &path,
            crate::RowDelete {
                table: b"Parent",
                row: locate(&path, b"Parent", 1)?,
            },
            &mut budget(),
        );
        assert!(
            matches!(
                referenced,
                Err(UpdateError::ScalarRelationshipConstraint { .. }
                    | UpdateError::RelationshipConstraint { .. })
            ),
            "{kind:?}: {referenced:?}"
        );
        assert_eq!(fs::read(&path)?, original);
        let repeated = crate::update_row(
            &path,
            crate::RowUpdate {
                table: b"Parent",
                row: locate(&path, b"Parent", 1)?,
                values: &[RowValue::Long(1), first],
            },
            &mut budget(),
        );
        assert!(
            matches!(
                repeated,
                Err(UpdateError::ScalarRelationshipConstraint { .. }
                    | UpdateError::RelationshipConstraint { .. })
            ),
            "{kind:?}: {repeated:?}"
        );
        assert_eq!(fs::read(&path)?, original);
        if kind == ColumnType::Long {
            let repeated = crate::update_field(
                &path,
                crate::FieldUpdate {
                    table: b"Parent",
                    row: locate(&path, b"Parent", 1)?,
                    column: ColumnOrdinal::new(1),
                    value: first,
                },
                &mut budget(),
            );
            assert!(matches!(
                repeated,
                Err(UpdateError::RelationshipConstraint { .. })
            ));
            assert_eq!(fs::read(&path)?, original);
        }
        crate::insert_row(
            &path,
            b"Parent",
            &[RowValue::Long(2), second],
            &mut budget(),
        )?;
        let payload = [b'x'; 4096];
        crate::update_row(
            &path,
            crate::RowUpdate {
                table: b"Child",
                row: locate(&path, b"Child", 11)?,
                values: &[RowValue::Long(11), second, RowValue::Memo(&payload)],
            },
            &mut budget(),
        )?;
        erase(&path, b"Parent", 1)?;
        crate::insert_row(
            &path,
            b"Child",
            &[RowValue::Long(12), second, RowValue::Null],
            &mut budget(),
        )?;
        if kind != ColumnType::Boolean {
            crate::update_row(
                &path,
                crate::RowUpdate {
                    table: b"Child",
                    row: locate(&path, b"Child", 11)?,
                    values: &[
                        RowValue::Long(11),
                        RowValue::Null,
                        RowValue::Memo(b"inline"),
                    ],
                },
                &mut budget(),
            )?;
        } else {
            erase(&path, b"Child", 11)?;
        }
        erase(&path, b"Child", 12)?;
        erase(&path, b"Parent", 2)?;
        verified(&path)?;
    }
    Ok(())
}

#[test]
fn scalar_relationship_creation_rejects_incompatible_endpoint_types() -> TestResult {
    let width = std::num::NonZeroU8::new(16).ok_or("width")?;
    for (parent, child) in [
        (ColumnType::Byte, ColumnType::Integer),
        (ColumnType::Integer, ColumnType::Long),
        (ColumnType::Single, ColumnType::Double),
        (ColumnType::Currency, ColumnType::Double),
        (ColumnType::DateTime, ColumnType::Double),
        (ColumnType::Guid, ColumnType::Binary { max_len: width }),
        (ColumnType::Boolean, ColumnType::Byte),
    ] {
        let directory = Directory::new()?;
        assert!(matches!(
            schema(parent, child, &[], &[], &directory.target()),
            Err(CreateDatabaseError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
        assert!(fs::read_dir(&directory.0)?.next().is_none());
    }
    Ok(())
}

#[test]
fn scalar_relationship_widths_and_text_storage_can_differ() -> TestResult {
    let narrow = std::num::NonZeroU8::new(8).ok_or("width")?;
    let wide = std::num::NonZeroU8::new(16).ok_or("width")?;
    for (parent, child, parent_value, matching, absent) in [
        (
            ColumnType::Text { max_len: narrow },
            ColumnType::Text { max_len: wide },
            RowValue::Text(b"shared"),
            RowValue::Text(b"shared"),
            RowValue::Text(b"longer-than-8"),
        ),
        (
            ColumnType::Text { max_len: narrow },
            ColumnType::FixedText { len: narrow },
            RowValue::Text(b"shared"),
            RowValue::Text(b"shared  "),
            RowValue::Text(b"missing "),
        ),
        (
            ColumnType::Binary { max_len: narrow },
            ColumnType::Binary { max_len: wide },
            RowValue::Binary(b"\0\xff"),
            RowValue::Binary(b"\0\xff"),
            RowValue::Binary(b"123456789"),
        ),
        (
            ColumnType::Text { max_len: narrow },
            ColumnType::Text { max_len: narrow },
            RowValue::Text(b"\xc9cho"),
            RowValue::Text(b"\xe9CHO "),
            RowValue::Text(b"Zulu"),
        ),
    ] {
        let directory = Directory::new()?;
        schema(
            parent,
            child,
            &[&[RowValue::Long(1), parent_value]],
            &[&[RowValue::Long(11), matching, RowValue::Null]],
            &directory.target(),
        )?;
        verified(&directory.target())?;
        let absent_path = directory.0.join("absent.mdb");
        assert!(matches!(
            schema(
                parent,
                child,
                &[&[RowValue::Long(1), parent_value]],
                &[&[RowValue::Long(11), absent, RowValue::Null]],
                &absent_path
            ),
            Err(CreateDatabaseError::Compose(
                ComposeError::OrphanInitialScalarRelationshipKey { row: 0 }
            ))
        ));
        assert!(!absent_path.exists());
    }
    Ok(())
}

#[test]
fn scalar_relationship_fixed_field_change_checks_parent_keys_before_publication() -> TestResult {
    let directory = Directory::new()?;
    let path = directory.target();
    let first = RowValue::Currency { scaled: -12345 };
    let second = RowValue::Currency { scaled: 23456 };
    schema(
        ColumnType::Currency,
        ColumnType::Currency,
        &[&[RowValue::Long(1), first], &[RowValue::Long(2), second]],
        &[&[RowValue::Long(11), first, RowValue::Memo(b"retained")]],
        &path,
    )?;
    let selected = locate(&path, b"Child", 11)?;
    crate::update_field(
        &path,
        crate::FieldUpdate {
            table: b"Child",
            row: selected,
            column: ColumnOrdinal::new(1),
            value: second,
        },
        &mut budget(),
    )?;
    verified(&path)?;
    let changed = fs::read(&path)?;
    let refusal = crate::update_field(
        &path,
        crate::FieldUpdate {
            table: b"Child",
            row: selected,
            column: ColumnOrdinal::new(1),
            value: RowValue::Currency { scaled: 34567 },
        },
        &mut budget(),
    );
    assert!(
        matches!(
            refusal,
            Err(UpdateError::ScalarRelationshipConstraint { .. })
        ),
        "{refusal:?}"
    );
    assert_eq!(fs::read(&path)?, changed);
    erase(&path, b"Parent", 1)?;
    verified(&path)?;
    Ok(())
}

#[test]
fn scalar_relationship_boolean_null_is_false_and_binary_empty_is_null() -> TestResult {
    let directory = Directory::new()?;
    schema(
        ColumnType::Boolean,
        ColumnType::Boolean,
        &[&[RowValue::Long(1), RowValue::Null]],
        &[&[RowValue::Long(11), RowValue::Null, RowValue::Null]],
        &directory.target(),
    )?;
    verified(&directory.target())?;
    let original = fs::read(directory.target())?;
    assert!(matches!(
        crate::insert_row(
            directory.target(),
            b"Parent",
            &[RowValue::Long(2), RowValue::Boolean(false)],
            &mut budget()
        ),
        Err(UpdateError::Unsupported("duplicate unique key"))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    let absent = directory.0.join("no-false.mdb");
    assert!(matches!(
        schema(
            ColumnType::Boolean,
            ColumnType::Boolean,
            &[&[RowValue::Long(1), RowValue::Boolean(true)]],
            &[&[RowValue::Long(11), RowValue::Null, RowValue::Null]],
            &absent
        ),
        Err(CreateDatabaseError::Compose(
            ComposeError::OrphanInitialScalarRelationshipKey { row: 0 }
        ))
    ));
    assert!(!absent.exists());
    let binary = ColumnType::Binary {
        max_len: std::num::NonZeroU8::new(8).ok_or("width")?,
    };
    let empty = directory.0.join("empty-binary.mdb");
    schema(
        binary,
        binary,
        &[],
        &[&[RowValue::Long(11), RowValue::Binary(b""), RowValue::Null]],
        &empty,
    )?;
    verified(&empty)?;
    Ok(())
}
