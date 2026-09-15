use super::*;
use crate::{RowDirectoryError, RowError};

fn overflow_bytes(move_self: bool) -> Vec<u8> {
    let mut bytes = database_bytes(b"MSysObjects", 4, 1, false);
    bytes.resize(6 * PAGE_BYTES, 0);
    let moved = if move_self {
        record(1, 1, 0x8000_0000, b"MSysObjects")
    } else {
        record(4, 1, 0, b"MovedTable")
    };
    let rows = if move_self {
        vec![vec![0, 5, 0, 0], record(4, 1, 0, b"OrdinaryTable")]
    } else {
        vec![record(1, 1, 0x8000_0000, b"MSysObjects"), vec![0, 5, 0, 0]]
    };
    write_rows(&mut bytes[3 * PAGE_BYTES..4 * PAGE_BYTES], &rows);
    let slot = usize::from(!move_self);
    bytes[3 * PAGE_BYTES + 11 + 2 * slot] |= 0x40;
    write_rows(&mut bytes[5 * PAGE_BYTES..6 * PAGE_BYTES], &[moved]);
    bytes[5 * PAGE_BYTES + 11] |= 0x80;
    bytes[5 * PAGE_BYTES + 4..5 * PAGE_BYTES + 8].copy_from_slice(&1_u32.to_le_bytes());
    // Catalog owns the target page too; hidden storage must not be emitted twice.
    bytes[3 * PAGE_BYTES - 1] |= 1 << 5;
    bytes
}

#[test]
fn moved_catalog_record_and_moved_self_record_stream_once() -> Result<(), Box<dyn std::error::Error>>
{
    for move_self in [false, true] {
        let bytes = overflow_bytes(move_self);
        let mut budget = operation(&bytes);
        let mut database = open(&bytes, &mut budget)?;
        let mut catalog = database.catalog(&mut budget)?;
        assert_eq!(catalog.next_record()?.ok_or("missing self")?.id().get(), 1);
        let user = catalog.next_record()?.ok_or("missing user")?;
        assert_eq!(user.id().get(), 4);
        assert_eq!(
            user.name().decoded_ascii(),
            Some(if move_self {
                "OrdinaryTable"
            } else {
                "MovedTable"
            })
        );
        assert!(catalog.next_record()?.is_none());
    }
    Ok(())
}

#[test]
fn malformed_catalog_overflow_exhausts_cursor() -> Result<(), Box<dyn std::error::Error>> {
    for case in 0..9 {
        let mut bytes = overflow_bytes(false);
        match case {
            0 => bytes[5 * PAGE_BYTES + 4] = 4,
            1 => bytes[5 * PAGE_BYTES + 11] &= !0x80,
            2 | 8 => {}
            3 => bytes[5 * PAGE_BYTES] = 3,
            4 => bytes[5 * PAGE_BYTES + 10..5 * PAGE_BYTES + 12]
                .copy_from_slice(&0xc800_u16.to_le_bytes()),
            5 | 6 => {
                write_rows(
                    &mut bytes[5 * PAGE_BYTES..6 * PAGE_BYTES],
                    &[vec![0, 5, 0, 0]],
                );
                bytes[5 * PAGE_BYTES + 11] |= 0xc0;
                if case == 6 {
                    bytes[6 * PAGE_BYTES - 4..6 * PAGE_BYTES].copy_from_slice(&[1, 3, 0, 0]);
                }
            }
            7 => bytes[3 * PAGE_BYTES + 12] += 1,
            _ => unreachable!(),
        }
        if case == 2 || case == 8 {
            let start = usize::from(
                u16::from_le_bytes([bytes[3 * PAGE_BYTES + 12], bytes[3 * PAGE_BYTES + 13]])
                    & 0x1fff,
            );
            bytes[3 * PAGE_BYTES + start + usize::from(case == 8)] = 9;
        }
        let mut budget = operation(&bytes);
        let mut database = open(&bytes, &mut budget)?;
        let mut catalog = database.catalog(&mut budget)?;
        catalog.next_record()?.ok_or("missing self")?;
        let error = catalog
            .next_record()
            .err()
            .ok_or("must reject overflow corruption")?;
        match (case, &error) {
            (
                0,
                CatalogError::Overflow(RowError::Directory(RowDirectoryError::UnexpectedOwner {
                    ..
                })),
            )
            | (1 | 4, CatalogError::Overflow(RowError::InvalidOverflowTarget { .. }))
            | (
                2,
                CatalogError::Overflow(RowError::Directory(RowDirectoryError::MissingRow {
                    ..
                })),
            )
            | (3, CatalogError::Overflow(RowError::UnexpectedOwnedPageKind { .. }))
            | (5, CatalogError::Overflow(RowError::SelfLink { .. }))
            | (7, CatalogError::InvalidOverflowPointerLength { .. })
            | (8, CatalogError::Overflow(RowError::Allocation(_)))
            | (6, CatalogError::Overflow(RowError::Cycle { .. })) => {}
            _ => return Err(format!("case {case}: {error:?}").into()),
        }
        let work = catalog.budget_mut().total_work_units();
        assert!(catalog.next_record()?.is_none());
        assert_eq!(catalog.budget_mut().total_work_units(), work);
    }
    Ok(())
}

#[test]
fn catalog_overflow_depth_limit_is_not_swallowed_during_discovery()
-> Result<(), Box<dyn std::error::Error>> {
    for maximum in [0, 1] {
        let bytes = overflow_bytes(true);
        let mut budget =
            ResourceBudget::new(operation(&bytes).limits().with_max_chain_depth(maximum));
        let mut database = open(&bytes, &mut budget)?;
        let result = database.catalog(&mut budget);
        if maximum == 0 {
            assert!(matches!(
                result,
                Err(CatalogError::Overflow(RowError::Resource(
                    Error::ResourceLimitExceeded {
                        kind: ResourceLimitKind::ChainDepth,
                        ..
                    }
                )))
            ));
        } else {
            assert!(result.is_ok());
        }
    }
    Ok(())
}

#[test]
fn catalog_source_slot_is_not_limited_by_the_target_locator_width()
-> Result<(), Box<dyn std::error::Error>> {
    for row in [255, 256, 1000] {
        let mut bytes = overflow_bytes(true);
        let mut rows = vec![Vec::new(); row];
        rows.push(vec![0, 5, 0, 0]);
        write_rows(&mut bytes[3 * PAGE_BYTES..4 * PAGE_BYTES], &rows);
        for slot in 0..row {
            bytes[3 * PAGE_BYTES + 11 + 2 * slot] |= 0xc0;
        }
        bytes[3 * PAGE_BYTES + 11 + 2 * row] |= 0x40;
        let mut budget = operation(&bytes);
        let mut database = open(&bytes, &mut budget)?;
        let mut catalog = database.catalog(&mut budget)?;
        assert_eq!(
            catalog
                .next_record()?
                .ok_or("missing moved self")?
                .id()
                .get(),
            1
        );
        assert!(catalog.next_record()?.is_none());
    }
    Ok(())
}
