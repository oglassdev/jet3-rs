use super::TableDefinitionError;
use crate::{
    AllocationTraversalError, ByteCount, ColumnPhysicalType, ColumnStorageClass, DatabaseReader,
    Error, IndexDefinitionError, IndexDefinitionKind, IndexDirection, JET3_PAGE_SIZE, PageNumber,
    ReadLimits, RelationshipSide, ResourceBudget, ResourceLimitKind, ResourceLimits, SliceSource,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const ROOT: usize = 1;
const MAP_PAGE: usize = 2;
const INDEX_ROOT: usize = 3;
const CONTINUATION: usize = 4;
const RELATED_ROOT: usize = 5;
const COLUMN_ONLY_OFFSET: usize = 43;
const COLUMN_OFFSET: usize = 51;
const PHYSICAL_OFFSET: usize = COLUMN_OFFSET + 18 + 3;
const LOGICAL_OFFSET: usize = PHYSICAL_OFFSET + 39;

fn column_record(physical_type: u8, class: u8, fixed_offset: u16, size: u16) -> [u8; 18] {
    let mut record = [0_u8; 18];
    record[0] = physical_type;
    record[7..9].copy_from_slice(&1_u16.to_le_bytes());
    record[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    record[13] = class;
    record[14..16].copy_from_slice(&fixed_offset.to_le_bytes());
    record[16..18].copy_from_slice(&size.to_le_bytes());
    record
}

fn definition_header(logical_count: u16, physical_count: u16) -> Vec<u8> {
    let mut bytes = vec![0_u8; 43];
    bytes[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    bytes[20] = 0x4e;
    bytes[21..23].copy_from_slice(&1_u16.to_le_bytes());
    bytes[25..27].copy_from_slice(&1_u16.to_le_bytes());
    bytes[27..29].copy_from_slice(&logical_count.to_le_bytes());
    bytes[31..33].copy_from_slice(&physical_count.to_le_bytes());
    bytes[35..39].copy_from_slice(&[0, MAP_PAGE as u8, 0, 0]);
    bytes[39..43].copy_from_slice(&[1, MAP_PAGE as u8, 0, 0]);
    bytes
}

fn finish(mut bytes: Vec<u8>) -> Vec<u8> {
    bytes.extend_from_slice(&[0xff, 0xff]);
    let length = u32::try_from(bytes.len()).unwrap_or_default();
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    bytes
}

fn column_only_definition() -> Vec<u8> {
    custom_column_definition(column_record(4, 3, 0, 4), 0)
}

fn custom_column_definition(record: [u8; 18], variable_count: u16) -> Vec<u8> {
    let mut bytes = definition_header(0, 0);
    bytes[23..25].copy_from_slice(&variable_count.to_le_bytes());
    bytes.extend_from_slice(&record);
    bytes.extend_from_slice(&[2, b'I', b'd']);
    finish(bytes)
}

fn physical_index(flags: u8) -> [u8; 39] {
    let mut record = [0_u8; 39];
    for slot in 0..10 {
        let offset = slot * 3;
        record[offset..offset + 2].copy_from_slice(&u16::MAX.to_le_bytes());
        record[offset + 2] = 0xa0_u8.saturating_add(u8::try_from(slot).unwrap_or_default());
    }
    record[..2].copy_from_slice(&0_u16.to_le_bytes());
    record[2] = 1;
    record[30] = 0;
    record[31..34].copy_from_slice(&[MAP_PAGE as u8, 0, 0]);
    record[34..38].copy_from_slice(&(INDEX_ROOT as u32).to_le_bytes());
    record[38] = flags;
    record
}

fn primary_definition() -> Vec<u8> {
    let mut bytes = definition_header(1, 1);
    bytes.extend_from_slice(&[1, 2, 3, 4, 5, 6, 7, 8]);
    bytes.extend_from_slice(&column_record(4, 3, 0, 4));
    bytes.extend_from_slice(&[2, b'I', b'd']);
    bytes.extend_from_slice(&physical_index(9));
    let mut logical = [0_u8; 20];
    logical[9..13].copy_from_slice(&u32::MAX.to_le_bytes());
    logical[17..19].copy_from_slice(&[4, 4]);
    logical[19] = 1;
    bytes.extend_from_slice(&logical);
    bytes.extend_from_slice(&[2, b'P', b'K']);
    finish(bytes)
}

fn relationship_definition() -> Vec<u8> {
    let mut bytes = definition_header(1, 1);
    bytes.extend_from_slice(&[0; 8]);
    bytes.extend_from_slice(&column_record(4, 3, 0, 4));
    bytes.extend_from_slice(&[2, b'I', b'd']);
    bytes.extend_from_slice(&physical_index(0));
    let mut logical = [0_u8; 20];
    logical[8] = 2;
    logical[9..13].copy_from_slice(&1_u32.to_le_bytes());
    logical[13..17].copy_from_slice(&(RELATED_ROOT as u32).to_le_bytes());
    logical[17..19].copy_from_slice(&[1, 1]);
    logical[19] = 2;
    bytes.extend_from_slice(&logical);
    bytes.extend_from_slice(&[3, b'R', b'e', b'l']);
    finish(bytes)
}

fn database_bytes(logical: &[u8], next: Option<usize>) -> Vec<u8> {
    let mut bytes = vec![0_u8; 6 * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    let root = &mut bytes[ROOT * PAGE_BYTES..(ROOT + 1) * PAGE_BYTES];
    let root_len = logical.len().min(PAGE_BYTES);
    root[..root_len].copy_from_slice(&logical[..root_len]);
    root[4..8].copy_from_slice(
        &u32::try_from(next.unwrap_or_default())
            .unwrap_or_default()
            .to_le_bytes(),
    );
    bytes[MAP_PAGE * PAGE_BYTES] = 1;
    bytes[INDEX_ROOT * PAGE_BYTES] = 4;
    bytes[RELATED_ROOT * PAGE_BYTES] = 2;
    if logical.len() > PAGE_BYTES {
        let page = &mut bytes[CONTINUATION * PAGE_BYTES..(CONTINUATION + 1) * PAGE_BYTES];
        page[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
        page[8..8 + logical.len() - PAGE_BYTES].copy_from_slice(&logical[PAGE_BYTES..]);
    }
    bytes
}

fn limits(bytes: &[u8]) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    ))
}

fn decode_with_limits(
    bytes: &[u8],
    limits: ResourceLimits,
) -> Result<(crate::TableDefinition, ResourceBudget), TableDefinitionError> {
    let mut budget = ResourceBudget::new(limits);
    let source =
        SliceSource::new(bytes, budget.read_budget()).map_err(TableDefinitionError::Resource)?;
    let mut database = DatabaseReader::from_source(source, &mut budget).map_err(|_| {
        TableDefinitionError::Resource(Error::Arithmetic {
            operation: "open synthetic table-definition database",
        })
    })?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    Ok((definition, budget))
}

fn decode(bytes: &[u8]) -> Result<crate::TableDefinition, TableDefinitionError> {
    decode_with_limits(bytes, limits(bytes)).map(|(definition, _)| definition)
}

#[test]
fn decodes_fixed_column_and_primary_index_losslessly() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(&primary_definition(), None);
    let definition = decode(&bytes)?;
    assert_eq!(definition.root(), PageNumber::new(1));
    assert_eq!(definition.columns().len(), 1);
    let column = &definition.columns()[0];
    assert_eq!(column.name().decoded_ascii(), Some("Id"));
    assert_eq!(column.physical_type(), ColumnPhysicalType::Long);
    assert_eq!(column.storage(), ColumnStorageClass::Fixed { offset: 0 });
    assert_eq!(definition.physical_indexes().len(), 1);
    let physical = &definition.physical_indexes()[0];
    assert_eq!(physical.sourced_prefix(), &[1, 2, 3, 4, 5, 6, 7, 8]);
    assert_eq!(physical.fields()[0].direction(), IndexDirection::Ascending);
    assert_eq!(physical.root(), PageNumber::new(INDEX_ROOT as u64));
    assert!(physical.unique() && physical.required());
    assert_eq!(definition.indexes()[0].name().decoded_ascii(), Some("PK"));
    assert_eq!(definition.indexes()[0].kind(), IndexDefinitionKind::Primary);
    Ok(())
}

#[test]
fn preserves_minimum_relationship_reference_without_cascade_claims()
-> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(&relationship_definition(), None);
    let definition = decode(&bytes)?;
    let IndexDefinitionKind::Relationship(reference) = definition.indexes()[0].kind() else {
        return Err("missing relationship definition".into());
    };
    assert_eq!(reference.side(), RelationshipSide::ForeignTable);
    assert_eq!(
        reference.related_table(),
        PageNumber::new(RELATED_ROOT as u64)
    );
    assert_eq!(reference.raw_relation_ordinal(), 1);
    assert_eq!(definition.indexes()[0].raw_record()[8], 2);
    Ok(())
}

#[test]
fn follows_exact_multi_page_chain_and_rejects_chain_corruption()
-> Result<(), Box<dyn std::error::Error>> {
    let mut logical = column_only_definition();
    logical.splice(logical.len() - 2..logical.len() - 2, vec![0x5a; PAGE_BYTES]);
    let length = u32::try_from(logical.len())?;
    logical[8..12].copy_from_slice(&length.to_le_bytes());
    let bytes = database_bytes(&logical, Some(CONTINUATION));
    let definition = decode(&bytes)?;
    assert_eq!(definition.logical_length(), length);
    assert_eq!(definition.raw_suffix().len(), PAGE_BYTES);

    let truncated = database_bytes(&logical, None);
    assert!(matches!(
        decode(&truncated),
        Err(TableDefinitionError::TruncatedChain { .. })
    ));

    let cycle = database_bytes(&logical, Some(ROOT));
    assert!(matches!(
        decode(&cycle),
        Err(TableDefinitionError::Chain(
            AllocationTraversalError::RepeatedPage { .. }
        ))
    ));

    let short = database_bytes(&column_only_definition(), Some(CONTINUATION));
    assert!(matches!(
        decode(&short),
        Err(TableDefinitionError::TrailingChainReference { .. })
    ));
    Ok(())
}

#[test]
fn rejects_count_ordinal_and_column_property_corruption() {
    let valid = column_only_definition();
    for (offset, value, expected) in [
        (25, 2, "count"),
        (COLUMN_ONLY_OFFSET + 1, 1, "ordinal"),
        (COLUMN_ONLY_OFFSET + 3, 1, "variable"),
        (COLUMN_ONLY_OFFSET + 7, 0, "constant"),
        (COLUMN_ONLY_OFFSET + 9, 0, "encoding"),
    ] {
        let mut logical = valid.clone();
        logical[offset] = value;
        let bytes = database_bytes(&logical, None);
        let result = decode(&bytes);
        assert!(result.is_err(), "{expected} corruption was accepted");
    }
}

#[test]
fn rejects_unsupported_type_size_class_offset_and_terminator() {
    let valid = column_only_definition();
    for (offset, value) in [
        (COLUMN_ONLY_OFFSET, 14),
        (COLUMN_ONLY_OFFSET + 13, 2),
        (COLUMN_ONLY_OFFSET + 14, 1),
        (COLUMN_ONLY_OFFSET + 16, 3),
    ] {
        let mut logical = valid.clone();
        logical[offset] = value;
        assert!(decode(&database_bytes(&logical, None)).is_err());
    }
    let mut logical = valid;
    let end = logical.len();
    logical[end - 1] = 0;
    assert!(matches!(
        decode(&database_bytes(&logical, None)),
        Err(TableDefinitionError::InvalidTerminator { .. })
    ));
}

#[test]
fn rejects_index_slots_flags_ordinals_and_reference_kinds() {
    let valid = primary_definition();
    for (offset, value) in [
        (PHYSICAL_OFFSET + 2, 2),
        (PHYSICAL_OFFSET + 3, 0),
        (PHYSICAL_OFFSET + 38, 0x80),
        (LOGICAL_OFFSET + 4, 1),
    ] {
        let mut logical = valid.clone();
        logical[offset] = value;
        assert!(matches!(
            decode(&database_bytes(&logical, None)),
            Err(TableDefinitionError::Index(_))
        ));
    }

    let mut logical = valid;
    logical[PHYSICAL_OFFSET + 34..PHYSICAL_OFFSET + 38]
        .copy_from_slice(&(MAP_PAGE as u32).to_le_bytes());
    assert!(matches!(
        decode(&database_bytes(&logical, None)),
        Err(TableDefinitionError::UnexpectedReferenceKind {
            role: "index root",
            ..
        })
    ));
}

#[test]
fn accepts_closed_type_inventory_and_text_size_boundaries() {
    let cases = [
        (1, 3, 1, 0),
        (2, 3, 1, 0),
        (3, 3, 2, 0),
        (4, 3, 4, 0),
        (5, 3, 8, 0),
        (6, 3, 4, 0),
        (7, 3, 8, 0),
        (8, 3, 8, 0),
        (9, 2, 13, 1),
        (10, 2, 1, 1),
        (10, 2, 255, 1),
        (10, 3, 255, 0),
        (11, 2, 0, 1),
        (12, 2, 0, 1),
        (15, 3, 16, 0),
        (4, 7, 4, 0),
    ];
    for (physical_type, class, size, variable_count) in cases {
        let logical =
            custom_column_definition(column_record(physical_type, class, 0, size), variable_count);
        assert!(
            decode(&database_bytes(&logical, None)).is_ok(),
            "type={physical_type} class={class} size={size}"
        );
    }
    for size in [0, 256] {
        let logical = custom_column_definition(column_record(10, 2, 0, size), 1);
        assert!(matches!(
            decode(&database_bytes(&logical, None)),
            Err(TableDefinitionError::UnsupportedColumnSize { .. })
        ));
    }
}

#[test]
fn rejects_truncated_counts_duplicate_keys_and_out_of_range_references() {
    let mut logical = column_only_definition();
    logical[21..23].copy_from_slice(&2_u16.to_le_bytes());
    logical[25..27].copy_from_slice(&2_u16.to_le_bytes());
    assert!(matches!(
        decode(&database_bytes(&logical, None)),
        Err(TableDefinitionError::Truncated { .. })
            | Err(TableDefinitionError::UnsupportedPhysicalType { .. })
    ));

    let valid = primary_definition();
    let mut duplicate = valid.clone();
    duplicate[PHYSICAL_OFFSET + 3..PHYSICAL_OFFSET + 5].copy_from_slice(&0_u16.to_le_bytes());
    duplicate[PHYSICAL_OFFSET + 5] = 1;
    assert!(matches!(
        decode(&database_bytes(&duplicate, None)),
        Err(TableDefinitionError::Index(
            IndexDefinitionError::DuplicateKeyColumn { .. }
        ))
    ));

    let mut bad_reference = valid;
    bad_reference[PHYSICAL_OFFSET + 34..PHYSICAL_OFFSET + 38].copy_from_slice(&6_u32.to_le_bytes());
    assert!(matches!(
        decode(&database_bytes(&bad_reference, None)),
        Err(TableDefinitionError::Index(
            IndexDefinitionError::InvalidPhysicalReference { .. }
        ))
    ));
}

#[test]
fn exact_allocation_item_and_chain_budgets_reject_one_over()
-> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(&column_only_definition(), None);
    let (_, observed) = decode_with_limits(&bytes, limits(&bytes))?;
    let allocation = observed.allocation_bytes();
    let items = observed.item_work();

    decode_with_limits(&bytes, limits(&bytes).with_max_allocation_bytes(allocation))?;
    let one_less = ByteCount::new(allocation.get().checked_sub(1).ok_or("zero allocation")?);
    assert!(matches!(
        decode_with_limits(&bytes, limits(&bytes).with_max_allocation_bytes(one_less),),
        Err(TableDefinitionError::Resource(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::AllocationBytes,
                ..
            }
        )) | Err(TableDefinitionError::Chain(
            AllocationTraversalError::Resource(Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::AllocationBytes,
                ..
            })
        ))
    ));

    decode_with_limits(&bytes, limits(&bytes).with_max_item_work(items))?;
    assert!(matches!(
        decode_with_limits(&bytes, limits(&bytes).with_max_item_work(items - 1)),
        Err(TableDefinitionError::Resource(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::ItemWork,
                ..
            }
        )) | Err(TableDefinitionError::Index(IndexDefinitionError::Resource(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::ItemWork,
                ..
            }
        )))
    ));

    let mut logical = column_only_definition();
    logical.splice(logical.len() - 2..logical.len() - 2, vec![0; PAGE_BYTES]);
    let length = u32::try_from(logical.len())?;
    logical[8..12].copy_from_slice(&length.to_le_bytes());
    let chained = database_bytes(&logical, Some(CONTINUATION));
    decode_with_limits(&chained, limits(&chained).with_max_chain_depth(2))?;
    assert!(matches!(
        decode_with_limits(&chained, limits(&chained).with_max_chain_depth(1)),
        Err(TableDefinitionError::Chain(
            AllocationTraversalError::Resource(Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::ChainDepth,
                ..
            })
        ))
    ));
    Ok(())
}
