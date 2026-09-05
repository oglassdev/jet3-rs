//! Initial generated Long values and persisted last-generated state (EXP-0136).
//! DAO observations cover 0..=256 and a subsequent generated 257 after deletion;
//! larger positive counts use the same checked scalar encoding as a candidate.

use super::*;
use crate::{BinaryWriter, ByteOffset};

/// EXP-0136: signed little-endian last-generated Long in the user TDEF root.
const STATE_OFFSET: usize = 16;

#[derive(Debug, Clone, Copy)]
pub(crate) struct InitialAutoIncrement {
    column: usize,
    last: i32,
}

impl InitialAutoIncrement {
    pub(crate) fn new(table: &TableSpec<'_>, rows: usize) -> Result<Option<Self>, ComposeError> {
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
        // Leave a representable positive successor; no overflow behavior is inferred.
        let last = i32::try_from(rows)
            .ok()
            .filter(|last| *last < i32::MAX)
            .ok_or_else(|| Self::refusal("generated count reaches the signed Long boundary"))?;
        Ok(Some(Self { column, last }))
    }

    fn refusal(detail: &'static str) -> ComposeError {
        ComposeError::InitialAutoIncrement { detail }
    }

    pub(crate) fn lower<'a>(
        self,
        values: &[RowValue<'a>],
        ordinal: usize,
        lowered: &mut [RowValue<'a>; u8::MAX as usize],
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        if values.len() > lowered.len() || self.column >= values.len() {
            return Err(Self::refusal("missing generation request"));
        }
        if !matches!(values[self.column], RowValue::AutoIncrement) {
            return Err(Self::refusal(
                "AutoIncrement requires an explicit generation request",
            ));
        }
        let generated = i32::try_from(ordinal)
            .ok()
            .and_then(|n| n.checked_add(1))
            .filter(|n| *n <= self.last)
            .ok_or_else(|| Self::refusal("generated row ordinal exceeds planned state"))?;
        budget.charge_work_units(values.len() as u64)?;
        lowered[..values.len()].copy_from_slice(values);
        lowered[self.column] = RowValue::Long(generated);
        Ok(())
    }

    pub(crate) fn write(
        self,
        root: &mut [u8; PAGE_BYTES],
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let mut writer = BinaryWriter::new(root, budget)?;
        writer.seek(ByteOffset::new(STATE_OFFSET as u64))?;
        writer.write_i32_le(self.last)?;
        Ok(())
    }

    pub(crate) fn matches(self, root: &[u8; PAGE_BYTES]) -> bool {
        root[STATE_OFFSET..STATE_OFFSET + size_of::<i32>()] == self.last.to_le_bytes()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn checked_counter_boundary_and_exact_state_preservation()
    -> Result<(), Box<dyn std::error::Error>> {
        let table = TableSpec {
            name: b"T",
            columns: &[crate::ColumnSpec::new(b"Id", ColumnType::AutoIncrement)],
            indexes: &[],
        };
        assert!(InitialAutoIncrement::new(&table, i32::MAX as usize).is_err());
        let generated =
            InitialAutoIncrement::new(&table, i32::MAX as usize - 1)?.ok_or("no generator")?;
        let mut bytes = [0xa5; PAGE_BYTES];
        let mut budget = ResourceBudget::new(crate::ResourceLimits::default());
        generated.write(&mut bytes, &mut budget)?;
        assert!(generated.matches(&bytes));
        assert_eq!(&bytes[..16], &[0xa5; 16]);
        assert_eq!(&bytes[20..], &[0xa5; PAGE_BYTES - 20]);
        let mut lowered = [RowValue::Null; u8::MAX as usize];
        generated.lower(
            &[RowValue::AutoIncrement],
            i32::MAX as usize - 2,
            &mut lowered,
            &mut budget,
        )?;
        assert_eq!(lowered[0], RowValue::Long(i32::MAX - 1));
        assert!(
            generated
                .lower(
                    &[RowValue::AutoIncrement],
                    i32::MAX as usize - 1,
                    &mut lowered,
                    &mut budget
                )
                .is_err()
        );
        Ok(())
    }
}
