use super::table::{TableDefinitionError, TableDefinitionKind};
use crate::{
    AllocationTraversalError, ByteCount, ColumnPhysicalType, ColumnStorageClass, DatabaseReader,
    Error, IndexDefinitionError, IndexDefinitionKind, IndexDirection, JET3_PAGE_SIZE,
    LongValueMapError, PAGE_BYTES, PageNumber, ReadLimits, RelationshipSide, ResourceBudget,
    ResourceLimitKind, ResourceLimits, SliceSource,
};

use crate::testkit::TestResult;

pub(super) const ROOT: usize = 1;
pub(super) const MAP_PAGE: usize = 2;
const MAP_ROWS: u16 = 4;
pub(super) const INDEX_ROOT: usize = 3;
pub(super) const CONTINUATION: usize = 4;
pub(super) const RELATED_ROOT: usize = 5;
pub(super) const COLUMN_ONLY_OFFSET: usize = 43;
const COLUMN_OFFSET: usize = 51;
pub(super) const PHYSICAL_OFFSET: usize = COLUMN_OFFSET + 18 + 3;
const LOGICAL_OFFSET: usize = PHYSICAL_OFFSET + 39;
pub(super) const USER_MARKER: u8 = 0x4e;
pub(super) const SYSTEM_MARKER: u8 = 0x53;

/// One column for [`build_definition`]: type, class, fixed offset, size, name.
pub(super) type ColumnSpec = (u8, u8, u16, u16, Vec<u8>);

/// Builds a complete logical definition under either marker, deriving the
/// variable counters, the marker-specific constant and ordinal repeat, and
/// the header counts from the supplied records.
pub(super) fn build_definition(
    marker: u8,
    columns: &[ColumnSpec],
    physical: &[[u8; 39]],
    logical: &[([u8; 20], &[u8])],
    suffix: &[u8],
) -> Vec<u8> {
    let mut bytes = definition_header(logical.len() as u16, physical.len() as u16);
    bytes[20] = marker;
    let variable_count = columns.iter().filter(|column| column.1 & 7 == 2).count() as u16;
    bytes[21..23].copy_from_slice(&(columns.len() as u16).to_le_bytes());
    bytes[23..25].copy_from_slice(&variable_count.to_le_bytes());
    bytes[25..27].copy_from_slice(&(columns.len() as u16).to_le_bytes());
    bytes.extend(std::iter::repeat_n(0, physical.len() * 8));
    let mut variables_seen = 0_u16;
    for (ordinal, (physical_type, class, fixed_offset, size, _)) in columns.iter().enumerate() {
        let ordinal = ordinal as u16;
        let mut record = column_record(*physical_type, *class, *fixed_offset, *size);
        record[1..3].copy_from_slice(&ordinal.to_le_bytes());
        if marker == SYSTEM_MARKER {
            record[5..7].fill(0);
            record[7..9].fill(0);
        } else {
            record[5..7].copy_from_slice(&ordinal.to_le_bytes());
        }
        record[3..5].copy_from_slice(&variables_seen.to_le_bytes());
        if class & 7 == 2 {
            variables_seen += 1;
        }
        bytes.extend_from_slice(&record);
    }
    for (_, _, _, _, name) in columns {
        bytes.push(name.len() as u8);
        bytes.extend_from_slice(name);
    }
    for record in physical {
        bytes.extend_from_slice(record);
    }
    for (record, _) in logical {
        bytes.extend_from_slice(record);
    }
    for (_, name) in logical {
        bytes.push(name.len() as u8);
        bytes.extend_from_slice(name);
    }
    bytes.extend_from_slice(suffix);
    finish(bytes)
}

pub(super) fn group(ordinal: u16, owned_row: u8, available_row: u8, page: u8) -> [u8; 10] {
    let [low, high] = ordinal.to_le_bytes();
    [low, high, owned_row, page, 0, 0, available_row, page, 0, 0]
}

/// A definition whose column records and suffix span two pages.
fn many_memo_definition() -> Vec<u8> {
    let columns: Vec<ColumnSpec> = (0..120_u16)
        .map(|ordinal| (12, 2, 0, 0, format!("M{ordinal}").into_bytes()))
        .collect();
    let suffix: Vec<u8> = (0..120_u16)
        .rev()
        .flat_map(|ordinal| group(ordinal, 0, 1, MAP_PAGE as u8))
        .collect();
    build_definition(USER_MARKER, &columns, &[], &[], &suffix)
}

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

pub(super) fn column_only_definition() -> Vec<u8> {
    custom_column_definition(column_record(4, 3, 0, 4), 0)
}

fn custom_column_definition(record: [u8; 18], variable_count: u16) -> Vec<u8> {
    let physical_type = record[0];
    let mut bytes = definition_header(0, 0);
    bytes[23..25].copy_from_slice(&variable_count.to_le_bytes());
    bytes.extend_from_slice(&record);
    bytes.extend_from_slice(&[2, b'I', b'd']);
    if matches!(physical_type, 11 | 12) {
        bytes.extend_from_slice(&group(0, 0, 1, MAP_PAGE as u8));
    }
    finish(bytes)
}

fn fixed_long_boolean_byte_definition(boolean_offset: u16, byte_offset: u16) -> Vec<u8> {
    let columns: [ColumnSpec; 3] = [
        (4, 3, 0, 4, b"Id".to_vec()),
        (1, 3, boolean_offset, 1, b"Flag".to_vec()),
        (2, 3, byte_offset, 1, b"Next".to_vec()),
    ];
    build_definition(USER_MARKER, &columns, &[], &[], &[])
}

pub(super) fn physical_index(flags: u8) -> [u8; 39] {
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

pub(super) fn primary_definition() -> Vec<u8> {
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

fn primary_side_relationship_definition() -> Vec<u8> {
    let mut bytes = relationship_definition();
    bytes[LOGICAL_OFFSET + 8] = 1;
    bytes
}

pub(super) fn database_bytes(logical: &[u8], next: Option<usize>) -> Vec<u8> {
    let mut bytes = crate::testkit::database_image(6);
    let root = &mut bytes[ROOT * PAGE_BYTES..(ROOT + 1) * PAGE_BYTES];
    let root_len = logical.len().min(PAGE_BYTES);
    root[..root_len].copy_from_slice(&logical[..root_len]);
    root[4..8].copy_from_slice(
        &u32::try_from(next.unwrap_or_default())
            .unwrap_or_default()
            .to_le_bytes(),
    );
    let map = &mut bytes[MAP_PAGE * PAGE_BYTES..(MAP_PAGE + 1) * PAGE_BYTES];
    map[0] = 1;
    map[8..10].copy_from_slice(&MAP_ROWS.to_le_bytes());
    for row in 0..usize::from(MAP_ROWS) {
        let start = (PAGE_BYTES - 8 * (row + 1)) as u16;
        map[10 + 2 * row..12 + 2 * row].copy_from_slice(&start.to_le_bytes());
    }
    bytes[INDEX_ROOT * PAGE_BYTES] = 4;
    bytes[RELATED_ROOT * PAGE_BYTES] = 2;
    if logical.len() > PAGE_BYTES {
        let page = &mut bytes[CONTINUATION * PAGE_BYTES..(CONTINUATION + 1) * PAGE_BYTES];
        page[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
        page[8..8 + logical.len() - PAGE_BYTES].copy_from_slice(&logical[PAGE_BYTES..]);
    }
    bytes
}

pub(super) fn limits(bytes: &[u8]) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    ))
}

pub(super) fn decode_with_limits(
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

pub(super) fn decode(bytes: &[u8]) -> Result<crate::TableDefinition, TableDefinitionError> {
    decode_with_limits(bytes, limits(bytes)).map(|(definition, _)| definition)
}

/// Decodes a single-page logical definition stored at the root.
pub(super) fn decode_logical(
    logical: &[u8],
) -> Result<crate::TableDefinition, TableDefinitionError> {
    decode(&database_bytes(logical, None))
}

#[test]
fn decodes_fixed_column_and_primary_index_losslessly() -> TestResult {
    let bytes = database_bytes(&primary_definition(), None);
    let definition = decode(&bytes)?;
    assert_eq!(definition.root(), PageNumber::new(1));
    assert_eq!(
        definition.maps().owned().page(),
        PageNumber::new(MAP_PAGE as u64)
    );
    assert_eq!(definition.maps().available().row(), 1);
    assert_eq!(definition.raw_header()[20], 0x4e);
    assert_eq!(definition.kind(), TableDefinitionKind::User);
    assert!(definition.long_value_maps().is_empty());
    assert_eq!(definition.columns().len(), 1);
    let column = &definition.columns()[0];
    assert_eq!(column.name().decoded_ascii(), Some("Id"));
    assert_eq!(column.name().raw_bytes(), b"Id");
    assert_eq!(
        column.name().encoding(),
        crate::DefinitionNameEncoding::DatabaseCodePage
    );
    assert_eq!(column.ordinal().get(), 0);
    assert_eq!(column.physical_type(), ColumnPhysicalType::Long);
    assert_eq!(column.physical_type().raw(), 4);
    assert_eq!(column.storage(), ColumnStorageClass::Fixed { offset: 0 });
    assert_eq!(column.size(), 4);
    assert!(!column.auto_increment());
    assert_eq!(column.raw_variable_counter(), 0);
    assert_eq!(column.sourced_constant(), 1);
    assert_eq!(column.raw_encoding_context(), &[0x09, 0x04, 0xe4, 0x04]);
    assert_eq!(column.raw_class_flags(), 3);
    assert_eq!(column.raw_record()[0], 4);
    assert_eq!(definition.physical_indexes().len(), 1);
    let physical = &definition.physical_indexes()[0];
    assert_eq!(physical.sourced_prefix(), &[1, 2, 3, 4, 5, 6, 7, 8]);
    assert_eq!(physical.fields()[0].direction(), IndexDirection::Ascending);
    assert_eq!(physical.fields()[0].column().get(), 0);
    assert_eq!(
        physical.usage_map().page(),
        PageNumber::new(MAP_PAGE as u64)
    );
    assert_eq!(physical.usage_map().row(), 0);
    assert_eq!(physical.root(), PageNumber::new(INDEX_ROOT as u64));
    assert!(physical.unique() && physical.required());
    assert_eq!(physical.raw_flags(), 9);
    assert_eq!(physical.raw_record()[38], 9);
    assert_eq!(definition.indexes()[0].name().decoded_ascii(), Some("PK"));
    assert_eq!(definition.indexes()[0].physical_index(), 0);
    assert_eq!(definition.indexes()[0].kind(), IndexDefinitionKind::Primary);
    assert_eq!(definition.indexes()[0].raw_record()[19], 1);
    assert!(definition.relationships().next().is_none());
    Ok(())
}

#[test]
fn preserves_minimum_relationship_reference_without_cascade_claims() -> TestResult {
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
    assert_eq!(reference.raw_selector(), 0);
    assert_eq!(reference.raw_context(), [1, 1]);
    assert!(reference.cascade_updates());
    assert!(reference.cascade_deletes());
    assert_eq!(definition.indexes()[0].raw_record()[8], 2);

    let relationship = definition
        .relationships()
        .next()
        .ok_or("missing relationship")?;
    assert_eq!(relationship.name().decoded_ascii(), Some("Rel"));
    assert_eq!(relationship.physical_index(), 0);
    assert_eq!(relationship.side(), RelationshipSide::ForeignTable);
    assert_eq!(
        relationship.related_table(),
        PageNumber::new(RELATED_ROOT as u64)
    );
    assert_eq!(relationship.raw_selector(), 0);
    assert_eq!(relationship.raw_relation_ordinal(), 1);
    assert_eq!(relationship.raw_context(), [1, 1]);
    assert_eq!(relationship.raw_record()[8], 2);
    assert_eq!(definition.relationships().size_hint(), (0, Some(1)));
    assert!(definition.relationships().nth(1).is_none());

    for context in [[0, 0], [1, 0], [0, 1], [1, 1]] {
        let mut logical = relationship_definition();
        logical[LOGICAL_OFFSET + 17..LOGICAL_OFFSET + 19].copy_from_slice(&context);
        let decoded = decode_logical(&logical)?;
        let item = decoded
            .relationships()
            .next()
            .ok_or("missing relationship")?;
        assert_eq!(item.raw_context(), context);
        assert_eq!(item.cascade_updates(), context[0] == 1);
        assert_eq!(item.cascade_deletes(), context[1] == 1);
    }
    Ok(())
}

#[test]
fn reads_both_sides_of_a_self_referencing_relationship() -> TestResult {
    // EXP-0273: both reciprocal records can point back to their own definition.
    for (mut logical, side) in [
        (relationship_definition(), RelationshipSide::ForeignTable),
        (
            primary_side_relationship_definition(),
            RelationshipSide::PrimaryTable,
        ),
    ] {
        logical[LOGICAL_OFFSET + 13..LOGICAL_OFFSET + 17]
            .copy_from_slice(&(ROOT as u32).to_le_bytes());
        let definition = decode_logical(&logical)?;
        let relation = definition.relationships().next().ok_or("relation absent")?;
        assert_eq!(relation.related_table(), definition.root());
        assert_eq!(relation.side(), side);
    }
    Ok(())
}

#[test]
fn follows_exact_multi_page_chain_and_rejects_chain_corruption() -> TestResult {
    let logical = many_memo_definition();
    assert!(logical.len() > PAGE_BYTES);
    let length = u32::try_from(logical.len())?;
    let bytes = database_bytes(&logical, Some(CONTINUATION));
    let definition = decode(&bytes)?;
    assert_eq!(definition.logical_length(), length);
    assert_eq!(definition.raw_suffix().len(), 1200);
    assert_eq!(definition.long_value_maps().len(), 120);
    assert_eq!(definition.long_value_maps()[0].column().get(), 119);

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

/// Decodes `logical` with byte `offset` replaced by `value`.
fn corrupt(
    logical: &[u8],
    offset: usize,
    value: u8,
) -> Result<crate::TableDefinition, TableDefinitionError> {
    let mut logical = logical.to_vec();
    logical[offset] = value;
    decode_logical(&logical)
}

#[test]
fn rejects_header_and_column_record_corruption() -> TestResult {
    let valid = column_only_definition();
    let column = COLUMN_ONLY_OFFSET;
    // Count, ordinal, variable counter, constant, encoding, type, class,
    // fixed offset and size.
    for (offset, value) in [
        (25, 2),
        (column + 1, 1),
        (column + 3, 1),
        (column + 7, 0),
        (column + 9, 0),
        (column, 14),
        (column + 13, 2),
        (column + 14, 1),
        (column + 16, 3),
    ] {
        assert!(corrupt(&valid, offset, value).is_err(), "offset {offset}");
    }
    assert!(matches!(
        corrupt(&valid, 20, 0),
        Err(TableDefinitionError::InvalidHeaderMarker { .. })
    ));
    assert!(matches!(
        corrupt(&valid, 29, 1),
        Err(TableDefinitionError::UnsupportedReservedCount { .. })
    ));
    assert!(matches!(
        corrupt(&valid, 8, 1),
        Err(TableDefinitionError::InvalidLogicalLength { .. })
    ));
    assert!(matches!(
        corrupt(&valid, valid.len() - 1, 0),
        Err(TableDefinitionError::InvalidTerminator { .. })
    ));

    let mut chained = valid;
    chained.splice(chained.len() - 2..chained.len() - 2, vec![0; PAGE_BYTES]);
    let logical_length = u32::try_from(chained.len())?;
    chained[8..12].copy_from_slice(&logical_length.to_le_bytes());
    let mut bytes = database_bytes(&chained, Some(CONTINUATION));
    bytes[CONTINUATION * PAGE_BYTES + 1] = 0;
    assert!(matches!(
        decode(&bytes),
        Err(TableDefinitionError::InvalidPrefix { .. })
    ));
    Ok(())
}

#[test]
fn rejects_index_slot_flag_ordinal_reference_and_class_corruption() -> TestResult {
    let primary = primary_definition();
    // Slot order, key column, flags, logical ordinal, relationship side,
    // related table, required flags, and class.
    for (offset, value) in [
        (PHYSICAL_OFFSET + 2, 2),
        (PHYSICAL_OFFSET + 3, 0),
        (PHYSICAL_OFFSET + 38, 0x80),
        (LOGICAL_OFFSET + 4, 1),
        (LOGICAL_OFFSET + 8, 1),
        (LOGICAL_OFFSET + 9, 0),
        (PHYSICAL_OFFSET + 38, 8),
        (LOGICAL_OFFSET + 19, 3),
    ] {
        assert!(
            matches!(
                corrupt(&primary, offset, value),
                Err(TableDefinitionError::Index(_))
            ),
            "offset {offset}"
        );
    }
    let relationship = relationship_definition();
    for (offset, value) in [
        (LOGICAL_OFFSET + 17, 4),
        (LOGICAL_OFFSET + 8, 3),
        (LOGICAL_OFFSET + 13, 0),
        (LOGICAL_OFFSET + 13, 6),
    ] {
        assert!(
            matches!(
                corrupt(&relationship, offset, value),
                Err(TableDefinitionError::Index(_))
            ),
            "offset {offset}"
        );
    }
    assert_eq!(
        corrupt(&primary, LOGICAL_OFFSET + 19, 0)?.indexes()[0].kind(),
        IndexDefinitionKind::Ordinary
    );
    assert!(matches!(
        corrupt(&primary, PHYSICAL_OFFSET + 38, 1),
        Err(TableDefinitionError::Index(
            IndexDefinitionError::InvalidPrimaryFlags { raw: 1, .. }
        ))
    ));
    assert!(matches!(
        corrupt(&primary, PHYSICAL_OFFSET + 34, MAP_PAGE as u8),
        Err(TableDefinitionError::UnexpectedReferenceKind {
            role: "index root",
            ..
        })
    ));

    let mut oversized_ordinal = primary;
    oversized_ordinal[LOGICAL_OFFSET + 4..LOGICAL_OFFSET + 8]
        .copy_from_slice(&u32::MAX.to_le_bytes());
    assert!(matches!(
        decode_logical(&oversized_ordinal),
        Err(TableDefinitionError::Index(
            IndexDefinitionError::InvalidPhysicalIndexOrdinal { .. }
        ))
    ));
    Ok(())
}

#[test]
fn accepts_closed_type_inventory_and_text_size_boundaries() -> TestResult {
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
        let definition = decode_logical(&logical)?;
        assert_eq!(definition.columns()[0].physical_type().raw(), physical_type);
    }
    for size in [0, 256] {
        let logical = custom_column_definition(column_record(10, 2, 0, size), 1);
        assert!(matches!(
            decode_logical(&logical),
            Err(TableDefinitionError::UnsupportedColumnSize { .. })
        ));
    }
    Ok(())
}

#[test]
fn boolean_fixed_offset_does_not_advance_byte_backed_columns() -> TestResult {
    let logical = fixed_long_boolean_byte_definition(0xbeef, 4);
    let definition = decode_logical(&logical)?;
    assert_eq!(
        definition.columns()[0].storage(),
        ColumnStorageClass::Fixed { offset: 0 }
    );
    assert_eq!(
        definition.columns()[1].storage(),
        ColumnStorageClass::Fixed { offset: 0xbeef }
    );
    assert_eq!(
        definition.columns()[2].storage(),
        ColumnStorageClass::Fixed { offset: 4 }
    );

    let corrupt = fixed_long_boolean_byte_definition(0xbeef, 5);
    assert!(matches!(
        decode_logical(&corrupt),
        Err(TableDefinitionError::InvalidFixedOffset {
            ordinal: 2,
            raw: 5,
            expected: 4,
        })
    ));
    Ok(())
}

#[test]
fn rejects_truncated_counts_duplicate_keys_and_out_of_range_references() {
    let mut logical = column_only_definition();
    logical[21..23].copy_from_slice(&2_u16.to_le_bytes());
    logical[25..27].copy_from_slice(&2_u16.to_le_bytes());
    assert!(matches!(
        decode_logical(&logical),
        Err(TableDefinitionError::Truncated { .. })
            | Err(TableDefinitionError::UnsupportedPhysicalType { .. })
    ));

    let valid = primary_definition();
    let mut duplicate = valid.clone();
    duplicate[PHYSICAL_OFFSET + 3..PHYSICAL_OFFSET + 5].copy_from_slice(&0_u16.to_le_bytes());
    duplicate[PHYSICAL_OFFSET + 5] = 1;
    assert!(matches!(
        decode_logical(&duplicate),
        Err(TableDefinitionError::Index(
            IndexDefinitionError::DuplicateKeyColumn { .. }
        ))
    ));

    let mut bad_reference = valid;
    bad_reference[PHYSICAL_OFFSET + 34..PHYSICAL_OFFSET + 38].copy_from_slice(&6_u32.to_le_bytes());
    assert!(matches!(
        decode_logical(&bad_reference),
        Err(TableDefinitionError::Index(
            IndexDefinitionError::InvalidPhysicalReference { .. }
        ))
    ));
}

/// The resource limit that rejected a decode, through any nesting.
fn exceeded(
    result: Result<(crate::TableDefinition, ResourceBudget), TableDefinitionError>,
) -> Option<ResourceLimitKind> {
    match result.err()? {
        TableDefinitionError::Resource(error)
        | TableDefinitionError::LongValueMap(LongValueMapError::Resource(error))
        | TableDefinitionError::Chain(AllocationTraversalError::Resource(error))
        | TableDefinitionError::Index(IndexDefinitionError::Resource(error)) => match error {
            Error::ResourceLimitExceeded { kind, .. } => Some(kind),
            _ => None,
        },
        _ => None,
    }
}

#[test]
fn exact_allocation_item_and_chain_budgets_reject_one_over() -> TestResult {
    let bytes = database_bytes(&column_only_definition(), None);
    let (_, observed) = decode_with_limits(&bytes, limits(&bytes))?;
    let allocation = observed.allocation_bytes();
    let items = observed.item_work();

    decode_with_limits(&bytes, limits(&bytes).with_max_allocation_bytes(allocation))?;
    let one_less = ByteCount::new(allocation.get().checked_sub(1).ok_or("zero allocation")?);
    assert_eq!(
        exceeded(decode_with_limits(
            &bytes,
            limits(&bytes).with_max_allocation_bytes(one_less)
        )),
        Some(ResourceLimitKind::AllocationBytes)
    );
    decode_with_limits(&bytes, limits(&bytes).with_max_item_work(items))?;
    assert_eq!(
        exceeded(decode_with_limits(
            &bytes,
            limits(&bytes).with_max_item_work(items - 1)
        )),
        Some(ResourceLimitKind::ItemWork)
    );

    let chained = database_bytes(&many_memo_definition(), Some(CONTINUATION));
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
fn exact_boundary_terminal_payload_is_slack_and_the_page_is_budgeted() -> TestResult {
    for length in [PAGE_BYTES, 2 * PAGE_BYTES - 8] {
        let (bytes, terminal) = terminal_bytes(length);
        let (definition, budget) = decode_with_limits(&bytes, limits(&bytes))?;
        assert_eq!(definition.logical_length() as usize, length);
        let depth = if terminal == CONTINUATION { 2 } else { 3 };
        let expected: Vec<_> = [ROOT, CONTINUATION, RELATED_ROOT][..depth]
            .iter()
            .map(|&page| PageNumber::new(page as u64))
            .collect();
        assert_eq!(definition.pages(), expected);
        assert!(matches!(
            decode_with_limits(
                &bytes,
                limits(&bytes).with_max_chain_depth(depth as u64 - 1)
            ),
            Err(TableDefinitionError::Chain(
                AllocationTraversalError::Resource(Error::ResourceLimitExceeded {
                    kind: ResourceLimitKind::ChainDepth,
                    ..
                })
            ))
        ));
        assert!(budget.page_visits() >= depth as u64);
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
