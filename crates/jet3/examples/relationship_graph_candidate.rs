//! Reproduce private relationship graphs through the public creation API.
use jet3::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexDirection, IndexKind, IndexNullPolicy,
    IndexSpec, RelationshipField, RelationshipSpec, ResourceBudget, ResourceLimits, RowValue,
    TableRef, TableRows, TableSpec, create_database_with_relationships_and_rows,
};
use serde_json::Value;
use std::{error::Error, fs, num::NonZeroU8, path::Path};
type Result<T> = std::result::Result<T, Box<dyn Error>>;

fn text<'a>(value: &'a Value, field: &str) -> Result<&'a str> {
    value[field]
        .as_str()
        .ok_or_else(|| format!("missing text {field}").into())
}
fn array<'a>(value: &'a Value, field: &str) -> Result<&'a [Value]> {
    value[field]
        .as_array()
        .map(Vec::as_slice)
        .ok_or_else(|| format!("missing array {field}").into())
}
fn bytes(value: &Value) -> Result<Option<Vec<u8>>> {
    if value.is_null() {
        return Ok(None);
    }
    if let Some(value) = value.as_str() {
        return Ok(Some(value.as_bytes().to_vec()));
    }
    if let Some(values) = value.as_array() {
        return values
            .iter()
            .map(|value| Ok(u8::try_from(value.as_u64().ok_or("byte")?)?))
            .collect::<Result<Vec<_>>>()
            .map(Some);
    }
    let length = value["length"].as_u64().ok_or("payload length")? as usize;
    Ok(Some(match text(value, "kind")? {
        "repeat" => vec![u8::try_from(value["byte"].as_u64().ok_or("payload byte")?)?; length],
        "pattern" => {
            let seed = value["seed"].as_u64().ok_or("payload seed")? as usize;
            (0..length)
                .map(|i| ((i * 37 + seed * 13 + 11) % 256) as u8)
                .collect()
        }
        _ => return Err("payload kind".into()),
    }))
}
fn width(field: &Value) -> Result<NonZeroU8> {
    NonZeroU8::new(u8::try_from(field["size"].as_u64().ok_or("size")?)?)
        .ok_or_else(|| "field width".into())
}
fn create(case: &Value, output: &Path, replicas: u64) -> Result<()> {
    let tables = array(case, "tables")?;
    let columns = tables
        .iter()
        .map(|table| {
            array(table, "fields")?
                .iter()
                .map(|field| {
                    let kind = match field["type"].as_u64().ok_or("type")? {
                        1 => ColumnType::Boolean,
                        2 => ColumnType::Byte,
                        3 => ColumnType::Integer,
                        4 if field["attributes"].as_u64() == Some(16) => ColumnType::AutoIncrement,
                        4 => ColumnType::Long,
                        5 => ColumnType::Currency,
                        6 => ColumnType::Single,
                        7 => ColumnType::Double,
                        8 => ColumnType::DateTime,
                        9 => ColumnType::Binary {
                            max_len: width(field)?,
                        },
                        10 if field["attributes"].as_u64() == Some(1) => {
                            ColumnType::FixedText { len: width(field)? }
                        }
                        10 => ColumnType::Text {
                            max_len: width(field)?,
                        },
                        11 => ColumnType::LongBinary,
                        12 => ColumnType::Memo,
                        15 => ColumnType::Guid,
                        _ => return Err("unsupported matrix field".into()),
                    };
                    let column = ColumnSpec::new(text(field, "name")?.as_bytes(), kind);
                    let column = if field["required"].as_bool().unwrap_or(false) {
                        column.with_required()
                    } else {
                        column
                    };
                    Ok(
                        if matches!(
                            kind,
                            ColumnType::Text { .. }
                                | ColumnType::FixedText { .. }
                                | ColumnType::Memo
                        ) && field["allow_zero_length"].as_bool().unwrap_or(true)
                        {
                            column.with_allow_zero_length()
                        } else {
                            column
                        },
                    )
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let payloads = tables
        .iter()
        .map(|table| {
            array(table, "rows")?
                .iter()
                .map(|row| {
                    array(table, "fields")?
                        .iter()
                        .map(|field| {
                            if matches!(field["type"].as_u64(), Some(9..=12 | 15)) {
                                bytes(&row[text(field, "name")?])
                            } else {
                                Ok(None)
                            }
                        })
                        .collect::<Result<Vec<_>>>()
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let rows = tables
        .iter()
        .zip(&columns)
        .zip(&payloads)
        .map(|((table, columns), payloads)| {
            array(table, "rows")?
                .iter()
                .zip(payloads)
                .map(|(row, payloads)| {
                    columns
                        .iter()
                        .zip(payloads)
                        .map(|(column, payload)| {
                            let value = &row[std::str::from_utf8(column.name())?];
                            Ok(match column.column_type() {
                                ColumnType::AutoIncrement if value.is_null() => {
                                    RowValue::AutoIncrement
                                }
                                _ if value.is_null() => RowValue::Null,
                                ColumnType::Boolean => {
                                    RowValue::Boolean(value.as_bool().ok_or("Boolean")?)
                                }
                                ColumnType::Byte => {
                                    RowValue::Byte(u8::try_from(value.as_u64().ok_or("Byte")?)?)
                                }
                                ColumnType::Integer => RowValue::Integer(i16::try_from(
                                    value.as_i64().ok_or("Integer")?,
                                )?),
                                ColumnType::AutoIncrement | ColumnType::Long => {
                                    RowValue::Long(i32::try_from(value.as_i64().ok_or("Long")?)?)
                                }
                                ColumnType::Currency => RowValue::Currency {
                                    scaled: value.as_i64().ok_or("scaled Currency")?,
                                },
                                ColumnType::Single => {
                                    RowValue::Single(value.as_f64().ok_or("Single")? as f32)
                                }
                                ColumnType::Double => {
                                    RowValue::Double(value.as_f64().ok_or("Double")?)
                                }
                                ColumnType::DateTime => RowValue::DateTime {
                                    days: value.as_f64().ok_or("Date")?,
                                },
                                ColumnType::Text { .. } | ColumnType::FixedText { .. } => {
                                    payload.as_deref().map_or(RowValue::Null, RowValue::Text)
                                }
                                ColumnType::Binary { .. } => {
                                    payload.as_deref().map_or(RowValue::Null, RowValue::Binary)
                                }
                                ColumnType::Guid => RowValue::Guid(
                                    payload.as_deref().ok_or("GUID bytes")?.try_into()?,
                                ),
                                ColumnType::Memo => {
                                    payload.as_deref().map_or(RowValue::Null, RowValue::Memo)
                                }
                                ColumnType::LongBinary => payload
                                    .as_deref()
                                    .map_or(RowValue::Null, RowValue::LongBinary),
                            })
                        })
                        .collect::<Result<Vec<_>>>()
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let slices = rows
        .iter()
        .map(|rows| rows.iter().map(Vec::as_slice).collect::<Vec<_>>())
        .collect::<Vec<_>>();
    let names = tables
        .iter()
        .map(|t| Ok(format!("By{}", text(t, "name")?)))
        .collect::<Result<Vec<_>>>()?;
    let index_fields = tables
        .iter()
        .map(|table| {
            if let Some(indexes) = table["indexes"].as_array() {
                indexes
                    .iter()
                    .map(|index| {
                        if let Some(fields) = index["fields"].as_array() {
                            fields
                                .iter()
                                .map(|field| {
                                    Ok(IndexColumnSpec {
                                        column: ColumnRef::Name(text(field, "name")?.as_bytes()),
                                        direction: if field["direction"].as_str() == Some("desc") {
                                            IndexDirection::Descending
                                        } else {
                                            IndexDirection::Ascending
                                        },
                                    })
                                })
                                .collect::<Result<Vec<_>>>()
                        } else {
                            Ok(vec![IndexColumnSpec {
                                column: ColumnRef::Name(text(index, "field")?.as_bytes()),
                                direction: if index["direction"].as_str() == Some("desc") {
                                    IndexDirection::Descending
                                } else {
                                    IndexDirection::Ascending
                                },
                            }])
                        }
                    })
                    .collect::<Result<Vec<_>>>()
            } else {
                Ok(vec![vec![IndexColumnSpec {
                    column: ColumnRef::Name(b"Id"),
                    direction: IndexDirection::Ascending,
                }]])
            }
        })
        .collect::<Result<Vec<_>>>()?;
    let indexes = tables
        .iter()
        .zip(&index_fields)
        .zip(&names)
        .map(|((table, fields), name)| {
            if let Some(indexes) = table["indexes"].as_array() {
                indexes
                    .iter()
                    .zip(fields)
                    .map(|(index, fields)| {
                        let mut kind = if index["primary"].as_bool() == Some(true) {
                            IndexKind::Primary
                        } else if index["unique"].as_bool() == Some(true) {
                            IndexKind::Unique
                        } else {
                            IndexKind::Ordinary
                        };
                        if index["ignore_nulls"].as_bool() == Some(true) {
                            kind = kind.with_null_policy(IndexNullPolicy::IgnoreAllNull);
                        } else if index["required"].as_bool() == Some(true) {
                            kind = kind.with_null_policy(IndexNullPolicy::Required);
                        }
                        Ok(IndexSpec {
                            name: text(index, "name")?.as_bytes(),
                            fields,
                            kind,
                        })
                    })
                    .collect::<Result<Vec<_>>>()
            } else {
                Ok(vec![IndexSpec {
                    name: name.as_bytes(),
                    fields: &fields[0],
                    kind: IndexKind::Primary,
                }])
            }
        })
        .collect::<Result<Vec<_>>>()?;
    let requests = tables
        .iter()
        .zip(&columns)
        .zip(&indexes)
        .zip(&slices)
        .map(|(((table, columns), indexes), rows)| {
            Ok(TableRows {
                table: TableSpec {
                    validation: jet3::TableValidation::NONE,
                    name: text(table, "name")?.as_bytes(),
                    columns,
                    indexes,
                },
                rows,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    let relations = array(case, "relations")?;
    let relationship_fields = relations
        .iter()
        .map(|relation| {
            let field = |field| -> Result<RelationshipField<'_>> {
                Ok(RelationshipField {
                    parent: ColumnRef::Name(text(field, "field")?.as_bytes()),
                    child: ColumnRef::Name(text(field, "foreign_field")?.as_bytes()),
                })
            };
            if let Some(fields) = relation.get("fields") {
                fields
                    .as_array()
                    .ok_or("relationship fields must be an array")?
                    .iter()
                    .map(field)
                    .collect::<Result<Vec<_>>>()
            } else {
                Ok(vec![field(relation)?])
            }
        })
        .collect::<Result<Vec<_>>>()?;
    let relationships = relations
        .iter()
        .zip(&relationship_fields)
        .map(|(relation, fields)| {
            Ok(RelationshipSpec {
                cascade_updates: relation["attributes"].as_u64().unwrap_or(0) & 256 != 0,
                cascade_deletes: relation["attributes"].as_u64().unwrap_or(0) & 4096 != 0,
                name: text(relation, "name")?.as_bytes(),
                parent: TableRef::Name(text(relation, "table")?.as_bytes()),
                child: TableRef::Name(text(relation, "foreign_table")?.as_bytes()),
                fields,
            })
        })
        .collect::<Result<Vec<_>>>()?;
    for replica in 1..=replicas {
        create_database_with_relationships_and_rows(
            output.join(format!("{}-r{replica}.mdb", text(case, "name")?)),
            &requests,
            &relationships,
            &mut ResourceBudget::new(ResourceLimits::default()),
        )?;
    }
    Ok(())
}
fn main() -> Result<()> {
    let args = std::env::args_os().skip(1).collect::<Vec<_>>();
    if args.len() != 2 {
        return Err("usage: relationship_graph_candidate MATRIX NEW_DIRECTORY".into());
    }
    let matrix: Value = serde_json::from_slice(&fs::read(&args[0])?)?;
    fs::create_dir(&args[1])?;
    for case in array(&matrix, "graphs")? {
        create(
            case,
            Path::new(&args[1]),
            matrix["replicas"].as_u64().unwrap_or(2),
        )?;
    }
    Ok(())
}
