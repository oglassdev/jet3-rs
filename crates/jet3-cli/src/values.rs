//! Shared typed JSON cells, request reading, resource limits and mutation failure reporting.
use jet3::{ByteCount, ResourceBudget, ResourceLimits, RowValue, WriteError};
use serde::{Deserialize, de::DeserializeOwned};
use std::{
    ffi::{OsStr, OsString},
    fs::File,
};

pub(crate) fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

/// Optional `--max-allocation-bytes`, `--max-work-units`, `--max-chain-depth` and
/// `--max-encoded-bytes` overrides of the default limits for one write command.
#[derive(Debug, Default)]
pub(crate) struct Limits([Option<u64>; 4]);

impl Limits {
    pub(crate) fn parse(mut args: impl Iterator<Item = OsString>) -> Result<Self, &'static str> {
        let mut limits = Self::default();
        while let Some(option) = args.next() {
            let index = match option.to_str() {
                Some("--max-allocation-bytes") => 0,
                Some("--max-work-units") => 1,
                Some("--max-chain-depth") => 2,
                Some("--max-encoded-bytes") => 3,
                _ => return Err("unexpected_argument"),
            };
            let value = crate::parse_u64(args.next(), "missing_option_value", "invalid_limit")?;
            if limits.0[index].replace(value).is_some() {
                return Err("duplicate_option");
            }
        }
        Ok(limits)
    }

    pub(crate) fn budget(&self) -> ResourceBudget {
        let [allocation, work, depth, encoded] = self.0;
        let mut limits = ResourceLimits::default();
        if let Some(value) = allocation {
            limits = limits.with_max_allocation_bytes(ByteCount::new(value));
        }
        if let Some(value) = work {
            limits = limits.with_max_total_work_units(value);
        }
        if let Some(value) = depth {
            limits = limits.with_max_chain_depth(value);
        }
        if let Some(value) = encoded {
            limits = limits.with_max_encoded_bytes(ByteCount::new(value));
        }
        ResourceBudget::new(limits)
    }
}

pub(crate) fn read_request<T: DeserializeOwned>(input: &OsStr) -> Result<T, String> {
    if input == "-" {
        serde_json::from_reader(std::io::stdin().lock()).map_err(|e| e.to_string())
    } else {
        serde_json::from_reader(File::open(input).map_err(|e| format!("read request: {e}"))?)
            .map_err(|e| e.to_string())
    }
}

pub(crate) struct Failure {
    pub message: String,
    pub publication_stage: Option<String>,
}
impl From<String> for Failure {
    fn from(message: String) -> Self {
        Self {
            message,
            publication_stage: None,
        }
    }
}
impl From<WriteError> for Failure {
    fn from(error: WriteError) -> Self {
        let publication_stage = match &error {
            WriteError::Publish(error) => Some(format!("{:?}", error.stage())),
            _ => None,
        };
        Self {
            message: error.to_string(),
            publication_stage,
        }
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
    LongValue(Vec<u8>),
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
            Self::LongValue(v) => RowValue::LongValue(v),
        })
    }
}

pub(crate) fn ascii(text: &str) -> Result<&[u8], String> {
    if text.is_ascii() {
        Ok(text.as_bytes())
    } else {
        Err("text strings must be ASCII; use byte arrays for encoded text".into())
    }
}
