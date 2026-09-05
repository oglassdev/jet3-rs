//! JSON requests over public row mutation APIs; no storage or publication logic.
use crate::values::{self, Cell};
use jet3::{
    CatalogObjectClass, ColumnOrdinal, DatabaseReader, ResourceBudget, RowLocator, RowValue,
    UpdateError,
};
use serde::Deserialize;
use serde_json::json;
use std::{
    ffi::{OsStr, OsString},
    path::PathBuf,
};

pub(crate) const HELP: &str = "\
  jet3-cli mutate <file.mdb> --input <request.json|->

mutate applies one insert, update or delete JSON request through the public API.
Targets use exact ASCII table names; update/delete require a current page/slot.
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
        table: String,
        values: Vec<Option<Cell>>,
    },
    Update {
        table: String,
        row: Locator,
        column: u16,
        #[serde(deserialize_with = "required_value")]
        value: Option<Cell>,
    },
    Delete {
        table: String,
        row: Locator,
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

pub(crate) struct Failure {
    pub message: String,
    pub publication_stage: Option<String>,
}
impl From<String> for Failure {
    fn from(message: String) -> Self {
        Self {
            message,
            publication_stage: None,
        }
    }
}
impl From<UpdateError> for Failure {
    fn from(error: UpdateError) -> Self {
        let publication_stage = match &error {
            UpdateError::Publish(error) => Some(format!("{:?}", error.stage())),
            _ => None,
        };
        Self {
            message: error.to_string(),
            publication_stage,
        }
    }
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
    let request: Request = values::read_request(&command.input)?;
    let mut budget = values::budget();
    let (operation, locator) = match &request {
        Request::Insert { table, values } => {
            let values = values
                .iter()
                .map(|cell| cell.as_ref().map_or(Ok(RowValue::Null), Cell::value))
                .collect::<Result<Vec<_>, _>>()?;
            (
                "insert",
                jet3::insert_row(&command.path, values::ascii(table)?, &values, &mut budget)?,
            )
        }
        Request::Update {
            table,
            row,
            column,
            value,
        } => {
            let table = values::ascii(table)?;
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
        Request::Delete { table, row } => {
            let table = values::ascii(table)?;
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
