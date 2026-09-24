use super::value::{CurrencyValue, DateTimeValue, GuidValue, ValueError, ValueKind, decode_value};
use crate::{
    ByteCount, ColumnPhysicalType, PageNumber, RawField, ResourceBudget, ResourceLimits,
    RowLocator, TextCodePage,
};

use crate::testkit::{TestResult, budget};

fn decode<'raw>(
    physical_type: ColumnPhysicalType,
    raw: RawField<'raw>,
    boolean_bit: bool,
    budget: &mut ResourceBudget,
) -> Result<super::value::DecodedValue<'raw>, ValueError> {
    decode_value(
        physical_type,
        raw,
        boolean_bit,
        RowLocator::new(PageNumber::new(3), 1),
        TextCodePage::Windows1252,
        budget,
    )
}

#[test]
fn decodes_fixed_scalars_nulls_and_empty_binary_retaining_exact_bytes() -> TestResult {
    let guid_raw = [
        0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x66, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee,
        0xff,
    ];
    let integer = (-12_345_i16).to_le_bytes();
    let long = i32::MIN.to_le_bytes();
    let currency = (-1_234_567_i64).to_le_bytes();
    let single = (-3.5_f32).to_bits().to_le_bytes();
    let double = f64::INFINITY.to_bits().to_le_bytes();
    let date = 45_000.25_f64.to_bits().to_le_bytes();
    let cases: [(ColumnPhysicalType, RawField<'_>, bool, ValueKind<'_>); 12] = [
        (
            ColumnPhysicalType::Boolean,
            RawField::Bytes(&[]),
            true,
            ValueKind::Boolean(true),
        ),
        (
            ColumnPhysicalType::Boolean,
            RawField::Bytes(&[]),
            false,
            ValueKind::Boolean(false),
        ),
        (
            ColumnPhysicalType::Byte,
            RawField::Bytes(&[0xfe]),
            false,
            ValueKind::Byte(0xfe),
        ),
        (
            ColumnPhysicalType::Integer,
            RawField::Bytes(&integer),
            false,
            ValueKind::Integer(-12_345),
        ),
        (
            ColumnPhysicalType::Long,
            RawField::Bytes(&long),
            false,
            ValueKind::Long(i32::MIN),
        ),
        (
            ColumnPhysicalType::Currency,
            RawField::Bytes(&currency),
            false,
            ValueKind::Currency(CurrencyValue { scaled: -1_234_567 }),
        ),
        (
            ColumnPhysicalType::Single,
            RawField::Bytes(&single),
            false,
            ValueKind::Single(-3.5),
        ),
        (
            ColumnPhysicalType::Double,
            RawField::Bytes(&double),
            false,
            ValueKind::Double(f64::INFINITY),
        ),
        (
            ColumnPhysicalType::DateTime,
            RawField::Bytes(&date),
            false,
            ValueKind::DateTime(DateTimeValue { days: 45_000.25 }),
        ),
        (
            ColumnPhysicalType::Guid,
            RawField::Bytes(&guid_raw),
            false,
            ValueKind::Guid(GuidValue {
                display_bytes: [
                    0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc,
                    0xdd, 0xee, 0xff,
                ],
            }),
        ),
        (
            ColumnPhysicalType::Binary,
            RawField::Bytes(&[]),
            false,
            ValueKind::Binary(&[]),
        ),
        (
            ColumnPhysicalType::Text,
            RawField::Null,
            false,
            ValueKind::Null,
        ),
    ];
    let mut resources = budget();
    for (physical_type, raw, boolean_bit, expected) in cases {
        let value = decode(physical_type, raw, boolean_bit, &mut resources)?;
        assert_eq!(value.kind(), &expected, "{physical_type:?}");
        assert_eq!(value.raw_bytes(), raw.raw_bytes(), "{physical_type:?}");
    }
    assert_eq!(CurrencyValue { scaled: 1 }.scale(), 4);
    assert_eq!(CurrencyValue { scaled: -1_234_567 }.scaled(), -1_234_567);
    assert_eq!(DateTimeValue { days: 45_000.25 }.days(), 45_000.25);
    assert_eq!(
        GuidValue {
            display_bytes: [1; 16]
        }
        .display_bytes(),
        [1; 16]
    );
    Ok(())
}

#[test]
fn rejects_wrong_width_and_charges_before_scalar_output() {
    assert!(matches!(
        decode(
            ColumnPhysicalType::Long,
            RawField::Bytes(&[0; 3]),
            false,
            &mut budget()
        ),
        Err(ValueError::InvalidWidth {
            physical_type: ColumnPhysicalType::Long,
            expected: 4,
            actual: 3,
        })
    ));

    let limits = ResourceLimits::default()
        .with_max_decoded_value_bytes(ByteCount::new(1))
        .with_max_total_decoded_bytes(ByteCount::new(1));
    let mut budget = ResourceBudget::new(limits);
    assert!(matches!(
        decode(
            ColumnPhysicalType::Integer,
            RawField::Bytes(&1_i16.to_le_bytes()),
            false,
            &mut budget,
        ),
        Err(ValueError::Resource(_))
    ));
    assert_eq!(budget.decoded_bytes(), ByteCount::new(0));
}
