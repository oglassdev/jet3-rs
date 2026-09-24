//! Catalog LvProp values and shared external ownership from EXP-0077/0266.
use crate::{
    ByteCount, ColumnOrdinal, ColumnPropertyError, DatabaseReader, LongValue, LongValueReference,
    PageNumber, ReadAt, ResourceBudget, TableDefinition, TextCodePage, ValueKind,
    alloc::mutation_map::MapBits,
    properties::{blob::PropertyBlob, reader::PropertyOptions},
};

/// A catalog row's stored `LvProp` before any external payload is read.
pub(crate) enum StoredProperties<'a> {
    Null,
    Inline(&'a [u8]),
    External(LongValueReference),
}

impl<'a> StoredProperties<'a> {
    /// Classifies an `LvProp` value; `None` for any other value kind.
    pub(crate) fn of(kind: &ValueKind<'a>) -> Option<Self> {
        match kind {
            ValueKind::Null => Some(Self::Null),
            ValueKind::LongValue(LongValue::Inline { value, .. }) => {
                Some(Self::Inline(value.raw_bytes()))
            }
            ValueKind::LongValue(LongValue::External(reference)) => {
                Some(Self::External(*reference))
            }
            _ => None,
        }
    }
}

enum Payload {
    Null,
    Inline(Vec<u8>),
    External(LongValueReference),
}

pub(crate) struct Properties {
    catalog: TableDefinition,
    property: ColumnOrdinal,
    values: Vec<(PageNumber, Payload)>,
    owned: Option<MapBits>,
}

impl Properties {
    pub(crate) fn load<S: ReadAt>(
        database: &mut DatabaseReader<S>,
        catalog_root: PageNumber,
        roots: &[PageNumber],
        budget: &mut ResourceBudget,
    ) -> Result<Self, ColumnPropertyError> {
        let catalog = database.table_definition(catalog_root, budget)?;
        let column = |name: &[u8]| {
            catalog
                .columns()
                .iter()
                .find(|column| column.name().raw_bytes() == name)
                .map(|column| column.ordinal())
                .ok_or(ColumnPropertyError::Invalid("catalog property column"))
        };
        let id = column(b"Id")?;
        let property = column(b"LvProp")?;
        let mut values = Vec::new();
        {
            let mut rows = database.rows(&catalog, budget)?;
            while let Some(mut row) = rows.next_row()? {
                let value = row
                    .value(id, TextCodePage::Windows1252)?
                    .ok_or(ColumnPropertyError::Invalid("catalog object Id"))?;
                let ValueKind::Long(id) = value.kind() else {
                    return Err(ColumnPropertyError::Invalid("catalog object Id type"));
                };
                let root = PageNumber::new(u64::from(*id as u32));
                row.budget_mut().charge_work_units(roots.len() as u64)?;
                if !roots.contains(&root) {
                    continue;
                }
                let value = row
                    .value(property, TextCodePage::Windows1252)?
                    .ok_or(ColumnPropertyError::Invalid("catalog LvProp"))?;
                let stored = StoredProperties::of(value.kind())
                    .ok_or(ColumnPropertyError::Invalid("catalog LvProp type"))?;
                let payload = match stored {
                    StoredProperties::Null => Payload::Null,
                    StoredProperties::Inline(source) => {
                        let mut saved = [0; crate::PAGE_BYTES];
                        let length = source.len();
                        saved
                            .get_mut(..length)
                            .ok_or(ColumnPropertyError::Invalid("inline property capacity"))?
                            .copy_from_slice(source);
                        let mut bytes = buffer(length, row.budget_mut())?;
                        bytes.extend_from_slice(&saved[..length]);
                        Payload::Inline(bytes)
                    }
                    StoredProperties::External(reference) => Payload::External(reference),
                };
                crate::format::resource::reserve(&mut values, 1, row.budget_mut())?;
                values.push((root, payload));
            }
        }
        Ok(Self {
            catalog,
            property,
            values,
            owned: None,
        })
    }

    pub(crate) fn options<S: ReadAt>(
        &mut self,
        database: &mut DatabaseReader<S>,
        table: &TableDefinition,
        budget: &mut ResourceBudget,
    ) -> Result<PropertyOptions, ColumnPropertyError> {
        match self.blob(database, table, budget)? {
            Some(blob) => crate::properties::reader::options(&blob, table.columns(), budget),
            None => Ok(PropertyOptions::default()),
        }
    }

    /// Parses the table's complete payload, or `None` when LvProp is null.
    pub(crate) fn blob<S: ReadAt>(
        &mut self,
        database: &mut DatabaseReader<S>,
        table: &TableDefinition,
        budget: &mut ResourceBudget,
    ) -> Result<Option<PropertyBlob>, ColumnPropertyError> {
        budget.charge_work_units(self.values.len() as u64)?;
        let payload = &self
            .values
            .iter()
            .find(|(root, _)| *root == table.root())
            .ok_or(ColumnPropertyError::Invalid("table column properties"))?
            .1;
        match payload {
            Payload::Null => Ok(None),
            Payload::Inline(bytes) => Ok(Some(PropertyBlob::parse(bytes, budget)?)),
            Payload::External(reference) => {
                let owned = match &self.owned {
                    Some(owned) => owned,
                    None => self.owned.insert(
                        crate::properties::ownership::load(
                            database,
                            &self.catalog,
                            self.property,
                            budget,
                        )
                        .map_err(ColumnPropertyError::from_ownership_check)?,
                    ),
                };
                let mut rows = database.rows(&self.catalog, budget)?;
                let mut stream = rows.long_value(*reference)?;
                let mut bytes = buffer(reference.length() as usize, stream.budget_mut())?;
                while let Some(chunk) = stream.next_chunk()? {
                    if !owned
                        .contains(chunk.locator().page())
                        .map_err(ColumnPropertyError::from_ownership_check)?
                    {
                        return Err(ColumnPropertyError::Invalid(
                            "property reference outside column map",
                        ));
                    }
                    let source = chunk.value().raw_bytes();
                    if source.len() > reference.length() as usize - bytes.len() {
                        return Err(ColumnPropertyError::Invalid("property declared length"));
                    }
                    bytes.extend_from_slice(source);
                }
                drop(rows);
                Ok(Some(PropertyBlob::parse(&bytes, budget)?))
            }
        }
    }
}

fn buffer(length: usize, budget: &mut ResourceBudget) -> Result<Vec<u8>, ColumnPropertyError> {
    let count = ByteCount::from_usize(length)?;
    budget.check_decoded_value(count)?;
    budget.charge_allocation(count)?;
    let mut bytes = Vec::new();
    bytes
        .try_reserve_exact(length)
        .map_err(|_| crate::Error::Io {
            operation: "reserve stored column properties",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
    Ok(bytes)
}
