//! Table and grant removal using EXP-0073/0087/0297 catalog identities.
use crate::page_edits::{PageEdits, reserve};
use crate::{ResourceBudget, RowValue, UpdateError};
use std::fs::File;

pub(crate) fn drop_table(
    file: &mut File,
    journal: &mut PageEdits,
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let relationships = crate::schema_publish::apply(file, journal, budget, |database, budget| {
        crate::update::indexed_writable_table(database, name, budget)?;
        crate::relationship_catalog::validate(database, budget)?;
        // EXP-0297/0301: DAO removes the table's relationships with it, except
        // that an enforced relationship referencing it from another table refuses.
        let mut names = Vec::new();
        for relation in crate::relationship_catalog::catalog(database, budget)? {
            budget.charge_work_units(1024)?;
            let parent =
                crate::catalog_name_key::catalog_names_equal(relation.parent_table(), name);
            let child = crate::catalog_name_key::catalog_names_equal(relation.child_table(), name);
            if !parent && !child {
                continue;
            }
            if !relation.interpreted() {
                return Err(UpdateError::Unsupported("relationship catalog flags"));
            }
            if relation.enforced() && parent && !child {
                return Err(UpdateError::Unsupported(
                    "table is referenced by a relationship",
                ));
            }
            reserve(&mut names, 1, budget)?;
            names.push(relation.name);
        }
        Ok((PageEdits::new(database.geometry().page_count()), names))
    })?;
    for name in relationships {
        crate::schema_relationship_drop::drop_relationship(file, journal, &name, budget)?;
    }
    let (catalog, row, grants, grant_rows, maps, definitions) = crate::schema_publish::apply(
        file,
        journal,
        budget,
        |database, budget| {
            let table = crate::update::indexed_writable_table(database, name, budget)?;
            crate::relationship_catalog::validate(database, budget)?;
            if table.relationships().next().is_some() {
                return Err(UpdateError::Unsupported(
                    "drop relationships before their table",
                ));
            }
            let catalog = crate::schema_catalog::table(database, b"MSysObjects", budget)?;
            let row = crate::schema_catalog::object(database, &catalog, table.root(), budget)?;
            let grants = crate::schema_catalog::table(database, b"MSysACEs", budget)?;
            let object = crate::schema_catalog::column(&grants, b"ObjectId")?;
            let mut grant_rows = Vec::new();
            let mut rows = database.rows(&grants, budget)?;
            while let Some(mut row) = rows.next_row()? {
                if matches!(crate::numeric_row_values::read_column(&mut row, object)?, RowValue::Long(value) if value as u32 as u64 == table.root().get())
                {
                    reserve(&mut grant_rows, 1, row.budget_mut())?;
                    grant_rows.push(row.locator());
                }
            }
            drop(rows);
            let mut definitions = Vec::new();
            reserve(&mut definitions, table.pages().len(), budget)?;
            definitions.extend_from_slice(table.pages());
            let maps = crate::schema_storage::locators(&table, budget)?;
            let edits = crate::schema_storage::release_table(database, &table, budget)?;
            Ok((
                edits,
                (
                    catalog.root(),
                    row,
                    grants.root(),
                    grant_rows,
                    maps,
                    definitions,
                ),
            ))
        },
    )?;
    for (root, row) in
        std::iter::once((catalog, row)).chain(grant_rows.into_iter().map(|row| (grants, row)))
    {
        crate::schema_publish::apply(file, journal, budget, |database, budget| {
            let table = database.table_definition(root, budget)?;
            let edits = crate::delete::plan(
                database,
                &table,
                crate::RowDelete { table: b"", row },
                false,
                budget,
            )?;
            Ok((edits, ()))
        })?;
    }
    for locator in maps {
        crate::schema_map::retire(file, journal, locator, budget)?;
    }
    crate::schema_publish::apply(file, journal, budget, |database, budget| {
        let mut edits = PageEdits::new(database.geometry().page_count());
        for page in definitions {
            let mut bytes = [0; crate::PAGE_BYTES];
            database.read_raw_page(page, &mut bytes, budget)?;
            bytes[0] = 8;
            edits.set_image(database, page, crate::PageImage::from_bytes(bytes), budget)?;
        }
        Ok((edits, ()))
    })
}
