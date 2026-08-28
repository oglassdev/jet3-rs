//! Comparison-excluded physical metadata for external long values.

use std::fmt::Write as _;

use jet3::ResourceBudget;

use super::SemanticSnapshotError;
use super::retained::RetainedLedger;
use crate::{PropertyMap, SemanticProtocolError, SemanticTable, Sha256, TypedValue};

pub(super) struct PendingLongValueHeader {
    pub(super) table_index: usize,
    pub(super) column_index: usize,
    pub(super) row_key: Sha256,
    pub(super) duplicate_ordinal: u64,
    pub(super) raw_header: [u8; 12],
}

pub(super) fn append_hex(output: &mut String, bytes: &[u8]) {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    for byte in bytes {
        output.push(char::from(DIGITS[usize::from(byte >> 4)]));
        output.push(char::from(DIGITS[usize::from(byte & 0x0f)]));
    }
}

pub(super) fn retain_long_value_headers(
    tables: &[SemanticTable],
    pending: &[PendingLongValueHeader],
    producer_extensions: &mut PropertyMap,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
) -> Result<(), SemanticSnapshotError> {
    ledger.reserve_properties(budget, producer_extensions, pending.len())?;
    for header in pending {
        let table = tables
            .get(header.table_index)
            .ok_or_else(missing_association)?;
        let row_index = table
            .rows
            .iter()
            .position(|row| {
                row.canonical_key == header.row_key
                    && row.duplicate_ordinal == header.duplicate_ordinal
            })
            .ok_or_else(missing_association)?;
        let column = table
            .columns
            .get(header.column_index)
            .ok_or_else(missing_association)?;
        let escaped = escape_pointer_token(&column.name, budget, ledger)?;
        let path_capacity = 53_usize
            .checked_add(decimal_digits(header.table_index))
            .and_then(|length| length.checked_add(decimal_digits(row_index)))
            .and_then(|length| length.checked_add(escaped.len()))
            .ok_or(SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
                operation: "size long-value header extension path",
            }))?;
        let mut key = String::new();
        ledger.reserve_string(budget, &mut key, path_capacity)?;
        write!(
            key,
            "/tables/{}/rows/{row_index}/values/{escaped}/jet_external_long_value_header",
            header.table_index
        )
        .map_err(|_| {
            SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
                operation: "format long-value header extension path",
            })
        })?;
        debug_assert_eq!(key.len(), path_capacity);
        let value = ledger.hex(budget, &header.raw_header)?;
        let raw_hex = Some(ledger.hex(budget, &header.raw_header)?);
        ledger.insert(
            budget,
            producer_extensions,
            key,
            TypedValue::Binary { value, raw_hex },
        )?;
    }
    Ok(())
}

fn escape_pointer_token(
    value: &str,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
) -> Result<String, SemanticSnapshotError> {
    let capacity = value
        .len()
        .checked_mul(2)
        .ok_or(SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
            operation: "size escaped long-value column name",
        }))?;
    let mut escaped = String::new();
    ledger.reserve_string(budget, &mut escaped, capacity)?;
    for character in value.chars() {
        match character {
            '~' => escaped.push_str("~0"),
            '/' => escaped.push_str("~1"),
            other => escaped.push(other),
        }
    }
    Ok(escaped)
}

fn decimal_digits(mut value: usize) -> usize {
    let mut digits = 1;
    while value >= 10 {
        value /= 10;
        digits += 1;
    }
    digits
}

fn missing_association() -> SemanticSnapshotError {
    SemanticSnapshotError::Protocol(SemanticProtocolError::InvalidModel {
        path: "$.producer_extensions".to_owned(),
        reason: "external long-value metadata association is missing",
    })
}

#[cfg(test)]
mod tests {
    use jet3::{ResourceBudget, ResourceLimits};

    use super::{PendingLongValueHeader, retain_long_value_headers};
    use crate::semantic_snapshot::retained::RetainedLedger;
    use crate::{
        HexString, PropertyMap, SemanticColumn, SemanticRow, SemanticTable, Sha256, TableKind,
        TypedValue,
    };

    #[test]
    fn external_headers_follow_canonical_duplicate_rows_and_escape_column_tokens()
    -> Result<(), Box<dyn std::error::Error>> {
        let row_key = Sha256::new("11".repeat(32))?;
        let memo_raw = HexString::new("73616d65")?;
        let value = || TypedValue::Memo {
            value: "same".into(),
            raw_hex: Some(memo_raw.clone()),
            code_page: Some(1252),
        };
        let rows = [0, 1]
            .map(|duplicate_ordinal| SemanticRow {
                canonical_key: row_key.clone(),
                duplicate_ordinal,
                values: PropertyMap::from([("A~/".into(), value())]),
            })
            .into();
        let tables = [SemanticTable {
            name: "Items".into(),
            kind: TableKind::User,
            attributes: 0,
            columns: vec![SemanticColumn {
                name: "A~/".into(),
                ordinal: 0,
                dao_type: "dbMemo".into(),
                auto_increment: false,
                size: Some(0),
                attributes: 2,
                properties: PropertyMap::new(),
            }],
            indexes: Vec::new(),
            properties: PropertyMap::new(),
            rows,
        }];
        let pending = [PendingLongValueHeader {
            table_index: 0,
            column_index: 0,
            row_key,
            duplicate_ordinal: 1,
            raw_header: [0x5a; 12],
        }];
        let mut extensions = PropertyMap::new();
        let mut budget = ResourceBudget::new(ResourceLimits::default());
        retain_long_value_headers(
            &tables,
            &pending,
            &mut extensions,
            &mut budget,
            &mut RetainedLedger::new(),
        )?;
        assert_eq!(
            extensions.get("/tables/0/rows/1/values/A~0~1/jet_external_long_value_header"),
            Some(&TypedValue::Binary {
                value: HexString::new("5a".repeat(12))?,
                raw_hex: Some(HexString::new("5a".repeat(12))?),
            })
        );
        Ok(())
    }
}
