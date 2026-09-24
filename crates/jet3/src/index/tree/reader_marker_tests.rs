use super::reader_tests::*;
use crate::{
    Error, ResourceLimitKind,
    index::tree::reader::{IndexNodeKind, IndexTreeError},
};

fn three_level_tree() -> Vec<u8> {
    let mut bytes = database_bytes(4, 3, 4);
    bytes.resize(11 * PAGE_BYTES, 0);
    for (slot, page) in (7..=10).enumerate() {
        let entry = leaf_entry(&[0x7f, 0x80, 0, 0, slot as u8], slot as u8);
        write_node(
            &mut bytes,
            NodeSpec {
                page,
                tag: 4,
                previous: if page == 7 { 0 } else { page - 1 },
                next: if page == 10 { 0 } else { page + 1 },
                tail_child: 0,
                prefix: &[0x7f, 0x80, 0, 0],
                entries: &[&entry],
            },
        );
    }
    for (page, previous, next, child, tail, slot) in [
        (4, 0, 5, 7, 8, 0),
        (5, 4, 0, 9, 10, 2),
        (INDEX_ROOT, 0, 0, 4, 5, 1),
    ] {
        let entry = branch_entry(&[0x7f, 0x80, 0, 0, slot], slot, child);
        write_node(
            &mut bytes,
            NodeSpec {
                page,
                tag: 3,
                previous,
                next,
                tail_child: tail,
                prefix: &[],
                entries: &[&entry],
            },
        );
    }
    bytes
}

#[test]
fn observed_root_classes_keep_three_level_traversal_and_depth_bounds()
-> Result<(), Box<dyn std::error::Error>> {
    let mut bytes = three_level_tree();
    for marker in [1, 2, 3] {
        bytes[INDEX_ROOT * PAGE_BYTES + 21] = marker;
        let (tree, _) = traverse_with_limits(&bytes, limits(&bytes).with_max_chain_depth(3))?;
        assert_eq!(tree.nodes().len(), 7);
        assert_eq!(tree.entries().len(), 4);
        for (slot, entry) in tree.entries().iter().enumerate() {
            assert_eq!(entry.key().raw_bytes(), &[0x7f, 0x80, 0, 0, slot as u8]);
            assert_eq!(entry.row().slot(), slot as u8);
        }
        assert!(
            tree.nodes()
                .iter()
                .filter(|node| node.kind() == IndexNodeKind::Leaf)
                .all(|node| node.depth() == 3)
        );
        let error = traverse_with_limits(&bytes, limits(&bytes).with_max_chain_depth(2))
            .err()
            .ok_or("depth limit unexpectedly succeeded")?;
        assert!(matches!(
            error.downcast_ref::<IndexTreeError>(),
            Some(IndexTreeError::Resource(Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::ChainDepth,
                ..
            }))
        ));
    }
    Ok(())
}

#[test]
fn unknown_branch_and_nonzero_leaf_markers_remain_rejected() {
    for (page, marker) in [
        (INDEX_ROOT, 0),
        (INDEX_ROOT, 4),
        (4, 255),
        (7, 1),
        (7, 2),
        (7, 3),
    ] {
        let mut bytes = three_level_tree();
        bytes[INDEX_ROOT * PAGE_BYTES + 21] = 2;
        bytes[page * PAGE_BYTES + 21] = marker;
        let result = traverse_with_limits(&bytes, limits(&bytes));
        assert!(
            matches!(result, Err(error) if matches!(error.downcast_ref::<IndexTreeError>(),
            Some(IndexTreeError::InvalidHeaderMarker { page: actual, offset: 21, raw })
                if actual.get() == page as u64 && *raw == marker))
        );
    }
}

#[test]
fn retained_tail_only_root_traverses_and_checks_its_child() -> Result<(), Box<dyn std::error::Error>>
{
    let mut bytes = branch_tree();
    bytes[INDEX_ROOT * PAGE_BYTES..(INDEX_ROOT + 1) * PAGE_BYTES].fill(0);
    write_node(
        &mut bytes,
        NodeSpec {
            page: INDEX_ROOT,
            tag: 3,
            previous: 0,
            next: 0,
            tail_child: FIRST_LEAF,
            prefix: &[],
            entries: &[],
        },
    );
    bytes[FIRST_LEAF * PAGE_BYTES + 12..FIRST_LEAF * PAGE_BYTES + 16]
        .copy_from_slice(&0_u32.to_le_bytes());
    let (tree, _) = traverse_with_limits(&bytes, limits(&bytes).with_max_chain_depth(2))?;
    assert_eq!(tree.nodes().len(), 2);
    assert_eq!(tree.entries().len(), 2);
    assert_eq!(tree.entries()[0].row().slot(), 0);
    assert_eq!(tree.entries()[1].row().slot(), 1);
    assert_eq!(tree.nodes()[1].depth(), 2);
    let error = traverse_with_limits(&bytes, limits(&bytes).with_max_chain_depth(1))
        .err()
        .ok_or("tail child ignored depth limit")?;
    assert!(matches!(
        error.downcast_ref::<IndexTreeError>(),
        Some(IndexTreeError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::ChainDepth,
            ..
        }))
    ));
    for child in [INDEX_ROOT, PAGE_COUNT] {
        let mut invalid = bytes.clone();
        invalid[INDEX_ROOT * PAGE_BYTES + 16..INDEX_ROOT * PAGE_BYTES + 20]
            .copy_from_slice(&(child as u32).to_le_bytes());
        let error = traverse_with_limits(&invalid, limits(&invalid))
            .err()
            .ok_or("invalid tail child accepted")?;
        assert!(matches!(
            error.downcast_ref::<IndexTreeError>(),
            Some(IndexTreeError::SelfReference { .. } | IndexTreeError::InvalidReference { .. })
        ));
    }
    bytes[INDEX_ROOT * PAGE_BYTES + 21] = 2;
    let error = traverse_with_limits(&bytes, limits(&bytes))
        .err()
        .ok_or("unobserved empty root class accepted")?;
    assert!(matches!(
        error.downcast_ref::<IndexTreeError>(),
        Some(IndexTreeError::EmptyIntermediate { .. })
    ));
    let mut nested = three_level_tree();
    nested[4 * PAGE_BYTES + 2..4 * PAGE_BYTES + 4].copy_from_slice(&1800_u16.to_le_bytes());
    nested[4 * PAGE_BYTES + 20..4 * PAGE_BYTES + 248].fill(0);
    nested[4 * PAGE_BYTES + 21] = 1;
    let error = traverse_with_limits(&nested, limits(&nested))
        .err()
        .ok_or("unobserved nested empty branch accepted")?;
    assert!(matches!(
        error.downcast_ref::<IndexTreeError>(),
        Some(IndexTreeError::EmptyIntermediate { .. })
    ));
    Ok(())
}
