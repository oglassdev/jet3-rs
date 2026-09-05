//! Shared typed JSON cells for creation and mutation requests.
use jet3::{ResourceBudget, ResourceLimits, RowValue};
use serde::{Deserialize, de::DeserializeOwned};
use std::{ffi::OsStr, fs::File};

pub(crate) fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
pub(crate) fn read_request<T: DeserializeOwned>(input: &OsStr) -> Result<T, String> {
    if input == "-" {
        serde_json::from_reader(std::io::stdin().lock()).map_err(|e| e.to_string())
    } else {
        serde_json::from_reader(File::open(input).map_err(|e| format!("read request: {e}"))?)
            .map_err(|e| e.to_string())
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Cell {
    AutoIncrement,
    Boolean(bool),
    Byte(u8),
    Integer(i16),
    Long(i32),
    Currency(i64),
    Single(f32),
    Double(f64),
    DateTime(f64),
    Text(Text),
    Memo(Text),
    Binary(Vec<u8>),
    LongBinary(Vec<u8>),
    Guid([u8; 16]),
}

#[derive(Deserialize)]
#[serde(untagged)]
pub(crate) enum Text {
    Ascii(String),
    Bytes(Vec<u8>),
}

impl Text {
    fn bytes(&self) -> Result<&[u8], String> {
        match self {
            Self::Ascii(text) => ascii(text),
            Self::Bytes(bytes) => Ok(bytes),
        }
    }
}

impl Cell {
    pub(crate) fn value(&self) -> Result<RowValue<'_>, String> {
        Ok(match self {
            Self::AutoIncrement => RowValue::AutoIncrement,
            Self::Boolean(v) => RowValue::Boolean(*v),
            Self::Byte(v) => RowValue::Byte(*v),
            Self::Integer(v) => RowValue::Integer(*v),
            Self::Long(v) => RowValue::Long(*v),
            Self::Currency(v) => RowValue::Currency { scaled: *v },
            Self::Single(v) if !v.is_finite() => {
                return Err("single value exceeds finite range".into());
            }
            Self::Single(v) => RowValue::Single(*v),
            Self::Double(v) => RowValue::Double(*v),
            Self::DateTime(v) => RowValue::DateTime { days: *v },
            Self::Text(v) => RowValue::Text(v.bytes()?),
            Self::Memo(v) => RowValue::Memo(v.bytes()?),
            Self::Binary(v) => RowValue::Binary(v),
            Self::LongBinary(v) => RowValue::LongBinary(v),
            Self::Guid(v) => RowValue::Guid(*v),
        })
    }
}

pub(crate) fn ascii(text: &str) -> Result<&[u8], String> {
    if text.is_ascii() {
        Ok(text.as_bytes())
    } else {
        Err("names and text strings must be ASCII; use byte arrays for encoded text".into())
    }
}
