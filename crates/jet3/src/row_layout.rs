//! Physical row-layout validation and field-boundary decoding from `EXP-0060`.

use std::ops::Range;

use crate::{
    ColumnOrdinal, ColumnPhysicalType, ColumnStorageClass, ResourceBudget, RowError,
    TableDefinition,
};

#[derive(Debug, Clone, Copy)]
pub(crate) struct RowLayout {
    pub(crate) fixed_boundary: usize,
    offsets_start: usize,
    null_start: usize,
    variable_count: u8,
    pub(crate) wide: bool,
}

impl RowLayout {
    pub(crate) fn validate(
        row: &[u8],
        definition: &TableDefinition,
        budget: &mut ResourceBudget,
    ) -> Result<Self, RowError> {
        let expected_count = u8::try_from(definition.columns().len()).map_err(|_| {
            RowError::ColumnCountNotRepresentable {
                count: definition.columns().len(),
            }
        })?;
        let null_len = usize::from(expected_count).div_ceil(8);
        let minimum = 1 + null_len;
        if row.len() < minimum {
            return Err(RowError::RowTooShort {
                length: row.len(),
                minimum,
            });
        }
        if row[0] != expected_count {
            return Err(RowError::ColumnCountMismatch {
                expected: expected_count,
                actual: row[0],
            });
        }
        budget
            .charge_items(u64::from(expected_count))
            .map_err(RowError::Resource)?;
        let fixed_size = definition
            .columns()
            .iter()
            .filter_map(|column| match column.storage() {
                ColumnStorageClass::Fixed { offset }
                    if column.physical_type() != ColumnPhysicalType::Boolean =>
                {
                    Some(usize::from(offset) + usize::from(column.size()))
                }
                _ => None,
            })
            .max()
            .unwrap_or(0);
        let fixed_boundary = 1 + fixed_size;
        let variable_count = definition
            .columns()
            .iter()
            .filter(|column| matches!(column.storage(), ColumnStorageClass::Variable { .. }))
            .count();
        let variable_count =
            u8::try_from(variable_count).map_err(|_| RowError::ColumnCountNotRepresentable {
                count: definition.columns().len(),
            })?;
        let null_start = row.len() - null_len;
        validate_unused_null_bits(row, expected_count, null_start)?;
        if variable_count == 0 {
            if null_start != fixed_boundary {
                return Err(RowError::InvalidFixedBoundary {
                    expected: fixed_boundary,
                    actual: null_start,
                });
            }
            return Ok(Self {
                fixed_boundary,
                offsets_start: null_start,
                null_start,
                variable_count,
                wide: false,
            });
        }
        if null_start == 0 {
            return Err(RowError::RowTooShort {
                length: row.len(),
                minimum: minimum + 1,
            });
        }
        let count_position = null_start - 1;
        let actual_variable_count = row[count_position];
        if actual_variable_count != variable_count {
            return Err(RowError::VariableCountMismatch {
                expected: variable_count,
                actual: actual_variable_count,
            });
        }
        let wide = row.len() > usize::from(u8::MAX);
        if wide && variable_count != 1 {
            return Err(RowError::UnsupportedWideVariableOffsets {
                variable_count,
                row_length: row.len(),
            });
        }
        let low_count = usize::from(variable_count) + 1;
        let jump_count = usize::from(wide);
        let trailer = low_count + jump_count;
        let offsets_start = count_position
            .checked_sub(trailer)
            .ok_or(RowError::RowTooShort {
                length: row.len(),
                minimum: minimum + 1 + trailer,
            })?;
        let layout = Self {
            fixed_boundary,
            offsets_start,
            null_start,
            variable_count,
            wide,
        };
        let actual_fixed = layout.boundary(row, 0)?;
        if actual_fixed != fixed_boundary {
            return Err(RowError::InvalidFixedBoundary {
                expected: fixed_boundary,
                actual: actual_fixed,
            });
        }
        let mut start = actual_fixed;
        for index in 0..variable_count {
            let end = layout.boundary(row, index + 1)?;
            if start > end || end > offsets_start {
                return Err(RowError::InvalidVariableBounds {
                    index: u16::from(index),
                    start,
                    end,
                    data_end: offsets_start,
                });
            }
            start = end;
        }
        if start != offsets_start {
            return Err(RowError::InvalidVariableBounds {
                index: u16::from(variable_count.saturating_sub(1)),
                start,
                end: offsets_start,
                data_end: offsets_start,
            });
        }
        Ok(layout)
    }

    pub(crate) fn present(self, row: &[u8], ordinal: ColumnOrdinal) -> bool {
        let bit = usize::from(ordinal.get());
        let byte = self.null_start + bit / 8;
        row.get(byte)
            .is_some_and(|raw| raw & (1_u8 << (bit % 8)) != 0)
    }

    pub(crate) fn variable_range(self, row: &[u8], index: u16) -> Option<Range<usize>> {
        let index = u8::try_from(index).ok()?;
        if index >= self.variable_count {
            return None;
        }
        let start = self.boundary(row, index).ok()?;
        let end = self.boundary(row, index + 1).ok()?;
        Some(start..end)
    }

    fn boundary(self, row: &[u8], ordinal: u8) -> Result<usize, RowError> {
        let reversed = usize::from(self.variable_count - ordinal);
        let low = usize::from(row[self.offsets_start + reversed]);
        if !self.wide {
            return Ok(low);
        }
        let jump = row[self.offsets_start + usize::from(self.variable_count) + 1];
        let high = usize::from((jump >> reversed) & 1);
        Ok(low + 256 * high)
    }
}

fn validate_unused_null_bits(
    row: &[u8],
    column_count: u8,
    null_start: usize,
) -> Result<(), RowError> {
    let used = column_count % 8;
    if used == 0 || column_count == 0 {
        return Ok(());
    }
    let mask = !((1_u8 << used) - 1);
    let raw = *row.last().ok_or(RowError::RowTooShort {
        length: row.len(),
        minimum: null_start + 1,
    })?;
    if raw & mask != 0 {
        return Err(RowError::NonzeroUnusedNullBits { raw, mask });
    }
    Ok(())
}
