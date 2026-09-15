//! Bounded numeric records: EXP-0126/0150 components, EXP-0148 null policies,
//! and EXP-0062 three-byte page plus one-byte slot locators.

use crate::numeric_index_key::{MAX_COMPONENT_BYTES, NumericKeyType};
use crate::{
    Error, IndexDirection, IndexNullPolicy, PageNumber, ResourceBudget, RowLocator, RowValue,
};

pub(crate) const MAX_FIELDS: usize = 2;
const LOCATOR_BYTES: usize = 4;
pub(crate) const ENTRY_CAPACITY: usize = MAX_FIELDS * MAX_COMPONENT_BYTES + LOCATOR_BYTES;

#[derive(Debug, Clone, Copy)]
pub(crate) struct NumericIndexField {
    pub(crate) column: usize,
    pub(crate) direction: IndexDirection,
    pub(crate) kind: NumericKeyType,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum EntryError {
    FieldCount { actual: usize },
    MissingColumn { column: usize },
    UnsupportedValue { column: usize, kind: NumericKeyType },
    NullRequired,
    Encoding(Error),
}

impl From<Error> for EntryError {
    fn from(error: Error) -> Self {
        Self::Encoding(error)
    }
}

impl std::fmt::Display for EntryError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "numeric index entry: {self:?}")
    }
}

impl std::error::Error for EntryError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct NumericIndexEntry {
    bytes: [u8; ENTRY_CAPACITY],
    key_len: usize,
    has_null: bool,
}

/// Checks the schema shape independently of current rows, including old branch fences.
pub(crate) fn valid_key_shape(
    fields: &[NumericIndexField],
    null_policy: IndexNullPolicy,
    mut key: &[u8],
) -> bool {
    if !(1..=MAX_FIELDS).contains(&fields.len()) {
        return false;
    }
    let mut all_null = true;
    for field in fields {
        let Some(length) = key
            .first()
            .and_then(|marker| field.kind.encoded_length(*marker, field.direction))
        else {
            return false;
        };
        let Some((component, rest)) = key.split_at_checked(length) else {
            return false;
        };
        if length == 1 && null_policy == IndexNullPolicy::Required {
            return false;
        }
        if field.kind == NumericKeyType::Boolean && !matches!(component[1], 0 | 0xff) {
            return false;
        }
        all_null &= length == 1;
        key = rest;
    }
    key.is_empty() && !(all_null && null_policy == IndexNullPolicy::IgnoreAllNull)
}

impl NumericIndexEntry {
    /// Encodes one or two fields from a complete row. An all-null key is omitted
    /// only for IgnoreAllNull. Uniqueness and entry ordering belong to the caller.
    pub(crate) fn encode(
        fields: &[NumericIndexField],
        values: &[RowValue<'_>],
        null_policy: IndexNullPolicy,
        locator: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<Option<Self>, EntryError> {
        budget.charge_items(1)?;
        if !(1..=MAX_FIELDS).contains(&fields.len()) {
            return Err(EntryError::FieldCount {
                actual: fields.len(),
            });
        }
        let page = locator.page().get();
        if page > 0x00ff_ffff {
            return Err(Error::IntegerConversion {
                value: page as u128,
                target: "24-bit index row page",
            }
            .into());
        }
        let mut entry = Self {
            bytes: [0; ENTRY_CAPACITY],
            key_len: 0,
            has_null: false,
        };
        let mut all_null = true;
        for field in fields {
            let value = values.get(field.column).ok_or(EntryError::MissingColumn {
                column: field.column,
            })?;
            let null = matches!(value, RowValue::Null);
            entry.has_null |= null;
            all_null &= null;
            let mut component = [0; MAX_COMPONENT_BYTES];
            let length = field
                .kind
                .encode(*value, field.direction, &mut component)
                .ok_or(EntryError::UnsupportedValue {
                    column: field.column,
                    kind: field.kind,
                })?;
            entry.bytes[entry.key_len..entry.key_len + length]
                .copy_from_slice(&component[..length]);
            entry.key_len += length;
        }
        if entry.has_null && null_policy == IndexNullPolicy::Required {
            return Err(EntryError::NullRequired);
        }
        if all_null && null_policy == IndexNullPolicy::IgnoreAllNull {
            return Ok(None);
        }
        entry.bytes[entry.key_len..entry.key_len + 3]
            .copy_from_slice(&(page as u32).to_be_bytes()[1..]);
        entry.bytes[entry.key_len + LOCATOR_BYTES - 1] = locator.slot();
        Ok(Some(entry))
    }

    pub(crate) fn key(&self) -> &[u8] {
        &self.bytes[..self.key_len]
    }

    pub(crate) fn record(&self) -> &[u8] {
        &self.bytes[..self.key_len + LOCATOR_BYTES]
    }

    pub(crate) const fn has_null(&self) -> bool {
        self.has_null
    }

    pub(crate) fn locator(&self) -> RowLocator {
        let bytes = &self.bytes[self.key_len..];
        RowLocator::new(
            PageNumber::new(u64::from(u32::from_be_bytes([
                0, bytes[0], bytes[1], bytes[2],
            ]))),
            bytes[3],
        )
    }
}

#[cfg(test)]
#[path = "numeric_index_entry_tests.rs"]
mod tests;
