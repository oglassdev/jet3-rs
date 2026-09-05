//! JSON input translation for the public database creation APIs.
use std::{ffi::OsString, num::NonZeroU8, path::PathBuf};

use crate::values::{self, Cell, ascii};
use jet3::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexDirection, IndexKind, IndexSpec,
    RelationshipColumn, RelationshipSpec, RowValue, TableRef, TableRows, TableSpec,
    create_database, create_database_with_relationship, create_database_with_relationship_rows,
    create_database_with_table_rows,
};
use serde::Deserialize;

pub(crate) const HELP: &str = "\
  jet3-cli create <output.mdb> --input <request.json|->

create reads a JSON request from a file or stdin (-), then calls the public
creation API once. Existing output files are refused. See crates/jet3-cli/README.md
for typed rows, indexes, relationships and the library's current limits.
";

#[derive(Debug)]
pub(crate) struct CreateCommand {
    output: PathBuf,
    input: OsString,
}

pub(crate) fn parse_args(
    mut arguments: impl Iterator<Item = OsString>,
) -> Result<CreateCommand, &'static str> {
    let output = arguments.next().ok_or("missing_file")?;
    if output.to_string_lossy().starts_with('-') {
        return Err("missing_file");
    }
    if arguments.next().as_deref() != Some(std::ffi::OsStr::new("--input")) {
        return Err("create_input_required");
    }
    let input = arguments.next().ok_or("missing_option_value")?;
    if arguments.next().is_some() {
        return Err("unexpected_argument");
    }
    Ok(CreateCommand {
        output: output.into(),
        input,
    })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    tables: Vec<Table>,
    relationship: Option<Relation>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Table {
    name: String,
    columns: Vec<Column>,
    #[serde(default)]
    indexes: Vec<Index>,
    #[serde(default)]
    rows: Vec<Vec<Option<Cell>>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Column {
    name: String,
    #[serde(rename = "type")]
    kind: Kind,
    size: Option<NonZeroU8>,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum Kind {
    Boolean,
    Byte,
    Integer,
    Long,
    AutoIncrement,
    Currency,
    Single,
    Double,
    DateTime,
    Guid,
    Text,
    FixedText,
    Binary,
    Memo,
    LongBinary,
}

impl Column {
    fn spec(&self) -> Result<ColumnSpec<'_>, String> {
        let size = || {
            self.size
                .ok_or_else(|| format!("column {} requires size (1..255)", self.name))
        };
        if self.size.is_some() && !matches!(self.kind, Kind::Text | Kind::FixedText | Kind::Binary)
        {
            return Err(format!("column {} does not accept size", self.name));
        }
        let kind = match self.kind {
            Kind::Boolean => ColumnType::Boolean,
            Kind::Byte => ColumnType::Byte,
            Kind::Integer => ColumnType::Integer,
            Kind::Long => ColumnType::Long,
            Kind::AutoIncrement => ColumnType::AutoIncrement,
            Kind::Currency => ColumnType::Currency,
            Kind::Single => ColumnType::Single,
            Kind::Double => ColumnType::Double,
            Kind::DateTime => ColumnType::DateTime,
            Kind::Guid => ColumnType::Guid,
            Kind::Text => ColumnType::Text { max_len: size()? },
            Kind::FixedText => ColumnType::FixedText { len: size()? },
            Kind::Binary => ColumnType::Binary { max_len: size()? },
            Kind::Memo => ColumnType::Memo,
            Kind::LongBinary => ColumnType::LongBinary,
        };
        Ok(ColumnSpec::new(ascii(&self.name)?, kind))
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Index {
    name: String,
    kind: KeyKind,
    fields: Vec<IndexField>,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum KeyKind {
    Primary,
    Unique,
    Ordinary,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IndexField {
    column: String,
    #[serde(default)]
    direction: Direction,
}

#[derive(Default, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Direction {
    #[default]
    Ascending,
    Descending,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Relation {
    name: String,
    parent: Endpoint,
    child: Endpoint,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Endpoint {
    table: String,
    column: String,
}

impl Endpoint {
    fn spec(&self) -> Result<RelationshipColumn<'_>, String> {
        Ok(RelationshipColumn {
            table: TableRef::Name(ascii(&self.table)?),
            column: ColumnRef::Name(ascii(&self.column)?),
        })
    }
}

pub(crate) fn run(command: &CreateCommand) -> Result<String, String> {
    let request: Request = values::read_request(&command.input)
        .map_err(|e| format!("invalid creation request: {e}"))?;
    let columns = request
        .tables
        .iter()
        .map(|t| t.columns.iter().map(Column::spec).collect())
        .collect::<Result<Vec<Vec<_>>, String>>()?;
    let fields = request
        .tables
        .iter()
        .map(|t| {
            t.indexes
                .iter()
                .map(|index| {
                    index
                        .fields
                        .iter()
                        .map(|field| {
                            Ok(IndexColumnSpec {
                                column: ColumnRef::Name(ascii(&field.column)?),
                                direction: match field.direction {
                                    Direction::Ascending => IndexDirection::Ascending,
                                    Direction::Descending => IndexDirection::Descending,
                                },
                            })
                        })
                        .collect::<Result<Vec<_>, String>>()
                })
                .collect()
        })
        .collect::<Result<Vec<Vec<_>>, String>>()?;
    let indexes = request
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
                        name: ascii(&index.name)?,
                        fields,
                        kind: match index.kind {
                            KeyKind::Primary => IndexKind::Primary,
                            KeyKind::Unique => IndexKind::Unique,
                            KeyKind::Ordinary => IndexKind::Ordinary,
                        },
                    })
                })
                .collect()
        })
        .collect::<Result<Vec<Vec<_>>, String>>()?;
    let rows = request
        .tables
        .iter()
        .map(|table| {
            table
                .rows
                .iter()
                .map(|row| {
                    row.iter()
                        .map(|cell| cell.as_ref().map_or(Ok(RowValue::Null), Cell::value))
                        .collect()
                })
                .collect()
        })
        .collect::<Result<Vec<Vec<Vec<_>>>, String>>()?;
    let slices = rows
        .iter()
        .map(|rows| rows.iter().map(Vec::as_slice).collect::<Vec<_>>())
        .collect::<Vec<_>>();
    let tables = request
        .tables
        .iter()
        .enumerate()
        .map(|(n, table)| {
            Ok(TableRows {
                table: TableSpec {
                    name: ascii(&table.name)?,
                    columns: &columns[n],
                    indexes: &indexes[n],
                },
                rows: &slices[n],
            })
        })
        .collect::<Result<Vec<_>, String>>()?;
    let mut budget = values::budget();
    let empty = tables.iter().all(|table| table.rows.is_empty());
    let schema = tables.iter().map(|table| table.table).collect::<Vec<_>>();
    if let Some(relation) = &request.relationship {
        let relationship = RelationshipSpec {
            name: ascii(&relation.name)?,
            parent: relation.parent.spec()?,
            child: relation.child.spec()?,
        };
        if empty {
            create_database_with_relationship(&command.output, &schema, &relationship, &mut budget)
        } else {
            create_database_with_relationship_rows(
                &command.output,
                &tables,
                &relationship,
                &mut budget,
            )
        }
    } else if empty {
        create_database(&command.output, &schema, &mut budget)
    } else {
        create_database_with_table_rows(&command.output, &tables, &mut budget)
    }
    .map_err(|e| format!("create database: {e}"))?;
    Ok(
        serde_json::json!({"ok": true, "operation": "create", "output": command.output.to_string_lossy()})
            .to_string()
            + "\n",
    )
}
