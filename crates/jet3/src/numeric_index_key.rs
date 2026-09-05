//! Numeric component transforms observed in EXP-0126/0150.
//!
//! Non-Long nullable/composite construction is a candidate generalization of
//! EXP-0148. Negative zero and nonfinite floating values remain unsupported.
use crate::{ColumnType, IndexDirection, RowValue};

pub(crate) const MAX_COMPONENT_BYTES: usize = 9;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum NumericKeyType {
    Boolean,
    Byte,
    Integer,
    Long,
    Currency,
    Single,
    Double,
}

impl NumericKeyType {
    pub(crate) fn from_column(column: ColumnType) -> Option<Self> {
        Some(match column {
            ColumnType::Boolean => Self::Boolean,
            ColumnType::Byte => Self::Byte,
            ColumnType::Integer => Self::Integer,
            ColumnType::Long | ColumnType::AutoIncrement => Self::Long,
            ColumnType::Currency => Self::Currency,
            ColumnType::Single => Self::Single,
            ColumnType::Double => Self::Double,
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
            (Self::Single, RowValue::Single(value))
                if value.is_finite() && value.to_bits() != 0x8000_0000 =>
            {
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
                if value.is_finite() && value.to_bits() != 0x8000_0000_0000_0000 =>
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
            assert_eq!(output, expected);
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
