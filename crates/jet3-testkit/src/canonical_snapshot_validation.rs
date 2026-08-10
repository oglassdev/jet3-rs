//! Private validation and canonical-order helpers for semantic snapshots.

use std::collections::BTreeSet;

use super::{Row, SnapshotError};

pub(super) fn is_lower_hex_digit(byte: u8) -> bool {
    byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
}

pub(super) fn is_invariant_decimal(value: &str) -> bool {
    let unsigned = value.strip_prefix('-').unwrap_or(value);
    let (integer, fraction) = match unsigned.split_once('.') {
        Some((integer, fraction)) => (integer, Some(fraction)),
        None => (unsigned, None),
    };
    let integer_valid = integer == "0"
        || (!integer.starts_with('0')
            && !integer.is_empty()
            && integer.bytes().all(|byte| byte.is_ascii_digit()));
    integer_valid
        && fraction.is_none_or(|digits| {
            !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit())
        })
}

pub(super) fn is_invariant_datetime(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() < 19 {
        return false;
    }
    for separator in [(4, b'-'), (7, b'-'), (10, b'T'), (13, b':'), (16, b':')] {
        if bytes.get(separator.0) != Some(&separator.1) {
            return false;
        }
    }
    let fixed_digits = [0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18];
    if !fixed_digits
        .into_iter()
        .all(|index| bytes.get(index).is_some_and(u8::is_ascii_digit))
    {
        return false;
    }
    bytes.len() == 19
        || (bytes.get(19) == Some(&b'.')
            && bytes.len() > 20
            && bytes[20..].iter().all(u8::is_ascii_digit))
}

pub(super) fn ensure_named_order<'a>(
    values: impl Iterator<Item = &'a str>,
    path: &str,
) -> Result<(), SnapshotError> {
    let mut previous: Option<&str> = None;
    for value in values {
        if let Some(prior) = previous {
            match prior.cmp(value) {
                std::cmp::Ordering::Greater => {
                    return Err(SnapshotError::NonCanonicalOrder {
                        path: path.to_owned(),
                    });
                }
                std::cmp::Ordering::Equal => {
                    return Err(SnapshotError::Duplicate {
                        path: path.to_owned(),
                        value: value.to_owned(),
                    });
                }
                std::cmp::Ordering::Less => {}
            }
        }
        previous = Some(value);
    }
    Ok(())
}

pub(super) fn ensure_ordinal_order(
    values: impl Iterator<Item = u64>,
    path: &str,
) -> Result<(), SnapshotError> {
    let mut previous = None;
    for value in values {
        if let Some(prior) = previous {
            if prior > value {
                return Err(SnapshotError::NonCanonicalOrder {
                    path: path.to_owned(),
                });
            }
            if prior == value {
                return Err(SnapshotError::Duplicate {
                    path: path.to_owned(),
                    value: value.to_string(),
                });
            }
        }
        previous = Some(value);
    }
    Ok(())
}

pub(super) fn ensure_unique_names<'a>(
    values: impl Iterator<Item = &'a str>,
    path: &str,
) -> Result<(), SnapshotError> {
    let mut seen = BTreeSet::new();
    for value in values {
        if !seen.insert(value) {
            return Err(SnapshotError::Duplicate {
                path: path.to_owned(),
                value: value.to_owned(),
            });
        }
    }
    Ok(())
}

pub(super) fn canonicalize_rows(rows: &mut Vec<Row>) -> Result<(), SnapshotError> {
    let mut keyed = rows
        .drain(..)
        .map(|row| {
            let values = crate::canonical_json::write_properties(&row.values)?;
            Ok((row, values))
        })
        .collect::<Result<Vec<_>, SnapshotError>>()?;
    keyed.sort_by(|(left, left_values), (right, right_values)| {
        left.canonical_key
            .cmp(&right.canonical_key)
            .then_with(|| left_values.cmp(right_values))
    });
    rows.extend(keyed.into_iter().map(|(row, _)| row));
    Ok(())
}

pub(super) fn ensure_row_order(rows: &[Row], path: &str) -> Result<(), SnapshotError> {
    let mut previous: Option<(&str, Vec<u8>)> = None;
    for row in rows {
        let values = crate::canonical_json::write_properties(&row.values)?;
        if let Some((previous_key, previous_values)) = &previous
            && ((*previous_key)
                .cmp(row.canonical_key.as_str())
                .then_with(|| previous_values.cmp(&values)))
                == std::cmp::Ordering::Greater
        {
            return Err(SnapshotError::NonCanonicalOrder {
                path: path.to_owned(),
            });
        }
        previous = Some((&row.canonical_key, values));
    }
    Ok(())
}
