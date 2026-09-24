//! Checked dense and retained storage layouts from EXP-0060/0297.

use super::{
    ColumnPhysicalType, ColumnStorageClass, Error, MAX_COLUMN_COUNT, ResourceBudget,
    RowColumnLayout, RowShape, RowValue, RowWriteError, value_width,
};

pub(super) fn validate(
    columns: &[RowColumnLayout],
    values: &[RowValue<'_>],
    budget: &mut ResourceBudget,
) -> Result<RowShape, RowWriteError> {
    if columns.len() > MAX_COLUMN_COUNT {
        return Err(RowWriteError::TooManyColumns {
            count: columns.len(),
            maximum: MAX_COLUMN_COUNT,
        });
    }
    if values.len() != columns.len() {
        return Err(RowWriteError::ValueCountMismatch {
            expected: columns.len(),
            actual: values.len(),
        });
    }
    budget
        .charge_items(columns.len() as u64)
        .map_err(RowWriteError::Resource)?;
    let retained = columns
        .iter()
        .all(|column| column.storage_ordinal.is_some());
    let mut column_count = 0;
    let mut next_fixed_offset = 0_u16;
    let mut variable_count = 0_usize;
    let mut variable_bytes = 0_usize;
    let mut seen_indexes = [false; MAX_COLUMN_COUNT];
    for (ordinal, (column, value)) in (0_u16..).zip(columns.iter().zip(values)) {
        let storage_ordinal = column.storage_ordinal.unwrap_or(ordinal);
        if usize::from(storage_ordinal) >= MAX_COLUMN_COUNT
            || usize::from(storage_ordinal) < column_count
        {
            return Err(RowWriteError::InvalidStorageOrdinal {
                ordinal,
                storage_ordinal,
            });
        }
        column_count = usize::from(storage_ordinal) + 1;
        if let ColumnStorageClass::Fixed { offset } = column.storage
            && column.physical_type != ColumnPhysicalType::Boolean
            && column.storage_ordinal.is_some()
        {
            budget
                .charge_work_units(u64::from(ordinal))
                .map_err(RowWriteError::Resource)?;
            for previous in &columns[..usize::from(ordinal)] {
                if let ColumnStorageClass::Fixed {
                    offset: previous_offset,
                } = previous.storage
                    && previous.physical_type != ColumnPhysicalType::Boolean
                    && u32::from(offset) < u32::from(previous_offset) + u32::from(previous.size)
                    && u32::from(previous_offset) < u32::from(offset) + u32::from(column.size)
                {
                    return Err(RowWriteError::InvalidFixedOffset {
                        ordinal,
                        offset,
                        expected: next_fixed_offset,
                    });
                }
            }
        }
        validate_column_layout(ordinal, *column, &mut next_fixed_offset)?;
        let width = value_width(ordinal, *column, *value)?;
        match column.storage {
            ColumnStorageClass::Fixed { .. } => {}
            ColumnStorageClass::Variable { index } => {
                let slot = seen_indexes
                    .get_mut(usize::from(index))
                    .filter(|seen| !**seen);
                let Some(slot) = slot else {
                    return Err(RowWriteError::InvalidVariableIndex {
                        ordinal,
                        index,
                        variable_count: columns.len(),
                    });
                };
                *slot = true;
                variable_count = if retained {
                    variable_count.max(usize::from(index) + 1)
                } else {
                    variable_count + 1
                };
                variable_bytes =
                    variable_bytes
                        .checked_add(width)
                        .ok_or(RowWriteError::Resource(Error::Arithmetic {
                            operation: "sum encoded-row variable bytes",
                        }))?;
            }
        }
    }
    // EXP-0306: fixed-only rows retain a two-byte minimum fixed area.
    let fixed_size = usize::from(next_fixed_offset).max(if variable_count == 0 { 2 } else { 0 });
    // Indexes are unique, so any index at or beyond the count leaves a hole.
    for (ordinal, column) in (0_u16..).zip(columns) {
        if let ColumnStorageClass::Variable { index } = column.storage
            && usize::from(index) >= variable_count
        {
            return Err(RowWriteError::InvalidVariableIndex {
                ordinal,
                index,
                variable_count,
            });
        }
    }
    let null_len = column_count.div_ceil(8);
    let mut length = 1_usize
        .checked_add(fixed_size)
        .and_then(|value| value.checked_add(variable_bytes))
        .and_then(|value| value.checked_add(null_len))
        .ok_or(RowWriteError::Resource(Error::Arithmetic {
            operation: "size encoded row",
        }))?;
    if variable_count > 0 {
        length = length
            .checked_add(variable_count)
            .and_then(|value| value.checked_add(2))
            .ok_or(RowWriteError::Resource(Error::Arithmetic {
                operation: "size encoded-row variable trailer",
            }))?;
    }
    let jumps = if variable_count == 0 {
        0
    } else {
        crate::row_offsets::jump_count(length)
    };
    length = length
        .checked_add(jumps)
        .ok_or(RowWriteError::Resource(Error::Arithmetic {
            operation: "size encoded-row jump bytes",
        }))?;
    let maximum = crate::row_offsets::maximum_length(variable_count);
    if length > maximum {
        return Err(RowWriteError::RowTooLong { length, maximum });
    }
    Ok(RowShape {
        column_count,
        fixed_size,
        variable_count,
        null_len,
        jumps,
        length,
    })
}

/// Validates the schema invariants established for column definitions.
pub(super) fn validate_column_layout(
    ordinal: u16,
    column: RowColumnLayout,
    next_fixed_offset: &mut u16,
) -> Result<(), RowWriteError> {
    let valid_size = match column.physical_type {
        ColumnPhysicalType::Boolean | ColumnPhysicalType::Byte => column.size == 1,
        ColumnPhysicalType::Integer => column.size == 2,
        ColumnPhysicalType::Long | ColumnPhysicalType::Single => column.size == 4,
        ColumnPhysicalType::Currency
        | ColumnPhysicalType::Double
        | ColumnPhysicalType::DateTime => column.size == 8,
        ColumnPhysicalType::Guid => column.size == 16,
        ColumnPhysicalType::Binary | ColumnPhysicalType::Text => (1..=255).contains(&column.size),
        ColumnPhysicalType::LongBinary | ColumnPhysicalType::Memo => column.size == 0,
    };
    if !valid_size {
        return Err(RowWriteError::InvalidColumnSize {
            ordinal,
            physical_type: column.physical_type,
            size: column.size,
        });
    }

    let storage_error = || RowWriteError::InvalidStorage {
        ordinal,
        physical_type: column.physical_type,
    };
    match column.storage {
        ColumnStorageClass::Fixed { offset } => {
            if matches!(
                column.physical_type,
                ColumnPhysicalType::Binary
                    | ColumnPhysicalType::LongBinary
                    | ColumnPhysicalType::Memo
            ) {
                return Err(storage_error());
            }
            // EXP-0198: DAO Boolean records can use zero instead of the current
            // fixed offset; the value occupies only its presence bit.
            let boolean_placeholder =
                column.physical_type == ColumnPhysicalType::Boolean && offset == 0;
            if column.storage_ordinal.is_none()
                && offset != *next_fixed_offset
                && !boolean_placeholder
            {
                return Err(RowWriteError::InvalidFixedOffset {
                    ordinal,
                    offset,
                    expected: *next_fixed_offset,
                });
            }
            if column.physical_type != ColumnPhysicalType::Boolean {
                *next_fixed_offset =
                    (*next_fixed_offset).max(offset.checked_add(column.size).ok_or(
                        RowWriteError::Resource(Error::Arithmetic {
                            operation: "advance encoded-row fixed offset",
                        }),
                    )?);
            }
        }
        ColumnStorageClass::Variable { .. } => {
            if !matches!(
                column.physical_type,
                ColumnPhysicalType::Binary
                    | ColumnPhysicalType::Text
                    | ColumnPhysicalType::LongBinary
                    | ColumnPhysicalType::Memo
            ) {
                return Err(storage_error());
            }
        }
    }
    Ok(())
}
