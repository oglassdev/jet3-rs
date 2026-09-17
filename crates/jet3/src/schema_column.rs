//! Column renames preserve row storage and update EXP-0297 relationship names.
use crate::page_edits::{PageEdits, reserve};
use crate::{ResourceBudget, RowValue, UpdateError};
use std::fs::File;

pub(crate) fn rename(
    file: &mut File,
    journal: &mut PageEdits,
    table: &[u8],
    old: &[u8],
    new: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    crate::schema_edit::name(new, 64)?;
    let (root, properties, relationships, updates) =
        crate::schema_publish::apply(file, journal, budget, |database, budget| {
            let definition = crate::update::indexed_writable_table(database, table, budget)?;
            let selected = definition
                .columns()
                .iter()
                .position(|column| column.name().raw_bytes() == old)
                .ok_or(UpdateError::NotFound("column"))?;
            crate::schema_edit::distinct(
                new,
                definition
                    .columns()
                    .iter()
                    .enumerate()
                    .filter(|(ordinal, _)| *ordinal != selected)
                    .map(|(_, column)| column.name().raw_bytes()),
                budget,
            )?;
            crate::relationship_catalog::validate(database, budget)?;
            let (catalog, row, properties) =
                crate::schema_properties::load(database, &definition, budget)?;
            let renamed = crate::schema_properties::rename(&properties, old, new, budget)?;
            let properties = if renamed == properties {
                None
            } else {
                Some((catalog.root(), row, renamed))
            };
            let relationships =
                crate::schema_catalog::table(database, b"MSysRelationships", budget)?;
            let fields = [
                (
                    crate::schema_catalog::column(&relationships, b"szObject")?,
                    crate::schema_catalog::column(&relationships, b"szColumn")?,
                ),
                (
                    crate::schema_catalog::column(&relationships, b"szReferencedObject")?,
                    crate::schema_catalog::column(&relationships, b"szReferencedColumn")?,
                ),
            ];
            let mut updates = Vec::new();
            let mut rows = database.rows(&relationships, budget)?;
            while let Some(mut row) = rows.next_row()? {
                for (object, field) in fields {
                    row.budget_mut().charge_work_units(2048)?;
                    if row
                        .field(object)
                        .and_then(|field| field.raw_bytes())
                        .is_some_and(|name| {
                            crate::catalog_name_key::catalog_names_equal(name, table)
                        })
                        && row
                            .field(field)
                            .and_then(|field| field.raw_bytes())
                            .is_some_and(|name| {
                                crate::catalog_name_key::catalog_names_equal(name, old)
                            })
                    {
                        let locator = row.locator();
                        reserve(&mut updates, 1, row.budget_mut())?;
                        updates.push((locator, field));
                    }
                }
            }
            drop(rows);
            let mut edited = crate::schema_definition::DefinitionEdit::new(&definition, budget)?;
            edited.columns[selected].name = new;
            let mut edits = PageEdits::new(database.geometry().page_count());
            edited.stage(database, &definition, &mut edits, budget)?;
            Ok((
                edits,
                (definition.root(), properties, relationships.root(), updates),
            ))
        })?;
    if let Some((catalog, row, bytes)) = properties {
        crate::schema_publish::apply(file, journal, budget, |database, budget| {
            let catalog = database.table_definition(catalog, budget)?;
            let column = crate::schema_catalog::column(&catalog, b"LvProp")?;
            let graph =
                crate::row_mutation_graph::RowGraph::load(database, &catalog, Some(row), budget)?;
            let edits = crate::field_update::plan_fields(
                database,
                &catalog,
                graph,
                row,
                &[(column, RowValue::LongBinary(&bytes))],
                budget,
            )?;
            Ok((edits, ()))
        })?;
    }
    for (row, column) in updates {
        crate::schema_publish::apply(file, journal, budget, |database, budget| {
            let definition = database.table_definition(relationships, budget)?;
            let graph = crate::row_mutation_graph::RowGraph::load(
                database,
                &definition,
                Some(row),
                budget,
            )?;
            let edits = crate::field_update::plan_fields(
                database,
                &definition,
                graph,
                row,
                &[(column, RowValue::Text(new))],
                budget,
            )?;
            Ok((edits, ()))
        })?;
    }
    crate::schema_publish::apply(file, journal, budget, |database, budget| {
        let definition = database.table_definition(root, budget)?;
        crate::column_value_policy::options(database, &definition, budget)?;
        crate::relationship_catalog::validate(database, budget)?;
        Ok((PageEdits::new(database.geometry().page_count()), ()))
    })
}
