//! Public view of stored catalog LvProp values: EXP-0266/0283 Boolean
//! properties and EXP-0299 text properties.
use crate::{
    ColumnOrdinal, ColumnPropertyError, DatabaseReader, ReadAt, ResourceBudget, TableDefinition,
    properties::{
        blob::{FIELD_BLOCK, PropertyBlob, TABLE_BLOCK},
        column::TextProperty,
    },
};

/// Stored properties of one live column.
///
/// Text values are the exact stored database-code-page bytes. DAO stores a
/// ValidationRule assigned to an existing field with one trailing NUL
/// (EXP-0299), and Rust edits do the same.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ColumnProperties {
    ordinal: ColumnOrdinal,
    required: bool,
    allow_zero_length: Option<bool>,
    text: [Option<Vec<u8>>; 4],
}

impl ColumnProperties {
    /// The column this entry describes.
    #[must_use]
    pub const fn ordinal(&self) -> ColumnOrdinal {
        self.ordinal
    }

    /// Stored Required value; absent properties read as false.
    #[must_use]
    pub const fn required(&self) -> bool {
        self.required
    }

    /// Stored AllowZeroLength value for Text and Memo columns.
    #[must_use]
    pub const fn allow_zero_length(&self) -> Option<bool> {
        self.allow_zero_length
    }

    /// Stored ValidationRule bytes.
    #[must_use]
    pub fn validation_rule(&self) -> Option<&[u8]> {
        self.text[0].as_deref()
    }

    /// Stored ValidationText bytes.
    #[must_use]
    pub fn validation_text(&self) -> Option<&[u8]> {
        self.text[1].as_deref()
    }

    /// Stored DefaultValue bytes; Rust writes never apply them.
    #[must_use]
    pub fn default_value(&self) -> Option<&[u8]> {
        self.text[2].as_deref()
    }

    /// Stored Description bytes.
    #[must_use]
    pub fn description(&self) -> Option<&[u8]> {
        self.text[3].as_deref()
    }
}

/// Stored table-level and per-column properties of one user table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TableProperties {
    validation_rule: Option<Vec<u8>>,
    validation_text: Option<Vec<u8>>,
    columns: Vec<ColumnProperties>,
}

impl TableProperties {
    /// Stored table ValidationRule bytes.
    #[must_use]
    pub fn validation_rule(&self) -> Option<&[u8]> {
        self.validation_rule.as_deref()
    }

    /// Stored table ValidationText bytes.
    #[must_use]
    pub fn validation_text(&self) -> Option<&[u8]> {
        self.validation_text.as_deref()
    }

    /// One entry per live column, in definition order.
    #[must_use]
    pub fn columns(&self) -> &[ColumnProperties] {
        &self.columns
    }
}

fn text(
    blob: &PropertyBlob,
    kind: u16,
    name: &[u8],
    property: TextProperty,
    budget: &mut ResourceBudget,
) -> Result<Option<Vec<u8>>, ColumnPropertyError> {
    budget.charge_work_units(blob.names().len() as u64 + blob.blocks().len() as u64)?;
    let Some(ordinal) = blob.ordinal(property.name()) else {
        return Ok(None);
    };
    let record = blob
        .blocks()
        .iter()
        .find(|block| block.kind() == kind && block.name() == name)
        .and_then(|block| block.record(ordinal));
    match record {
        Some(record) => Ok(Some(crate::properties::blob::owned(
            crate::properties::reader::text(record)?,
            budget,
        )?)),
        None => Ok(None),
    }
}

impl<S: ReadAt> DatabaseReader<S> {
    /// Reads a user table's stored properties from its catalog row.
    ///
    /// The payload is checked as for validation: ownership, framing and the
    /// Boolean and rule records must be consistent. Unknown properties are ignored.
    pub fn table_properties(
        &mut self,
        table: &TableDefinition,
        budget: &mut ResourceBudget,
    ) -> Result<TableProperties, ColumnPropertyError> {
        let catalog = self.catalog(budget)?.root();
        let blob =
            crate::properties::values::Properties::load(self, catalog, &[table.root()], budget)?
                .blob(self, table, budget)?
                .unwrap_or_else(PropertyBlob::empty);
        let options = crate::properties::reader::options(&blob, table.columns(), budget)?;
        let mut columns = Vec::new();
        crate::format::resource::reserve(&mut columns, table.columns().len(), budget)?;
        for (column, option) in table.columns().iter().zip(options.columns) {
            let name = column.name().raw_bytes();
            let mut values = [None, None, None, None];
            for (value, property) in values.iter_mut().zip(TextProperty::FIELD_ORDER) {
                *value = text(&blob, FIELD_BLOCK, name, property, budget)?;
            }
            columns.push(ColumnProperties {
                ordinal: column.ordinal(),
                required: option.required,
                allow_zero_length: option.allow_zero_length,
                text: values,
            });
        }
        Ok(TableProperties {
            validation_rule: text(
                &blob,
                TABLE_BLOCK,
                b"",
                TextProperty::ValidationRule,
                budget,
            )?,
            validation_text: text(
                &blob,
                TABLE_BLOCK,
                b"",
                TextProperty::ValidationText,
                budget,
            )?,
            columns,
        })
    }
}
