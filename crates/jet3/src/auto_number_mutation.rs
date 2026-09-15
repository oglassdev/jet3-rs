//! Generated/explicit insertion and immutable existing AutoNumber fields (EXP-0237).
use crate::auto_number_state::AutoNumberState;
use crate::{
    ColumnPhysicalType, PageImage, ResourceBudget, RowValue, RowView, TableDefinition,
    TextCodePage, UpdateError, ValueKind,
};

#[derive(Debug, Clone, Copy)]
pub(crate) struct AutoNumber {
    column: crate::ColumnOrdinal,
    columns: usize,
    state: AutoNumberState,
}

impl AutoNumber {
    pub fn load(table: &TableDefinition) -> Result<Option<Self>, UpdateError> {
        let mut columns = table
            .columns()
            .iter()
            .filter(|column| column.auto_increment());
        let Some(column) = columns.next() else {
            return Ok(None);
        };
        if columns.next().is_some() || column.physical_type() != ColumnPhysicalType::Long {
            return Err(UpdateError::Unsupported("AutoNumber schema"));
        }
        Ok(Some(Self {
            column: column.ordinal(),
            columns: table.columns().len(),
            state: AutoNumberState::decode(table.raw_header())?,
        }))
    }

    pub fn copy_values<'a>(
        self,
        values: &[RowValue<'a>],
        lowered: &mut [RowValue<'a>; u8::MAX as usize],
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        if values.len() != self.columns {
            return Err(crate::RowWriteError::ValueCountMismatch {
                expected: self.columns,
                actual: values.len(),
            }
            .into());
        }
        if values.len() > lowered.len() {
            return Err(UpdateError::Unsupported("AutoNumber row column count"));
        }
        budget.charge_work_units(values.len() as u64)?;
        lowered[..values.len()].copy_from_slice(values);
        Ok(())
    }

    pub fn insert(self, values: &mut [RowValue<'_>]) -> Result<Self, UpdateError> {
        let target = values
            .get_mut(usize::from(self.column.get()))
            .ok_or(UpdateError::NotFound("AutoNumber insertion value"))?;
        let explicit = match target {
            RowValue::AutoIncrement => None,
            RowValue::Long(value) => Some(*value),
            _ => {
                return Err(UpdateError::Unsupported(
                    "AutoNumber requires generation or an explicit Long",
                ));
            }
        };
        let (state, value) = self.state.allocate(explicit);
        *target = RowValue::Long(value);
        Ok(Self { state, ..self })
    }

    pub fn read(self, row: &mut RowView<'_, '_>) -> Result<i32, UpdateError> {
        let value = row
            .value(self.column, TextCodePage::Windows1252)?
            .ok_or(UpdateError::NotFound("AutoNumber column"))?;
        match value.kind() {
            ValueKind::Long(value) => Ok(*value),
            _ => Err(UpdateError::Mismatch(
                "AutoNumber row value is not a present Long",
            )),
        }
    }

    pub fn retain(self, values: &mut [RowValue<'_>], original: i32) -> Result<(), UpdateError> {
        let value = values
            .get_mut(usize::from(self.column.get()))
            .ok_or(UpdateError::NotFound("AutoNumber replacement value"))?;
        match value {
            RowValue::AutoIncrement => *value = RowValue::Long(original),
            RowValue::Long(value) if *value == original => (),
            _ => {
                return Err(UpdateError::Unsupported(
                    "AutoNumber field cannot be updated",
                ));
            }
        }
        Ok(())
    }

    pub fn write(
        self,
        page: &mut PageImage,
        budget: &mut ResourceBudget,
    ) -> Result<(), UpdateError> {
        self.state.write(page, budget)?;
        Ok(())
    }
}
