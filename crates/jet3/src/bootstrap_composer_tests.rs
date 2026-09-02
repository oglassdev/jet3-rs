use super::{
    ALPHA_LVPROP_PAYLOAD, BootstrapComposeError, INDEX_KEY_CAPACITY, OwnedIndexEntry,
    PARENT_NAME_KEYS, compose_alpha_database, compose_empty_database,
};
use crate::{
    ByteCount, CatalogObjectClass, ColumnOrdinal, DatabaseReader, JET3_PAGE_SIZE, LongValue,
    LongValueChunkValue, MapRowLocator, PageKind, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimitKind, ResourceLimits, SliceSource, TableDefinitionKind, TextCodePage, ValueKind,
    classify_page, locate_usage_map, page_tag,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn compose_budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn read_budget(byte_len: usize) -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::new(
        ByteCount::new(byte_len as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    )))
}

fn bytes(alpha: bool) -> Result<Vec<u8>, BootstrapComposeError> {
    let mut budget = compose_budget();
    let plan = if alpha {
        compose_alpha_database(&mut budget)?
    } else {
        compose_empty_database(&mut budget)?
    };
    let mut bytes = Vec::with_capacity(plan.pages().len() * crate::PAGE_BYTES);
    for (slot, page) in plan.pages().iter().enumerate() {
        assert_eq!(page.number(), PageNumber::new(slot as u64));
        bytes.extend_from_slice(page.image().as_bytes());
    }
    Ok(bytes)
}

fn expected_parent_entries(count: usize) -> Vec<(&'static [u8], PageNumber, u8)> {
    let mut entries = PARENT_NAME_KEYS[..count]
        .iter()
        .enumerate()
        .map(|(row, key)| (*key, PageNumber::new(18), row as u8))
        .collect::<Vec<_>>();
    entries.sort_unstable_by(|left, right| left.0.cmp(right.0));
    entries
}

fn inline_map_bit(
    bytes: &[u8],
    map_page: u64,
    map_row: u8,
    target_page: u64,
) -> Result<bool, Box<dyn std::error::Error>> {
    let start = usize::try_from(map_page)? * crate::PAGE_BYTES;
    let raw: &[u8; crate::PAGE_BYTES] = bytes[start..start + crate::PAGE_BYTES].try_into()?;
    let mut budget = compose_budget();
    let classified = classify_page(PageNumber::new(map_page), raw, &mut budget)?;
    let record = locate_usage_map(
        classified,
        MapRowLocator::new(PageNumber::new(map_page), map_row),
        &mut budget,
    )?;
    let map = record.raw();
    if map.len() < 5 || map[0] != 0 || map[1..5] != 0_u32.to_le_bytes() {
        return Err("expected a page-zero-based inline usage map".into());
    }
    let bit = usize::try_from(target_page)?;
    let byte = map.get(5 + bit / 8).ok_or("map bit is out of range")?;
    Ok(byte & (1 << (bit % 8)) != 0)
}

#[test]
fn empty_image_assigns_all_twenty_observed_page_roles() -> TestResult {
    let bytes = bytes(false)?;
    assert_eq!(bytes.len(), 20 * crate::PAGE_BYTES);
    let expected = [
        PageKind::DatabaseDefinition,
        PageKind::Data,
        PageKind::TableDefinition,
        PageKind::TableDefinition,
        PageKind::TableDefinition,
        PageKind::TableDefinition,
        PageKind::Data,
        PageKind::Data,
        PageKind::Data,
        PageKind::LeafIndex,
        PageKind::Data,
        PageKind::LeafIndex,
        PageKind::Data,
        PageKind::LeafIndex,
        PageKind::LeafIndex,
        PageKind::LeafIndex,
        PageKind::LeafIndex,
        PageKind::LeafIndex,
        PageKind::Data,
        PageKind::Data,
    ];
    for (page, expected_kind) in bytes.chunks_exact(crate::PAGE_BYTES).zip(expected) {
        assert_eq!(page[0], page_tag(expected_kind));
    }
    Ok(())
}

#[test]
fn normal_reader_decodes_empty_catalog_definitions_maps_and_indexes() -> TestResult {
    let bytes = bytes(false)?;
    let mut budget = read_budget(bytes.len());
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;

    let mut names = Vec::new();
    {
        let mut catalog = database.catalog(&mut budget)?;
        while let Some(record) = catalog.next_record()? {
            names.push((
                record.id().get(),
                record.class(),
                record.name().raw_bytes().to_vec(),
            ));
        }
    }
    assert_eq!(names.len(), 8);
    assert!(
        names
            .iter()
            .all(|(_, class, _)| *class == CatalogObjectClass::System)
    );
    assert!(
        names
            .iter()
            .any(|(id, _, name)| *id == 2 && name == b"MSysObjects")
    );

    let objects = database.table_definition(PageNumber::new(2), &mut budget)?;
    assert_eq!(objects.kind(), TableDefinitionKind::System);
    assert_eq!(objects.logical_length(), 708);
    assert_eq!(
        objects
            .columns()
            .iter()
            .map(|column| column.name().raw_bytes())
            .collect::<Vec<_>>(),
        [
            b"Id".as_slice(),
            b"ParentId",
            b"Name",
            b"Type",
            b"DateCreate",
            b"DateUpdate",
            b"Owner",
            b"Flags",
            b"Database",
            b"Connect",
            b"ForeignName",
            b"RmtInfoShort",
            b"RmtInfoLong",
            b"Lv",
            b"LvProp",
            b"LvModule",
            b"LvExtra",
        ]
    );
    assert_eq!(objects.columns()[6].raw_class_flags(), 0x32);
    assert_eq!(objects.columns()[11].raw_class_flags(), 0x12);
    assert_eq!(objects.physical_indexes()[0].raw_flags(), 0x01);
    assert_eq!(objects.physical_indexes()[1].raw_flags(), 0x01);
    assert_eq!(
        u32::from_le_bytes(objects.raw_header()[12..16].try_into()?),
        8
    );
    assert_eq!(objects.long_value_maps().len(), 7);
    let lv_extra = objects
        .long_value_maps()
        .iter()
        .find(|map| map.column().get() == 16)
        .ok_or("missing LvExtra map")?;
    assert_eq!(lv_extra.available().page(), PageNumber::new(7));
    let parent_name = database.index_tree(&objects, 0, &mut budget)?;
    assert_eq!(
        parent_name
            .entries()
            .iter()
            .map(|entry| (
                entry.key().raw_bytes(),
                entry.row().page(),
                entry.row().slot(),
            ))
            .collect::<Vec<_>>(),
        expected_parent_entries(8)
    );
    assert_eq!(
        database
            .index_tree(&objects, 1, &mut budget)?
            .entries()
            .len(),
        8
    );

    let aces = database.table_definition(PageNumber::new(3), &mut budget)?;
    assert_eq!(aces.columns()[1].raw_class_flags(), 0x32);
    assert_eq!(aces.physical_indexes()[0].raw_flags(), 0x08);
    assert_eq!(
        aces.columns()[3].storage(),
        crate::ColumnStorageClass::Fixed { offset: 0 }
    );
    assert_eq!(
        u32::from_le_bytes(aces.raw_header()[12..16].try_into()?),
        16
    );
    assert_eq!(
        database.index_tree(&aces, 0, &mut budget)?.entries().len(),
        16
    );
    let queries = database.table_definition(PageNumber::new(4), &mut budget)?;
    assert_eq!(queries.logical_length(), 319);
    assert_eq!(queries.columns()[2].raw_class_flags(), 0x12);
    assert_eq!(queries.physical_indexes()[0].raw_flags(), 0x01);
    assert!(
        database
            .index_tree(&queries, 0, &mut budget)?
            .entries()
            .is_empty()
    );
    let relationships = database.table_definition(PageNumber::new(5), &mut budget)?;
    assert_eq!(relationships.logical_length(), 526);
    assert!(
        relationships
            .physical_indexes()
            .iter()
            .all(|index| index.raw_flags() == 0x02)
    );
    for ordinal in 0..3 {
        assert!(
            database
                .index_tree(&relationships, ordinal, &mut budget)?
                .entries()
                .is_empty()
        );
    }
    Ok(())
}

#[test]
fn alpha_transition_appends_three_pages_and_updates_catalog_counts_and_indexes() -> TestResult {
    let empty_bytes = bytes(false)?;
    let bytes = bytes(true)?;
    assert_eq!(bytes.len(), 23 * crate::PAGE_BYTES);
    assert_eq!(empty_bytes[1538], 0);
    assert_eq!(bytes[1538], 2);
    assert_eq!(
        (0..crate::PAGE_BYTES)
            .filter(|offset| empty_bytes[*offset] != bytes[*offset])
            .collect::<Vec<_>>(),
        [1538]
    );
    assert_eq!(
        bytes[20 * crate::PAGE_BYTES],
        page_tag(PageKind::TableDefinition)
    );
    assert_eq!(bytes[21 * crate::PAGE_BYTES], page_tag(PageKind::Data));
    assert_eq!(bytes[22 * crate::PAGE_BYTES], page_tag(PageKind::Data));
    assert_eq!(
        &bytes[22 * crate::PAGE_BYTES + 4..22 * crate::PAGE_BYTES + 8],
        b"LVAL"
    );
    assert!(inline_map_bit(&empty_bytes, 1, 0, 20)?);
    assert!(!inline_map_bit(&bytes, 1, 0, 20)?);
    assert!(!inline_map_bit(&bytes, 1, 0, 22)?);
    assert!(inline_map_bit(&bytes, 1, 0, 23)?);
    assert!(!inline_map_bit(&empty_bytes, 6, 10, 22)?);
    assert!(!inline_map_bit(&empty_bytes, 6, 11, 22)?);
    assert!(inline_map_bit(&bytes, 6, 10, 22)?);
    assert!(inline_map_bit(&bytes, 6, 11, 22)?);

    let mut budget = read_budget(bytes.len());
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut alpha_seen = false;
    {
        let mut catalog = database.catalog(&mut budget)?;
        let mut count = 0;
        while let Some(record) = catalog.next_record()? {
            count += 1;
            if record.name().raw_bytes() == b"Alpha" {
                alpha_seen = true;
                assert_eq!(record.id().get(), 20);
                assert_eq!(record.class(), CatalogObjectClass::User);
                assert_eq!(record.table_definition(), Some(PageNumber::new(20)));
            }
        }
        assert_eq!(count, 9);
    }
    assert!(alpha_seen);

    let objects = database.table_definition(PageNumber::new(2), &mut budget)?;
    assert_eq!(
        u32::from_le_bytes(objects.raw_header()[12..16].try_into()?),
        9
    );
    assert_eq!(
        objects.physical_indexes()[0].sourced_prefix()[4..8],
        9_u32.to_le_bytes()
    );
    let parent_name = database.index_tree(&objects, 0, &mut budget)?;
    assert_eq!(
        parent_name
            .entries()
            .iter()
            .map(|entry| (
                entry.key().raw_bytes(),
                entry.row().page(),
                entry.row().slot(),
            ))
            .collect::<Vec<_>>(),
        expected_parent_entries(9)
    );
    assert_eq!(
        database
            .index_tree(&objects, 1, &mut budget)?
            .entries()
            .len(),
        9
    );
    let aces = database.table_definition(PageNumber::new(3), &mut budget)?;
    assert_eq!(
        u32::from_le_bytes(aces.raw_header()[12..16].try_into()?),
        18
    );
    assert_eq!(
        aces.physical_indexes()[0].sourced_prefix()[4..8],
        9_u32.to_le_bytes()
    );
    assert_eq!(
        database.index_tree(&aces, 0, &mut budget)?.entries().len(),
        18
    );
    let mut ace_rows = database.rows(&aces, &mut budget)?;
    for _ in 0..16 {
        ace_rows.next_row()?.ok_or("missing initial ACE row")?;
    }
    for (sid, acm) in [
        (b"\x03\x01".as_slice(), 983_294),
        (b"\x02\x01".as_slice(), 1_048_319),
    ] {
        let mut row = ace_rows.next_row()?.ok_or("missing Alpha ACE row")?;
        assert!(matches!(
            row.value(ColumnOrdinal::new(0), TextCodePage::Windows1252)?
                .ok_or("missing ACE object")?
                .kind(),
            ValueKind::Long(20)
        ));
        assert!(matches!(
            row.value(ColumnOrdinal::new(1), TextCodePage::Windows1252)?
                .ok_or("missing ACE SID")?
                .kind(),
            ValueKind::Binary(actual) if *actual == sid
        ));
        assert!(matches!(
            row.value(ColumnOrdinal::new(2), TextCodePage::Windows1252)?
                .ok_or("missing ACE mask")?
                .kind(),
            ValueKind::Long(actual) if *actual == acm
        ));
        assert!(matches!(
            row.value(ColumnOrdinal::new(3), TextCodePage::Windows1252)?
                .ok_or("missing ACE inheritance flag")?
                .kind(),
            ValueKind::Boolean(false)
        ));
    }
    assert!(ace_rows.next_row()?.is_none());

    let mut object_rows = database.rows(&objects, &mut budget)?;
    for _ in 0..8 {
        object_rows
            .next_row()?
            .ok_or("missing initial object row")?;
    }
    let reference = {
        let mut alpha_row = object_rows.next_row()?.ok_or("missing Alpha object row")?;
        match alpha_row
            .value(ColumnOrdinal::new(14), TextCodePage::Windows1252)?
            .ok_or("missing Alpha LvProp")?
            .kind()
        {
            ValueKind::LongValue(LongValue::External(reference)) => *reference,
            other => return Err(format!("unexpected Alpha LvProp: {other:?}").into()),
        }
    };
    assert_eq!(
        reference.raw_header(),
        [
            0x2b, 0x00, 0x00, 0x40, 0x00, 0x16, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        ]
    );
    assert_eq!(reference.length(), 43);
    assert_eq!(reference.target().page(), PageNumber::new(22));
    assert_eq!(reference.target().slot(), 0);
    let mut long_value = object_rows.long_value(reference)?;
    let chunk = long_value
        .next_chunk()?
        .ok_or("missing Alpha LvProp chunk")?;
    assert_eq!(chunk.raw_row(), ALPHA_LVPROP_PAYLOAD);
    assert!(matches!(
        chunk.value(),
        LongValueChunkValue::Binary(value) if *value == ALPHA_LVPROP_PAYLOAD
    ));
    assert!(long_value.next_chunk()?.is_none());

    let alpha = database.table_definition(PageNumber::new(20), &mut budget)?;
    assert_eq!(alpha.kind(), TableDefinitionKind::User);
    assert_eq!(alpha.columns().len(), 1);
    assert_eq!(alpha.columns()[0].name().raw_bytes(), b"Id");
    assert_eq!(alpha.maps().owned().page(), PageNumber::new(21));
    Ok(())
}

#[test]
fn composition_is_deterministic_and_resource_rejection_is_structured() -> TestResult {
    assert_eq!(bytes(false)?, bytes(false)?);
    assert_eq!(bytes(true)?, bytes(true)?);

    let mut budget =
        ResourceBudget::new(ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)));
    assert!(matches!(
        compose_empty_database(&mut budget),
        Err(BootstrapComposeError::UsageMap(
            crate::UsageMapWriteError::Encoding(crate::Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::AllocationBytes,
                ..
            })
        ))
    ));
    assert!(matches!(
        OwnedIndexEntry::raw(&[0; INDEX_KEY_CAPACITY + 1], 0),
        Err(BootstrapComposeError::IndexKeyTooLong { needed, available })
            if needed == INDEX_KEY_CAPACITY + 1 && available == INDEX_KEY_CAPACITY
    ));
    Ok(())
}
