use super::table_tests::*;
use crate::{
    AllocationTraversalError, Error, PageNumber, ResourceLimitKind,
    definition::table::TableDefinitionError,
};

fn exact_definition(length: usize) -> Vec<u8> {
    let count = if length == PAGE_BYTES { 80 } else { 128 };
    let mut columns = (0..count)
        .map(|n| (4, 3, n as u16 * 4, 4, format!("C{n:04}").into_bytes()))
        .collect::<Vec<_>>();
    for n in 0..length - 45 - count * 24 {
        columns[n % count].4.push(b'x');
    }
    build_definition(USER_MARKER, &columns, &[], &[], &[])
}

fn terminal_bytes(length: usize) -> (Vec<u8>, usize) {
    let logical = exact_definition(length);
    let mut bytes = database_bytes(&logical, Some(CONTINUATION));
    let terminal = if length == PAGE_BYTES {
        CONTINUATION
    } else {
        RELATED_ROOT
    };
    if terminal == RELATED_ROOT {
        bytes[CONTINUATION * PAGE_BYTES + 4..CONTINUATION * PAGE_BYTES + 8]
            .copy_from_slice(&(terminal as u32).to_le_bytes());
    }
    bytes[terminal * PAGE_BYTES..terminal * PAGE_BYTES + 4].copy_from_slice(&[2, 1, 0x56, 0x43]);
    bytes[terminal * PAGE_BYTES + 8..(terminal + 1) * PAGE_BYTES].fill(0xa5);
    (bytes, terminal)
}

#[test]
fn exact_boundary_terminal_payload_is_slack_and_the_page_is_budgeted()
-> Result<(), Box<dyn std::error::Error>> {
    for length in [PAGE_BYTES, 2 * PAGE_BYTES - 8] {
        let (bytes, terminal) = terminal_bytes(length);
        let (definition, budget) = decode_with_limits(&bytes, limits(&bytes))?;
        assert_eq!(definition.logical_length() as usize, length);
        let expected = if terminal == CONTINUATION {
            vec![ROOT, CONTINUATION]
        } else {
            vec![ROOT, CONTINUATION, RELATED_ROOT]
        };
        assert_eq!(
            definition.pages(),
            expected
                .into_iter()
                .map(|p| PageNumber::new(p as u64))
                .collect::<Vec<_>>()
        );
        let depth = if length == PAGE_BYTES { 2 } else { 3 };
        assert!(matches!(
            decode_with_limits(&bytes, limits(&bytes).with_max_chain_depth(depth - 1)),
            Err(TableDefinitionError::Chain(
                AllocationTraversalError::Resource(Error::ResourceLimitExceeded {
                    kind: ResourceLimitKind::ChainDepth,
                    ..
                })
            ))
        ));
        assert!(budget.page_visits() >= depth);
    }
    Ok(())
}

#[test]
fn terminal_prefix_reference_and_page_kind_remain_checked() {
    let (bytes, terminal) = terminal_bytes(PAGE_BYTES);
    for (offset, replacement) in [(terminal * PAGE_BYTES, 1), (terminal * PAGE_BYTES + 2, 0)] {
        let mut corrupted = bytes.clone();
        corrupted[offset] = replacement;
        assert!(decode(&corrupted).is_err());
    }
    for next in [ROOT, RELATED_ROOT] {
        let mut corrupted = bytes.clone();
        corrupted[terminal * PAGE_BYTES + 4..terminal * PAGE_BYTES + 8]
            .copy_from_slice(&(next as u32).to_le_bytes());
        assert!(matches!(
            decode(&corrupted),
            Err(TableDefinitionError::TrailingChainReference { .. })
        ));
    }
    for next in [ROOT, 99] {
        let mut corrupted = bytes.clone();
        corrupted[ROOT * PAGE_BYTES + 4..ROOT * PAGE_BYTES + 8]
            .copy_from_slice(&(next as u32).to_le_bytes());
        assert!(matches!(
            decode(&corrupted),
            Err(TableDefinitionError::Chain(_))
        ));
    }
}
