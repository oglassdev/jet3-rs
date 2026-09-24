use super::builder::*;
use crate::{
    ByteCount, Error, IndexDirection, IndexNullPolicy, PAGE_BYTES, PageNumber, ResourceBudget,
    ResourceLimitKind, ResourceLimits, RowLocator, RowValue,
    index::{
        entry::{ScalarIndexEntry, ScalarIndexField},
        key::scalar::ScalarKeyType,
    },
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn entry(
    values: [RowValue<'_>; 2],
    slot: u8,
) -> Result<ScalarIndexEntry, Box<dyn std::error::Error>> {
    ScalarIndexEntry::encode(
        &[
            ScalarIndexField {
                column: 0,
                kind: ScalarKeyType::Currency,
                direction: IndexDirection::Ascending,
            },
            ScalarIndexField {
                column: 1,
                kind: ScalarKeyType::Double,
                direction: IndexDirection::Descending,
            },
        ],
        &values,
        IndexNullPolicy::Include,
        RowLocator::new(PageNumber::new(42), slot),
        &mut budget(),
    )?
    .ok_or_else(|| "entry omitted".into())
}

#[test]
fn variable_width_boundary_uses_explicit_pages_and_preserves_payload_slack() -> TestResult {
    let mut entries = Vec::new();
    for slot in 0..3 {
        entries.push(entry([RowValue::Null; 2], slot)?);
    }
    for slot in 0..82 {
        entries.push(entry(
            [
                RowValue::Currency {
                    scaled: slot.into(),
                },
                RowValue::Double(1.0),
            ],
            slot,
        )?);
    }
    // Three six-byte records plus 81 twenty-two-byte records fill one leaf.
    assert_eq!(
        ScalarIndexPages::new(&entries[..84], 3, &mut budget())?.len(),
        1
    );
    let layout = ScalarIndexPages::new(&entries, 3, &mut budget())?;
    assert_eq!(layout.len(), 3);
    let ids = [
        PageNumber::new(700),
        PageNumber::new(5),
        PageNumber::new(91),
    ];
    let owner = PageNumber::new(20);
    let before = [0xa5; PAGE_BYTES];
    let first = layout.image(
        0,
        &entries,
        |n| ids.get(n).copied(),
        owner,
        &before,
        &mut budget(),
    )?;
    assert_eq!(&first.as_bytes()[2..4], &0_u16.to_le_bytes());
    assert_eq!(&first.as_bytes()[8..12], &[0; 4]);
    assert_eq!(&first.as_bytes()[12..16], &5_u32.to_le_bytes());
    assert_eq!(first.as_bytes()[BITMAP + AREA_BYTES / 8], 1);
    let mut charged = budget();
    let last = layout.image(
        1,
        &entries,
        |n| ids.get(n).copied(),
        owner,
        &before,
        &mut charged,
    )?;
    let mut expected = before;
    expected[..AREA].fill(0);
    expected[0..2].copy_from_slice(&[4, 1]);
    expected[2..4].copy_from_slice(&1778_u16.to_le_bytes());
    expected[4..8].copy_from_slice(&20_u32.to_le_bytes());
    expected[8..12].copy_from_slice(&700_u32.to_le_bytes());
    expected[BITMAP + 22 / 8] = 1 << (22 % 8);
    expected[AREA..AREA + 22].copy_from_slice(entries[84].record());
    assert_eq!(last.as_bytes(), &expected);
    assert_eq!(charged.encoded_bytes().get(), PAGE_BYTES as u64);
    let root = layout.image(
        2,
        &entries,
        |n| ids.get(n).copied(),
        owner,
        &before,
        &mut budget(),
    )?;
    expected = before;
    expected[..AREA].fill(0);
    expected[0..2].copy_from_slice(&[3, 1]);
    expected[2..4].copy_from_slice(&1774_u16.to_le_bytes());
    expected[4..8].copy_from_slice(&20_u32.to_le_bytes());
    expected[16..20].copy_from_slice(&5_u32.to_le_bytes());
    expected[21] = 1;
    expected[BITMAP + 26 / 8] = 1 << (26 % 8);
    expected[AREA..AREA + 22].copy_from_slice(entries[83].record());
    expected[AREA + 22..AREA + 26].copy_from_slice(&700_u32.to_be_bytes());
    assert_eq!(root.as_bytes(), &expected);
    Ok(())
}

#[test]
fn empty_tree_resets_header_and_keeps_all_payload_slack() -> TestResult {
    let layout = ScalarIndexPages::new(&[] as &[ScalarIndexEntry], 1, &mut budget())?;
    assert_eq!(layout.len(), 1);
    let mut expected = [0x5a; PAGE_BYTES];
    let result = layout.image(
        0,
        &[] as &[ScalarIndexEntry],
        |_| Some(PageNumber::new(91)),
        PageNumber::new(20),
        &expected,
        &mut budget(),
    )?;
    expected[..AREA].fill(0);
    expected[0..2].copy_from_slice(&[4, 1]);
    expected[2..4].copy_from_slice(&1800_u16.to_le_bytes());
    expected[4..8].copy_from_slice(&20_u32.to_le_bytes());
    assert_eq!(result.as_bytes(), &expected);
    Ok(())
}

#[test]
fn invalid_inventory_assignment_widths_and_node_limits_are_checked() -> TestResult {
    let narrow = entry([RowValue::Null; 2], 0)?;
    let wide = entry([RowValue::Currency { scaled: 1 }, RowValue::Double(1.0)], 0)?;
    let entries = vec![narrow; 84];
    let layout = ScalarIndexPages::new(&entries, 1, &mut budget())?;
    let image = |ordinal, entries: &[ScalarIndexEntry], page, owner| {
        layout.image(
            ordinal,
            entries,
            |_| page,
            owner,
            &[0; PAGE_BYTES],
            &mut budget(),
        )
    };
    let page = Some(PageNumber::new(10));
    let owner = PageNumber::new(20);
    for result in [
        image(1, &entries, page, owner),
        image(0, &entries[..83], page, owner),
        image(0, &entries, None, owner),
        image(0, &vec![wide.clone(); 84], page, owner),
    ] {
        assert!(matches!(result, Err(TreeBuildError::Layout(_))));
    }
    for result in [
        image(0, &entries, Some(PageNumber::new(u64::MAX)), owner),
        image(0, &entries, page, PageNumber::new(u64::MAX)),
    ] {
        assert!(matches!(
            result,
            Err(TreeBuildError::Encoding(Error::IntegerConversion { .. }))
        ));
    }
    assert!(matches!(
        ScalarIndexPages::new(&[] as &[ScalarIndexEntry], 0, &mut budget()),
        Err(TreeBuildError::NodeLimit { maximum: 0 })
    ));
    assert!(matches!(
        ScalarIndexPages::new(&vec![wide; 82], 2, &mut budget()),
        Err(TreeBuildError::NodeLimit { maximum: 2 })
    ));
    Ok(())
}

#[test]
fn node_allocation_work_and_output_share_the_caller_budget() -> TestResult {
    let mut allocation =
        ResourceBudget::new(ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)));
    assert!(matches!(
        ScalarIndexPages::new(&[] as &[ScalarIndexEntry], 1, &mut allocation),
        Err(TreeBuildError::Encoding(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            ..
        }))
    ));
    let mut work = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(matches!(
        ScalarIndexPages::new(&[] as &[ScalarIndexEntry], 1, &mut work),
        Err(TreeBuildError::Encoding(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::TotalWorkUnits,
            ..
        }))
    ));
    let layout = ScalarIndexPages::new(&[] as &[ScalarIndexEntry], 1, &mut budget())?;
    let mut encoded =
        ResourceBudget::new(ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(2047)));
    assert!(matches!(
        layout.image(
            0,
            &[] as &[ScalarIndexEntry],
            |_| Some(PageNumber::new(10)),
            PageNumber::new(20),
            &[0; PAGE_BYTES],
            &mut encoded
        ),
        Err(TreeBuildError::Encoding(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::EncodedBytes,
            ..
        }))
    ));
    Ok(())
}

#[test]
fn checked_records_with_larger_keys_reuse_the_same_tree_encoder() -> TestResult {
    struct Wide([u8; 80]);
    impl IndexRecord for Wide {
        fn record(&self) -> &[u8] {
            &self.0
        }
    }
    let entries = (0..23)
        .map(|n| {
            let mut bytes = [0x7f; 80];
            bytes[75] = n;
            bytes[76..].copy_from_slice(&[0, 0, 42, n]);
            Wide(bytes)
        })
        .collect::<Vec<_>>();
    let layout = ScalarIndexPages::new(&entries, 3, &mut budget())?;
    assert_eq!(layout.len(), 3);
    let root = layout.image(
        2,
        &entries,
        |n| Some(PageNumber::new(n as u64 + 10)),
        PageNumber::new(2),
        &[0; PAGE_BYTES],
        &mut budget(),
    )?;
    assert_eq!(&root.as_bytes()[AREA..AREA + 80], entries[21].record());
    assert_eq!(
        &root.as_bytes()[AREA + 80..AREA + 84],
        &10_u32.to_be_bytes()
    );
    assert_eq!(&root.as_bytes()[16..20], &11_u32.to_le_bytes());
    Ok(())
}

#[test]
fn record_widths_that_cannot_form_nonempty_branches_are_refused() {
    struct Raw<'a>(&'a [u8]);
    impl IndexRecord for Raw<'_> {
        fn record(&self) -> &[u8] {
            self.0
        }
    }
    for bytes in [&[0; 4][..], &[0; 897][..], &[0; PAGE_BYTES][..]] {
        assert!(matches!(
            ScalarIndexPages::new(&[Raw(bytes)], 3, &mut budget()),
            Err(TreeBuildError::Layout("record width"))
        ));
    }
}
