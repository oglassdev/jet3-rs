use super::*;
use crate::{
    ColumnOrdinal, InlineLongValue, LongValue, LongValueChunkValue, TextCodePage, ValueKind,
};

const PRIMARY: &[IndexSpec<'static>] = &[IndexSpec {
    name: b"ById",
    fields: &[IndexColumnSpec {
        column: ColumnRef::Ordinal(0),
        direction: IndexDirection::Ascending,
    }],
    kind: IndexKind::Primary,
}];
const RELATION: crate::RelationshipSpec<'static> = crate::RelationshipSpec {
    name: b"ParentChild",
    parent: RelationshipColumn {
        table: TableRef::Ordinal(0),
        column: ColumnRef::Ordinal(0),
    },
    child: RelationshipColumn {
        table: TableRef::Ordinal(1),
        column: ColumnRef::Ordinal(1),
    },
};

#[test]
fn rich_relationship_rows_keep_primary_null_keys_payloads_and_generated_ids() -> TestResult {
    for wide in [false, true] {
        let directory = Directory::new()?;
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
                    max_len: crate::column_definition_writer::nz(32),
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
                            max_len: crate::column_definition_writer::nz(64),
                        },
                    )
                    .with_allow_zero_length(),
                );
            }
        }
        let parent = TableSpec {
            name: b"Parent",
            columns: &parent_columns,
            indexes: PRIMARY,
        };
        let child = TableSpec {
            name: b"Child",
            columns: &columns,
            indexes: PRIMARY,
        };
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
        crate::create_database_with_relationship_rows(
            directory.target(),
            &requests,
            &RELATION,
            &mut budget(),
        )?;
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
    use crate::creation::schema_plan::{plan_table_schema, plan_table_schema_with_logical_index};
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
                        max_len: crate::column_definition_writer::nz(1),
                    },
                )
            }));
            let parent = TableSpec {
                name: b"Parent",
                columns: &columns,
                indexes: PRIMARY,
            };
            let plan = plan_table_schema_with_logical_index(&parent, 20, true, Some(b".rB"))?;
            if plan.definition_len() != 2048 {
                continue;
            }
            assert!(
                plan_table_schema(&parent, 20, true)?
                    .continuation_page()
                    .is_none()
            );
            assert!(plan.continuation_page().is_some());
            let payload = vec![b'p'; 4096];
            let mut values = vec![RowValue::Long(1), RowValue::Memo(&payload)];
            values.extend((0..count).map(|_| RowValue::Null));
            let child_columns = [
                ColumnSpec::new(b"Id", ColumnType::Long),
                ColumnSpec::new(b"ParentId", ColumnType::Long),
            ];
            let child = TableSpec {
                name: b"Child",
                columns: &child_columns,
                indexes: PRIMARY,
            };
            let directory = Directory::new()?;
            crate::create_database_with_relationship_rows(
                directory.target(),
                &[
                    TableRows {
                        table: parent,
                        rows: &[&values],
                    },
                    TableRows {
                        table: child,
                        rows: &[&[RowValue::Long(1), RowValue::Long(1)]],
                    },
                ],
                &RELATION,
                &mut budget(),
            )?;
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
            TableSpec {
                name: b"Parent",
                columns: &parent_columns,
                indexes: &parent_index,
            },
            TableSpec {
                name: b"Child",
                columns: &child_columns,
                indexes: PRIMARY,
            },
        ];
        let relation = crate::RelationshipSpec {
            name: if parent_name == b".rB" {
                b"ParentChild"
            } else {
                b"ById"
            },
            ..RELATION
        };
        let directory = Directory::new()?;
        assert!(matches!(
            crate::create_database_with_relationship(
                directory.target(),
                &tables,
                &relation,
                &mut budget()
            ),
            Err(CreateDatabaseError::Compose(ComposeError::Schema(_)))
        ));
        assert!(directory.empty()?);
    }
    Ok(())
}
