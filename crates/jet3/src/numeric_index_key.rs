//! Scalar component transforms observed in EXP-0126/0150/0243.
//!
//! Non-Long nullable/composite construction is a candidate generalization of
//! EXP-0148. Nonfinite floating values remain unsupported.
use crate::{ColumnType, IndexDirection, RowValue};

pub(crate) const MAX_COMPONENT_BYTES: usize = crate::text_index_key::MAX_TEXT_COMPONENT;

pub(crate) enum KeyPrefix {
    Complete(usize),
    Partial { maximum: usize },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum NumericKeyType {
    Boolean,
    Byte,
    Integer,
    Long,
    Currency,
    Single,
    Double,
    DateTime,
    Binary { max_len: u8 },
    Text { max_len: u8 },
    Guid,
}

impl NumericKeyType {
    pub(crate) const fn maximum_length(self) -> usize {
        match self {
            Self::Boolean | Self::Byte => 2,
            Self::Integer => 3,
            Self::Long | Self::Single => 5,
            Self::Currency | Self::Double | Self::DateTime => 9,
            Self::Binary { max_len } => 1 + 9 * (max_len as usize).div_ceil(8),
            Self::Text { max_len } => 3 * max_len as usize + 2,
            Self::Guid => 19,
        }
    }

    pub(crate) fn prefix(self, key: &[u8], direction: IndexDirection) -> Option<KeyPrefix> {
        let Some(&marker) = key.first() else {
            return Some(KeyPrefix::Partial {
                maximum: self.maximum_length(),
            });
        };
        let marker = if direction == IndexDirection::Descending {
            marker ^ 0xff
        } else {
            marker
        };
        match marker {
            0 if self != Self::Boolean => Some(KeyPrefix::Complete(1)),
            0x7f => {
                if let Self::Binary { max_len } = self {
                    return crate::binary_index_key::prefix(key, max_len, direction);
                }
                if let Self::Text { max_len } = self {
                    return crate::text_index_key::prefix(key, max_len, direction);
                }
                // EXP-0248: two full Binary chunks in GUID display-byte order.
                if self == Self::Guid {
                    let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
                    if key.get(9).is_some_and(|byte| *byte != 9)
                        || key.get(18).is_some_and(|byte| *byte != (8 ^ mask))
                    {
                        return None;
                    }
                }
                if self == Self::Boolean && key.get(1).is_some_and(|byte| !matches!(byte, 0 | 0xff))
                {
                    return None;
                }
                let length = self.maximum_length();
                Some(if key.len() < length {
                    KeyPrefix::Partial { maximum: length }
                } else {
                    KeyPrefix::Complete(length)
                })
            }
            _ => None,
        }
    }

    pub(crate) fn from_column(column: ColumnType) -> Option<Self> {
        Some(match column {
            ColumnType::Boolean => Self::Boolean,
            ColumnType::Byte => Self::Byte,
            ColumnType::Integer => Self::Integer,
            ColumnType::Long | ColumnType::AutoIncrement => Self::Long,
            ColumnType::Currency => Self::Currency,
            ColumnType::Single => Self::Single,
            ColumnType::Double => Self::Double,
            ColumnType::DateTime => Self::DateTime,
            ColumnType::Binary { max_len } => Self::Binary {
                max_len: max_len.get(),
            },
            ColumnType::Text { max_len } => Self::Text {
                max_len: max_len.get(),
            },
            ColumnType::Guid => Self::Guid,
            _ => return None,
        })
    }

    pub(crate) fn encode(
        self,
        value: RowValue<'_>,
        direction: IndexDirection,
        output: &mut [u8; MAX_COMPONENT_BYTES],
    ) -> Option<usize> {
        output.fill(0);
        output[0] = 0x7f;
        let length = match (self, value) {
            (Self::Boolean, RowValue::Null) => return None,
            (_, RowValue::Null) => {
                output[0] = 0;
                1
            }
            (Self::Binary { max_len }, RowValue::Binary(value)) => {
                return crate::binary_index_key::encode(value, max_len, direction, output);
            }
            (Self::Text { max_len }, RowValue::Text(value)) => {
                return crate::text_index_key::encode(value, max_len, direction, output);
            }
            (Self::Guid, RowValue::Guid(value)) => {
                return crate::binary_index_key::encode(&value, 16, direction, output);
            }
            (Self::Boolean, RowValue::Boolean(value)) => {
                output[1] = if value { 0 } else { 0xff };
                2
            }
            (Self::Byte, RowValue::Byte(value)) => {
                output[1] = value;
                2
            }
            (Self::Integer, RowValue::Integer(value)) => {
                output[1..3].copy_from_slice(&value.to_be_bytes());
                output[1] ^= 0x80;
                3
            }
            (Self::Long, RowValue::Long(value)) => {
                output[..5].copy_from_slice(&crate::long_index_key::encode(
                    value,
                    IndexDirection::Ascending,
                ));
                5
            }
            (Self::Currency, RowValue::Currency { scaled }) => {
                output[1..9].copy_from_slice(&scaled.to_be_bytes());
                output[1] ^= 0x80;
                9
            }
            (Self::Single, RowValue::Single(value)) if value.is_finite() => {
                let bits = value.to_bits();
                let ordered = if value.is_sign_negative() {
                    !bits
                } else {
                    bits ^ 0x8000_0000
                };
                output[1..5].copy_from_slice(&ordered.to_be_bytes());
                5
            }
            (Self::Double, RowValue::Double(value))
            | (Self::DateTime, RowValue::DateTime { days: value })
                if value.is_finite() =>
            {
                let bits = value.to_bits();
                let ordered = if value.is_sign_negative() {
                    !bits
                } else {
                    bits ^ 0x8000_0000_0000_0000
                };
                output[1..9].copy_from_slice(&ordered.to_be_bytes());
                9
            }
            _ => return None,
        };
        if direction == IndexDirection::Descending {
            for byte in &mut output[..length] {
                *byte ^= 0xff;
            }
        }
        Some(length)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floating_zero_subnormals_normals_and_extremes_order_exactly() {
        let mut output = [0; MAX_COMPONENT_BYTES];
        for (value, expected) in [
            (-0.0_f32, [0x7f, 0x7f, 0xff, 0xff, 0xff]),
            (0.0_f32, [0x7f, 0x80, 0, 0, 0]),
            (f32::from_bits(1), [0x7f, 0x80, 0, 0, 1]),
            (f32::MIN_POSITIVE, [0x7f, 0x80, 0x80, 0, 0]),
            (-f32::MAX, [0x7f, 0, 0x80, 0, 0]),
            (f32::MAX, [0x7f, 0xff, 0x7f, 0xff, 0xff]),
        ] {
            assert_eq!(
                NumericKeyType::Single.encode(
                    RowValue::Single(value),
                    IndexDirection::Ascending,
                    &mut output
                ),
                Some(5)
            );
            assert_eq!(output[..5], expected);
        }
        for (value, expected) in [
            (
                -0.0_f64,
                [0x7f, 0x7f, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff],
            ),
            (0.0_f64, [0x7f, 0x80, 0, 0, 0, 0, 0, 0, 0]),
            (f64::from_bits(1), [0x7f, 0x80, 0, 0, 0, 0, 0, 0, 1]),
            (f64::MIN_POSITIVE, [0x7f, 0x80, 0x10, 0, 0, 0, 0, 0, 0]),
            (-f64::MAX, [0x7f, 0, 0x10, 0, 0, 0, 0, 0, 0]),
            (
                f64::MAX,
                [0x7f, 0xff, 0xef, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff],
            ),
        ] {
            assert_eq!(
                NumericKeyType::Double.encode(
                    RowValue::Double(value),
                    IndexDirection::Ascending,
                    &mut output
                ),
                Some(9)
            );
            assert_eq!(output[..9], expected);
        }
        assert_eq!(
            NumericKeyType::Integer.encode(
                RowValue::Long(1),
                IndexDirection::Ascending,
                &mut output
            ),
            None
        );
    }
}
