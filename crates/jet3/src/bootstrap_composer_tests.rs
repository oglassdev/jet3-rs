use super::{
    ALPHA_LVPROP_PAYLOAD, BootstrapComposeError, compose_alpha_database,
    compose_alpha_database_with_null_lvprop, compose_empty_database,
};
use crate::page_append_plan::EMPTY_DATABASE_PAGE_COUNT;

// EXP-0073/EXP-0085: the accepted Alpha image's appended page numbers.
const ALPHA_ROOT: u64 = 20;
const ALPHA_MAP_PAGE: u64 = 21;
use crate::table_schema_plan::{TableSchemaSpec, plan_table_schema};
use crate::{
    ByteCount, CatalogObjectClass, ColumnOrdinal, ColumnPhysicalType, ColumnSpec,
    ColumnStorageKind, DatabaseReader, JET3_PAGE_SIZE, LongValue, LongValueChunkValue,
    MapRowLocator, PageKind, PageNumber, ReadLimits, ResourceBudget, ResourceLimitKind,
    ResourceLimits, SliceSource, TableDefinitionKind, TextCodePage, ValueKind, classify_page,
    locate_usage_map, page_tag,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

pub(super) fn compose_budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

pub(super) fn read_budget(byte_len: usize) -> ResourceBudget {
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

fn null_lvprop_bytes() -> Result<Vec<u8>, BootstrapComposeError> {
    let mut budget = compose_budget();
    let plan = compose_alpha_database_with_null_lvprop(&mut budget)?;
    let mut bytes = Vec::with_capacity(plan.pages().len() * crate::PAGE_BYTES);
    for (slot, page) in plan.pages().iter().enumerate() {
        assert_eq!(page.number(), PageNumber::new(slot as u64));
        bytes.extend_from_slice(page.image().as_bytes());
    }
    Ok(bytes)
}

// EXP-0079 recorded these complete `ParentId`/`Name` keys for the fixed
// bootstrap rows. The composer now derives its keys from the EXP-0087 encoding
// instead of holding them, so this inventory is the check that the derivation
// still reproduces the recorded bytes.
const RECORDED_PARENT_NAME_KEYS: [&[u8]; 9] = [
    b"\x7f\x8f\x00\x00\x00\x7f\x77\x60\x61\x6d\x66\x76\x00",
    b"\x7f\x8f\x00\x00\x00\x7f\x64\x60\x77\x60\x61\x60\x76\x66\x76\x00",
    b"\x7f\x8f\x00\x00\x00\x7f\x75\x66\x6d\x60\x77\x6a\x72\x70\x76\x69\x6a\x73\x76\x00",
    b"\x7f\x8f\x00\x00\x02\x7f\x6f\x76\x7d\x76\x64\x61\x00",
    b"\x7f\x8f\x00\x00\x01\x7f\x6f\x76\x7d\x76\x72\x61\x6b\x66\x62\x77\x76\x00",
    b"\x7f\x8f\x00\x00\x01\x7f\x6f\x76\x7d\x76\x60\x62\x66\x76\x00",
    b"\x7f\x8f\x00\x00\x01\x7f\x6f\x76\x7d\x76\x74\x78\x66\x75\x6a\x66\x76\x00",
    b"\x7f\x8f\x00\x00\x01\x7f\x6f\x76\x7d\x76\x75\x66\x6d\x60\x77\x6a\x72\x70\x76\x69\x6a\x73\x76\x00",
    b"\x7f\x8f\x00\x00\x01\x7f\x60\x6d\x73\x69\x60\x00",
];

fn expected_parent_entries(count: usize) -> Vec<(&'static [u8], PageNumber, u8)> {
    let mut entries = RECORDED_PARENT_NAME_KEYS[..count]
        .iter()
        .enumerate()
        .map(|(row, key)| (*key, PageNumber::new(18), row as u8))
        .collect::<Vec<_>>();
    entries.sort_unstable_by(|left, right| left.0.cmp(right.0));
    entries
}

pub(super) fn inline_map_bit(
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
    assert_eq!(
        &bytes[crate::PAGE_BYTES + 4..crate::PAGE_BYTES + 8],
        &1_u32.to_le_bytes()
    );
    Ok(())
}

#[test]
fn page_zero_uses_the_fixed_preregistered_candidate_template() -> TestResult {
    let empty = bytes(false)?;
    let alpha = bytes(true)?;
    let fixed_opaque = [
        0xb5, 0x6e, 0x03, 0x62, 0x60, 0x09, 0xc2, 0x55, 0xe9, 0xa9, 0x67, 0x72, 0x40, 0x3f, 0x00,
        0x9c, 0x7e, 0x9f, 0x90, 0xff, 0x85, 0x9a, 0x31, 0xc5, 0x79, 0xba, 0xed, 0x30, 0xbc, 0xdf,
        0xcc, 0x9d, 0x63, 0xd9, 0xed, 0xc7, 0x9f, 0x46, 0xfb, 0x8a, 0xbc, 0x4e, 0x86, 0xfb, 0xec,
        0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6, 0x8a, 0x60, 0x54, 0x94,
        0x7b, 0x36, 0xbc, 0x54, 0xdf, 0xb1, 0x77, 0xf4, 0x13, 0x43, 0xcf, 0xaf, 0xb1, 0x33, 0x34,
        0x61, 0x79, 0x5b, 0x92, 0xb5, 0x7c, 0x2a, 0x05, 0xf1, 0x7c, 0x99, 0x01, 0x1b, 0x98, 0xfd,
        0x12, 0x4f, 0x4a, 0x94, 0x6c, 0x3e, 0x60, 0x26, 0x5f, 0x95, 0xf8, 0xd0, 0x89, 0x24, 0x85,
        0x67, 0xc6, 0x1f, 0x27, 0x44, 0xd2, 0xee, 0xcf, 0x65, 0xed, 0xff, 0x07, 0xc7, 0x46, 0xa1,
        0x78, 0x16, 0x0c, 0xed, 0xe9, 0x2d,
    ];

    assert_eq!(&empty[..24], b"\0\x01\0\0Standard Jet DB\0\0\0\0\0");
    assert_eq!(&empty[24..150], fixed_opaque);
    assert!(empty[150..1536].iter().all(|byte| *byte == 0));
    assert_eq!(empty[1536], 1);
    assert_eq!(empty[1538], 0);
    assert!(
        empty[1537..crate::PAGE_BYTES]
            .iter()
            .step_by(2)
            .all(|byte| *byte == 1)
    );
    assert!(
        empty[1540..crate::PAGE_BYTES]
            .iter()
            .step_by(2)
            .all(|byte| *byte == 0)
    );
    assert_eq!(alpha[1538], 2);
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
    {
        let mut rows = database.rows(&objects, &mut budget)?;
        for expected in [
            b"\x02\x03".as_slice(),
            b"\x02\x03",
            b"\x02\x03",
            b"\x03\x01",
            b"\x02\x03",
            b"\x02\x03",
            b"\x02\x03",
            b"\x02\x03",
        ] {
            let mut row = rows.next_row()?.ok_or("missing catalog row")?;
            assert!(matches!(
                row.value(ColumnOrdinal::new(6), TextCodePage::Windows1252)?
                    .ok_or("missing catalog owner")?
                    .kind(),
                ValueKind::Binary(actual) if *actual == expected
            ));
        }
        assert!(rows.next_row()?.is_none());
    }
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
        assert!(matches!(
            alpha_row
                .value(ColumnOrdinal::new(6), TextCodePage::Windows1252)?
                .ok_or("missing Alpha owner")?
                .kind(),
            ValueKind::Binary(actual) if *actual == b"\x03\x01"
        ));
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
fn null_lvprop_candidate_changes_only_the_catalog_and_lval_pages() -> TestResult {
    let fixed = bytes(true)?;
    let null = null_lvprop_bytes()?;
    assert_eq!(fixed.len(), 23 * crate::PAGE_BYTES);
    assert_eq!(null.len(), fixed.len());

    let differing_pages = fixed
        .chunks_exact(crate::PAGE_BYTES)
        .zip(null.chunks_exact(crate::PAGE_BYTES))
        .enumerate()
        .filter_map(|(page, (left, right))| (left != right).then_some(page))
        .collect::<Vec<_>>();
    assert_eq!(differing_pages, [18, 22]);

    for image in [&fixed, &null] {
        assert_eq!(
            &image[22 * crate::PAGE_BYTES + 4..22 * crate::PAGE_BYTES + 8],
            b"LVAL"
        );
        assert!(inline_map_bit(image, 6, 10, 22)?);
        assert!(inline_map_bit(image, 6, 11, 22)?);
    }
    assert_eq!(
        u16::from_le_bytes(
            fixed[22 * crate::PAGE_BYTES + 8..22 * crate::PAGE_BYTES + 10].try_into()?
        ),
        1
    );
    assert_eq!(
        u16::from_le_bytes(
            null[22 * crate::PAGE_BYTES + 8..22 * crate::PAGE_BYTES + 10].try_into()?
        ),
        0
    );

    for (image, expected_null) in [(&fixed, false), (&null, true)] {
        let mut budget = read_budget(image.len());
        let source = SliceSource::new(image, budget.read_budget())?;
        let mut database = DatabaseReader::from_source(source, &mut budget)?;
        let objects = database.table_definition(PageNumber::new(2), &mut budget)?;
        let mut rows = database.rows(&objects, &mut budget)?;
        for _ in 0..8 {
            rows.next_row()?.ok_or("missing initial object row")?;
        }
        let mut alpha = rows.next_row()?.ok_or("missing Alpha object row")?;
        assert!(matches!(
            alpha
                .value(ColumnOrdinal::new(0), TextCodePage::Windows1252)?
                .ok_or("missing Alpha id")?
                .kind(),
            ValueKind::Long(20)
        ));
        assert!(matches!(
            alpha
                .value(ColumnOrdinal::new(2), TextCodePage::Windows1252)?
                .ok_or("missing Alpha name")?
                .kind(),
            ValueKind::Text(name) if name.raw_bytes() == b"Alpha"
        ));
        let lvprop = alpha
            .value(ColumnOrdinal::new(14), TextCodePage::Windows1252)?
            .ok_or("missing Alpha LvProp value")?;
        assert!(matches!(
            (expected_null, lvprop.kind()),
            (true, ValueKind::Null) | (false, ValueKind::LongValue(LongValue::External(_)))
        ));
    }
    Ok(())
}

#[test]
fn composition_is_deterministic_and_resource_rejection_is_structured() -> TestResult {
    assert_eq!(bytes(false)?, bytes(false)?);
    assert_eq!(bytes(true)?, bytes(true)?);
    assert_eq!(null_lvprop_bytes()?, null_lvprop_bytes()?);

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
    Ok(())
}

#[test]
#[ignore = "writes private deterministic candidates for preregistered DAO validation"]
fn export_dao_validation_candidates() -> TestResult {
    use std::path::PathBuf;

    let root = PathBuf::from(
        std::env::var_os("JET3_BOOTSTRAP_CANDIDATE_DIR")
            .ok_or("JET3_BOOTSTRAP_CANDIDATE_DIR is required")?,
    );
    export_candidates(&root)
}

#[test]
#[ignore = "writes private deterministic candidates for preregistered DAO validation"]
fn export_lvprop_null_candidates() -> TestResult {
    use std::path::PathBuf;

    let root = PathBuf::from(
        std::env::var_os("JET3_LVPROP_NULL_CANDIDATE_DIR")
            .ok_or("JET3_LVPROP_NULL_CANDIDATE_DIR is required")?,
    );
    export_candidate_set(
        &root,
        [
            ("lvprop-fixed-alpha.mdb", bytes(true)?),
            ("lvprop-null-alpha.mdb", null_lvprop_bytes()?),
        ],
    )
}

fn export_candidates(root: &std::path::Path) -> TestResult {
    export_candidate_set(
        root,
        [
            ("bootstrap-composer-empty.mdb", bytes(false)?),
            ("bootstrap-composer-alpha.mdb", bytes(true)?),
        ],
    )
}

fn export_candidate_set<const N: usize>(
    root: &std::path::Path,
    candidates: [(&str, Vec<u8>); N],
) -> TestResult {
    use std::fs::OpenOptions;
    use std::io::Write;

    if !root.is_dir() {
        return Err("candidate output directory must already exist".into());
    }
    if root.read_dir()?.next().is_some() {
        return Err("candidate output directory must be empty".into());
    }
    for (name, candidate) in candidates {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(root.join(name))?;
        file.write_all(&candidate)?;
    }
    Ok(())
}

#[test]
fn candidate_export_refuses_nonempty_directory() -> TestResult {
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);
    let root = std::env::temp_dir().join(format!(
        "jet3-bootstrap-export-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root)?;
    let sentinel = root.join("sentinel");
    fs::write(&sentinel, b"preserve")?;

    let result = export_candidates(&root);

    assert!(result.is_err());
    assert_eq!(fs::read(&sentinel)?, b"preserve");
    assert!(!root.join("bootstrap-composer-empty.mdb").exists());
    assert!(!root.join("bootstrap-composer-alpha.mdb").exists());
    fs::remove_file(sentinel)?;
    fs::remove_dir(root)?;
    Ok(())
}

#[test]
fn the_planner_reproduces_the_accepted_alpha_page_assignment() -> TestResult {
    // The Alpha image DAO accepted in EXP-0085 is the fixed case the general
    // EXP-0087 assignment has to agree with.
    let columns = [ColumnSpec::new(
        b"Id",
        ColumnPhysicalType::Long,
        ColumnStorageKind::Fixed,
        4,
    )];
    let plan = plan_table_schema(
        &TableSchemaSpec {
            name: b"Alpha",
            columns: &columns,
            indexes: &[],
        },
        EMPTY_DATABASE_PAGE_COUNT,
    )?;
    assert_eq!(plan.object_id(), ALPHA_ROOT as i32);
    assert_eq!(plan.definition_root(), PageNumber::new(ALPHA_ROOT));
    assert_eq!(plan.map_page(), PageNumber::new(ALPHA_MAP_PAGE));
    // The accepted Alpha image appends three pages; the planner reports two
    // because it deliberately plans no long-value page. EXP-0087 observed one
    // only for a database's first create and establishes no property grammar,
    // so the composer wiring has to place it.
    assert_eq!(plan.index_root(), None);
    assert_eq!(plan.appended_page_count(), 2);
    let alpha_pages = bytes(true)?.len() as u64 / JET3_PAGE_SIZE.get();
    assert_eq!(
        alpha_pages,
        EMPTY_DATABASE_PAGE_COUNT + plan.appended_page_count() + 1
    );
    Ok(())
}
