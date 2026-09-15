//! Bounded numeric records: EXP-0126/0150 components, EXP-0148 null policies,
//! and EXP-0062 three-byte page plus one-byte slot locators.

use crate::binary_index_key::MAX_KEY_BYTES;
use crate::numeric_index_key::{KeyPrefix, MAX_COMPONENT_BYTES, NumericKeyType};
use crate::{
    ByteCount, Error, IndexDirection, IndexNullPolicy, PageNumber, ResourceBudget, RowLocator,
    RowValue,
};

// EXP-0059 ten physical key slots; EXP-0252 native composite boundary.
pub(crate) const MAX_FIELDS: usize = crate::column_definition_writer::KEY_SLOT_COUNT;
const LOCATOR_BYTES: usize = 4;
const INLINE_BYTES: usize = 22;
pub(crate) const ENTRY_CAPACITY: usize = MAX_KEY_BYTES + LOCATOR_BYTES;

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

#[derive(Debug, Clone, PartialEq, Eq)]
enum RecordBytes {
    Inline([u8; INLINE_BYTES]),
    Wide(Vec<u8>),
}

impl RecordBytes {
    fn new(raw: &[u8], budget: &mut ResourceBudget) -> Result<Self, Error> {
        if raw.len() <= INLINE_BYTES {
            let mut bytes = [0; INLINE_BYTES];
            bytes[..raw.len()].copy_from_slice(raw);
            return Ok(Self::Inline(bytes));
        }
        budget.charge_allocation(ByteCount::new(raw.len() as u64))?;
        let mut bytes = Vec::new();
        bytes.try_reserve_exact(raw.len()).map_err(|_| Error::Io {
            operation: "reserve wide index record",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
        bytes.extend_from_slice(raw);
        Ok(Self::Wide(bytes))
    }

    fn bytes(&self) -> &[u8] {
        match self {
            Self::Inline(bytes) => bytes,
            Self::Wide(bytes) => bytes,
        }
    }
}

pub(crate) fn record_capacity(fields: &[NumericIndexField]) -> usize {
    fields
        .iter()
        .fold(0_usize, |total, field| {
            total.saturating_add(field.kind.maximum_length())
        })
        .min(MAX_KEY_BYTES)
        + LOCATOR_BYTES
}

pub(crate) fn sort_cost(fields: &[NumericIndexField]) -> u64 {
    (4 * record_capacity(fields) + size_of::<NumericIndexEntry>()) as u64
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct NumericIndexEntry {
    bytes: RecordBytes,
    key_len: usize,
    has_null: bool,
}

/// Checks the schema shape independently of current rows, including old branch fences.
pub(crate) fn valid_key_shape(
    fields: &[NumericIndexField],
    null_policy: IndexNullPolicy,
    key: &[u8],
) -> bool {
    if !(1..=MAX_FIELDS).contains(&fields.len()) || key.len() > MAX_KEY_BYTES {
        return false;
    }
    key_shape(fields, null_policy, key, false)
        || (key.len() == MAX_KEY_BYTES
            && key_shape(fields, null_policy, &key[..MAX_KEY_BYTES - 2], true))
}

fn key_shape(
    fields: &[NumericIndexField],
    null_policy: IndexNullPolicy,
    mut key: &[u8],
    shortened: bool,
) -> bool {
    let mut consumed = 0;
    let mut all_null = true;
    for (ordinal, field) in fields.iter().enumerate() {
        let Some(prefix) = field.kind.prefix(key, field.direction) else {
            return false;
        };
        let length = match prefix {
            KeyPrefix::Complete(length) => length,
            KeyPrefix::Partial { maximum } => {
                let remaining: usize = fields[ordinal + 1..]
                    .iter()
                    .map(|field| field.kind.maximum_length())
                    .sum();
                return shortened && consumed + maximum + remaining > MAX_KEY_BYTES;
            }
        };
        let Some((_, rest)) = key.split_at_checked(length) else {
            return false;
        };
        if length == 1 && null_policy == IndexNullPolicy::Required {
            return false;
        }
        all_null &= length == 1;
        consumed += length;
        key = rest;
    }
    !shortened && key.is_empty() && !(all_null && null_policy == IndexNullPolicy::IgnoreAllNull)
}

impl NumericIndexEntry {
    /// Encodes up to ten fields from a complete row. An all-null key is omitted
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
        let mut key_len = 0;
        let mut has_null = false;
        let mut all_null = true;
        let mut raw_key = [0; MAX_FIELDS * MAX_COMPONENT_BYTES];
        for field in fields {
            let value = values.get(field.column).ok_or(EntryError::MissingColumn {
                column: field.column,
            })?;
            let null = matches!(value, RowValue::Null)
                || (matches!(field.kind, NumericKeyType::Binary { .. })
                    && matches!(value, RowValue::Binary([])));
            has_null |= null;
            all_null &= null;
            let mut component = [0; MAX_COMPONENT_BYTES];
            let length = field
                .kind
                .encode(*value, field.direction, &mut component)
                .ok_or(EntryError::UnsupportedValue {
                    column: field.column,
                    kind: field.kind,
                })?;
            raw_key[key_len..key_len + length].copy_from_slice(&component[..length]);
            key_len += length;
        }
        if has_null && null_policy == IndexNullPolicy::Required {
            return Err(EntryError::NullRequired);
        }
        if all_null && null_policy == IndexNullPolicy::IgnoreAllNull {
            return Ok(None);
        }
        budget.charge_work_units(key_len as u64 * 9)?;
        key_len = crate::binary_index_key::shorten(&mut raw_key[..key_len]);
        raw_key[key_len..key_len + 3].copy_from_slice(&(page as u32).to_be_bytes()[1..]);
        raw_key[key_len + LOCATOR_BYTES - 1] = locator.slot();
        let bytes = RecordBytes::new(&raw_key[..key_len + LOCATOR_BYTES], budget)?;
        Ok(Some(Self {
            bytes,
            key_len,
            has_null,
        }))
    }

    pub(crate) fn key(&self) -> &[u8] {
        &self.bytes.bytes()[..self.key_len]
    }

    pub(crate) fn record(&self) -> &[u8] {
        &self.bytes.bytes()[..self.key_len + LOCATOR_BYTES]
    }

    pub(crate) const fn has_null(&self) -> bool {
        self.has_null
    }

    pub(crate) fn locator(&self) -> RowLocator {
        let bytes = &self.bytes.bytes()[self.key_len..];
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
