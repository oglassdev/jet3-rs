//! Allocation-free row ordering for protocol 1.2 models.

use super::{SemanticProtocolError, SemanticTable, invalid, sha256};
use crate::PropertyMap;

pub(super) fn canonicalize_rows(table: &mut SemanticTable) -> Result<(), SemanticProtocolError> {
    for (acquisition_order, row) in table.rows.iter_mut().enumerate() {
        if row.values.len() != table.columns.len()
            || table
                .columns
                .iter()
                .any(|column| row.values.get(&column.name).is_none())
        {
            return Err(invalid(
                "$.tables[].rows[].values",
                "keys must equal declared columns",
            ));
        }
        let bytes = canonical_row_bytes(&row.values)?;
        row.canonical_key = sha256(&bytes)?;
        row.duplicate_ordinal = u64::try_from(acquisition_order).map_err(|_| {
            invalid(
                "$.tables[].rows",
                "row acquisition order is not representable",
            )
        })?;
    }
    table.rows.sort_unstable_by(|left, right| {
        left.canonical_key
            .cmp(&right.canonical_key)
            .then_with(|| left.duplicate_ordinal.cmp(&right.duplicate_ordinal))
    });

    let mut previous_bytes = None;
    for index in 0..table.rows.len() {
        let bytes = canonical_row_bytes(&table.rows[index].values)?;
        let duplicate_ordinal = if index > 0
            && table.rows[index - 1].canonical_key == table.rows[index].canonical_key
        {
            if previous_bytes.as_ref() != Some(&bytes) {
                return Err(SemanticProtocolError::RowHashCollision {
                    table: table.name.clone(),
                });
            }
            table.rows[index - 1]
                .duplicate_ordinal
                .checked_add(1)
                .ok_or_else(|| {
                    invalid(
                        "$.tables[].rows[].duplicate_ordinal",
                        "duplicate ordinal overflowed",
                    )
                })?
        } else {
            0
        };
        table.rows[index].duplicate_ordinal = duplicate_ordinal;
        previous_bytes = Some(bytes);
    }
    Ok(())
}

pub(super) fn canonical_row_bytes(values: &PropertyMap) -> Result<Vec<u8>, SemanticProtocolError> {
    let mut bytes = crate::semantic_json::write_properties(values, "$.tables[].rows[].values")?;
    bytes.push(b'\n');
    Ok(bytes)
}
