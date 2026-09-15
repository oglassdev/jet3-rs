//! Initial generated/explicit Long values and persisted allocation state (EXP-0136/0237).
use super::*;
use crate::auto_number_state::AutoNumberState;

#[derive(Debug, Clone, Copy)]
pub(crate) struct InitialAutoIncrement {
    column: usize,
    final_state: AutoNumberState,
    current: AutoNumberState,
    next_row: usize,
}

impl InitialAutoIncrement {
    pub(crate) fn new(
        table: &TableSpec<'_>,
        rows: &[&[RowValue<'_>]],
        budget: &mut ResourceBudget,
    ) -> Result<Option<Self>, ComposeError> {
        let mut columns = table
            .columns
            .iter()
            .enumerate()
            .filter(|(_, column)| column.column_type() == ColumnType::AutoIncrement);
        let Some((column, _)) = columns.next() else {
            return Ok(None);
        };
        if columns.next().is_some() {
            return Err(Self::refusal("multiple AutoIncrement columns"));
        }
        budget.charge_items(rows.len() as u64)?;
        let mut final_state = AutoNumberState::default();
        for row in rows {
            final_state = final_state.allocate(Self::explicit(row.get(column))?).0;
        }
        Ok(Some(Self {
            column,
            final_state,
            current: AutoNumberState::default(),
            next_row: 0,
        }))
    }

    fn refusal(detail: &'static str) -> ComposeError {
        ComposeError::InitialAutoIncrement { detail }
    }

    fn explicit(value: Option<&RowValue<'_>>) -> Result<Option<i32>, ComposeError> {
        match value {
            Some(RowValue::AutoIncrement) => Ok(None),
            Some(RowValue::Long(value)) => Ok(Some(*value)),
            _ => Err(Self::refusal(
                "AutoIncrement requires generation or an explicit Long",
            )),
        }
    }

    pub(crate) fn lower<'a>(
        &mut self,
        values: &[RowValue<'a>],
        ordinal: usize,
        lowered: &mut [RowValue<'a>; u8::MAX as usize],
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        if values.len() > lowered.len() || self.column >= values.len() || ordinal != self.next_row {
            return Err(Self::refusal("AutoIncrement row position or value count"));
        }
        let (next, value) = self
            .current
            .allocate(Self::explicit(values.get(self.column))?);
        budget.charge_work_units(values.len() as u64)?;
        let next_row = self
            .next_row
            .checked_add(1)
            .ok_or_else(|| Self::refusal("AutoIncrement row ordinal overflow"))?;
        lowered[..values.len()].copy_from_slice(values);
        lowered[self.column] = RowValue::Long(value);
        self.current = next;
        self.next_row = next_row;
        Ok(())
    }

    pub(crate) fn write(
        self,
        root: &mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        self.final_state.write_bytes(root, budget)?;
        Ok(())
    }

    pub(crate) fn matches(self, root: &[u8; PAGE_BYTES]) -> bool {
        AutoNumberState::decode(root).is_ok_and(|state| state == self.final_state)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_wraps_explicit_resets_and_exact_state_preservation()
    -> Result<(), Box<dyn std::error::Error>> {
        let table = TableSpec {
            name: b"T",
            columns: &[crate::ColumnSpec::new(b"Id", ColumnType::AutoIncrement)],
            indexes: &[],
        };
        let rows: &[&[RowValue<'_>]] = &[
            &[RowValue::Long(i32::MAX)],
            &[RowValue::AutoIncrement],
            &[RowValue::Long(-1)],
            &[RowValue::AutoIncrement],
        ];
        let mut budget = ResourceBudget::new(crate::ResourceLimits::default());
        let mut generated =
            InitialAutoIncrement::new(&table, rows, &mut budget)?.ok_or("generator")?;
        let mut bytes = [0xa5; PAGE_BYTES];
        generated.write(&mut bytes, &mut budget)?;
        assert!(generated.matches(&bytes));
        assert_eq!(&bytes[..16], &[0xa5; 16]);
        assert_eq!(&bytes[16..20], &[0; 4]);
        assert_eq!(&bytes[20..], &[0xa5; PAGE_BYTES - 20]);
        let mut lowered = [RowValue::Null; u8::MAX as usize];
        assert!(
            generated
                .lower(rows[0], 1, &mut lowered, &mut budget)
                .is_err()
        );
        for (ordinal, (row, value)) in rows.iter().zip([i32::MAX, i32::MIN, -1, 0]).enumerate() {
            generated.lower(row, ordinal, &mut lowered, &mut budget)?;
            assert_eq!(lowered[0], RowValue::Long(value));
        }
        Ok(())
    }
}
