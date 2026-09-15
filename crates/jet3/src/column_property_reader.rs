//! Checked named Boolean properties from EXP-0208/0266.
use crate::{BinaryCursor, ByteCount, ColumnDefinition, ResourceBudget, UpdateError};

fn require(valid: bool, detail: &'static str) -> Result<(), UpdateError> {
    if valid {
        Ok(())
    } else {
        Err(UpdateError::Mismatch(detail))
    }
}

fn end(cursor: &mut BinaryCursor<'_, '_>, maximum: u64, minimum: u32) -> Result<u64, UpdateError> {
    let start = cursor.position().get();
    let length = cursor.read_u32_le()?;
    let end = start
        .checked_add(u64::from(length))
        .ok_or(UpdateError::Mismatch("property block length overflow"))?;
    require(length >= minimum && end <= maximum, "property block bounds")?;
    Ok(end)
}

pub(crate) fn decode(
    data: &[u8],
    columns: &[ColumnDefinition],
    budget: &mut ResourceBudget,
) -> Result<[bool; 255], UpdateError> {
    require(columns.len() <= 255, "property column count")?;
    budget.charge_work_units(
        (data.len() as u64)
            .checked_mul(columns.len() as u64 + 1)
            .ok_or(UpdateError::Mismatch("property work overflow"))?,
    )?;
    let mut cursor = BinaryCursor::new(data, budget.read_budget())?;
    require(
        cursor.read_exact(ByteCount::new(4))? == b"KKD\0",
        "property signature",
    )?;
    let dictionary_end = end(&mut cursor, data.len() as u64, 6)?;
    require(cursor.read_u16_le()? == 0x80, "property dictionary kind")?;
    let mut zero_length = None;
    let mut names = 0_u32;
    while cursor.position().get() < dictionary_end {
        let length = cursor.read_u16_le()?;
        let name = cursor.read_exact(ByteCount::new(u64::from(length)))?;
        require(
            cursor.position().get() <= dictionary_end
                && length != 0
                && names <= u32::from(u16::MAX),
            "property dictionary name bounds",
        )?;
        if name == b"AllowZeroLength" {
            require(
                zero_length.is_none(),
                "duplicate AllowZeroLength property name",
            )?;
            zero_length = Some(names as u16);
        }
        names += 1;
    }
    let mut result = [false; 255];
    let mut seen = [false; 255];
    while cursor.position().get() < data.len() as u64 {
        let block_end = end(&mut cursor, data.len() as u64, 12)?;
        require(
            cursor.read_u16_le()? == 1,
            "unsupported named property block",
        )?;
        let nested_length = cursor.read_u32_le()?;
        let length = cursor.read_u16_le()?;
        require(
            nested_length == u32::from(length) + 6,
            "property field-name framing",
        )?;
        let name = cursor.read_exact(ByteCount::new(u64::from(length)))?;
        require(
            cursor.position().get() <= block_end && length != 0,
            "property field-name bounds",
        )?;
        let column = columns
            .iter()
            .position(|column| column.name().raw_bytes() == name);
        if let Some(column) = column {
            require(!seen[column], "duplicate field property block")?;
            seen[column] = true;
        }
        let mut seen_zero = false;
        while cursor.position().get() < block_end {
            let start = cursor.position().get();
            let length = cursor.read_u16_le()?;
            require(
                length >= 8 && start + u64::from(length) <= block_end,
                "property record bounds",
            )?;
            let bytes = cursor.read_exact(ByteCount::new(u64::from(length - 2)))?;
            // The record prefix names its dictionary entry; unrequested values stay opaque.
            let ordinal = u16::from_le_bytes([bytes[2], bytes[3]]);
            require(u32::from(ordinal) < names, "property dictionary reference")?;
            if Some(ordinal) == zero_length {
                require(
                    !seen_zero
                        && length == 9
                        && bytes[..2] == [1, 1]
                        && bytes[4..6] == [1, 0]
                        && matches!(bytes[6], 0 | 0xff),
                    "AllowZeroLength Boolean record",
                )?;
                seen_zero = true;
                if let Some(column) = column {
                    require(
                        crate::column_properties::has_zero_length_property(
                            columns[column].physical_type(),
                        ),
                        "AllowZeroLength column type",
                    )?;
                    result[column] = bytes[6] != 0;
                }
            }
        }
    }
    Ok(result)
}
