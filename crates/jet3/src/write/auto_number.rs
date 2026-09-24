//! Generated/explicit insertion and immutable existing AutoNumber fields (EXP-0237).
use crate::{
    ColumnPhysicalType, Error, PAGE_BYTES, PageImage, PageOffset, ResourceBudget, RowValue,
    RowView, TableDefinition, TextCodePage, UpdateError, ValueKind,
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

// EXP-0136/0237: TDEF AutoNumber allocation state and explicit unsigned resets.
const OFFSET: usize = 16;

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct AutoNumberState(u32);

impl AutoNumberState {
    pub fn decode(header: &[u8]) -> Result<Self, Error> {
        let raw = header.get(OFFSET..OFFSET + 4).ok_or(Error::Arithmetic {
            operation: "locate AutoNumber state",
        })?;
        let bytes = raw.try_into().map_err(|_| Error::Arithmetic {
            operation: "decode AutoNumber state",
        })?;
        Ok(Self(u32::from_le_bytes(bytes)))
    }

    pub const fn allocate(self, explicit: Option<i32>) -> (Self, i32) {
        let allocated = self.0.wrapping_add(1);
        let value = match explicit {
            Some(value) => value,
            None => allocated as i32,
        };
        let state = if value as u32 > allocated {
            value as u32
        } else {
            allocated
        };
        (Self(state), value)
    }

    pub fn write(self, page: &mut PageImage, budget: &mut ResourceBudget) -> Result<(), Error> {
        page.write_at(
            PageOffset::new(OFFSET as u64),
            &self.0.to_le_bytes(),
            budget,
        )
    }

    pub fn write_bytes(
        self,
        page: &mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<(), Error> {
        let mut writer = crate::BinaryWriter::new(page, budget)?;
        writer.seek(crate::ByteOffset::new(OFFSET as u64))?;
        writer.write_u32_le(self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_ids_allocate_first_and_compare_unsigned_across_wraps() {
        let mut state = AutoNumberState::default();
        for (explicit, expected_id, expected_state) in [
            (None, 1, 1),
            (Some(100), 100, 100),
            (Some(0), 0, 101),
            (Some(-5), -5, -5),
            (Some(50), 50, -4),
            (None, -3, -3),
            (Some(-1), -1, -1),
            (None, 0, 0),
            (Some(i32::MAX), i32::MAX, i32::MAX),
            (Some(1000), 1000, i32::MIN),
            (None, i32::MIN + 1, i32::MIN + 1),
        ] {
            let (next, value) = state.allocate(explicit);
            assert_eq!(value, expected_id);
            assert_eq!(next.0 as i32, expected_state);
            state = next;
        }
    }
}
