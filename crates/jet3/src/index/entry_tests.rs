use super::entry::*;
use crate::{
    Error, IndexDirection, IndexNullPolicy, PageNumber, ResourceBudget, ResourceLimitKind,
    ResourceLimits, RowLocator, RowValue, index::key::scalar::ScalarKeyType,
};

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

const FIELDS: [ScalarIndexField; 2] = [
    ScalarIndexField {
        column: 1,
        direction: IndexDirection::Ascending,
        kind: ScalarKeyType::Long,
    },
    ScalarIndexField {
        column: 0,
        direction: IndexDirection::Descending,
        kind: ScalarKeyType::Long,
    },
];
const LOCATOR: RowLocator = RowLocator::new(PageNumber::new(0x12_3456), 255);

#[test]
fn composite_null_records_keep_direction_and_locator() -> Result<(), Box<dyn std::error::Error>> {
    let entry = ScalarIndexEntry::encode(
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
        ScalarIndexEntry::encode(
            &FIELDS,
            &[RowValue::Long(1), RowValue::Null],
            IndexNullPolicy::IgnoreAllNull,
            LOCATOR,
            &mut budget(),
        )?,
        Some(entry)
    );
    assert_eq!(
        ScalarIndexEntry::encode(
            &FIELDS,
            &[RowValue::Null, RowValue::Null],
            IndexNullPolicy::IgnoreAllNull,
            LOCATOR,
            &mut budget(),
        )?,
        None
    );
    assert_eq!(
        ScalarIndexEntry::encode(
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
    for fields in [&[][..], &[FIELDS[0]; MAX_FIELDS + 1][..]] {
        assert_eq!(
            ScalarIndexEntry::encode(
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
        ScalarIndexEntry::encode(
            &FIELDS,
            &[],
            IndexNullPolicy::Include,
            LOCATOR,
            &mut budget(),
        ),
        Err(EntryError::MissingColumn { column: 1 })
    );
    for (kind, value) in [
        (ScalarKeyType::Long, RowValue::Byte(1)),
        (ScalarKeyType::Double, RowValue::Double(f64::NAN)),
        (
            ScalarKeyType::DateTime,
            RowValue::DateTime {
                days: f64::INFINITY,
            },
        ),
        (ScalarKeyType::Single, RowValue::Single(f32::INFINITY)),
    ] {
        assert_eq!(
            ScalarIndexEntry::encode(
                &[ScalarIndexField {
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
    let fields = [ScalarIndexField {
        column: 0,
        ..FIELDS[0]
    }];
    let locator = RowLocator::new(PageNumber::new(0xff_ffff), 255);
    let entry = ScalarIndexEntry::encode(
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
            ScalarIndexEntry::encode(
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
        ScalarIndexEntry::encode(
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

#[test]
fn key_shapes_check_each_component_null_policy_and_complete_width()
-> Result<(), Box<dyn std::error::Error>> {
    for (kind, value) in [
        (ScalarKeyType::Boolean, RowValue::Boolean(false)),
        (ScalarKeyType::Byte, RowValue::Byte(7)),
        (ScalarKeyType::Integer, RowValue::Integer(-5)),
        (ScalarKeyType::Long, RowValue::Long(-500)),
        (
            ScalarKeyType::Currency,
            RowValue::Currency { scaled: 120001 },
        ),
        (ScalarKeyType::Single, RowValue::Single(-1.25)),
        (ScalarKeyType::Double, RowValue::Double(2.5)),
        (ScalarKeyType::DateTime, RowValue::DateTime { days: -1.25 }),
    ] {
        for direction in [IndexDirection::Ascending, IndexDirection::Descending] {
            let fields = [ScalarIndexField {
                column: 0,
                kind,
                direction,
            }];
            let entry = ScalarIndexEntry::encode(
                &fields,
                &[value],
                IndexNullPolicy::Required,
                LOCATOR,
                &mut budget(),
            )?
            .ok_or("entry")?;
            assert!(valid_key_shape(
                &fields,
                IndexNullPolicy::Required,
                entry.key()
            ));
            assert!(!valid_key_shape(
                &fields,
                IndexNullPolicy::Required,
                &entry.key()[..entry.key().len() - 1]
            ));
            let mut bad = entry.key().to_vec();
            bad.push(0);
            assert!(!valid_key_shape(&fields, IndexNullPolicy::Required, &bad));
            bad.pop();
            bad[0] = 0x7e;
            assert!(!valid_key_shape(&fields, IndexNullPolicy::Include, &bad));
            if kind == ScalarKeyType::Boolean {
                bad[0] = entry.key()[0];
                bad[1] = 1;
                assert!(!valid_key_shape(&fields, IndexNullPolicy::Required, &bad));
            }
            let null = [if direction == IndexDirection::Ascending {
                0
            } else {
                0xff
            }];
            assert_eq!(
                valid_key_shape(&fields, IndexNullPolicy::Include, &null),
                kind != ScalarKeyType::Boolean
            );
            assert!(!valid_key_shape(
                &fields,
                IndexNullPolicy::IgnoreAllNull,
                &null
            ));
            assert!(!valid_key_shape(&fields, IndexNullPolicy::Required, &null));
        }
    }
    for values in [
        [RowValue::Null, RowValue::Null],
        [RowValue::Long(1), RowValue::Null],
        [RowValue::Null, RowValue::Long(2)],
        [RowValue::Long(1), RowValue::Long(2)],
    ] {
        let entry = ScalarIndexEntry::encode(
            &FIELDS,
            &values,
            IndexNullPolicy::Include,
            LOCATOR,
            &mut budget(),
        )?
        .ok_or("entry")?;
        assert!(valid_key_shape(
            &FIELDS,
            IndexNullPolicy::Include,
            entry.key()
        ));
        assert_eq!(
            valid_key_shape(&FIELDS, IndexNullPolicy::Required, entry.key()),
            !entry.has_null()
        );
        assert_eq!(
            valid_key_shape(&FIELDS, IndexNullPolicy::IgnoreAllNull, entry.key()),
            entry.key().len() > 2
        );
    }
    Ok(())
}

#[test]
fn binary_components_and_shortened_keys_keep_observed_framing()
-> Result<(), Box<dyn std::error::Error>> {
    let mut fields = [ScalarIndexField {
        column: 0,
        direction: IndexDirection::Ascending,
        kind: ScalarKeyType::Binary { max_len: 255 },
    }];
    for (direction, suffix225, suffix255) in [
        (IndexDirection::Ascending, [1, 0], [0x44, 0xda]),
        (IndexDirection::Descending, [0xff, 0x8f], [1, 0xff]),
    ] {
        fields[0].direction = direction;
        let mask = if direction == IndexDirection::Descending {
            255
        } else {
            0
        };
        for size in [1_usize, 8, 9, 17, 224, 225, 255] {
            let payload = vec![0; size];
            let entry = ScalarIndexEntry::encode(
                &fields,
                &[RowValue::Binary(&payload)],
                IndexNullPolicy::Include,
                LOCATOR,
                &mut budget(),
            )?
            .ok_or("missing entry")?;
            assert_eq!(entry.key()[0], 0x7f ^ mask);
            if size <= 224 {
                assert_eq!(entry.key().len(), 1 + 9 * size.div_ceil(8));
                assert_eq!(
                    entry.key()[entry.key().len() - 1],
                    ((size - 1) % 8 + 1) as u8 ^ mask
                );
            } else {
                assert_eq!(entry.key().len(), 255);
                assert_eq!(
                    &entry.key()[253..],
                    if size == 225 { &suffix225 } else { &suffix255 }
                );
            }
            if size > 8 {
                assert_eq!(entry.key()[9], 9);
            }
            assert_eq!(entry.locator(), LOCATOR);
            assert!(valid_key_shape(
                &fields,
                IndexNullPolicy::Include,
                entry.key()
            ));
            let mut damaged = entry.key().to_vec();
            damaged[9] = 0;
            assert!(!valid_key_shape(
                &fields,
                IndexNullPolicy::Include,
                &damaged
            ));
        }
    }
    assert_eq!(
        ScalarIndexEntry::encode(
            &fields,
            &[RowValue::Binary(&[])],
            IndexNullPolicy::IgnoreAllNull,
            LOCATOR,
            &mut budget()
        )?,
        None
    );
    assert_eq!(
        ScalarIndexEntry::encode(
            &fields,
            &[RowValue::Binary(&[])],
            IndexNullPolicy::Required,
            LOCATOR,
            &mut budget()
        ),
        Err(EntryError::NullRequired)
    );
    assert!(matches!(
        ScalarIndexEntry::encode(
            &fields,
            &[RowValue::Binary(&[0; 256])],
            IndexNullPolicy::Include,
            LOCATOR,
            &mut budget()
        ),
        Err(EntryError::UnsupportedValue { .. })
    ));
    Ok(())
}

#[test]
fn shortened_key_prefixes_enforce_schema_capacity_and_padding()
-> Result<(), Box<dyn std::error::Error>> {
    let field = ScalarIndexField {
        column: 0,
        direction: IndexDirection::Ascending,
        kind: ScalarKeyType::Binary { max_len: 255 },
    };
    let entry = ScalarIndexEntry::encode(
        &[field],
        &[RowValue::Binary(&[0; 255])],
        IndexNullPolicy::Include,
        LOCATOR,
        &mut budget(),
    )?
    .ok_or("entry")?;
    assert!(!valid_key_shape(
        &[ScalarIndexField {
            kind: ScalarKeyType::Binary { max_len: 224 },
            ..field
        }],
        IndexNullPolicy::Include,
        entry.key()
    ));
    for length in [0, 1, 9, 252, 254] {
        assert!(!valid_key_shape(
            &[field],
            IndexNullPolicy::Include,
            &entry.key()[..length]
        ));
    }
    let mut padded = ScalarIndexEntry::encode(
        &[field],
        &[RowValue::Binary(&[1])],
        IndexNullPolicy::Include,
        LOCATOR,
        &mut budget(),
    )?
    .ok_or("entry")?
    .key()
    .to_vec();
    padded[2] = 1;
    assert!(!valid_key_shape(
        &[field],
        IndexNullPolicy::Include,
        &padded
    ));
    Ok(())
}

#[test]
fn whole_composite_key_is_shortened_after_its_components() -> Result<(), Box<dyn std::error::Error>>
{
    for (direction, expected) in [
        (IndexDirection::Ascending, [0xf5, 0x75]),
        (IndexDirection::Descending, [0x0b, 0x06]),
    ] {
        let other = if direction == IndexDirection::Ascending {
            IndexDirection::Descending
        } else {
            IndexDirection::Ascending
        };
        let fields = [
            ScalarIndexField {
                column: 0,
                direction,
                kind: ScalarKeyType::Binary { max_len: 255 },
            },
            ScalarIndexField {
                column: 1,
                direction: other,
                kind: ScalarKeyType::Long,
            },
        ];
        let entry = ScalarIndexEntry::encode(
            &fields,
            &[RowValue::Binary(&[0; 224]), RowValue::Long(13)],
            IndexNullPolicy::Required,
            LOCATOR,
            &mut budget(),
        )?
        .ok_or("entry")?;
        // EXP-0245 held-out native Binary224 + Long13 composite, both directions.
        assert_eq!(entry.key().len(), 255);
        assert_eq!(&entry.key()[253..], &expected);
        assert!(valid_key_shape(
            &fields,
            IndexNullPolicy::Required,
            entry.key()
        ));
    }
    Ok(())
}

#[test]
fn binary_record_storage_charges_before_heap_allocation() -> Result<(), Box<dyn std::error::Error>>
{
    let fields = [ScalarIndexField {
        column: 0,
        direction: IndexDirection::Ascending,
        kind: ScalarKeyType::Binary { max_len: 255 },
    }];
    let limited = |bytes| {
        ResourceBudget::new(
            ResourceLimits::default().with_max_allocation_bytes(crate::ByteCount::new(bytes)),
        )
    };
    ScalarIndexEntry::encode(
        &fields,
        &[RowValue::Binary(&[0; 8])],
        IndexNullPolicy::Include,
        LOCATOR,
        &mut limited(0),
    )?
    .ok_or("inline entry")?;
    assert!(matches!(
        ScalarIndexEntry::encode(
            &fields,
            &[RowValue::Binary(&[0; 9])],
            IndexNullPolicy::Include,
            LOCATOR,
            &mut limited(22)
        ),
        Err(EntryError::Encoding(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            ..
        }))
    ));
    let entry = ScalarIndexEntry::encode(
        &fields,
        &[RowValue::Binary(&[0; 9])],
        IndexNullPolicy::Include,
        LOCATOR,
        &mut limited(23),
    )?
    .ok_or("wide entry")?;
    assert_eq!(entry.record().len(), 23);
    assert_eq!(entry.locator(), LOCATOR);
    Ok(())
}
