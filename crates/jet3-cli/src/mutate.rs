//! JSON requests over public row mutation APIs; no storage or publication logic.
use crate::names::Name;
use crate::values::{self, Cell, Failure};
use jet3::{
    CatalogObjectClass, ColumnOrdinal, DatabaseReader, ResourceBudget, RowLocator, RowValue,
};
use serde::Deserialize;
use serde_json::json;
use std::{
    ffi::{OsStr, OsString},
    path::PathBuf,
};

pub(crate) const HELP: &str = "\
  jet3-cli mutate <file.mdb> --input <request.json|->

mutate applies one insert, update, replace or delete JSON request through the public API.
Targets use exact database-code-page table names; update/replace/delete require a current page/slot.
Callers must exclude concurrent writers. See README.md for current library bounds.
";
#[derive(Debug)]
pub(crate) struct MutationCommand {
    path: PathBuf,
    input: OsString,
}
pub(crate) fn parse_args(
    mut args: impl Iterator<Item = OsString>,
) -> Result<MutationCommand, &'static str> {
    let path = args
        .next()
        .filter(|p| !p.to_string_lossy().starts_with('-'))
        .ok_or("missing_file")?;
    if args.next().as_deref() != Some(OsStr::new("--input")) {
        return Err("mutation_input_required");
    }
    let input = args.next().ok_or("missing_option_value")?;
    if args.next().is_some() {
        return Err("unexpected_argument");
    }
    Ok(MutationCommand {
        path: path.into(),
        input,
    })
}
#[derive(Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    Insert {
        table: Name,
        values: Vec<Option<Cell>>,
    },
    Update {
        table: Name,
        row: Locator,
        column: u16,
        #[serde(deserialize_with = "required_value")]
        value: Option<Cell>,
    },
    Delete {
        table: Name,
        row: Locator,
    },
    Replace {
        table: Name,
        row: Locator,
        values: Vec<Option<Cell>>,
    },
}
fn required_value<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<Option<Cell>, D::Error> {
    Option::deserialize(deserializer)
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Locator {
    page: u64,
    slot: u8,
}

fn resolve(
    path: &std::path::Path,
    table: &[u8],
    target: &Locator,
    budget: &mut ResourceBudget,
    column: Option<u16>,
) -> Result<(RowLocator, Option<ColumnOrdinal>), String> {
    let mut database = DatabaseReader::open(path, budget).map_err(|e| e.to_string())?;
    let root = {
        let mut catalog = database.catalog(budget).map_err(|e| e.to_string())?;
        let mut root = None;
        while let Some(record) = catalog.next_record().map_err(|e| e.to_string())? {
            if record.class() == CatalogObjectClass::User && record.name().raw_bytes() == table {
                if root.is_some() {
                    return Err("ambiguous table name".into());
                }
                root = record.table_definition();
            }
        }
        root.ok_or("table not found")?
    };
    let definition = database
        .table_definition(root, budget)
        .map_err(|e| e.to_string())?;
    let column = column
        .map(|ordinal| {
            definition
                .columns()
                .iter()
                .find(|c| c.ordinal().get() == ordinal)
                .map(|c| c.ordinal())
                .ok_or_else(|| "column ordinal not found".to_owned())
        })
        .transpose()?;
    let mut rows = database
        .rows(&definition, budget)
        .map_err(|e| e.to_string())?;
    while let Some(row) = rows.next_row().map_err(|e| e.to_string())? {
        let locator = row.locator();
        if locator.page().get() == target.page && locator.slot() == target.slot {
            return Ok((locator, column));
        }
    }
    Err("row locator not found in requested table".into())
}

pub(crate) fn run(command: &MutationCommand) -> Result<String, Failure> {
    let request: Request = crate::names::read_request(&command.input, &command.path)?;
    let mut budget = values::budget();
    let (operation, locator) = match &request {
        Request::Insert { table, values } => {
            let values = values
                .iter()
                .map(|cell| cell.as_ref().map_or(Ok(RowValue::Null), Cell::value))
                .collect::<Result<Vec<_>, _>>()?;
            (
                "insert",
                jet3::insert_row(&command.path, table.bytes(), &values, &mut budget)?,
            )
        }
        Request::Update {
            table,
            row,
            column,
            value,
        } => {
            let table = table.bytes();
            let (locator, column) = resolve(&command.path, table, row, &mut budget, Some(*column))?;
            let value = value.as_ref().map_or(Ok(RowValue::Null), Cell::value)?;
            jet3::update_field(
                &command.path,
                jet3::FieldUpdate {
                    table,
                    row: locator,
                    column: column.ok_or_else(|| "column ordinal not found".to_owned())?,
                    value,
                },
                &mut budget,
            )?;
            ("update", locator)
        }
        Request::Replace { table, row, values } => {
            let table = table.bytes();
            let (locator, _) = resolve(&command.path, table, row, &mut budget, None)?;
            let values = values
                .iter()
                .map(|cell| cell.as_ref().map_or(Ok(RowValue::Null), Cell::value))
                .collect::<Result<Vec<_>, _>>()?;
            jet3::update_row(
                &command.path,
                jet3::RowUpdate {
                    table,
                    row: locator,
                    values: &values,
                },
                &mut budget,
            )?;
            ("replace", locator)
        }
        Request::Delete { table, row } => {
            let table = table.bytes();
            let (locator, _) = resolve(&command.path, table, row, &mut budget, None)?;
            jet3::delete_row(
                &command.path,
                jet3::RowDelete {
                    table,
                    row: locator,
                },
                &mut budget,
            )?;
            ("delete", locator)
        }
    };
    Ok(json!({"ok":true,"operation":operation,"file":command.path.to_string_lossy(),"row":{"page":locator.page().get(),"slot":locator.slot()}}).to_string()+"\n")
}
