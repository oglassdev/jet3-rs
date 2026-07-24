//! Deterministic, bounded properties for format-neutral foundations.
//!
//! These tests exercise arithmetic, byte decoding, random access, and raw
//! fixed-size page mapping. They make no claim about the meaning or validity
//! of Jet page contents.

use jet3::limits::{ReadBudget, ReadLimits};
use jet3::{
    BinaryCursor, ByteCount, ByteOffset, Error, JET3_PAGE_SIZE, Jet3PageReader, PageGeometry,
    PageNumber, ReadAt, ResourceBudget, ResourceLimits, SliceSource,
};
use proptest::prelude::*;
use proptest::test_runner::{Config, RngSeed};

const CASES: u32 = 256;
const SEED: u64 = 0x4a45_5433_5052_4f50;
const MAX_SLICE_BYTES: usize = 4_096;
const MAX_PAGES: u64 = 32;
const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

fn deterministic_config() -> Config {
    Config {
        cases: CASES,
        max_shrink_iters: 4_096,
        rng_seed: RngSeed::Fixed(SEED),
        ..Config::default()
    }
}

fn edge_u64() -> impl Strategy<Value = u64> {
    prop_oneof![
        Just(0),
        Just(1),
        Just(u64::MAX - 1),
        Just(u64::MAX),
        any::<u64>(),
    ]
}

fn read_limits(input: u64, single: u64, total: u64) -> ReadLimits {
    ReadLimits::new(
        ByteCount::new(input),
        ByteCount::new(single),
        ByteCount::new(total),
    )
}

proptest! {
    #![proptest_config(deterministic_config())]

    #[test]
    fn checked_byte_arithmetic_matches_u64_reference(
        left in edge_u64(),
        right in edge_u64(),
    ) {
        let count_add = ByteCount::new(left)
            .checked_add(ByteCount::new(right))
            .ok()
            .map(ByteCount::get);
        let count_sub = ByteCount::new(left)
            .checked_sub(ByteCount::new(right))
            .ok()
            .map(ByteCount::get);
        let offset_add = ByteOffset::new(left)
            .checked_add(ByteCount::new(right))
            .ok()
            .map(ByteOffset::get);
        let offset_sub = ByteOffset::new(left)
            .checked_sub(ByteCount::new(right))
            .ok()
            .map(ByteOffset::get);

        prop_assert_eq!(count_add, left.checked_add(right));
        prop_assert_eq!(count_sub, left.checked_sub(right));
        prop_assert_eq!(offset_add, left.checked_add(right));
        prop_assert_eq!(offset_sub, left.checked_sub(right));
    }

    #[test]
    fn page_geometry_partitions_aligned_lengths_and_rejects_bounds(
        page_size in 1_u64..=4_096,
        page_count in 0_u64..=256,
    ) {
        let source_len = page_size * page_count;
        let geometry = PageGeometry::new(
            ByteCount::new(source_len),
            ByteCount::new(page_size),
        );
        prop_assert!(geometry.is_ok());
        let geometry = geometry?;
        prop_assert_eq!(geometry.source_len(), ByteCount::new(source_len));
        prop_assert_eq!(geometry.page_size(), ByteCount::new(page_size));
        prop_assert_eq!(geometry.page_count(), page_count);

        let mut previous_end = 0_u64;
        for page in 0..page_count {
            let range = geometry.page_byte_range(PageNumber::new(page));
            prop_assert!(range.is_ok());
            let (start, count) = range?;
            prop_assert_eq!(start.get(), previous_end);
            prop_assert_eq!(count.get(), page_size);
            previous_end = start.get() + count.get();
        }
        prop_assert_eq!(previous_end, source_len);
        prop_assert!(!geometry.contains(PageNumber::new(page_count)));
        let at_count_is_bounds = matches!(
            geometry.page_byte_range(PageNumber::new(page_count)),
            Err(Error::PageOutOfBounds { page, page_count: available })
                if page == page_count && available == page_count
        );
        prop_assert!(at_count_is_bounds);
        if page_count < u64::MAX {
            prop_assert!(geometry
                .page_byte_range(PageNumber::new(page_count + 1))
                .is_err());
        }
    }

    #[test]
    fn page_geometry_rejects_zero_and_partial_sizes(
        source_len in 0_u64..=1_048_576,
        page_size in 1_u64..=4_096,
    ) {
        let zero_is_invalid = matches!(
            PageGeometry::new(ByteCount::new(source_len), ByteCount::new(0)),
            Err(Error::InvalidPageSize { .. })
        );
        prop_assert!(zero_is_invalid);

        let result = PageGeometry::new(
            ByteCount::new(source_len),
            ByteCount::new(page_size),
        );
        if source_len % page_size == 0 {
            prop_assert!(result.is_ok());
        } else {
            let is_exact_partial = matches!(
                result,
                Err(Error::PartialPage { input_len, page_size: actual_size, trailing })
                    if input_len.get() == source_len
                        && actual_size.get() == page_size
                        && trailing.get() == source_len % page_size
            );
            prop_assert!(is_exact_partial);
        }
    }

    #[test]
    fn binary_cursor_decodes_little_endian_primitive_bits(
        values in any::<(u8, i8, u16, i16, u32, i32, u64, i64, u32, u64)>(),
    ) {
        let (u8_value, i8_value, u16_value, i16_value, u32_value, i32_value,
            u64_value, i64_value, f32_bits, f64_bits) = values;
        let mut input = Vec::with_capacity(42);
        input.push(u8_value);
        input.extend_from_slice(&i8_value.to_le_bytes());
        input.extend_from_slice(&u16_value.to_le_bytes());
        input.extend_from_slice(&i16_value.to_le_bytes());
        input.extend_from_slice(&u32_value.to_le_bytes());
        input.extend_from_slice(&i32_value.to_le_bytes());
        input.extend_from_slice(&u64_value.to_le_bytes());
        input.extend_from_slice(&i64_value.to_le_bytes());
        input.extend_from_slice(&f32_bits.to_le_bytes());
        input.extend_from_slice(&f64_bits.to_le_bytes());

        let input_len = input.len() as u64;
        let mut budget = ReadBudget::new(read_limits(input_len, input_len, input_len));
        let mut cursor = BinaryCursor::new(&input, &mut budget)?;
        prop_assert_eq!(cursor.read_u8()?, u8_value);
        prop_assert_eq!(cursor.read_i8()?, i8_value);
        prop_assert_eq!(cursor.read_u16_le()?, u16_value);
        prop_assert_eq!(cursor.read_i16_le()?, i16_value);
        prop_assert_eq!(cursor.read_u32_le()?, u32_value);
        prop_assert_eq!(cursor.read_i32_le()?, i32_value);
        prop_assert_eq!(cursor.read_u64_le()?, u64_value);
        prop_assert_eq!(cursor.read_i64_le()?, i64_value);
        prop_assert_eq!(cursor.read_f32_le()?.to_bits(), f32_bits);
        prop_assert_eq!(cursor.read_f64_le()?.to_bits(), f64_bits);
        prop_assert_eq!(cursor.position(), ByteOffset::new(input_len));
        prop_assert_eq!(cursor.total_read(), ByteCount::new(input_len));
        prop_assert_eq!(cursor.remaining()?, ByteCount::new(0));
    }

    #[test]
    fn slice_source_matches_checked_range_and_budget_model(
        input in proptest::collection::vec(any::<u8>(), 0..=MAX_SLICE_BYTES),
        offset in prop_oneof![0_u64..=4_352, Just(u64::MAX)],
        request_len in 0_usize..=4_352,
        single_limit in 0_u64..=4_352,
        total_limit in 0_u64..=4_352,
    ) {
        let input_len = input.len() as u64;
        let mut budget = ReadBudget::new(read_limits(input_len, single_limit, total_limit));
        let mut source = SliceSource::new(&input, &budget)?;
        let mut destination = vec![0xa5_u8; request_len];
        let before = destination.clone();
        let result = source.read_exact_at(
            ByteOffset::new(offset),
            &mut destination,
            &mut budget,
        );

        let count = request_len as u64;
        let within_budget = count <= single_limit && count <= total_limit;
        let end = offset.checked_add(count);
        let within_range = offset <= input_len && end.is_some_and(|end| end <= input_len);
        let should_succeed = within_budget && within_range;
        prop_assert_eq!(result.is_ok(), should_succeed);
        prop_assert_eq!(
            budget.total_read(),
            ByteCount::new(if should_succeed { count } else { 0 })
        );

        if should_succeed {
            let start = offset as usize;
            let end = start + request_len;
            prop_assert_eq!(destination.as_slice(), &input[start..end]);
        } else {
            prop_assert_eq!(destination, before);
            if !within_budget {
                let is_budget_error = matches!(
                    result,
                    Err(Error::LimitExceeded { .. }) | Err(Error::Arithmetic { .. })
                );
                prop_assert!(is_budget_error);
            } else if end.is_none() {
                let is_arithmetic = matches!(result, Err(Error::Arithmetic { .. }));
                prop_assert!(is_arithmetic);
            } else if offset > input_len {
                let is_bounds = matches!(result, Err(Error::OffsetOutOfBounds { .. }));
                prop_assert!(is_bounds);
            } else {
                let is_truncation = matches!(result, Err(Error::UnexpectedEnd { .. }));
                prop_assert!(is_truncation);
            }
        }
    }

    #[test]
    fn raw_page_reader_maps_aligned_pages_with_resource_bounds(
        page_count in 0_u64..=MAX_PAGES,
        requested_page in prop_oneof![0_u64..=(MAX_PAGES + 2), Just(u64::MAX)],
        single_limit in prop_oneof![Just(2_047_u64), Just(2_048), Just(4_096)],
        total_limit in prop_oneof![Just(2_047_u64), Just(2_048), Just(4_096)],
        page_limit in 0_u64..=2,
        work_limit in 0_u64..=2,
    ) {
        let input_len = page_count * PAGE_BYTES as u64;
        let input: Vec<u8> = (0..input_len)
            .map(|position| ((position / PAGE_BYTES as u64) ^ position) as u8)
            .collect();
        let limits = ResourceLimits::new(read_limits(input_len, single_limit, total_limit))
            .with_max_page_visits(page_limit)
            .with_max_total_work_units(work_limit);
        let mut budget = ResourceBudget::new(limits);
        let source = SliceSource::new(&input, budget.read_budget())?;
        let mut reader = Jet3PageReader::new(source)?;
        let mut destination = [0xa5_u8; PAGE_BYTES];
        let before = destination;
        let result = reader.read_page(
            PageNumber::new(requested_page),
            &mut destination,
            &mut budget,
        );

        let read_allowed = JET3_PAGE_SIZE.get() <= single_limit
            && JET3_PAGE_SIZE.get() <= total_limit;
        let page_exists = requested_page < page_count;
        let resource_allowed = page_limit >= 1 && work_limit >= 1;
        let should_succeed = read_allowed && page_exists && resource_allowed;
        prop_assert_eq!(result.is_ok(), should_succeed);

        if should_succeed {
            let start = requested_page as usize * PAGE_BYTES;
            prop_assert_eq!(destination.as_slice(), &input[start..start + PAGE_BYTES]);
            prop_assert_eq!(budget.read_budget().total_read(), JET3_PAGE_SIZE);
            prop_assert_eq!(budget.page_visits(), 1);
            prop_assert_eq!(budget.total_work_units(), 1);
        } else {
            prop_assert_eq!(destination, before);
            prop_assert_eq!(budget.read_budget().total_read(), ByteCount::new(0));
            prop_assert_eq!(budget.page_visits(), 0);
            prop_assert_eq!(budget.total_work_units(), 0);
        }
    }
}
