//! EXP-0297 drops live column metadata while retaining old row storage IDs and bytes.
use crate::{
    ResourceBudget, WriteError, definition::header::COLUMN_COUNT, write::page_edits::PageEdits,
};
use std::fs::File;

pub(crate) fn drop_column(
    file: &mut File,
    journal: &mut PageEdits,
    table: &[u8],
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<(), WriteError> {
    let table_name = table;
    let (catalog, row, properties, retired) =
        crate::schema::edit::apply(file, journal, budget, |database, budget| {
            let order = database.header().sort_order();
            let table = crate::write::update::indexed_writable_table(database, table, budget)?;
            let column = table
                .columns()
                .iter()
                .find(|column| column.name().raw_bytes() == name)
                .ok_or(WriteError::NotFound("column"))?;
            if table.columns().len() == 1 {
                return Err(WriteError::Unsupported("table must retain a column"));
            }
            if table.physical_indexes().iter().any(|index| {
                index
                    .fields()
                    .iter()
                    .any(|field| field.column() == column.ordinal())
            }) {
                return Err(WriteError::Unsupported("drop indexes before their column"));
            }
            crate::relationship::catalog::validate(database, budget)?;
            // EXP-0301: DAO refuses to drop any relationship key column (3303/3280).
            for relation in crate::relationship::catalog::catalog(database, budget)? {
                budget.charge_work_units(2048)?;
                let equal = |left: &[u8], right: &[u8]| {
                    crate::catalog::name_key::catalog_names_equal(left, right, order)
                };
                if relation.fields().iter().any(|field| {
                    (equal(relation.parent_table(), table_name) && equal(field.parent(), name))
                        || (equal(relation.child_table(), table_name) && equal(field.child(), name))
                }) {
                    return Err(WriteError::Unsupported(
                        "drop relationships before their column",
                    ));
                }
            }
            crate::row::mutation_graph::RowGraph::load(database, &table, None, budget)?;
            crate::long_value::mutation::LongValues::load(database, &table, None, budget)?;
            let (catalog, row, properties) =
                crate::schema::properties::load(database, &table, budget)?;
            let properties = crate::schema::properties::remove(&properties, name, budget)?;
            let mut edited = crate::schema::definition::DefinitionEdit::new(&table, budget)?;
            edited.columns.remove(usize::from(column.ordinal().get()));
            edited.header[COLUMN_COUNT..COLUMN_COUNT + 2]
                .copy_from_slice(&(edited.columns.len() as u16).to_le_bytes());
            let mut edits = PageEdits::new(database.geometry().page_count());
            let retired = table
                .long_value_maps()
                .iter()
                .find(|map| map.column() == column.ordinal())
                .map(|map| [map.owned(), map.available()]);
            if let Some(locators) = retired {
                for (position, locator) in locators.into_iter().enumerate() {
                    let map = crate::alloc::mutation_map::MapBits::load(database, locator, budget)?;
                    for page in
                        map.existing_pages(database.geometry().page_count(), false, budget)?
                    {
                        edits.map_bit(database, locator, page, true, false, budget)?;
                        if position == 0 {
                            edits.map_bit(
                                database,
                                crate::alloc::mutation_map::global_locator(),
                                page,
                                false,
                                true,
                                budget,
                            )?;
                        }
                    }
                }
                let position = edited
                    .suffix
                    .chunks_exact(crate::LONG_VALUE_MAP_GROUP_LEN)
                    .position(|group| group[..2] == column.storage_ordinal().to_le_bytes())
                    .ok_or(WriteError::Mismatch("long-value map suffix"))?;
                edited.suffix.drain(
                    position * crate::LONG_VALUE_MAP_GROUP_LEN
                        ..(position + 1) * crate::LONG_VALUE_MAP_GROUP_LEN,
                );
            }
            edited.stage(database, &table, &mut edits, budget)?;
            Ok((edits, (catalog.root(), row, properties, retired)))
        })?;
    crate::schema::properties::store(file, journal, catalog, row, &properties, budget)?;
    for locator in retired.into_iter().flatten() {
        crate::schema::map::retire(file, journal, locator, budget)?;
    }
    Ok(())
}
