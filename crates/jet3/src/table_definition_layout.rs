//! Shared validation of the row layout a table's columns imply (`EXP-0059`).
//!
//! The table-definition encoder and the table planner both have to accept
//! exactly the same schemas, so both run these checks rather than each
//! carrying its own copy of the rules.

use crate::column_definition_writer::{
    COLUMN_RECORD_LEN, ColumnSpec, LOGICAL_RECORD_LEN, MAX_NAME_LEN, PHYSICAL_PREFIX_LEN,
    PHYSICAL_RECORD_LEN, SystemColumnClassSpec, resolve_column,
};
use crate::data_page_directory::MAX_STORED_ROW_LEN;
use crate::table_definition_writer::TableDefinitionWriteError;
use crate::{Error, TableDefinitionKind};

use crate::LONG_VALUE_MAP_GROUP_LEN;

/// `EXP-0060`: the one-byte row column count bounds a table's columns.
const MAX_COLUMN_COUNT: usize = u8::MAX as usize;
/// `EXP-0059`: fixed bytes before the first physical-index record.
const DEFINITION_HEADER_LEN: usize = 43;
/// `EXP-0059`: the two-byte end-of-definition marker.
const TERMINATOR_LEN: usize = 2;

/// Returns the exact logical length of the definition this shape encodes to.
///
/// The length depends only on the counts and name lengths, never on the pages
/// the definition names, so a planner can measure a definition before it has
/// assigned any.
pub(crate) fn definition_len<'a>(
    columns: &[ColumnSpec<'_>],
    index_names: impl ExactSizeIterator<Item = &'a [u8]>,
    physical_index_count: usize,
    long_value_map_count: usize,
) -> Result<usize, TableDefinitionWriteError> {
    if columns.len() > MAX_COLUMN_COUNT {
        return Err(TableDefinitionWriteError::TooManyColumns {
            count: columns.len(),
            maximum: MAX_COLUMN_COUNT,
        });
    }
    for (role, count) in [
        ("physical", physical_index_count),
        ("logical", index_names.len()),
    ] {
        if u16::try_from(count).is_err() {
            return Err(TableDefinitionWriteError::TooManyIndexes { role, count });
        }
    }
    let too_long = || TableDefinitionWriteError::DefinitionTooLong { length: usize::MAX };
    let mut total = DEFINITION_HEADER_LEN;
    let mut add = |amount: usize| -> Result<(), TableDefinitionWriteError> {
        total = total.checked_add(amount).ok_or_else(too_long)?;
        Ok(())
    };
    add(physical_index_count
        .checked_mul(PHYSICAL_PREFIX_LEN + PHYSICAL_RECORD_LEN)
        .ok_or_else(too_long)?)?;
    add(columns
        .len()
        .checked_mul(COLUMN_RECORD_LEN)
        .ok_or_else(too_long)?)?;
    for column in columns {
        add(1)?;
        add(column.name().len())?;
    }
    add(index_names
        .len()
        .checked_mul(LOGICAL_RECORD_LEN)
        .ok_or_else(too_long)?)?;
    for name in index_names {
        add(1)?;
        add(name.len())?;
    }
    add(long_value_map_count
        .checked_mul(LONG_VALUE_MAP_GROUP_LEN)
        .ok_or_else(too_long)?)?;
    add(TERMINATOR_LEN)?;
    if u32::try_from(total).is_err() {
        return Err(TableDefinitionWriteError::DefinitionTooLong { length: total });
    }
    Ok(total)
}

/// Validates every column name and class and checks that the row layout they
/// imply still admits an all-null row in one data-page row slot.
///
/// Returns the number of variable-storage columns the layout carries.
pub(crate) fn validate_column_layout(
    columns: &[ColumnSpec<'_>],
    kind: TableDefinitionKind,
    system_column_classes: &[SystemColumnClassSpec],
) -> Result<u16, TableDefinitionWriteError> {
    if columns.len() > MAX_COLUMN_COUNT {
        return Err(TableDefinitionWriteError::TooManyColumns {
            count: columns.len(),
            maximum: MAX_COLUMN_COUNT,
        });
    }
    let mut next_fixed_offset = 0_u16;
    let mut variables = 0_u16;
    for (ordinal, column) in (0_u16..).zip(columns) {
        validate_name(
            "column",
            ordinal,
            column.name(),
            columns[..usize::from(ordinal)].iter().map(ColumnSpec::name),
        )?;
        resolve_column(
            ordinal,
            column,
            kind,
            system_column_classes.get(usize::from(ordinal)).copied(),
            &mut next_fixed_offset,
            &mut variables,
        )?;
    }
    let variable_trailer = if variables == 0 {
        0
    } else {
        usize::from(variables)
            .checked_add(2)
            .ok_or(TableDefinitionWriteError::Resource(Error::Arithmetic {
                operation: "size minimum encoded-row variable trailer",
            }))?
    };
    let mut minimum_row_len = 1_usize
        .checked_add(usize::from(next_fixed_offset))
        .and_then(|value| value.checked_add(columns.len().div_ceil(8)))
        .and_then(|value| value.checked_add(variable_trailer))
        .ok_or(TableDefinitionWriteError::Resource(Error::Arithmetic {
            operation: "size minimum encoded row",
        }))?;
    if variables > 0 && minimum_row_len > u8::MAX as usize {
        minimum_row_len =
            minimum_row_len
                .checked_add(1)
                .ok_or(TableDefinitionWriteError::Resource(Error::Arithmetic {
                    operation: "size minimum encoded-row jump table",
                }))?;
    }
    if minimum_row_len > MAX_STORED_ROW_LEN {
        return Err(TableDefinitionWriteError::RowLayoutTooLarge {
            minimum: minimum_row_len,
            maximum: MAX_STORED_ROW_LEN,
        });
    }
    Ok(variables)
}

pub(crate) fn validate_name<'a>(
    role: &'static str,
    ordinal: u16,
    name: &[u8],
    earlier: impl Iterator<Item = &'a [u8]>,
) -> Result<(), TableDefinitionWriteError> {
    if name.is_empty() {
        return Err(TableDefinitionWriteError::EmptyName { role, ordinal });
    }
    if name.len() > MAX_NAME_LEN {
        return Err(TableDefinitionWriteError::NameTooLong {
            role,
            ordinal,
            length: name.len(),
            maximum: MAX_NAME_LEN,
        });
    }
    let mut earlier = earlier;
    if earlier.any(|other| other == name) {
        return Err(TableDefinitionWriteError::DuplicateName { role, ordinal });
    }
    Ok(())
}
