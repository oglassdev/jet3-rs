//! Reproduce the private rich relationship creation matrix through public APIs.
use jet3::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexDirection, IndexKind, IndexSpec,
    RelationshipField, RelationshipSpec, ResourceBudget, ResourceLimits, RowValue, TableRef,
    TableRows, TableSpec, create_database_with_relationship_rows,
};
use serde_json::Value;
use std::{error::Error, fs, num::NonZeroU8, path::Path};

type Result<T> = std::result::Result<T, Box<dyn Error>>;

fn payload(value: &Value, name: &str, row: usize, column: usize) -> Result<Option<Vec<u8>>> {
    let number = |field: &str| -> Result<usize> {
        Ok(value[field].as_u64().ok_or("payload number")? as usize)
    };
    Ok(match value["kind"].as_str().ok_or("payload kind")? {
        "null" => None,
        "empty" => Some(Vec::new()),
        "repeat" => Some(vec![number("byte")? as u8; number("length")?]),
        "pattern" => {
            let seed = number("seed")?;
            Some(
                (0..number("length")?)
                    .map(|i| ((i * 37 + seed * 13 + 11) % 256) as u8)
                    .collect(),
            )
        }
        "ascii" => Some(
            if name == "Label" {
                format!("label-{row}")
            } else {
                format!("r{row:02}-c{column:02}")
            }
            .into_bytes(),
        ),
        _ => return Err("unknown payload recipe".into()),
    })
}

fn create(arm: &Value, output: &Path, replicas: u64) -> Result<()> {
    let fields = arm["fields"].as_array().ok_or("fields")?;
    let source_rows = arm["rows"].as_array().ok_or("rows")?;
    let columns = fields
        .iter()
        .map(|field| -> Result<_> {
            let kind = match field["type"].as_u64().ok_or("field type")? {
                4 if field["attributes"].as_u64() == Some(16) => ColumnType::AutoIncrement,
                4 => ColumnType::Long,
                10 => ColumnType::Text {
                    max_len: NonZeroU8::new(u8::try_from(field["size"].as_u64().ok_or("size")?)?)
                        .ok_or("zero Text width")?,
                },
                11 => ColumnType::LongBinary,
                12 => ColumnType::Memo,
                _ => return Err("unsupported matrix field".into()),
            };
            let column =
                ColumnSpec::new(field["name"].as_str().ok_or("field name")?.as_bytes(), kind);
            Ok(if field["allow_zero_length"].as_bool() == Some(true) {
                column.with_allow_zero_length()
            } else {
                column
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let payloads = source_rows
        .iter()
        .enumerate()
        .map(|(r, row)| {
            fields
                .iter()
                .enumerate()
                .map(|(c, field)| {
                    if field["type"].as_u64() == Some(4) {
                        return Ok(None);
                    }
                    let name = field["name"].as_str().ok_or("field name")?;
                    payload(&row[name], name, r, c)
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let rows = source_rows
        .iter()
        .zip(&payloads)
        .map(|(row, payloads)| {
            fields
                .iter()
                .zip(&columns)
                .zip(payloads)
                .map(|((field, column), bytes)| -> Result<_> {
                    let name = field["name"].as_str().ok_or("name")?;
                    Ok(match column.column_type() {
                        ColumnType::AutoIncrement => RowValue::AutoIncrement,
                        ColumnType::Long if row[name].is_null() => RowValue::Null,
                        ColumnType::Long => {
                            RowValue::Long(i32::try_from(row[name].as_i64().ok_or("Long value")?)?)
                        }
                        ColumnType::Text { .. } => {
                            bytes.as_deref().map_or(RowValue::Null, RowValue::Text)
                        }
                        ColumnType::Memo => bytes.as_deref().map_or(RowValue::Null, RowValue::Memo),
                        ColumnType::LongBinary => bytes
                            .as_deref()
                            .map_or(RowValue::Null, RowValue::LongBinary),
                        _ => return Err("unsupported row kind".into()),
                    })
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let child_rows = rows.iter().map(Vec::as_slice).collect::<Vec<_>>();
    let parents = arm["parents"]
        .as_array()
        .ok_or("parents")?
        .iter()
        .map(|p| -> Result<_> {
            Ok([
                RowValue::Long(i32::try_from(p["Id"].as_i64().ok_or("parent Id")?)?),
                RowValue::Text(p["Label"].as_str().ok_or("parent Label")?.as_bytes()),
            ])
        })
        .collect::<Result<Vec<_>>>()?;
    let parent_rows = parents.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    let parent_columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(
            b"Label",
            ColumnType::Text {
                max_len: NonZeroU8::new(32).ok_or("width")?,
            },
        ),
    ];
    let primary_fields = [IndexColumnSpec {
        column: ColumnRef::Ordinal(0),
        direction: IndexDirection::Ascending,
    }];
    let parent_indexes = [IndexSpec {
        name: b"ByParent",
        fields: &primary_fields,
        kind: IndexKind::Primary,
    }];
    let child_indexes = [IndexSpec {
        name: b"ByChild",
        ..parent_indexes[0]
    }];
    let requests = [
        TableRows {
            table: TableSpec {
                name: b"Parent",
                columns: &parent_columns,
                indexes: &parent_indexes,
            },
            rows: &parent_rows,
        },
        TableRows {
            table: TableSpec {
                name: b"Child",
                columns: &columns,
                indexes: &child_indexes,
            },
            rows: &child_rows,
        },
    ];
    let relation = RelationshipSpec {
        name: b"ParentChild",
        parent: TableRef::Ordinal(0),
        child: TableRef::Ordinal(1),
        fields: &[RelationshipField {
            parent: ColumnRef::Ordinal(0),
            child: ColumnRef::Ordinal(1),
        }],
    };
    for replica in 1..=replicas {
        let path = output.join(format!(
            "{}-r{replica}.mdb",
            arm["name"].as_str().ok_or("arm name")?
        ));
        create_database_with_relationship_rows(
            path,
            &requests,
            &relation,
            &mut ResourceBudget::new(ResourceLimits::default()),
        )?;
    }
    Ok(())
}

fn main() -> Result<()> {
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    if args.len() != 2 {
        return Err("usage: rich_relationship_candidate MATRIX NEW_DIRECTORY".into());
    }
    let matrix: Value = serde_json::from_slice(&fs::read(&args[0])?)?;
    fs::create_dir(&args[1])?;
    for arm in matrix["arms"].as_array().ok_or("arms")? {
        create(
            arm,
            Path::new(&args[1]),
            matrix["replicas"].as_u64().ok_or("replicas")?,
        )?;
    }
    Ok(())
}
