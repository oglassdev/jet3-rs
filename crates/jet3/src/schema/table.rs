//! Existing-database table creation using EXP-0073/0087 catalogs and EXP-0059/0077 definitions.
use crate::{
    ByteCount, DatabaseReader, FileSource, LongValueMapSpec, PAGE_BYTES, PageImage, ResourceBudget,
    RowValue, TableDefinitionKind, TableDefinitionSpec, TableSpec, UpdateError,
    write::page_edits::{PageEdits, reserve},
};
use std::fs::File;

pub(crate) fn create(
    file: &mut File,
    journal: &mut PageEdits,
    spec: TableSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let (root, parent, order) =
        crate::schema::publish::apply(file, journal, budget, |database, budget| {
            let order = database.header().sort_order();
            crate::create::schema_plan::plan_table_schema_for_order(
                &spec,
                database.geometry().page_count(),
                false,
                &[],
                spec.indexes.len(),
                order,
                budget,
            )?;
            if spec
                .columns
                .iter()
                .filter(|column| column.column_type() == crate::ColumnType::AutoIncrement)
                .count()
                > 1
                || spec.columns.iter().any(|column| {
                    column.allow_zero_length()
                        && !crate::properties::column::has_zero_length_property(
                            column.physical_type(),
                        )
                })
            {
                return Err(UpdateError::Unsupported("column options"));
            }
            let parent = validate_name(database, spec.name, None, budget)?;
            let mut edits = PageEdits::new(database.geometry().page_count());
            let root = crate::schema::definition::allocate(
                database,
                &mut edits,
                PageImage::from_bytes([0; PAGE_BYTES]),
                budget,
            )?;
            let owned = crate::schema::map::create(database, &mut edits, &[], budget)?;
            let available = crate::schema::map::create(database, &mut edits, &[], budget)?;
            let mut maps = Vec::new();
            for (ordinal, column) in spec.columns.iter().enumerate() {
                if column.column_type().is_long_value() {
                    let owned = crate::schema::map::create(database, &mut edits, &[], budget)?;
                    let available = crate::schema::map::create(database, &mut edits, &[], budget)?;
                    reserve(&mut maps, 1, budget)?;
                    maps.push(LongValueMapSpec {
                        column: ordinal as u16,
                        owned,
                        available,
                    });
                }
            }
            let definition = TableDefinitionSpec {
                kind: TableDefinitionKind::User,
                columns: spec.columns,
                system_column_classes: &[],
                physical_indexes: &[],
                indexes: &[],
                owned_map: owned,
                available_map: available,
                row_count: 0,
                long_value_maps: &maps,
            };
            let length = crate::table_definition_len(&definition)?;
            let mut bytes = Vec::new();
            reserve(&mut bytes, length, budget)?;
            bytes.resize(length, 0);
            crate::definition::table_writer::encode_table_definition_with_context(
                &definition,
                &mut bytes,
                order
                    .encoding_context()
                    .ok_or(UpdateError::Unsupported("column encoding context"))?,
                budget,
            )?;
            crate::schema::definition::stage_bytes(database, &[root], &bytes, &mut edits, budget)?;
            Ok((edits, (root, parent, order)))
        })?;
    let mut properties = Vec::new();
    if let Some(description) = crate::properties::column::CreationProperties::for_order(
        spec.columns,
        spec.validation,
        order,
        budget,
    )? {
        budget.check_decoded_value(ByteCount::new(description.len() as u64))?;
        reserve(&mut properties, description.len(), budget)?;
        properties.resize(description.len(), 0);
        description.encode(&mut properties, budget)?;
    }
    // EXP-0087: owner 0301 and two grants for an ordinary local table.
    let id =
        i32::try_from(root.get()).map_err(|_| UpdateError::Unsupported("table object identity"))?;
    insert(
        file,
        journal,
        b"MSysObjects",
        &[
            (b"Id", RowValue::Long(id)),
            (b"ParentId", RowValue::Long(parent)),
            (b"Name", RowValue::Text(spec.name)),
            (b"Type", RowValue::Integer(1)),
            (b"DateCreate", RowValue::DateTime { days: 0.0 }),
            (b"DateUpdate", RowValue::DateTime { days: 0.0 }),
            (b"Owner", RowValue::Binary(b"\x03\x01")),
            (b"Flags", RowValue::Long(0)),
            (
                b"LvProp",
                if properties.is_empty() {
                    RowValue::Null
                } else {
                    RowValue::LongBinary(&properties)
                },
            ),
        ],
        budget,
    )?;
    for (sid, access) in [
        (b"\x03\x01".as_slice(), 983294),
        (b"\x02\x01".as_slice(), 1048319),
    ] {
        insert(
            file,
            journal,
            b"MSysACEs",
            &[
                (b"ObjectId", RowValue::Long(id)),
                (b"SID", RowValue::Binary(sid)),
                (b"ACM", RowValue::Long(access)),
                (b"FInheritable", RowValue::Boolean(false)),
            ],
            budget,
        )?;
    }
    for index in spec.indexes {
        crate::schema::publish::apply(file, journal, budget, |database, budget| {
            let table = database.table_definition(root, budget)?;
            let edits = crate::schema::index::plan(
                database,
                &table,
                crate::SchemaEdit::CreateIndex {
                    table: spec.name,
                    index: *index,
                },
                budget,
            )?;
            Ok((edits, ()))
        })?;
    }
    Ok(())
}

pub(crate) fn insert(
    file: &mut File,
    journal: &mut PageEdits,
    name: &[u8],
    assigned: &[(&[u8], RowValue<'_>)],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    crate::schema::publish::apply(file, journal, budget, |database, budget| {
        let table = crate::schema::catalog::table(database, name, budget)?;
        if table.columns().len() > 255 {
            return Err(UpdateError::Unsupported("system column count"));
        }
        let mut values = [RowValue::Null; 255];
        for &(name, value) in assigned {
            let ordinal = crate::schema::catalog::column(&table, name)?;
            values[usize::from(ordinal.get())] = value;
        }
        let (edits, _) = crate::write::insert::plan(
            database,
            &table,
            name,
            &values[..table.columns().len()],
            false,
            budget,
        )?;
        Ok((edits, ()))
    })
}

pub(crate) fn validate_name(
    database: &mut DatabaseReader<FileSource>,
    name: &[u8],
    except: Option<crate::PageNumber>,
    budget: &mut ResourceBudget,
) -> Result<i32, UpdateError> {
    let order = database.header().sort_order();
    crate::schema::edit::name(order, name, 64)?;
    let parent = {
        let mut catalog = database.catalog(budget)?;
        let mut parent = None;
        while let Some(record) = catalog.next_record()? {
            if record.name().raw_bytes() == b"Tables" && record.kind().raw() == 3 {
                parent = Some(record.id().get() as i32);
            }
        }
        parent.ok_or(UpdateError::NotFound("Tables container"))?
    };
    let catalog = crate::schema::catalog::table(database, b"MSysObjects", budget)?;
    let parent_column = crate::schema::catalog::column(&catalog, b"ParentId")?;
    let id_column = crate::schema::catalog::column(&catalog, b"Id")?;
    let name_column = crate::schema::catalog::column(&catalog, b"Name")?;
    let mut rows = database.rows(&catalog, budget)?;
    while let Some(mut row) = rows.next_row()? {
        if !matches!(crate::row::scalar_values::read_column(&mut row, parent_column)?, RowValue::Long(value) if value == parent)
        {
            continue;
        }
        if matches!(crate::row::scalar_values::read_column(&mut row, id_column)?, RowValue::Long(value) if except.is_some_and(|root| root.get() == u64::from(value as u32)))
        {
            continue;
        }
        let mut saved = [0; 255];
        let existing = row
            .field(name_column)
            .and_then(|f| f.raw_bytes())
            .ok_or(UpdateError::Mismatch("catalog name"))?;
        if existing.len() > saved.len() {
            return Err(UpdateError::Mismatch("catalog name length"));
        }
        let length = existing.len();
        saved[..length].copy_from_slice(existing);
        crate::schema::edit::distinct(
            order,
            name,
            std::iter::once(&saved[..length]),
            row.budget_mut(),
        )?;
    }
    Ok(parent)
}
