use super::{CurrencyValue, DateTimeValue, GuidValue, ValueError, ValueKind, decode_value};
use crate::{
    ByteCount, ColumnPhysicalType, PageNumber, RawField, ResourceBudget, ResourceLimits,
    RowLocator, TextCodePage,
};

fn source() -> RowLocator {
    RowLocator::new(PageNumber::new(3), 1)
}

fn decode<'raw>(
    physical_type: ColumnPhysicalType,
    raw: &'raw [u8],
    boolean_bit: bool,
    budget: &mut ResourceBudget,
) -> Result<super::DecodedValue<'raw>, ValueError> {
    decode_value(
        physical_type,
        RawField::Bytes(raw),
        boolean_bit,
        source(),
        TextCodePage::Windows1252,
        budget,
    )
}

#[test]
fn decodes_fixed_scalars_and_retains_exact_bytes() -> Result<(), Box<dyn std::error::Error>> {
    let mut budget = ResourceBudget::new(ResourceLimits::default());

    let boolean = decode(ColumnPhysicalType::Boolean, &[], true, &mut budget)?;
    assert_eq!(boolean.raw_bytes(), Some(&[][..]));
    assert_eq!(boolean.kind(), &ValueKind::Boolean(true));

    let byte = decode(ColumnPhysicalType::Byte, &[0xfe], false, &mut budget)?;
    assert_eq!(byte.kind(), &ValueKind::Byte(0xfe));

    let integer_raw = (-12_345_i16).to_le_bytes();
    let integer = decode(
        ColumnPhysicalType::Integer,
        &integer_raw,
        false,
        &mut budget,
    )?;
    assert_eq!(integer.kind(), &ValueKind::Integer(-12_345));

    let long_raw = i32::MIN.to_le_bytes();
    let long = decode(ColumnPhysicalType::Long, &long_raw, false, &mut budget)?;
    assert_eq!(long.kind(), &ValueKind::Long(i32::MIN));

    let currency_raw = (-1_234_567_i64).to_le_bytes();
    let currency = decode(
        ColumnPhysicalType::Currency,
        &currency_raw,
        false,
        &mut budget,
    )?;
    assert_eq!(
        currency.kind(),
        &ValueKind::Currency(CurrencyValue { scaled: -1_234_567 })
    );
    assert_eq!(CurrencyValue { scaled: 1 }.scale(), 4);

    let single_raw = (-3.5_f32).to_bits().to_le_bytes();
    let single = decode(ColumnPhysicalType::Single, &single_raw, false, &mut budget)?;
    assert_eq!(single.kind(), &ValueKind::Single(-3.5));

    let double_raw = f64::INFINITY.to_bits().to_le_bytes();
    let double = decode(ColumnPhysicalType::Double, &double_raw, false, &mut budget)?;
    assert_eq!(double.kind(), &ValueKind::Double(f64::INFINITY));

    let date_raw = 45_000.25_f64.to_bits().to_le_bytes();
    let date = decode(ColumnPhysicalType::DateTime, &date_raw, false, &mut budget)?;
    assert_eq!(
        date.kind(),
        &ValueKind::DateTime(DateTimeValue { days: 45_000.25 })
    );

    let guid_raw = [
        0x33, 0x22, 0x11, 0x00, 0x55, 0x44, 0x77, 0x66, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd, 0xee,
        0xff,
    ];
    let guid = decode(ColumnPhysicalType::Guid, &guid_raw, false, &mut budget)?;
    assert_eq!(
        guid.kind(),
        &ValueKind::Guid(GuidValue {
            display_bytes: [
                0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xaa, 0xbb, 0xcc, 0xdd,
                0xee, 0xff,
            ],
        })
    );
    assert_eq!(guid.raw_bytes(), Some(&guid_raw[..]));
    Ok(())
}

#[test]
fn distinguishes_null_empty_binary_and_explicit_boolean_false()
-> Result<(), Box<dyn std::error::Error>> {
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    let null = decode_value(
        ColumnPhysicalType::Text,
        RawField::Null,
        false,
        source(),
        TextCodePage::Windows1252,
        &mut budget,
    )?;
    assert_eq!(null.raw_bytes(), None);
    assert_eq!(null.kind(), &ValueKind::Null);

    let binary = decode(ColumnPhysicalType::Binary, &[], false, &mut budget)?;
    assert_eq!(binary.raw_bytes(), Some(&[][..]));
    assert_eq!(binary.kind(), &ValueKind::Binary(&[]));

    let boolean = decode(ColumnPhysicalType::Boolean, &[], false, &mut budget)?;
    assert_eq!(boolean.kind(), &ValueKind::Boolean(false));
    Ok(())
}

#[test]
fn rejects_wrong_width_and_charges_before_scalar_output() {
    let mut budget = ResourceBudget::new(ResourceLimits::default());
    assert!(matches!(
        decode(ColumnPhysicalType::Long, &[0; 3], false, &mut budget),
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
            &1_i16.to_le_bytes(),
            false,
            &mut budget,
        ),
        Err(ValueError::Resource(_))
    ));
    assert_eq!(budget.decoded_bytes(), ByteCount::new(0));
}
