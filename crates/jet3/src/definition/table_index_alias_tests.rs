use super::table_tests::*;
use crate::{IndexDefinitionError, IndexDefinitionKind, definition::table::TableDefinitionError};

#[test]
fn ordinary_logical_aliases_share_one_physical_index() -> Result<(), Box<dyn std::error::Error>> {
    let mut first = [0_u8; 20];
    first[9..13].copy_from_slice(&u32::MAX.to_le_bytes());
    first[17..19].copy_from_slice(&[4, 4]);
    let mut second = first;
    second[..4].copy_from_slice(&1_u32.to_le_bytes());
    let bytes = build_definition(
        USER_MARKER,
        &[(4, 3, 0, 4, b"Id".to_vec())],
        &[physical_index(0)],
        &[(first, b"FkA"), (second, b"FkB")],
        &[],
    );
    let definition = decode(&database_bytes(&bytes, None))?;
    assert_eq!(definition.physical_indexes().len(), 1);
    assert_eq!(definition.indexes().len(), 2);
    for (index, expected) in definition.indexes().iter().zip([first, second]) {
        assert_eq!(index.physical_index(), 0);
        assert_eq!(index.kind(), IndexDefinitionKind::Ordinary);
        assert_eq!(index.raw_record(), &expected);
    }
    second[4..8].copy_from_slice(&1_u32.to_le_bytes());
    let invalid = build_definition(
        USER_MARKER,
        &[(4, 3, 0, 4, b"Id".to_vec())],
        &[physical_index(0)],
        &[(first, b"FkA"), (second, b"FkB")],
        &[],
    );
    assert!(matches!(
        decode(&database_bytes(&invalid, None)),
        Err(TableDefinitionError::Index(
            IndexDefinitionError::InvalidPhysicalIndexOrdinal {
                logical_index: 1,
                ordinal: 1,
                physical_count: 1,
            }
        ))
    ));
    Ok(())
}
