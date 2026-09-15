use super::*;

#[test]
fn common_prefix_can_include_leaf_row_locator_bytes() -> Result<(), Box<dyn std::error::Error>> {
    let first = leaf_entry(&[0], 0);
    let second = leaf_entry(&[0], 1);
    for prefix_len in 0..first.len() {
        let mut bytes = database_bytes(4, 3, 4);
        write_node(
            &mut bytes,
            NodeSpec {
                page: INDEX_ROOT,
                tag: 4,
                previous: 0,
                next: 0,
                tail_child: 0,
                prefix: &first[..prefix_len],
                entries: &[&first, &second],
            },
        );
        let (tree, _) = traverse_with_limits(&bytes, limits(&bytes))?;
        assert_eq!(tree.entries().len(), 2);
        for (slot, entry) in tree.entries().iter().enumerate() {
            assert_eq!(entry.key().raw_bytes(), &[0]);
            assert_eq!(entry.row().page(), PageNumber::new(ROW_PAGE as u64));
            assert_eq!(usize::from(entry.row().slot()), slot);
        }
    }
    Ok(())
}

#[test]
fn branch_trailers_are_decoded_after_prefix_reconstruction()
-> Result<(), Box<dyn std::error::Error>> {
    let separator = branch_entry(&[0], 0, FIRST_LEAF);
    let first = leaf_entry(&[0], 0);
    let second = leaf_entry(&[0], 1);
    for prefix_len in 0..separator.len() {
        let mut bytes = database_bytes(4, 3, 4);
        write_node(
            &mut bytes,
            NodeSpec {
                page: INDEX_ROOT,
                tag: 3,
                previous: 0,
                next: 0,
                tail_child: SECOND_LEAF,
                prefix: &separator[..prefix_len],
                entries: &[&separator],
            },
        );
        for (page, previous, next, entry) in [
            (FIRST_LEAF, 0, SECOND_LEAF, &first),
            (SECOND_LEAF, FIRST_LEAF, 0, &second),
        ] {
            write_node(
                &mut bytes,
                NodeSpec {
                    page,
                    tag: 4,
                    previous,
                    next,
                    tail_child: 0,
                    prefix: &[],
                    entries: &[entry],
                },
            );
        }
        let (tree, _) = traverse_with_limits(&bytes, limits(&bytes))?;
        assert_eq!(tree.nodes().len(), 3);
        assert_eq!(tree.entries().len(), 2);
    }
    Ok(())
}
