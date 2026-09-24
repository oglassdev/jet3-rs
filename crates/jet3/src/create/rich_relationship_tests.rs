use super::api_relationship_tests::*;
use crate::WriteError;
use crate::testkit::create_spec;
use crate::testkit::{index, table};
use crate::{
    ColumnOrdinal, ColumnRef, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec,
    IndexDirection, IndexKind, IndexSpec, InlineLongValue, LongValue, LongValueChunkValue,
    PageNumber, RelationshipField, RowValue, TableRef, TextCodePage, ValueKind,
    create::{api::*, composer::ComposeError},
};
use std::fs;

const PRIMARY: &[IndexSpec<'static>] = &[index(
    b"ById",
    &[IndexColumnSpec {
        column: ColumnRef::Ordinal(0),
        direction: IndexDirection::Ascending,
    }],
    IndexKind::Primary,
)];
const RELATION: crate::RelationshipSpec<'static> = crate::RelationshipSpec {
    unique: false,
    enforce: true,
    join: crate::RelationshipJoin::Inner,
    cascade_updates: false,
    cascade_deletes: false,
    name: b"ParentChild",
    parent: TableRef::Ordinal(0),
    child: TableRef::Ordinal(1),
    fields: &[RelationshipField {
        parent: ColumnRef::Ordinal(0),
        child: ColumnRef::Ordinal(1),
    }],
};

#[test]
fn rich_relationship_rows_keep_primary_null_keys_payloads_and_generated_ids() -> TestResult {
    for wide in [false, true] {
        let directory = TempDir::new("create")?;
        let names = (0..40)
            .map(|n| format!("Column{n:02}{}", "x".repeat(40)).into_bytes())
            .collect::<Vec<_>>();
        let parent_columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
        let mut columns = vec![
            ColumnSpec::new(b"Id", ColumnType::AutoIncrement),
            ColumnSpec::new(b"ParentId", ColumnType::Long),
            ColumnSpec::new(
                b"Text",
                ColumnType::Text {
                    max_len: crate::definition::column_writer::nz(32),
                },
            )
            .with_allow_zero_length(),
            ColumnSpec::new(b"Memo", ColumnType::Memo).with_allow_zero_length(),
            ColumnSpec::new(b"OLE", ColumnType::LongBinary),
        ];
        if wide {
            for name in &names[..6] {
                columns.push(ColumnSpec::new(name, ColumnType::Memo).with_allow_zero_length());
            }
            for name in &names[6..] {
                columns.push(
                    ColumnSpec::new(
                        name,
                        ColumnType::Text {
                            max_len: crate::definition::column_writer::nz(64),
                        },
                    )
                    .with_allow_zero_length(),
                );
            }
        }
        let parent = table(b"Parent", &parent_columns, PRIMARY);
        let child = table(b"Child", &columns, PRIMARY);
        let payload = vec![b'q'; 4096];
        let mut values = Vec::new();
        for row in 0..4 {
            let mut value = vec![
                RowValue::AutoIncrement,
                if row == 0 {
                    RowValue::Null
                } else {
                    RowValue::Long(1)
                },
                RowValue::Text(b""),
                RowValue::Memo(if row % 2 == 0 { &payload } else { b"" }),
                RowValue::LongBinary(if row % 2 == 0 { b"" } else { &payload }),
            ];
            if wide {
                value.extend(
                    (0..6).map(|column| RowValue::Memo(if column == row { &payload } else { b"" })),
                );
                value.extend((6..40).map(|_| RowValue::Text(b"")));
            }
            values.push(value);
        }
        let rows = values.iter().map(Vec::as_slice).collect::<Vec<_>>();
        let requests = [
            TableRows {
                table: parent,
                rows: &[&[RowValue::Long(1)]],
            },
            TableRows {
                table: child,
                rows: &rows,
            },
        ];
        create_spec(directory.target(), &single(&requests, &RELATION))?;
        let mut operation = budget();
        let mut reader = DatabaseReader::open(directory.target(), &mut operation)?;
        let parent = reader.table_definition(PageNumber::new(20), &mut operation)?;
        let parent_relation = parent.relationships().next().ok_or("parent relation")?;
        assert_eq!(
            (
                parent_relation.raw_selector(),
                parent_relation.raw_relation_ordinal()
            ),
            (1, 1)
        );
        let child = reader.table_definition(parent_relation.related_table(), &mut operation)?;
        let foreign = child.relationships().next().ok_or("child relation")?;
        assert_eq!(
            (
                foreign.physical_index(),
                foreign.raw_selector(),
                foreign.raw_relation_ordinal()
            ),
            (1, 1, 1)
        );
        assert_eq!(child.long_value_maps().len(), if wide { 8 } else { 2 });
        assert!(child.columns()[0].auto_increment());
        if wide {
            assert!(
                child
                    .long_value_maps()
                    .iter()
                    .any(|maps| maps.owned().page() != child.maps().owned().page())
            );
        }
        for index in 0..2 {
            let tree = reader.index_tree(&child, index, &mut operation)?;
            assert_eq!(tree.entries().len(), 4);
        }
        let mut cursor = reader.rows(&child, &mut operation)?;
        for (position, expected) in values.iter().enumerate() {
            let mut row = cursor.next_row()?.ok_or("missing child")?;
            assert!(
                matches!(row.value(ColumnOrdinal::new(0), TextCodePage::Windows1252)?.ok_or("id")?.kind(), ValueKind::Long(id) if *id == position as i32 + 1)
            );
            let mut external = Vec::new();
            for (column, expected) in expected.iter().enumerate().skip(3) {
                let actual = row
                    .value(ColumnOrdinal::new(column as u16), TextCodePage::Windows1252)?
                    .ok_or("column")?;
                let payload = match expected {
                    RowValue::LongBinary([]) => {
                        assert!(matches!(actual.kind(), ValueKind::Null));
                        continue;
                    }
                    RowValue::Memo(bytes) | RowValue::LongBinary(bytes) => *bytes,
                    _ => continue,
                };
                match actual.kind() {
                    ValueKind::LongValue(LongValue::Inline { value, .. }) => assert_eq!(
                        match value {
                            InlineLongValue::Text(text) => text.raw_bytes(),
                            InlineLongValue::Binary(bytes) => bytes,
                        },
                        payload
                    ),
                    ValueKind::LongValue(LongValue::External(reference)) => {
                        external.push((*reference, payload))
                    }
                    _ => return Err("payload kind".into()),
                }
            }
            for (reference, expected) in external {
                let mut stream = cursor.long_value(reference)?;
                let mut actual = Vec::new();
                while let Some(chunk) = stream.next_chunk()? {
                    actual.extend_from_slice(match chunk.value() {
                        LongValueChunkValue::Text(text) => text.raw_bytes(),
                        LongValueChunkValue::Binary(bytes) => bytes,
                    });
                }
                assert_eq!(actual, expected);
            }
        }
        assert!(cursor.next_row()?.is_none());
        drop(cursor);
        drop(reader);
        crate::insert_row(directory.target(), b"Child", &values[0], &mut budget())?;
        let mut reader = DatabaseReader::open(directory.target(), &mut operation)?;
        let child = reader.table_definition(child.root(), &mut operation)?;
        assert_eq!(child.row_count(), 5);
        for index in 0..2 {
            assert_eq!(
                reader
                    .index_tree(&child, index, &mut operation)?
                    .entries()
                    .len(),
                5
            );
        }
    }
    Ok(())
}

#[test]
fn parent_relationship_record_at_definition_boundary_keeps_external_payload_start() -> TestResult {
    use crate::create::schema_plan::{plan_table_schema, plan_table_schema_for_order};
    let names = (0..32)
        .map(|n| format!("Extra{n:02}{}", "p".repeat(40)).into_bytes())
        .collect::<Vec<_>>();
    for count in 20..=32 {
        let mut names = names.clone();
        for width in 1..=64 {
            names[count - 1] = vec![b'Z'; width];
            let mut columns = vec![
                ColumnSpec::new(b"Id", ColumnType::Long),
                ColumnSpec::new(b"Body", ColumnType::Memo),
            ];
            columns.extend(names[..count].iter().map(|name| {
                ColumnSpec::new(
                    name,
                    ColumnType::Text {
                        max_len: crate::definition::column_writer::nz(1),
                    },
                )
            }));
            let parent = table(b"Parent", &columns, PRIMARY);
            let plan = plan_table_schema_for_order(
                &parent,
                20,
                true,
                &[b".rB".as_slice()],
                parent.indexes.len(),
                crate::SortOrder::General,
                &mut budget(),
            )?;
            if plan.definition_len() != 2048 {
                continue;
            }
            let plain = plan_table_schema(&parent, 20, true, &mut budget())?;
            assert!(plain.continuation_page().is_none());
            assert!(plan.continuation_page().is_some());
            let payload = vec![b'p'; 4096];
            let mut values = vec![RowValue::Long(1), RowValue::Memo(&payload)];
            values.extend((0..count).map(|_| RowValue::Null));
            let child_columns = [
                ColumnSpec::new(b"Id", ColumnType::Long),
                ColumnSpec::new(b"ParentId", ColumnType::Long),
            ];
            let child = table(b"Child", &child_columns, PRIMARY);
            let directory = TempDir::new("create")?;
            let requests = [
                TableRows {
                    table: parent,
                    rows: &[&values],
                },
                TableRows {
                    table: child,
                    rows: &[&[RowValue::Long(1), RowValue::Long(1)]],
                },
            ];
            create_spec(directory.target(), &single(&requests, &RELATION))?;
            let raw = fs::read(directory.target())?;
            let continuation = plan.continuation_page().ok_or("continuation")?.get() as usize;
            assert_eq!(
                &raw[continuation * crate::PAGE_BYTES + 8..(continuation + 1) * crate::PAGE_BYTES],
                &[0; crate::PAGE_BYTES - 8]
            );
            return Ok(());
        }
    }
    Err("fixture must reach the exact definition boundary".into())
}

#[test]
fn relationship_names_cannot_replace_declared_primary_indexes() -> TestResult {
    let parent_columns = [ColumnSpec::new(b"Id", ColumnType::Long)];
    let child_columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"ParentId", ColumnType::Long),
    ];
    for parent_name in [b"ById".as_slice(), b".rB"] {
        let parent_index = [IndexSpec {
            name: parent_name,
            ..PRIMARY[0]
        }];
        let tables = [
            table(b"Parent", &parent_columns, &parent_index),
            table(b"Child", &child_columns, PRIMARY),
        ];
        let relation = crate::RelationshipSpec {
            name: if parent_name == b".rB" {
                b"ParentChild"
            } else {
                b"ById"
            },
            ..RELATION
        };
        let directory = TempDir::new("create")?;
        assert!(matches!(
            create_spec(
                directory.target(),
                &single(&tables.map(TableRows::empty), &relation)
            ),
            Err(WriteError::Compose(ComposeError::Schema(_)))
        ));
        assert!(directory.is_empty()?);
    }
    Ok(())
}
