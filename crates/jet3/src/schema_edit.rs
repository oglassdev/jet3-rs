//! Atomic edits of existing user schemas. Format layouts are sourced by the low-level planners.
use crate::schema_column_options::PropertyEdit;
use crate::{IndexSpec, ResourceBudget, UpdateError};
use std::path::Path;

/// A requested change to one stored text property.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum PropertyChange<'a> {
    /// Leave the stored value unchanged.
    #[default]
    Keep,
    /// Store opaque database-code-page bytes: 1 to 2,048 bytes without NUL or
    /// undefined CP1252 bytes. Expressions are not parsed or evaluated.
    Set(&'a [u8]),
    /// Remove the stored value, as DAO does when assigning an empty string.
    Clear,
}

/// One schema change to apply to an existing database.
#[derive(Debug, Clone, Copy)]
pub enum SchemaEdit<'a> {
    /// Atomically rebuild an index with new fields, name or options.
    ReplaceIndex {
        /// Exact database-encoded table name.
        table: &'a [u8],
        /// Exact database-encoded existing index name.
        index: &'a [u8],
        /// Replacement index to build over the existing rows.
        replacement: IndexSpec<'a>,
    },
    /// Add a relationship between existing tables. Enforced relationships check
    /// existing rows; unenforced ones (EXP-0301) only record their catalog rows.
    CreateRelationship {
        /// Table references must use names; column references may use names or live ordinals.
        relationship: crate::RelationshipSpec<'a>,
    },
    /// Atomically drop and recreate a relationship with new fields, enforcement,
    /// join type or cascade settings.
    ReplaceRelationship {
        /// Exact database-encoded existing relationship name.
        name: &'a [u8],
        /// Replacement relationship; table references must use names.
        relationship: crate::RelationshipSpec<'a>,
    },
    /// Drop a relationship, retaining shared ordinary indexes.
    DropRelationship {
        /// Exact database-encoded relationship name.
        name: &'a [u8],
    },
    /// Change constraints for future writes; existing values remain intact.
    SetColumnOptions {
        /// Exact database-encoded table name.
        table: &'a [u8],
        /// Exact database-encoded column name.
        column: &'a [u8],
        /// Set Required when present.
        required: Option<bool>,
        /// Set AllowZeroLength when present; Text and Memo only.
        allow_zero_length: Option<bool>,
    },
    /// Change a column's text properties (EXP-0299); existing rows are unchanged.
    ///
    /// A stored nonempty ValidationRule makes later inserts and updates on the
    /// table fail with [`UpdateError::ValidationRule`]. Binary, OLE and GUID
    /// columns refuse validation properties, as DAO does.
    SetColumnProperties {
        /// Exact database-encoded table name.
        table: &'a [u8],
        /// Exact database-encoded column name.
        column: &'a [u8],
        /// DefaultValue expression; never applied by Rust writes.
        default_value: PropertyChange<'a>,
        /// ValidationRule expression.
        validation_rule: PropertyChange<'a>,
        /// ValidationText message.
        validation_text: PropertyChange<'a>,
        /// Access Description.
        description: PropertyChange<'a>,
    },
    /// Change the table-level ValidationRule and ValidationText (EXP-0299).
    SetTableProperties {
        /// Exact database-encoded table name.
        table: &'a [u8],
        /// ValidationRule expression.
        validation_rule: PropertyChange<'a>,
        /// ValidationText message.
        validation_text: PropertyChange<'a>,
    },
    /// Remove an unindexed column that no relationship names, retaining the other
    /// columns' storage identities.
    DropColumn {
        /// Exact database-encoded table name.
        table: &'a [u8],
        /// Exact database-encoded column name.
        column: &'a [u8],
    },
    /// Drop a table and its rows, indexes, payloads, properties, grants and
    /// relationships. Enforced references from other tables must be dropped first.
    DropTable {
        /// Exact database-encoded table name.
        table: &'a [u8],
    },
    /// Append a column. Existing rows read its absent value as null (false for Boolean).
    CreateColumn {
        /// Exact database-encoded table name.
        table: &'a [u8],
        /// Name, type, Boolean and text properties of the new column.
        column: crate::ColumnSpec<'a>,
    },
    /// Rename a column, retaining its values, options and index participation.
    RenameColumn {
        /// Exact database-encoded table name.
        table: &'a [u8],
        /// Exact database-encoded existing column name.
        column: &'a [u8],
        /// New database-encoded column name.
        name: &'a [u8],
    },
    /// Create an empty table with its columns and indexes.
    CreateTable {
        /// Schema of the new table.
        table: crate::TableSpec<'a>,
    },
    /// Rename a table and the table-name references in its relationships.
    RenameTable {
        /// Exact database-encoded existing name.
        table: &'a [u8],
        /// New database-encoded name.
        name: &'a [u8],
    },
    /// Build an index over the table's existing rows.
    CreateIndex {
        /// Exact database-encoded table name.
        table: &'a [u8],
        /// Name, ordered fields and options of the new index.
        index: IndexSpec<'a>,
    },
    /// Remove a named index. Relationship indexes must be changed through their relationship.
    DropIndex {
        /// Exact database-encoded table name.
        table: &'a [u8],
        /// Exact database-encoded index name.
        index: &'a [u8],
    },
    /// Rename an ordinary or primary index, retaining its tree and options.
    RenameIndex {
        /// Exact database-encoded table name.
        table: &'a [u8],
        /// Exact database-encoded existing name.
        index: &'a [u8],
        /// New database-encoded name.
        name: &'a [u8],
    },
}

/// Applies one schema edit through private staging and atomic publication.
///
/// Unrelated objects are retained. Appending AutoIncrement backfills existing rows;
/// other column metadata edits retain row bytes. Invalid requests and
/// failures before publication leave the original file unchanged. Callers must
/// exclude concurrent writers for the entire operation.
/// A database whose sort order is not General (EXP-0299) refuses with
/// [`UpdateError::UnsupportedSortOrder`], preserving the file.
pub fn edit_schema(
    path: impl AsRef<Path>,
    request: SchemaEdit<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    crate::schema_publish::run(
        path.as_ref(),
        budget,
        |file, journal, budget| match request {
            SchemaEdit::ReplaceIndex {
                table,
                index,
                replacement,
            } => {
                crate::schema_index::edit(
                    file,
                    journal,
                    table,
                    SchemaEdit::DropIndex { table, index },
                    budget,
                )?;
                crate::schema_index::edit(
                    file,
                    journal,
                    table,
                    SchemaEdit::CreateIndex {
                        table,
                        index: replacement,
                    },
                    budget,
                )
            }
            SchemaEdit::CreateRelationship { relationship } => {
                crate::schema_relationship_create::create(file, journal, relationship, budget)
            }
            SchemaEdit::ReplaceRelationship { name, relationship } => {
                crate::schema_relationship_drop::drop_relationship(file, journal, name, budget)?;
                crate::schema_relationship_create::create(file, journal, relationship, budget)
            }
            SchemaEdit::DropRelationship { name } => {
                crate::schema_relationship_drop::drop_relationship(file, journal, name, budget)
            }
            SchemaEdit::SetColumnOptions {
                table,
                column,
                required,
                allow_zero_length,
            } => crate::schema_column_options::set(
                file,
                journal,
                table,
                PropertyEdit {
                    column: Some(column),
                    required,
                    allow_zero_length,
                    text: [PropertyChange::Keep; 4],
                },
                budget,
            ),
            SchemaEdit::SetColumnProperties {
                table,
                column,
                default_value,
                validation_rule,
                validation_text,
                description,
            } => crate::schema_column_options::set(
                file,
                journal,
                table,
                PropertyEdit {
                    column: Some(column),
                    required: None,
                    allow_zero_length: None,
                    text: [validation_rule, validation_text, default_value, description],
                },
                budget,
            ),
            SchemaEdit::SetTableProperties {
                table,
                validation_rule,
                validation_text,
            } => crate::schema_column_options::set(
                file,
                journal,
                table,
                PropertyEdit {
                    column: None,
                    required: None,
                    allow_zero_length: None,
                    text: [
                        validation_rule,
                        validation_text,
                        PropertyChange::Keep,
                        PropertyChange::Keep,
                    ],
                },
                budget,
            ),
            SchemaEdit::DropColumn { table, column } => {
                crate::schema_column_drop::drop_column(file, journal, table, column, budget)
            }
            SchemaEdit::DropTable { table } => {
                crate::schema_table_drop::drop_table(file, journal, table, budget)
            }
            SchemaEdit::CreateColumn { table, column } => {
                crate::schema_column_create::create(file, journal, table, column, budget)
            }
            SchemaEdit::RenameColumn {
                table,
                column,
                name,
            } => crate::schema_column::rename(file, journal, table, column, name, budget),
            SchemaEdit::CreateTable { table } => {
                crate::schema_table::create(file, journal, table, budget)
            }
            SchemaEdit::RenameTable { table, name } => {
                crate::schema_catalog::rename_table(file, journal, table, name, budget)
            }
            SchemaEdit::CreateIndex { table, .. }
            | SchemaEdit::DropIndex { table, .. }
            | SchemaEdit::RenameIndex { table, .. } => {
                crate::schema_index::edit(file, journal, table, request, budget)
            }
        },
    )
}

pub(crate) fn name(name: &[u8], maximum: usize) -> Result<(), UpdateError> {
    if name.len() > maximum || crate::catalog_name_key::validate_catalog_name(name).is_err() {
        return Err(UpdateError::Unsupported("schema name grammar or length"));
    }
    Ok(())
}

pub(crate) fn distinct<'a>(
    name: &[u8],
    others: impl Iterator<Item = &'a [u8]>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    for other in others {
        budget.charge_work_units(1024)?;
        if crate::catalog_name_key::catalog_names_equal(name, other) {
            return Err(UpdateError::Unsupported("duplicate schema name"));
        }
    }
    Ok(())
}
