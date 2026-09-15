use super::*;
use crate::{ResourceLimitKind, ResourceLimits};

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

const FIELDS: [NumericIndexField; 2] = [
    NumericIndexField {
        column: 1,
        direction: IndexDirection::Ascending,
        kind: NumericKeyType::Long,
    },
    NumericIndexField {
        column: 0,
        direction: IndexDirection::Descending,
        kind: NumericKeyType::Long,
    },
];
const LOCATOR: RowLocator = RowLocator::new(PageNumber::new(0x12_3456), 255);

#[test]
fn composite_null_records_keep_direction_and_locator() -> Result<(), Box<dyn std::error::Error>> {
    let entry = NumericIndexEntry::encode(
        &FIELDS,
        &[RowValue::Long(1), RowValue::Null],
        IndexNullPolicy::Include,
        LOCATOR,
        &mut budget(),
    )?
    .ok_or("entry omitted")?;
    // EXP-0148 ascending null followed by descending present Long.
    assert_eq!(entry.key(), [0, 0x80, 0x7f, 0xff, 0xff, 0xfe]);
    assert_eq!(
        entry.record(),
        [0, 0x80, 0x7f, 0xff, 0xff, 0xfe, 0x12, 0x34, 0x56, 255]
    );
    assert!(entry.has_null());
    assert_eq!(entry.locator(), LOCATOR);
    assert_eq!(
        NumericIndexEntry::encode(
            &FIELDS,
            &[RowValue::Long(1), RowValue::Null],
            IndexNullPolicy::IgnoreAllNull,
            LOCATOR,
            &mut budget(),
        )?,
        Some(entry)
    );
    assert_eq!(
        NumericIndexEntry::encode(
            &FIELDS,
            &[RowValue::Null, RowValue::Null],
            IndexNullPolicy::IgnoreAllNull,
            LOCATOR,
            &mut budget(),
        )?,
        None
    );
    assert_eq!(
        NumericIndexEntry::encode(
            &FIELDS,
            &[RowValue::Long(1), RowValue::Null],
            IndexNullPolicy::Required,
            LOCATOR,
            &mut budget(),
        ),
        Err(EntryError::NullRequired)
    );
    Ok(())
}

#[test]
fn schema_and_value_refusals_are_structured() {
    for fields in [&[][..], &[FIELDS[0]; 3][..]] {
        assert_eq!(
            NumericIndexEntry::encode(
                fields,
                &[],
                IndexNullPolicy::Include,
                LOCATOR,
                &mut budget(),
            ),
            Err(EntryError::FieldCount {
                actual: fields.len()
            })
        );
    }
    assert_eq!(
        NumericIndexEntry::encode(
            &FIELDS,
            &[],
            IndexNullPolicy::Include,
            LOCATOR,
            &mut budget(),
        ),
        Err(EntryError::MissingColumn { column: 1 })
    );
    for (kind, value) in [
        (NumericKeyType::Long, RowValue::Byte(1)),
        (NumericKeyType::Boolean, RowValue::Null),
        (NumericKeyType::Double, RowValue::Double(-0.0)),
        (NumericKeyType::Single, RowValue::Single(f32::INFINITY)),
    ] {
        assert_eq!(
            NumericIndexEntry::encode(
                &[NumericIndexField {
                    column: 0,
                    kind,
                    ..FIELDS[0]
                }],
                &[value],
                IndexNullPolicy::Include,
                LOCATOR,
                &mut budget(),
            ),
            Err(EntryError::UnsupportedValue { column: 0, kind })
        );
    }
}

#[test]
fn locator_width_and_budget_are_checked() -> Result<(), Box<dyn std::error::Error>> {
    let fields = [NumericIndexField {
        column: 0,
        ..FIELDS[0]
    }];
    let locator = RowLocator::new(PageNumber::new(0xff_ffff), 255);
    let entry = NumericIndexEntry::encode(
        &fields,
        &[RowValue::Long(i32::MIN)],
        IndexNullPolicy::Include,
        locator,
        &mut budget(),
    )?
    .ok_or("entry omitted")?;
    assert_eq!(entry.locator(), locator);
    assert_eq!(entry.key(), [0x7f, 0, 0, 0, 0]);
    assert!(!entry.has_null());
    for page in [0x100_0000, u64::MAX] {
        assert!(matches!(
            NumericIndexEntry::encode(
                &fields,
                &[RowValue::Long(1)],
                IndexNullPolicy::Include,
                RowLocator::new(PageNumber::new(page), 0),
                &mut budget(),
            ),
            Err(EntryError::Encoding(Error::IntegerConversion {
                target: "24-bit index row page",
                ..
            }))
        ));
    }
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_item_work(0));
    assert!(matches!(
        NumericIndexEntry::encode(
            &fields,
            &[RowValue::Long(1)],
            IndexNullPolicy::Include,
            locator,
            &mut limited,
        ),
        Err(EntryError::Encoding(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::ItemWork,
            ..
        }))
    ));
    Ok(())
}
