//! JSON input translation for the public database creation APIs.
use std::{ffi::OsString, path::PathBuf};

use crate::names::Name;
use crate::schema_input::{Column, Index, Relation};
use crate::values::{self, Cell};
use jet3::{
    RowValue, TableRows, TableSpec, create_database, create_database_with_relationship,
    create_database_with_relationship_rows, create_database_with_relationships,
    create_database_with_relationships_and_rows, create_database_with_table_rows,
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
    relationships: Option<Vec<Relation>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Table {
    name: Name,
    columns: Vec<Column>,
    #[serde(default)]
    indexes: Vec<Index>,
    #[serde(default)]
    rows: Vec<Vec<Option<Cell>>>,
}

pub(crate) fn run(command: &CreateCommand) -> Result<String, String> {
    let request: Request = values::read_request(&command.input)
        .map_err(|e| format!("invalid creation request: {e}"))?;
    if request.relationship.is_some() && request.relationships.is_some() {
        return Err("specify either relationship or relationships".into());
    }
    let columns = request
        .tables
        .iter()
        .map(|t| t.columns.iter().map(Column::spec).collect())
        .collect::<Result<Vec<Vec<_>>, String>>()?;
    let fields = request
        .tables
        .iter()
        .map(|t| t.indexes.iter().map(Index::fields).collect::<Vec<_>>())
        .collect::<Vec<_>>();
    let indexes = request
        .tables
        .iter()
        .zip(&fields)
        .map(|(table, fields)| {
            table
                .indexes
                .iter()
                .zip(fields)
                .map(|(index, fields)| index.spec(fields))
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
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
        .map(|(n, table)| TableRows {
            table: TableSpec {
                validation: jet3::TableValidation::NONE,
                name: table.name.bytes(),
                columns: &columns[n],
                indexes: &indexes[n],
            },
            rows: &slices[n],
        })
        .collect::<Vec<_>>();
    let mut budget = values::budget();
    let empty = tables.iter().all(|table| table.rows.is_empty());
    let schema = tables.iter().map(|table| table.table).collect::<Vec<_>>();
    if let Some(relations) = &request.relationships {
        let fields = relations
            .iter()
            .map(Relation::fields)
            .collect::<Result<Vec<_>, _>>()?;
        let relationships = relations
            .iter()
            .zip(&fields)
            .map(|(relation, fields)| relation.spec(fields))
            .collect::<Vec<_>>();
        if empty {
            create_database_with_relationships(
                &command.output,
                &schema,
                &relationships,
                &mut budget,
            )
        } else {
            create_database_with_relationships_and_rows(
                &command.output,
                &tables,
                &relationships,
                &mut budget,
            )
        }
    } else if let Some(relation) = &request.relationship {
        let fields = relation.fields()?;
        let relationship = relation.spec(&fields);
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
