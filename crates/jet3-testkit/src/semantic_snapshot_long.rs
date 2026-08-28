//! Comparison-excluded physical metadata for external long values.

use std::fmt::Write as _;

use jet3::ResourceBudget;

use super::SemanticSnapshotError;
use super::retained::{RetainedLedger, RetainedPropertyBatch};
use crate::{SemanticProtocolError, SemanticRow, SemanticTable, TypedValue};

pub(super) struct CollectedSemanticRow {
    pub(super) row: SemanticRow,
    pub(super) canonical_bytes: Vec<u8>,
    pub(super) headers: Vec<(usize, [u8; 12])>,
    pub(super) acquisition_order: usize,
}

pub(super) struct PendingLongValueHeader {
    pub(super) table_index: usize,
    pub(super) column_index: usize,
    pub(super) row_index: usize,
    pub(super) raw_header: [u8; 12],
}

pub(super) fn canonicalize_collected_rows(
    table_name: &str,
    table_index: usize,
    mut collected: Vec<CollectedSemanticRow>,
    pending: &mut Vec<PendingLongValueHeader>,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
) -> Result<Vec<SemanticRow>, SemanticSnapshotError> {
    let work = canonical_row_order_work(collected.len())?;
    budget
        .charge_work_units(work)
        .map_err(SemanticSnapshotError::Resource)?;
    collected.sort_unstable_by(|left, right| {
        left.row
            .canonical_key
            .cmp(&right.row.canonical_key)
            .then_with(|| left.canonical_bytes.cmp(&right.canonical_bytes))
            .then_with(|| left.acquisition_order.cmp(&right.acquisition_order))
    });

    for index in 0..collected.len() {
        let duplicate_ordinal = if index == 0
            || collected[index - 1].row.canonical_key != collected[index].row.canonical_key
        {
            0
        } else {
            if collected[index - 1].canonical_bytes != collected[index].canonical_bytes {
                return Err(SemanticProtocolError::RowHashCollision {
                    table: table_name.to_owned(),
                }
                .into());
            }
            collected[index - 1]
                .row
                .duplicate_ordinal
                .checked_add(1)
                .ok_or(SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
                    operation: "assign semantic row duplicate ordinal",
                }))?
        };
        collected[index].row.duplicate_ordinal = duplicate_ordinal;
    }

    let mut rows = Vec::new();
    ledger.reserve_vec(budget, &mut rows, collected.len())?;
    for (row_index, collected) in collected.into_iter().enumerate() {
        for (column_index, raw_header) in collected.headers {
            ledger.push(
                budget,
                pending,
                PendingLongValueHeader {
                    table_index,
                    column_index,
                    row_index,
                    raw_header,
                },
            )?;
        }
        rows.push(collected.row);
    }
    Ok(rows)
}

fn canonical_row_order_work(row_count: usize) -> Result<u64, SemanticSnapshotError> {
    let rows = u64::try_from(row_count).map_err(|_| {
        SemanticSnapshotError::Resource(jet3::Error::IntegerConversion {
            value: row_count as u128,
            target: "u64",
        })
    })?;
    let levels = if rows <= 1 {
        0
    } else {
        u64::from(u64::BITS - (rows - 1).leading_zeros())
    };
    // Account for the allocation-free canonical sort and the single
    // duplicate-ordinal/association pass.
    let passes = levels
        .checked_add(1)
        .ok_or(SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
            operation: "size semantic row ordering work",
        }))?;
    rows.checked_mul(passes)
        .ok_or(SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
            operation: "size semantic row ordering work",
        }))
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
    producer_extensions: &mut RetainedPropertyBatch,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
) -> Result<(), SemanticSnapshotError> {
    producer_extensions.reserve(budget, ledger, pending.len())?;
    let association_work = u64::try_from(pending.len()).map_err(|_| {
        SemanticSnapshotError::Resource(jet3::Error::IntegerConversion {
            value: pending.len() as u128,
            target: "u64",
        })
    })?;
    budget
        .charge_work_units(association_work)
        .map_err(SemanticSnapshotError::Resource)?;
    for header in pending {
        let table = tables
            .get(header.table_index)
            .ok_or_else(missing_association)?;
        table
            .rows
            .get(header.row_index)
            .ok_or_else(missing_association)?;
        let column = table
            .columns
            .get(header.column_index)
            .ok_or_else(missing_association)?;
        let escaped = escape_pointer_token(&column.name, budget, ledger)?;
        let path_capacity = 53_usize
            .checked_add(decimal_digits(header.table_index))
            .and_then(|length| length.checked_add(decimal_digits(header.row_index)))
            .and_then(|length| length.checked_add(escaped.len()))
            .ok_or(SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
                operation: "size long-value header extension path",
            }))?;
        let mut key = String::new();
        ledger.reserve_string(budget, &mut key, path_capacity)?;
        write!(
            key,
            "/tables/{}/rows/{}/values/{escaped}/jet_external_long_value_header",
            header.table_index, header.row_index
        )
        .map_err(|_| {
            SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
                operation: "format long-value header extension path",
            })
        })?;
        debug_assert_eq!(key.len(), path_capacity);
        let value = ledger.hex(budget, &header.raw_header)?;
        let raw_hex = Some(ledger.hex(budget, &header.raw_header)?);
        producer_extensions.push(budget, ledger, key, TypedValue::Binary { value, raw_hex })?;
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

    use super::{
        CollectedSemanticRow, PendingLongValueHeader, canonical_row_order_work,
        canonicalize_collected_rows, retain_long_value_headers,
    };
    use crate::semantic_snapshot::retained::{RetainedLedger, RetainedPropertyBatch};
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
            row_index: 1,
            raw_header: [0x5a; 12],
        }];
        let mut extensions = RetainedPropertyBatch::new();
        let mut budget = ResourceBudget::new(ResourceLimits::default());
        retain_long_value_headers(
            &tables,
            &pending,
            &mut extensions,
            &mut budget,
            &mut RetainedLedger::new(),
        )?;
        let extensions = extensions.finish(&mut budget)?;
        assert_eq!(
            extensions.get("/tables/0/rows/1/values/A~0~1/jet_external_long_value_header"),
            Some(&TypedValue::Binary {
                value: HexString::new("5a".repeat(12))?,
                raw_hex: Some(HexString::new("5a".repeat(12))?),
            })
        );
        Ok(())
    }

    #[test]
    fn canonical_row_pass_keeps_duplicate_headers_with_their_sorted_rows()
    -> Result<(), Box<dyn std::error::Error>> {
        let first_key = Sha256::new("11".repeat(32))?;
        let second_key = Sha256::new("22".repeat(32))?;
        let row = |canonical_key, marker, header, acquisition_order| CollectedSemanticRow {
            row: SemanticRow {
                canonical_key,
                duplicate_ordinal: 0,
                values: PropertyMap::from([(
                    "A".into(),
                    TypedValue::Integer {
                        value: i16::from(marker),
                        raw_hex: None,
                    },
                )]),
            },
            canonical_bytes: vec![marker],
            headers: vec![(0, [header; 12])],
            acquisition_order,
        };
        let collected = vec![
            row(second_key, 2, 0x22, 0),
            row(first_key.clone(), 1, 0x11, 2),
            row(first_key, 1, 0x10, 1),
        ];
        let mut pending = Vec::new();
        let mut budget = ResourceBudget::new(ResourceLimits::default());
        let rows = canonicalize_collected_rows(
            "Items",
            0,
            collected,
            &mut pending,
            &mut budget,
            &mut RetainedLedger::new(),
        )?;

        assert_eq!(
            rows.iter()
                .map(|row| row.duplicate_ordinal)
                .collect::<Vec<_>>(),
            [0, 1, 0]
        );
        assert_eq!(
            pending
                .iter()
                .map(|header| (header.row_index, header.raw_header[0]))
                .collect::<Vec<_>>(),
            [(0, 0x10), (1, 0x11), (2, 0x22)]
        );
        Ok(())
    }

    #[test]
    fn canonical_row_work_bound_grows_subquadratically() -> Result<(), Box<dyn std::error::Error>> {
        let small = canonical_row_order_work(4_096)?;
        let doubled = canonical_row_order_work(8_192)?;
        assert!(doubled < small.checked_mul(3).ok_or("work bound overflow")?);
        assert_eq!(canonical_row_order_work(0)?, 0);
        assert_eq!(canonical_row_order_work(1)?, 1);
        Ok(())
    }

    #[test]
    fn canonical_row_ordering_has_an_exact_total_work_boundary()
    -> Result<(), Box<dyn std::error::Error>> {
        let key = Sha256::new("11".repeat(32))?;
        let collected = || {
            (0..4)
                .map(|_| CollectedSemanticRow {
                    row: SemanticRow {
                        canonical_key: key.clone(),
                        duplicate_ordinal: 0,
                        values: PropertyMap::new(),
                    },
                    canonical_bytes: vec![0],
                    headers: Vec::new(),
                    acquisition_order: 0,
                })
                .collect()
        };
        let run = |limits| {
            let mut budget = ResourceBudget::new(limits);
            let result = canonicalize_collected_rows(
                "Items",
                0,
                collected(),
                &mut Vec::new(),
                &mut budget,
                &mut RetainedLedger::new(),
            );
            (result, budget.total_work_units())
        };

        let (_, required) = run(ResourceLimits::default());
        let (exact, charged) = run(ResourceLimits::default().with_max_total_work_units(required));
        assert!(exact.is_ok());
        assert_eq!(charged, required);
        let (one_below, _) = run(ResourceLimits::default()
            .with_max_total_work_units(required.checked_sub(1).ok_or("zero work")?));
        assert!(one_below.is_err());
        Ok(())
    }

    #[test]
    fn canonical_row_output_allocation_is_charged_before_growth()
    -> Result<(), Box<dyn std::error::Error>> {
        let collected = || {
            (0_u8..=127)
                .rev()
                .enumerate()
                .map(|(acquisition_order, marker)| {
                    Ok(CollectedSemanticRow {
                        row: SemanticRow {
                            canonical_key: Sha256::new(format!("{marker:02x}").repeat(32))?,
                            duplicate_ordinal: 0,
                            values: PropertyMap::new(),
                        },
                        canonical_bytes: vec![marker],
                        headers: Vec::new(),
                        acquisition_order,
                    })
                })
                .collect::<Result<Vec<_>, crate::SemanticProtocolError>>()
        };
        let run = |limits| -> Result<_, Box<dyn std::error::Error>> {
            let mut budget = ResourceBudget::new(limits);
            let result = canonicalize_collected_rows(
                "Items",
                0,
                collected()?,
                &mut Vec::new(),
                &mut budget,
                &mut RetainedLedger::new(),
            );
            Ok((result, budget.allocation_bytes()))
        };

        let (measured, exact) = run(ResourceLimits::default())?;
        measured?;
        let (accepted, charged) = run(ResourceLimits::default().with_max_allocation_bytes(exact))?;
        accepted?;
        assert_eq!(charged, exact);
        let one_below = exact.checked_sub(jet3::ByteCount::new(1))?;
        let (rejected, charged) =
            run(ResourceLimits::default().with_max_allocation_bytes(one_below))?;
        assert!(rejected.is_err());
        assert_eq!(charged, jet3::ByteCount::new(0));
        Ok(())
    }
}
