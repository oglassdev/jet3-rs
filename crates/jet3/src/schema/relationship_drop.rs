//! EXP-0297 removes both reciprocal aliases, central records, object and grants.
use crate::{
    PageNumber, ResourceBudget, RowLocator, RowValue, UpdateError,
    write::page_edits::{PageEdits, reserve},
};
use std::fs::File;

pub(crate) fn drop_relationship(
    file: &mut File,
    journal: &mut PageEdits,
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let (endpoints, catalog, object, id, central, records) =
        crate::schema::publish::apply(file, journal, budget, |database, budget| {
            crate::relationship::catalog::validate(database, budget)?;
            let central = crate::schema::catalog::table(database, b"MSysRelationships", budget)?;
            let relationship = crate::schema::catalog::column(&central, b"szRelationship")?;
            let child_name = crate::schema::catalog::column(&central, b"szObject")?;
            let grbit = crate::schema::catalog::column(&central, b"grbit")?;
            let mut records = Vec::new();
            let mut child = Vec::new();
            let mut attributes = None;
            let mut rows = database.rows(&central, budget)?;
            while let Some(mut row) = rows.next_row()? {
                if row.field(relationship).and_then(|field| field.raw_bytes()) == Some(name) {
                    if attributes.is_none() {
                        let RowValue::Long(raw) =
                            crate::row::scalar_values::read_column(&mut row, grbit)?
                        else {
                            return Err(UpdateError::Mismatch("relationship attributes"));
                        };
                        attributes = Some(raw);
                    }
                    if child.is_empty() {
                        let mut saved = [0; 255];
                        let bytes = row
                            .field(child_name)
                            .and_then(|field| field.raw_bytes())
                            .ok_or(UpdateError::Mismatch("relationship child name"))?;
                        let len = bytes.len();
                        saved
                            .get_mut(..len)
                            .ok_or(UpdateError::Mismatch("relationship name capacity"))?
                            .copy_from_slice(bytes);
                        reserve(&mut child, len, row.budget_mut())?;
                        child.extend_from_slice(&saved[..len]);
                    }
                    reserve(&mut records, 1, row.budget_mut())?;
                    records.push(row.locator());
                }
            }
            drop(rows);
            if records.is_empty() {
                return Err(UpdateError::NotFound("relationship"));
            }
            let child_name = child;
            let child =
                crate::write::update::indexed_writable_table(database, &child_name, budget)?;
            let catalog = crate::schema::catalog::table(database, b"MSysObjects", budget)?;
            let (object, id) = object(database, &catalog, name, budget)?;
            let flags = attributes
                .and_then(crate::relationship::flags::RelationshipFlags::decode)
                .ok_or(UpdateError::Unsupported("relationship catalog flags"))?;
            // EXP-0301: unenforced relationships have no index records to remove.
            if !flags.enforced {
                return Ok((
                    PageEdits::new(database.geometry().page_count()),
                    (None, catalog.root(), object, id, central.root(), records),
                ));
            }
            crate::relationship::catalog::load(database, &child, &child_name, budget)?;
            let foreign = child
                .relationships()
                .find(|relation| {
                    relation.name().raw_bytes() == name
                        && relation.side() == crate::RelationshipSide::ForeignTable
                })
                .ok_or(UpdateError::Mismatch("foreign relationship index"))?;
            let parent = database.table_definition(foreign.related_table(), budget)?;
            let primary = parent
                .relationships()
                .find(|relation| {
                    relation.side() == crate::RelationshipSide::PrimaryTable
                        && relation.related_table() == child.root()
                        && relation.raw_selector() == foreign.raw_relation_ordinal()
                        && relation.raw_relation_ordinal() == foreign.raw_selector()
                })
                .ok_or(UpdateError::Mismatch("primary relationship index"))?;
            let endpoints = [
                (child.root(), foreign.raw_selector()),
                (parent.root(), primary.raw_selector()),
            ];
            Ok((
                PageEdits::new(database.geometry().page_count()),
                (
                    Some(endpoints),
                    catalog.root(),
                    object,
                    id,
                    central.root(),
                    records,
                ),
            ))
        })?;
    for (root, selector) in endpoints.into_iter().flatten() {
        let retired = crate::schema::publish::apply(file, journal, budget, |database, budget| {
            let table = database.table_definition(root, budget)?;
            crate::index::mutation::load(database, &table, budget)?;
            let position = table.indexes().iter().position(|index| matches!(index.kind(), crate::IndexDefinitionKind::Relationship(relation) if relation.raw_selector() == selector)).ok_or(UpdateError::Mismatch("relationship index identity"))?;
            let physical = table.indexes()[position].physical_index();
            let remaining = table
                .indexes()
                .iter()
                .enumerate()
                .any(|(at, index)| at != position && index.physical_index() == physical);
            let map = table.physical_indexes()[usize::from(physical)].usage_map();
            let retired = (!remaining).then_some(crate::MapRowLocator::new(map.page(), map.row()));
            let mut definition = crate::schema::definition::DefinitionEdit::new(&table, budget)?;
            let mut edits = PageEdits::new(database.geometry().page_count());
            crate::schema::index::remove_position(
                database,
                &table,
                position,
                &mut definition,
                &mut edits,
                budget,
            )?;
            definition.stage(database, &table, &mut edits, budget)?;
            Ok((edits, retired))
        })?;
        if let Some(map) = retired {
            crate::schema::map::retire(file, journal, map, budget)?;
        }
    }
    for row in records {
        delete_row(file, journal, central, row, budget)?;
    }
    delete_row(file, journal, catalog, object, budget)?;
    delete_grants(file, journal, id, budget)?;
    crate::schema::publish::apply(file, journal, budget, |database, budget| {
        crate::relationship::catalog::validate(database, budget)?;
        Ok((PageEdits::new(database.geometry().page_count()), ()))
    })
}

fn object(
    database: &mut crate::DatabaseReader<crate::FileSource>,
    table: &crate::TableDefinition,
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(RowLocator, i32), UpdateError> {
    let names = crate::schema::catalog::column(table, b"Name")?;
    let kind = crate::schema::catalog::column(table, b"Type")?;
    let id = crate::schema::catalog::column(table, b"Id")?;
    let mut found = None;
    let mut rows = database.rows(table, budget)?;
    while let Some(mut row) = rows.next_row()? {
        if row.field(names).and_then(|field| field.raw_bytes()) == Some(name)
            && matches!(
                crate::row::scalar_values::read_column(&mut row, kind)?,
                RowValue::Integer(8)
            )
        {
            let RowValue::Long(id) = crate::row::scalar_values::read_column(&mut row, id)? else {
                return Err(UpdateError::Mismatch("relationship object identity"));
            };
            if found.replace((row.locator(), id)).is_some() {
                return Err(UpdateError::Mismatch("duplicate relationship object"));
            }
        }
    }
    found.ok_or(UpdateError::NotFound("relationship catalog object"))
}

pub(crate) fn delete_row(
    file: &mut File,
    journal: &mut PageEdits,
    root: PageNumber,
    row: RowLocator,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    crate::schema::publish::apply(file, journal, budget, |database, budget| {
        let table = database.table_definition(root, budget)?;
        let edits = crate::write::delete::plan(
            database,
            &table,
            crate::RowDelete { table: b"", row },
            false,
            budget,
        )?;
        Ok((edits, ()))
    })
}

pub(crate) fn delete_grants(
    file: &mut File,
    journal: &mut PageEdits,
    id: i32,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let (root, locators) = crate::schema::publish::apply(
        file,
        journal,
        budget,
        |database, budget| {
            let table = crate::schema::catalog::table(database, b"MSysACEs", budget)?;
            let object = crate::schema::catalog::column(&table, b"ObjectId")?;
            let mut locators = Vec::new();
            let mut rows = database.rows(&table, budget)?;
            while let Some(mut row) = rows.next_row()? {
                if matches!(crate::row::scalar_values::read_column(&mut row, object)?, RowValue::Long(value) if value == id)
                {
                    reserve(&mut locators, 1, row.budget_mut())?;
                    locators.push(row.locator());
                }
            }
            drop(rows);
            Ok((
                PageEdits::new(database.geometry().page_count()),
                (table.root(), locators),
            ))
        },
    )?;
    for row in locators {
        delete_row(file, journal, root, row, budget)?;
    }
    Ok(())
}
