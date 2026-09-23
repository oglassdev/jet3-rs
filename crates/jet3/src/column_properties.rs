//! Creation-time catalog LvProp: EXP-0208/0266/0270/0277/0283/0284 Boolean
//! properties and EXP-0299 text properties, encoded through [`PropertyBlob`].
use crate::property_blob::{
    BOOLEAN, Block, FIELD_BLOCK, MEMO, PropertyBlob, Record, TABLE_BLOCK, TEXT,
};
use crate::{
    BinaryWriter, ColumnPhysicalType, ColumnPropertyError, ColumnSpec, ColumnType, Error,
    ResourceBudget, TableValidation,
};

pub(crate) const fn has_zero_length_property(kind: ColumnPhysicalType) -> bool {
    matches!(kind, ColumnPhysicalType::Text | ColumnPhysicalType::Memo)
}

/// The EXP-0299 text properties with their record flag and value type.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum TextProperty {
    ValidationRule,
    ValidationText,
    DefaultValue,
    Description,
}

impl TextProperty {
    /// Native pre-append record order within a field block (EXP-0299).
    pub(crate) const FIELD_ORDER: [Self; 4] = [
        Self::ValidationRule,
        Self::ValidationText,
        Self::DefaultValue,
        Self::Description,
    ];

    pub(crate) const fn name(self) -> &'static [u8] {
        match self {
            Self::ValidationRule => b"ValidationRule",
            Self::ValidationText => b"ValidationText",
            Self::DefaultValue => b"DefaultValue",
            Self::Description => b"Description",
        }
    }

    /// Description is a user-defined property with flag 0; the others are engine properties.
    pub(crate) const fn flag(self) -> u8 {
        match self {
            Self::Description => 0,
            _ => 1,
        }
    }

    /// Field rules and defaults are Memo, text values Text (EXP-0299).
    pub(crate) const fn field_kind(self) -> u8 {
        match self {
            Self::ValidationRule | Self::DefaultValue => MEMO,
            Self::ValidationText | Self::Description => TEXT,
        }
    }

    /// DAO 3313 refuses validation properties on Binary, OLE and GUID fields (EXP-0299).
    pub(crate) const fn eligible(self, kind: ColumnPhysicalType) -> bool {
        !matches!(self, Self::ValidationRule | Self::ValidationText)
            || !matches!(
                kind,
                ColumnPhysicalType::Binary
                    | ColumnPhysicalType::LongBinary
                    | ColumnPhysicalType::Guid
            )
    }

    pub(crate) fn of<'a>(self, column: &ColumnSpec<'a>) -> Option<&'a [u8]> {
        match self {
            Self::ValidationRule => column.validation_rule(),
            Self::ValidationText => column.validation_text(),
            Self::DefaultValue => column.default_value(),
            Self::Description => column.description(),
        }
    }
}

/// Complete creation-time payload of one table.
#[derive(Debug, Clone)]
pub(crate) struct ColumnProperties {
    blob: PropertyBlob,
}

fn has_text(column: &ColumnSpec<'_>) -> bool {
    TextProperty::FIELD_ORDER
        .iter()
        .any(|property| property.of(column).is_some())
}

impl ColumnProperties {
    /// Returns `None` when the table needs no payload or a name is outside the catalog grammar.
    pub(crate) fn new(
        columns: &[ColumnSpec<'_>],
        validation: TableValidation<'_>,
        budget: &mut ResourceBudget,
    ) -> Result<Option<Self>, ColumnPropertyError> {
        let text =
            validation.rule.is_some() || validation.text.is_some() || columns.iter().any(has_text);
        if columns.len() > u8::MAX as usize
            || !text
                && !columns.iter().any(|column| {
                    has_zero_length_property(column.physical_type()) || column.required()
                })
            || columns.iter().any(|column| {
                crate::catalog_name_key::validate_catalog_name(column.name()).is_err()
            })
        {
            return Ok(None);
        }
        budget.charge_work_units(columns.len() as u64 * 8)?;
        let mut blob = PropertyBlob::empty();
        if !text {
            // EXP-0266/0283 accepted creation order, retained byte-for-byte.
            blob.intern(b"Required", budget)?;
            if columns
                .iter()
                .any(|column| has_zero_length_property(column.physical_type()))
            {
                blob.intern(b"AllowZeroLength", budget)?;
            }
        }
        let mut descriptions = Vec::new();
        for column in columns {
            let auto = column.column_type() == ColumnType::AutoIncrement;
            if auto && !has_text(column) {
                continue;
            }
            let mut block = Block::new(FIELD_BLOCK, column.name(), budget)?;
            if !auto {
                if has_zero_length_property(column.physical_type()) {
                    boolean(
                        &mut blob,
                        &mut block,
                        b"AllowZeroLength",
                        column.allow_zero_length(),
                        budget,
                    )?;
                }
                boolean(
                    &mut blob,
                    &mut block,
                    b"Required",
                    column.required(),
                    budget,
                )?;
            }
            for property in TextProperty::FIELD_ORDER {
                let Some(value) = property.of(column) else {
                    continue;
                };
                if property == TextProperty::Description {
                    crate::resource::reserve(&mut descriptions, 1, budget)?;
                    descriptions.push((blob.blocks().len(), value));
                    continue;
                }
                let name = blob.intern(property.name(), budget)?;
                let record =
                    Record::new(property.flag(), property.field_kind(), name, value, budget)?;
                block.set(record, budget)?;
            }
            blob.push(block, budget)?;
        }
        if validation.rule.is_some() || validation.text.is_some() {
            // EXP-0299: pre-append table rules and messages are both Text.
            let mut block = Block::new(TABLE_BLOCK, b"", budget)?;
            for (property, value) in [
                (TextProperty::ValidationRule, validation.rule),
                (TextProperty::ValidationText, validation.text),
            ] {
                if let Some(value) = value {
                    let name = blob.intern(property.name(), budget)?;
                    block.set(Record::new(1, TEXT, name, value, budget)?, budget)?;
                }
            }
            blob.push(block, budget)?;
        }
        // EXP-0299: Access-layer Description is appended after the table exists.
        for (position, value) in descriptions {
            let name = blob.intern(TextProperty::Description.name(), budget)?;
            let record = Record::new(0, TEXT, name, value, budget)?;
            blob.block_at(position)?.set(record, budget)?;
        }
        Ok(Some(Self { blob }))
    }

    pub(crate) fn len(&self) -> usize {
        self.blob.len()
    }

    pub(crate) fn encode(
        &self,
        output: &mut [u8],
        budget: &mut ResourceBudget,
    ) -> Result<usize, Error> {
        let mut writer = BinaryWriter::new(output, budget)?;
        self.blob.write(&mut writer)?;
        Ok(self.len())
    }
}

fn boolean(
    blob: &mut PropertyBlob,
    block: &mut Block,
    name: &[u8],
    value: bool,
    budget: &mut ResourceBudget,
) -> Result<(), ColumnPropertyError> {
    let name = blob.intern(name, budget)?;
    block.set(
        Record::new(1, BOOLEAN, name, &[if value { 0xff } else { 0 }], budget)?,
        budget,
    )
}

/// Largest text property value Rust writes; EXP-0299 observed DAO accepting it.
pub(crate) const MAX_TEXT_PROPERTY: usize = 2048;

/// Checks one opaque value: nonempty, bounded, without NUL (DAO truncates at
/// NUL, EXP-0299) or bytes undefined in CP1252.
pub(crate) fn check_value(value: &[u8]) -> Result<(), &'static str> {
    if value.is_empty() {
        return Err("empty property value");
    }
    if value.len() > MAX_TEXT_PROPERTY {
        return Err("property value too long");
    }
    if value.iter().any(|&byte| {
        byte == 0 || crate::text::mapped_character(crate::TextCodePage::Windows1252, byte).is_none()
    }) {
        return Err("property value byte");
    }
    Ok(())
}

/// Checks every requested text property of a table specification.
pub(crate) fn check(
    columns: &[ColumnSpec<'_>],
    validation: TableValidation<'_>,
) -> Result<(), (Option<usize>, &'static [u8], &'static str)> {
    for (ordinal, column) in columns.iter().enumerate() {
        for property in TextProperty::FIELD_ORDER {
            if let Some(value) = property.of(column) {
                if !property.eligible(column.physical_type()) {
                    return Err((Some(ordinal), property.name(), "column type"));
                }
                check_value(value).map_err(|detail| (Some(ordinal), property.name(), detail))?;
            }
        }
    }
    for (property, value) in [
        (TextProperty::ValidationRule, validation.rule),
        (TextProperty::ValidationText, validation.text),
    ] {
        if let Some(value) = value {
            check_value(value).map_err(|detail| (None, property.name(), detail))?;
        }
    }
    Ok(())
}
