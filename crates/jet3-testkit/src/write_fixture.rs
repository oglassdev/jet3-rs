//! Declarative fixtures built through the public creation API for the write leg.
use std::{num::NonZeroU8, path::Path};

use jet3::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexDirection, IndexKind, IndexSpec,
    RelationshipColumn, RelationshipSpec, ResourceBudget, ResourceLimits, RowValue, TableRef,
    TableRows, TableSpec, create_database_with_relationship_rows, create_database_with_table_rows,
};
use serde::Deserialize;
use serde_json::Value;

/// Separate write inventory; read inventory membership remains unchanged.
pub const WRITE_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/write-scenarios.json");
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;

#[derive(Deserialize)]
struct Inventory {
    scenarios: Vec<Recipe>,
}
#[derive(Deserialize)]
struct Recipe {
    id: String,
    tables: Vec<Table>,
    relationship: Option<Relation>,
}
#[derive(Deserialize)]
struct Table {
    name: String,
    columns: Vec<Column>,
    indexes: Vec<Index>,
    rows: Vec<Vec<Value>>,
    repeat: Option<usize>,
}
#[derive(Deserialize)]
struct Column {
    name: String,
    kind: String,
    size: Option<u8>,
}
#[derive(Deserialize)]
struct Index {
    name: String,
    kind: String,
    fields: Vec<Field>,
}
#[derive(Deserialize)]
struct Field {
    column: u16,
    descending: bool,
}
#[derive(Deserialize)]
struct Relation {
    name: String,
    parent_table: u16,
    parent_column: u16,
    child_table: u16,
    child_column: u16,
}

fn column_type(column: &Column) -> Result<ColumnType> {
    Ok(match column.kind.as_str() {
        "Boolean" => ColumnType::Boolean,
        "Byte" => ColumnType::Byte,
        "Integer" => ColumnType::Integer,
        "Long" => ColumnType::Long,
        "AutoIncrement" => ColumnType::AutoIncrement,
        "Currency" => ColumnType::Currency,
        "Single" => ColumnType::Single,
        "Double" => ColumnType::Double,
        "DateTime" => ColumnType::DateTime,
        "Guid" => ColumnType::Guid,
        "Memo" => ColumnType::Memo,
        "LongBinary" => ColumnType::LongBinary,
        "Text" => ColumnType::Text {
            max_len: NonZeroU8::new(column.size.ok_or("missing size")?).ok_or("zero size")?,
        },
        "Binary" => ColumnType::Binary {
            max_len: NonZeroU8::new(column.size.ok_or("missing size")?).ok_or("zero size")?,
        },
        _ => return Err("unknown recipe column type".into()),
    })
}

fn expand(value: &Value, row: usize) -> Result<Value> {
    if let Some(start) = value.get("sequence").and_then(Value::as_i64) {
        return Ok(Value::from(
            start
                .checked_add(row.try_into()?)
                .ok_or("sequence overflow")?,
        ));
    }
    if let Some(text) = value.get("repeat_text").and_then(Value::as_str) {
        return Ok(Value::String(
            text.repeat(
                value["count"]
                    .as_u64()
                    .ok_or("missing repeat count")?
                    .try_into()?,
            ),
        ));
    }
    Ok(value.clone())
}

fn row_value<'a>(value: &'a Value, column: &Column) -> Result<RowValue<'a>> {
    if value.is_null() {
        return Ok(RowValue::Null);
    }
    let integer = || value.as_i64().ok_or("expected integer");
    let number = || value.as_f64().ok_or("expected number");
    let text = || value.as_str().ok_or("expected ASCII text");
    Ok(match column.kind.as_str() {
        "AutoIncrement" if value.get("auto").and_then(Value::as_bool) == Some(true) => {
            RowValue::AutoIncrement
        }
        "Boolean" => RowValue::Boolean(value.as_bool().ok_or("expected boolean")?),
        "Byte" => RowValue::Byte(integer()?.try_into()?),
        "Integer" => RowValue::Integer(integer()?.try_into()?),
        "Long" => RowValue::Long(integer()?.try_into()?),
        "Currency" => RowValue::Currency { scaled: integer()? },
        "Single" => RowValue::Single(number()? as f32),
        "Double" => RowValue::Double(number()?),
        "DateTime" => RowValue::DateTime { days: number()? },
        "Text" | "Memo" | "Binary" | "LongBinary" => {
            let bytes = text()?.as_bytes();
            if !bytes.is_ascii() {
                return Err("fixture strings must be ASCII".into());
            }
            match column.kind.as_str() {
                "Text" => RowValue::Text(bytes),
                "Memo" => RowValue::Memo(bytes),
                "Binary" => RowValue::Binary(bytes),
                _ => RowValue::LongBinary(bytes),
            }
        }
        "Guid" => {
            let bytes = value
                .as_array()
                .ok_or("expected GUID byte array")?
                .iter()
                .map(|byte| Ok(u8::try_from(byte.as_u64().ok_or("invalid GUID byte")?)?))
                .collect::<Result<Vec<_>>>()?;
            RowValue::Guid(bytes.try_into().map_err(|_| "GUID needs 16 bytes")?)
        }
        _ => return Err("invalid recipe value".into()),
    })
}

/// Creates one inventory fixture with exactly one public creation call.
pub fn write_fixture(id: &str, output: &Path) -> Result<()> {
    let inventory: Inventory = serde_json::from_str(WRITE_SCENARIOS)?;
    let recipe = inventory
        .scenarios
        .into_iter()
        .find(|r| r.id == id)
        .ok_or("unknown write scenario")?;
    let columns = recipe
        .tables
        .iter()
        .map(|table| {
            table
                .columns
                .iter()
                .map(|column| {
                    Ok(ColumnSpec::new(
                        column.name.as_bytes(),
                        column_type(column)?,
                    ))
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let fields = recipe
        .tables
        .iter()
        .map(|table| {
            table
                .indexes
                .iter()
                .map(|index| {
                    index
                        .fields
                        .iter()
                        .map(|field| IndexColumnSpec {
                            column: ColumnRef::Ordinal(field.column),
                            direction: if field.descending {
                                IndexDirection::Descending
                            } else {
                                IndexDirection::Ascending
                            },
                        })
                        .collect::<Vec<_>>()
                })
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    let indexes = recipe
        .tables
        .iter()
        .zip(&fields)
        .map(|(table, fields)| {
            table
                .indexes
                .iter()
                .zip(fields)
                .map(|(index, fields)| {
                    Ok(IndexSpec {
                        name: index.name.as_bytes(),
                        fields,
                        kind: match index.kind.as_str() {
                            "primary" => IndexKind::Primary,
                            "unique" => IndexKind::Unique,
                            "ordinary" => IndexKind::Ordinary,
                            _ => return Err("invalid index kind".into()),
                        },
                    })
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let values = recipe
        .tables
        .iter()
        .map(|table| {
            (0..table.repeat.unwrap_or(table.rows.len()))
                .map(|row| {
                    let source = table
                        .rows
                        .get(if table.repeat.is_some() { 0 } else { row })
                        .ok_or("missing row")?;
                    source
                        .iter()
                        .map(|value| expand(value, row))
                        .collect::<Result<Vec<_>>>()
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let rows = recipe
        .tables
        .iter()
        .zip(&values)
        .map(|(table, rows)| {
            rows.iter()
                .map(|row| {
                    if row.len() != table.columns.len() {
                        return Err("row width mismatch".into());
                    }
                    row.iter()
                        .zip(&table.columns)
                        .map(|(value, column)| row_value(value, column))
                        .collect::<Result<Vec<_>>>()
                })
                .collect::<Result<Vec<_>>>()
        })
        .collect::<Result<Vec<_>>>()?;
    let slices = rows
        .iter()
        .map(|rows| rows.iter().map(Vec::as_slice).collect::<Vec<_>>())
        .collect::<Vec<_>>();
    let requests = recipe
        .tables
        .iter()
        .enumerate()
        .map(|(n, table)| TableRows {
            table: TableSpec {
                name: table.name.as_bytes(),
                columns: &columns[n],
                indexes: &indexes[n],
            },
            rows: &slices[n],
        })
        .collect::<Vec<_>>();
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    if let Some(relation) = recipe.relationship {
        create_database_with_relationship_rows(
            output,
            &requests,
            &RelationshipSpec {
                name: relation.name.as_bytes(),
                parent: RelationshipColumn {
                    table: TableRef::Ordinal(usize::from(relation.parent_table)),
                    column: ColumnRef::Ordinal(relation.parent_column),
                },
                child: RelationshipColumn {
                    table: TableRef::Ordinal(usize::from(relation.child_table)),
                    column: ColumnRef::Ordinal(relation.child_column),
                },
            },
            &mut budget,
        )?;
    } else {
        create_database_with_table_rows(output, &requests, &mut budget)?;
    }
    Ok(())
}
