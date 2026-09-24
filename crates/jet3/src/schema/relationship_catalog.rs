//! EXP-0297 relationship catalog rows, objects and grants; EXP-0301 unenforced
//! relationships consist of these rows alone.
use crate::{
    ColumnRef, DatabaseReader, FileSource, RelationshipSpec, ResourceBudget, TableDefinition,
    UpdateError,
    catalog::system_rows::{AceRow, ObjectRow, RelationshipRow},
    schema::table::insert,
    write::page_edits::{PageEdits, reserve},
};
use std::fs::File;

pub(crate) fn create_unenforced(
    file: &mut File,
    journal: &mut PageEdits,
    spec: &RelationshipSpec<'_>,
    tables: (&[u8], &[u8]),
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    // DAO refuses cascades on unenforced relationships (EXP-0301, error 3001).
    if spec.cascade_updates || spec.cascade_deletes {
        return Err(UpdateError::Unsupported(
            "unenforced relationships cannot cascade",
        ));
    }
    let (pairs, identity) =
        crate::schema::edit::apply(file, journal, budget, |database, budget| {
            crate::relationship::catalog::validate(database, budget)?;
            let parent = crate::write::update::indexed_writable_table(database, tables.0, budget)?;
            let child = crate::write::update::indexed_writable_table(database, tables.1, budget)?;
            // EXP-0301: unenforced relationships have no index and its ten-field
            // limit; bound them by the 255-column table limit.
            if !(1..=255).contains(&spec.fields.len()) {
                return Err(UpdateError::Unsupported("relationship field count"));
            }
            let mut pairs: Vec<(Vec<u8>, Vec<u8>)> = Vec::new();
            reserve(&mut pairs, spec.fields.len(), budget)?;
            for pair in spec.fields {
                let names = (
                    owned(column(&parent, pair.parent)?, budget)?,
                    owned(column(&child, pair.child)?, budget)?,
                );
                pairs.push(names);
            }
            let identity = object_identity(database, spec.name, budget)?;
            Ok((
                PageEdits::new(database.geometry().page_count()),
                (pairs, identity),
            ))
        })?;
    let mut borrowed = Vec::new();
    reserve(&mut borrowed, pairs.len(), budget)?;
    borrowed.extend(pairs.iter().map(|(a, b)| (a.as_slice(), b.as_slice())));
    publish(file, journal, spec, tables, &borrowed, identity, budget)
}

fn column<'a>(
    table: &'a TableDefinition,
    reference: ColumnRef<'_>,
) -> Result<&'a [u8], UpdateError> {
    match reference {
        ColumnRef::Name(name) => table
            .columns()
            .iter()
            .find(|column| column.name().raw_bytes() == name),
        ColumnRef::Ordinal(ordinal) => table.columns().get(usize::from(ordinal)),
    }
    .map(|column| column.name().raw_bytes())
    .ok_or(UpdateError::NotFound("relationship column"))
}

fn owned(name: &[u8], budget: &mut ResourceBudget) -> Result<Vec<u8>, UpdateError> {
    let mut result = Vec::new();
    reserve(&mut result, name.len(), budget)?;
    result.extend_from_slice(name);
    Ok(result)
}

/// Inserts the central rows, relationship object and its two grants, then validates.
pub(crate) fn publish(
    file: &mut File,
    journal: &mut PageEdits,
    spec: &RelationshipSpec<'_>,
    (parent_name, child_name): (&[u8], &[u8]),
    pairs: &[(&[u8], &[u8])],
    (object_id, folder): (i32, i32),
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    for (ordinal, (parent_column, child_column)) in pairs.iter().enumerate() {
        let row = RelationshipRow {
            name: spec.name,
            flags: spec.flags().raw(),
            field_count: pairs.len() as i32,
            field_ordinal: ordinal as i32,
            child_table: child_name,
            child_column,
            parent_table: parent_name,
            parent_column,
        };
        insert(file, journal, b"MSysRelationships", &row.columns(), budget)?;
    }
    let object = ObjectRow::relationship(object_id, folder, spec.name);
    insert(file, journal, b"MSysObjects", &object.columns(), budget)?;
    for grant in AceRow::relationship_grants(object_id) {
        insert(file, journal, b"MSysACEs", &grant.columns(), budget)?;
    }
    crate::schema::edit::apply(file, journal, budget, |database, budget| {
        crate::relationship::catalog::validate(database, budget)?;
        Ok((PageEdits::new(database.geometry().page_count()), ()))
    })
}

pub(crate) fn object_identity(
    database: &mut DatabaseReader<FileSource>,
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(i32, i32), UpdateError> {
    let order = database.header().sort_order();
    crate::schema::edit::name(order, name, 63)?;
    let mut catalog = database.catalog(budget)?;
    let mut folder = None;
    let mut next = 0x8000_0000_u32;
    while let Some(record) = catalog.next_record()? {
        if record.id().get() >= 0x8000_0000 {
            next = next.max(
                record
                    .id()
                    .get()
                    .checked_add(1)
                    .ok_or(UpdateError::Unsupported(
                        "relationship object identity capacity",
                    ))?,
            );
        }
        if record.kind().raw() == 3 && record.name().raw_bytes() == b"Relationships" {
            folder = Some(record.id().get() as i32);
        }
        if record.kind().raw() == 8 {
            crate::schema::edit::distinct(
                order,
                name,
                std::iter::once(record.name().raw_bytes()),
                catalog.budget_mut(),
            )?;
        }
    }
    Ok((
        next as i32,
        folder.ok_or(UpdateError::NotFound("Relationships container"))?,
    ))
}
