//! EXP-0288 scalar endpoint compatibility and relationship key normalization.
use super::api_relationship_graph_tests::*;
use crate::testkit::create_spec;
use crate::testkit::index;
use crate::testkit::table;
use crate::{
    ColumnOrdinal, ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexDirection, IndexKind,
    IndexSpec, RelationshipField, RelationshipSpec, RowValue, TableRef, WriteError,
    create::{DatabaseSpec, TableRows, composer::ComposeError},
};
use std::fs;
use std::path::Path;

const ID_KEY: &[IndexColumnSpec<'_>] = &[IndexColumnSpec {
    column: ColumnRef::Ordinal(0),
    direction: IndexDirection::Ascending,
}];
const VALUE_KEY: &[IndexColumnSpec<'_>] = &[IndexColumnSpec {
    column: ColumnRef::Ordinal(1),
    direction: IndexDirection::Ascending,
}];
const PARENT_INDEXES: &[IndexSpec<'_>] = &[
    index(b"ById", ID_KEY, IndexKind::Primary),
    index(b"ByKey", VALUE_KEY, IndexKind::Unique),
];

fn schema(
    parent: ColumnType,
    child: ColumnType,
    parent_rows: &[&[RowValue<'_>]],
    child_rows: &[&[RowValue<'_>]],
    path: &Path,
) -> Result<(), WriteError> {
    create_spec(
        path,
        &DatabaseSpec {
            tables: &[
                TableRows {
                    table: table(
                        b"Parent",
                        &[
                            ColumnSpec::new(b"Id", ColumnType::Long),
                            ColumnSpec::new(b"Key", parent),
                        ],
                        PARENT_INDEXES,
                    ),
                    rows: parent_rows,
                },
                TableRows {
                    table: table(
                        b"Child",
                        &[
                            ColumnSpec::new(b"Id", ColumnType::Long),
                            ColumnSpec::new(b"Key", child),
                            ColumnSpec::new(b"Body", ColumnType::Memo),
                        ],
                        &PARENT_INDEXES[..1],
                    ),
                    rows: child_rows,
                },
            ],
            relationships: &[RelationshipSpec {
                unique: false,
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
            ..DatabaseSpec::default()
        },
    )
}

fn erase(path: &Path, table: &[u8], id: i32) -> Result<(), WriteError> {
    let row = locate(path, table, id).map_err(|_| WriteError::NotFound("test row"))?;
    crate::delete_row(path, crate::RowDelete { table, row }, &mut budget())
}
fn replace(path: &Path, table: &[u8], id: i32, values: &[RowValue<'_>]) -> Result<(), WriteError> {
    let row = locate(path, table, id).map_err(|_| WriteError::NotFound("test row"))?;
    let request = crate::RowUpdate { table, row, values };
    crate::update_row(path, request, &mut budget())
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
        let directory = TempDir::new("create")?;
        let path = directory.target();
        schema(
            kind,
            kind,
            &[&[RowValue::Long(1), first]],
            &[&[RowValue::Long(11), first, RowValue::Memo(b"old")]],
            &path,
        )?;
        assert_eq!(verified(&path)?, 1);
        let original = fs::read(&path)?;
        let orphan = crate::insert_row(
            &path,
            b"Child",
            &[RowValue::Long(12), second, RowValue::Null],
            &mut budget(),
        )
        .map(|_| ());
        let referenced = erase(&path, b"Parent", 1);
        let repeated = replace(&path, b"Parent", 1, &[RowValue::Long(1), first]);
        for result in [orphan, referenced, repeated] {
            assert!(
                matches!(
                    result,
                    Err(WriteError::ScalarRelationshipConstraint { .. }
                        | WriteError::RelationshipConstraint { .. })
                ),
                "{kind:?}: {result:?}"
            );
        }
        assert_eq!(fs::read(&path)?, original);
        if kind == ColumnType::Long {
            let request = crate::FieldUpdate {
                table: b"Parent",
                row: locate(&path, b"Parent", 1)?,
                column: ColumnOrdinal::new(1),
                value: first,
            };
            assert!(matches!(
                crate::update_field(&path, request, &mut budget()),
                Err(WriteError::RelationshipConstraint { .. })
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
        let moved = [RowValue::Long(11), second, RowValue::Memo(&payload)];
        replace(&path, b"Child", 11, &moved)?;
        erase(&path, b"Parent", 1)?;
        let child = [RowValue::Long(12), second, RowValue::Null];
        crate::insert_row(&path, b"Child", &child, &mut budget())?;
        if kind != ColumnType::Boolean {
            let nulled = [
                RowValue::Long(11),
                RowValue::Null,
                RowValue::Memo(b"inline"),
            ];
            replace(&path, b"Child", 11, &nulled)?;
        } else {
            erase(&path, b"Child", 11)?;
        }
        erase(&path, b"Child", 12)?;
        erase(&path, b"Parent", 2)?;
        assert_eq!(verified(&path)?, 1);
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
        let directory = TempDir::new("create")?;
        assert!(matches!(
            schema(parent, child, &[], &[], &directory.target()),
            Err(WriteError::Compose(
                ComposeError::UnsupportedRelationship { .. }
            ))
        ));
        assert!(fs::read_dir(&*directory)?.next().is_none());
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
        let directory = TempDir::new("create")?;
        schema(
            parent,
            child,
            &[&[RowValue::Long(1), parent_value]],
            &[&[RowValue::Long(11), matching, RowValue::Null]],
            &directory.target(),
        )?;
        assert_eq!(verified(&directory.target())?, 1);
        let absent_path = directory.join("absent.mdb");
        assert!(matches!(
            schema(
                parent,
                child,
                &[&[RowValue::Long(1), parent_value]],
                &[&[RowValue::Long(11), absent, RowValue::Null]],
                &absent_path
            ),
            Err(WriteError::Compose(
                ComposeError::OrphanInitialScalarRelationshipKey { row: 0 }
            ))
        ));
        assert!(!absent_path.exists());
    }
    Ok(())
}

#[test]
fn scalar_relationship_fixed_field_change_checks_parent_keys_before_publication() -> TestResult {
    let directory = TempDir::new("create")?;
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
    let assign = |value| {
        let request = crate::FieldUpdate {
            table: b"Child",
            row: selected,
            column: ColumnOrdinal::new(1),
            value,
        };
        crate::update_field(&path, request, &mut budget())
    };
    assign(second)?;
    assert_eq!(verified(&path)?, 1);
    let changed = fs::read(&path)?;
    let refusal = assign(RowValue::Currency { scaled: 34567 });
    assert!(
        matches!(
            refusal,
            Err(WriteError::ScalarRelationshipConstraint { .. })
        ),
        "{refusal:?}"
    );
    assert_eq!(fs::read(&path)?, changed);
    erase(&path, b"Parent", 1)?;
    assert_eq!(verified(&path)?, 1);
    Ok(())
}

#[test]
fn scalar_relationship_boolean_null_is_false_and_binary_empty_is_null() -> TestResult {
    let directory = TempDir::new("create")?;
    schema(
        ColumnType::Boolean,
        ColumnType::Boolean,
        &[&[RowValue::Long(1), RowValue::Null]],
        &[&[RowValue::Long(11), RowValue::Null, RowValue::Null]],
        &directory.target(),
    )?;
    assert_eq!(verified(&directory.target())?, 1);
    let original = fs::read(directory.target())?;
    assert!(matches!(
        crate::insert_row(
            directory.target(),
            b"Parent",
            &[RowValue::Long(2), RowValue::Boolean(false)],
            &mut budget()
        ),
        Err(WriteError::Unsupported("duplicate unique key"))
    ));
    assert_eq!(fs::read(directory.target())?, original);
    let absent = directory.join("no-false.mdb");
    assert!(matches!(
        schema(
            ColumnType::Boolean,
            ColumnType::Boolean,
            &[&[RowValue::Long(1), RowValue::Boolean(true)]],
            &[&[RowValue::Long(11), RowValue::Null, RowValue::Null]],
            &absent
        ),
        Err(WriteError::Compose(
            ComposeError::OrphanInitialScalarRelationshipKey { row: 0 }
        ))
    ));
    assert!(!absent.exists());
    let binary = ColumnType::Binary {
        max_len: std::num::NonZeroU8::new(8).ok_or("width")?,
    };
    let empty = directory.join("empty-binary.mdb");
    schema(
        binary,
        binary,
        &[],
        &[&[RowValue::Long(11), RowValue::Binary(b""), RowValue::Null]],
        &empty,
    )?;
    assert_eq!(verified(&empty)?, 1);
    Ok(())
}
