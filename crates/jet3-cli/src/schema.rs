//! JSON requests over the public atomic schema-edit API.
use crate::names::Name;
use crate::schema_input::{Column, Index, Relation};
use crate::values::{self, Failure};
use jet3::{SchemaEdit, TableSpec, edit_schema};
use serde::Deserialize;
use std::{
    ffi::{OsStr, OsString},
    path::PathBuf,
};

pub(crate) const HELP: &str = "\
  jet3-cli schema <file.mdb> --input <request.json|->

schema applies one table, column, index or relationship edit JSON request.
Names use exact CP1252 bytes. Callers must exclude concurrent writers.
See crates/jet3-cli/README.md for request shapes and current library bounds.
";

#[derive(Debug)]
pub(crate) struct SchemaCommand {
    path: PathBuf,
    input: OsString,
}

pub(crate) fn parse_args(
    mut args: impl Iterator<Item = OsString>,
) -> Result<SchemaCommand, &'static str> {
    let path = args
        .next()
        .filter(|path| !path.to_string_lossy().starts_with('-'))
        .ok_or("missing_file")?;
    if args.next().as_deref() != Some(OsStr::new("--input")) {
        return Err("schema_input_required");
    }
    let input = args.next().ok_or("missing_option_value")?;
    if args.next().is_some() {
        return Err("unexpected_argument");
    }
    Ok(SchemaCommand {
        path: path.into(),
        input,
    })
}

#[derive(Deserialize)]
#[serde(tag = "operation", rename_all = "snake_case", deny_unknown_fields)]
enum Request {
    ReplaceIndex {
        table: Name,
        index: Name,
        replacement: Index,
    },
    CreateRelationship {
        relationship: Relation,
    },
    DropRelationship {
        name: Name,
    },
    ReplaceRelationship {
        name: Name,
        relationship: Relation,
    },
    SetColumnOptions {
        table: Name,
        column: Name,
        required: Option<bool>,
        allow_zero_length: Option<bool>,
    },
    DropTable {
        table: Name,
    },
    DropColumn {
        table: Name,
        column: Name,
    },
    CreateColumn {
        table: Name,
        column: Column,
    },
    RenameColumn {
        table: Name,
        column: Name,
        name: Name,
    },
    CreateTable {
        table: Table,
    },
    RenameTable {
        table: Name,
        name: Name,
    },
    CreateIndex {
        table: Name,
        index: Index,
    },
    DropIndex {
        table: Name,
        index: Name,
    },
    RenameIndex {
        table: Name,
        index: Name,
        name: Name,
    },
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Table {
    name: Name,
    columns: Vec<Column>,
    #[serde(default)]
    indexes: Vec<Index>,
}

pub(crate) fn run(command: &SchemaCommand) -> Result<String, Failure> {
    let request: Request = values::read_request(&command.input)?;
    let fields;
    let columns;
    let table_fields;
    let indexes;
    let relationship_fields;
    let (operation, edit) = match &request {
        Request::ReplaceIndex {
            table,
            index,
            replacement,
        } => {
            fields = replacement.fields();
            (
                "replace_index",
                SchemaEdit::ReplaceIndex {
                    table: table.bytes(),
                    index: index.bytes(),
                    replacement: replacement.spec(&fields),
                },
            )
        }
        Request::CreateRelationship { relationship } => {
            relationship_fields = relationship.fields()?;
            (
                "create_relationship",
                SchemaEdit::CreateRelationship {
                    relationship: relationship.spec(&relationship_fields),
                },
            )
        }
        Request::DropRelationship { name } => (
            "drop_relationship",
            SchemaEdit::DropRelationship { name: name.bytes() },
        ),
        Request::ReplaceRelationship { name, relationship } => {
            relationship_fields = relationship.fields()?;
            (
                "replace_relationship",
                SchemaEdit::ReplaceRelationship {
                    name: name.bytes(),
                    relationship: relationship.spec(&relationship_fields),
                },
            )
        }
        Request::SetColumnOptions {
            table,
            column,
            required,
            allow_zero_length,
        } => (
            "set_column_options",
            SchemaEdit::SetColumnOptions {
                table: table.bytes(),
                column: column.bytes(),
                required: *required,
                allow_zero_length: *allow_zero_length,
            },
        ),
        Request::DropTable { table } => (
            "drop_table",
            SchemaEdit::DropTable {
                table: table.bytes(),
            },
        ),
        Request::DropColumn { table, column } => (
            "drop_column",
            SchemaEdit::DropColumn {
                table: table.bytes(),
                column: column.bytes(),
            },
        ),
        Request::CreateColumn { table, column } => (
            "create_column",
            SchemaEdit::CreateColumn {
                table: table.bytes(),
                column: column.spec()?,
            },
        ),
        Request::RenameColumn {
            table,
            column,
            name,
        } => (
            "rename_column",
            SchemaEdit::RenameColumn {
                table: table.bytes(),
                column: column.bytes(),
                name: name.bytes(),
            },
        ),
        Request::CreateTable { table } => {
            columns = table
                .columns
                .iter()
                .map(Column::spec)
                .collect::<Result<Vec<_>, _>>()?;
            table_fields = table.indexes.iter().map(Index::fields).collect::<Vec<_>>();
            indexes = table
                .indexes
                .iter()
                .zip(&table_fields)
                .map(|(index, fields)| index.spec(fields))
                .collect::<Vec<_>>();
            (
                "create_table",
                SchemaEdit::CreateTable {
                    table: TableSpec {
                        validation: jet3::TableValidation::NONE,
                        name: table.name.bytes(),
                        columns: &columns,
                        indexes: &indexes,
                    },
                },
            )
        }
        Request::RenameTable { table, name } => (
            "rename_table",
            SchemaEdit::RenameTable {
                table: table.bytes(),
                name: name.bytes(),
            },
        ),
        Request::CreateIndex { table, index } => {
            fields = index.fields();
            (
                "create_index",
                SchemaEdit::CreateIndex {
                    table: table.bytes(),
                    index: index.spec(&fields),
                },
            )
        }
        Request::DropIndex { table, index } => (
            "drop_index",
            SchemaEdit::DropIndex {
                table: table.bytes(),
                index: index.bytes(),
            },
        ),
        Request::RenameIndex { table, index, name } => (
            "rename_index",
            SchemaEdit::RenameIndex {
                table: table.bytes(),
                index: index.bytes(),
                name: name.bytes(),
            },
        ),
    };
    edit_schema(&command.path, edit, &mut values::budget())?;
    Ok(serde_json::json!({"ok": true, "operation": operation, "file": command.path.to_string_lossy()}).to_string() + "\n")
}
