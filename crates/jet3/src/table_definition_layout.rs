//! Shared validation of the row layout a table's columns imply (`EXP-0059`).
//!
//! The table-definition encoder and the table planner both have to accept
//! exactly the same schemas, so both run these checks rather than each
//! carrying its own copy of the rules.

use crate::column_definition_writer::{
    ColumnSpec, MAX_NAME_LEN, SystemColumnClassSpec, resolve_column,
};
use crate::data_page_directory::MAX_STORED_ROW_LEN;
use crate::table_definition_writer::TableDefinitionWriteError;
use crate::{Error, TableDefinitionKind};

/// `EXP-0060`: the one-byte row column count bounds a table's columns.
const MAX_COLUMN_COUNT: usize = u8::MAX as usize;

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
