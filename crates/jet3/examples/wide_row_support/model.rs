//! Complete deterministic field models; payloads use CP1252 bytes or Binary bytes.
use super::*;
#[derive(Clone, Debug, PartialEq)]
pub(super) enum Scalar {
    Null,
    Long(i32),
    Binary(Vec<u8>),
    Text(Vec<u8>),
    Memo(Vec<u8>),
    Ole(Vec<u8>),
}
impl Scalar {
    pub(super) fn value(&self) -> RowValue<'_> {
        match self {
            Self::Null => RowValue::Null,
            Self::Long(n) => RowValue::Long(*n),
            Self::Binary(b) => RowValue::Binary(b),
            Self::Text(b) => RowValue::Text(b),
            Self::Memo(b) => RowValue::Memo(b),
            Self::Ole(b) => RowValue::LongBinary(b),
        }
    }
    pub(super) fn json(&self) -> String {
        match self {
            Self::Null => "null".into(),
            Self::Long(n) => n.to_string(),
            Self::Binary(b) | Self::Text(b) | Self::Memo(b) | Self::Ole(b) => quote(&hex(b)),
        }
    }
    pub(super) fn read(kind: &ValueKind<'_>) -> Result<Self> {
        Ok(match kind {
            ValueKind::Null => Self::Null,
            ValueKind::Long(n) => Self::Long(*n),
            ValueKind::Binary(b) => Self::Binary(b.to_vec()),
            ValueKind::Text(t) => Self::Text(t.raw_bytes().to_vec()),
            ValueKind::LongValue(LongValue::Inline {
                value: InlineLongValue::Text(t),
                ..
            }) => Self::Memo(t.raw_bytes().to_vec()),
            ValueKind::LongValue(LongValue::Inline {
                value: InlineLongValue::Binary(b),
                ..
            }) => Self::Ole(b.to_vec()),
            _ => return Err("unexpected field type".into()),
        })
    }
}
pub(super) type Row = Vec<Scalar>;
pub(super) enum Operation {
    Insert(Row),
    Replace(i32, Row),
    Delete(i32),
    Field(i32, u16, Scalar),
}
pub(super) fn values(row: &[Scalar]) -> Vec<RowValue<'_>> {
    row.iter().map(Scalar::value).collect()
}
pub(super) fn id(row: &[Scalar]) -> Result<i32> {
    if let Some(Scalar::Long(n)) = row.first() {
        Ok(*n)
    } else {
        Err("Id type".into())
    }
}
pub(super) fn row_json(row: &[Scalar]) -> String {
    format!(
        "[{}]",
        row.iter().map(Scalar::json).collect::<Vec<_>>().join(",")
    )
}
pub(super) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
pub(super) fn quote(text: &str) -> String {
    let mut out = String::from("\"");
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if c.is_control() => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
pub(super) fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
pub(super) fn payload(length: usize, id: i32, column: usize, text: bool) -> Vec<u8> {
    let alphabet = b"aAezZ\xe9\xc9\xc6\xe6\xdf\x8a\x9a";
    (0..length)
        .map(|n| {
            if text {
                alphabet[(n * 7 + id as usize * 13 + column * 31) % alphabet.len()]
            } else {
                ((n * 37 + id as usize * 13 + column * 31) % 256) as u8
            }
        })
        .collect()
}
