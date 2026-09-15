//! Checked physical row counts and variable trailers from EXP-0060/0257/0258.
use super::{
    ColumnOrdinal, ColumnPhysicalType, ColumnStorageClass, ResourceBudget, RowError,
    TableDefinition,
};
use std::ops::Range;

#[derive(Debug, Clone, Copy)]
pub(super) struct RowLayout {
    pub(super) fixed_boundary: usize,
    offsets_start: usize,
    null_start: usize,
    column_count: u8,
    variable_count: u8,
    jumps: usize,
}

impl RowLayout {
    pub(super) fn validate(
        row: &[u8],
        definition: &TableDefinition,
        budget: &mut ResourceBudget,
    ) -> Result<Self, RowError> {
        let expected = u8::try_from(definition.columns().len()).map_err(|_| {
            RowError::ColumnCountNotRepresentable {
                count: definition.columns().len(),
            }
        })?;
        let column_count = *row.first().ok_or(RowError::RowTooShort {
            length: 0,
            minimum: 1,
        })?;
        // EXP-0257 admits old rows after appending variable columns only.
        if column_count > expected
            || definition.columns()[usize::from(column_count)..]
                .iter()
                .any(|c| !matches!(c.storage(), ColumnStorageClass::Variable { .. }))
        {
            return Err(RowError::ColumnCountMismatch {
                expected,
                actual: column_count,
            });
        }
        let columns = &definition.columns()[..usize::from(column_count)];
        let null_len = usize::from(column_count).div_ceil(8);
        let minimum = 1 + null_len;
        if row.len() < minimum {
            return Err(RowError::RowTooShort {
                length: row.len(),
                minimum,
            });
        }
        budget
            .charge_items(u64::from(expected))
            .map_err(RowError::Resource)?;
        let fixed_boundary = 1 + columns
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
        let variable_count = columns
            .iter()
            .filter(|c| matches!(c.storage(), ColumnStorageClass::Variable { .. }))
            .count() as u8;
        let null_start = row.len() - null_len;
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
                column_count,
                variable_count,
                jumps: 0,
            });
        }
        let count_position = null_start.checked_sub(1).ok_or(RowError::RowTooShort {
            length: row.len(),
            minimum: minimum + 1,
        })?;
        if row[count_position] != variable_count {
            return Err(RowError::VariableCountMismatch {
                expected: variable_count,
                actual: row[count_position],
            });
        }
        let jumps = (row.len() - 1) / 256;
        if crate::row_offsets::jump_count(row.len() - jumps) != jumps {
            return Err(RowError::UnsupportedWideVariableOffsets {
                variable_count,
                row_length: row.len(),
            });
        }
        let trailer = usize::from(variable_count) + 1 + jumps;
        let offsets_start = count_position
            .checked_sub(trailer)
            .ok_or(RowError::RowTooShort {
                length: row.len(),
                minimum: minimum + 1 + trailer,
            })?;
        budget
            .charge_work_units((u64::from(variable_count) + 1 + 2 * jumps as u64) * jumps as u64)
            .map_err(RowError::Resource)?;
        let layout = Self {
            fixed_boundary,
            offsets_start,
            null_start,
            column_count,
            variable_count,
            jumps,
        };
        // EXP-0258: ordinal 255 shares the unused-jump marker. The trailer
        // independently locates the final boundary; its low byte must agree.
        if variable_count == u8::MAX && usize::from(row[offsets_start]) != offsets_start % 256 {
            return Err(RowError::InvalidVariableBounds {
                index: u16::from(variable_count - 1),
                start: usize::from(row[offsets_start]),
                end: offsets_start,
                data_end: offsets_start,
            });
        }
        let actual_fixed = layout.boundary(row, 0);
        if actual_fixed != fixed_boundary {
            return Err(RowError::InvalidFixedBoundary {
                expected: fixed_boundary,
                actual: actual_fixed,
            });
        }
        let mut start = actual_fixed;
        for index in 0..variable_count {
            let end = layout.boundary(row, index + 1);
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
                index: u16::from(variable_count - 1),
                start,
                end: offsets_start,
                data_end: offsets_start,
            });
        }
        for (position, &jump) in layout.jump_bytes(row).iter().enumerate() {
            let threshold = 256 * (jumps - position);
            let valid = if jump == 0xff {
                offsets_start < threshold
                    || (variable_count == u8::MAX && layout.boundary(row, u8::MAX - 1) < threshold)
            } else {
                jump <= variable_count
                    && layout.boundary(row, jump) >= threshold
                    && (jump == 0 || layout.boundary(row, jump - 1) < threshold)
            };
            if !valid {
                return Err(RowError::UnsupportedWideVariableOffsets {
                    variable_count,
                    row_length: row.len(),
                });
            }
        }
        Ok(layout)
    }

    pub(super) fn present(self, row: &[u8], ordinal: ColumnOrdinal) -> bool {
        let bit = usize::from(ordinal.get());
        bit < usize::from(self.column_count)
            && row
                .get(self.null_start + bit / 8)
                .is_some_and(|raw| raw & (1 << (bit % 8)) != 0)
    }

    pub(super) fn variable_range(self, row: &[u8], index: u16) -> Option<Range<usize>> {
        let index = u8::try_from(index).ok()?;
        if index >= self.variable_count {
            return None;
        }
        Some(self.boundary(row, index)..self.boundary(row, index + 1))
    }

    fn jump_bytes(self, row: &[u8]) -> &[u8] {
        let start = self.offsets_start + usize::from(self.variable_count) + 1;
        &row[start..start + self.jumps]
    }

    fn boundary(self, row: &[u8], ordinal: u8) -> usize {
        if ordinal == u8::MAX {
            return self.offsets_start;
        }
        let low = usize::from(row[self.offsets_start + usize::from(self.variable_count - ordinal)]);
        let high = self
            .jump_bytes(row)
            .iter()
            .filter(|&&jump| jump != 0xff && ordinal >= jump)
            .count();
        low + 256 * high
    }
}
