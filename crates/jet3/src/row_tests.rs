use super::{RawField, RowError};
use crate::{
    ByteCount, DatabaseReader, Error, JET3_PAGE_SIZE, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimitKind, ResourceLimits, SliceSource, TextCodePage, ValueKind,
};
use std::error::Error as _;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const ROOT: usize = 1;
const MAP_PAGE: usize = 2;
const FIRST_DATA: usize = 3;
const SECOND_DATA: usize = 4;

fn column_record(
    physical_type: u8,
    ordinal: u16,
    variable_counter: u16,
    class: u8,
    fixed_offset: u16,
    size: u16,
) -> [u8; 18] {
    let mut record = [0_u8; 18];
    record[0] = physical_type;
    record[1..3].copy_from_slice(&ordinal.to_le_bytes());
    record[3..5].copy_from_slice(&variable_counter.to_le_bytes());
    record[5..7].copy_from_slice(&ordinal.to_le_bytes());
    record[7..9].copy_from_slice(&1_u16.to_le_bytes());
    record[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    record[13] = class;
    record[14..16].copy_from_slice(&fixed_offset.to_le_bytes());
    record[16..18].copy_from_slice(&size.to_le_bytes());
    record
}

fn definition() -> Vec<u8> {
    let mut bytes = vec![0_u8; 43];
    bytes[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    bytes[20] = 0x4e;
    bytes[21..23].copy_from_slice(&2_u16.to_le_bytes());
    bytes[23..25].copy_from_slice(&1_u16.to_le_bytes());
    bytes[25..27].copy_from_slice(&2_u16.to_le_bytes());
    bytes[35..39].copy_from_slice(&[0, MAP_PAGE as u8, 0, 0]);
    bytes[39..43].copy_from_slice(&[1, MAP_PAGE as u8, 0, 0]);
    bytes.extend_from_slice(&column_record(4, 0, 0, 3, 0, 4));
    bytes.extend_from_slice(&column_record(10, 1, 0, 2, 0, 255));
    bytes.extend_from_slice(&[2, b'I', b'd', 7, b'P', b'a', b'y', b'l', b'o', b'a', b'd']);
    bytes.extend_from_slice(&[0xff, 0xff]);
    let length = u32::try_from(bytes.len()).unwrap_or_default();
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    bytes
}

fn write_rows(page: &mut [u8], owner: u32, rows: &[(&[u8], u16)]) {
    page[0] = 1;
    page[4..8].copy_from_slice(&owner.to_le_bytes());
    page[8..10].copy_from_slice(&u16::try_from(rows.len()).unwrap_or_default().to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, (row, flags)) in rows.iter().enumerate() {
        start -= row.len();
        let raw = u16::try_from(start).unwrap_or_default() | flags;
        page[10 + 2 * index..12 + 2 * index].copy_from_slice(&raw.to_le_bytes());
        page[start..start + row.len()].copy_from_slice(row);
    }
}

fn direct_row(id: u32, value: &[u8]) -> Vec<u8> {
    let end = 5 + value.len();
    let mut row = vec![2];
    row.extend_from_slice(&id.to_le_bytes());
    row.extend_from_slice(value);
    if end <= usize::from(u8::MAX) {
        row.extend_from_slice(&[u8::try_from(end).unwrap_or_default(), 5, 1, 3]);
    } else {
        row.extend_from_slice(&[u8::try_from(end & 0xff).unwrap_or_default(), 5, 1, 1, 3]);
    }
    row
}

fn database_bytes(pointer: [u8; 4], target_flags: u16, target_owner: u32) -> Vec<u8> {
    let mut bytes = vec![0_u8; 5 * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    let logical = definition();
    bytes[ROOT * PAGE_BYTES..ROOT * PAGE_BYTES + logical.len()].copy_from_slice(&logical);

    let owned = [0, 0, 0, 0, 0, (1 << FIRST_DATA) | (1 << SECOND_DATA)];
    let available = [0, 0, 0, 0, 0];
    write_rows(
        &mut bytes[MAP_PAGE * PAGE_BYTES..(MAP_PAGE + 1) * PAGE_BYTES],
        0,
        &[(&owned, 0), (&available, 0)],
    );

    let first = direct_row(1, b"a");
    write_rows(
        &mut bytes[FIRST_DATA * PAGE_BYTES..(FIRST_DATA + 1) * PAGE_BYTES],
        ROOT as u32,
        &[(&first, 0), (&pointer, 0x4000)],
    );
    let target = direct_row(5, &[b'O'; 255]);
    write_rows(
        &mut bytes[SECOND_DATA * PAGE_BYTES..(SECOND_DATA + 1) * PAGE_BYTES],
        target_owner,
        &[(&target, target_flags)],
    );
    bytes
}

fn limits(bytes: &[u8]) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    ))
}

fn open<'a>(
    bytes: &'a [u8],
    budget: &mut ResourceBudget,
) -> Result<DatabaseReader<SliceSource<'a>>, Box<dyn std::error::Error>> {
    let source = SliceSource::new(bytes, budget.read_budget())?;
    Ok(DatabaseReader::from_source(source, budget)?)
}

#[test]
fn streams_direct_and_overflow_rows_with_lossless_field_slices()
-> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes([0, SECOND_DATA as u8, 0, 0], 0x8000, ROOT as u32);
    let mut budget = ResourceBudget::new(limits(&bytes));
    let mut database = open(&bytes, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let id = definition.columns()[0].ordinal();
    let payload = definition.columns()[1].ordinal();
    let mut rows = database.rows(&definition, &mut budget)?;

    {
        let mut first = rows.next_row()?.ok_or("missing direct row")?;
        assert_eq!(first.locator().page(), PageNumber::new(FIRST_DATA as u64));
        assert_eq!(first.storage_locator(), first.locator());
        assert_eq!(first.field(id), Some(RawField::Bytes(&1_u32.to_le_bytes())));
        assert_eq!(first.field(payload), Some(RawField::Bytes(b"a")));
        assert!(!first.field(id).ok_or("missing id")?.is_null());
        assert!(RawField::Null.is_null());
        assert_eq!(RawField::Null.raw_bytes(), None);
        assert_eq!(
            first
                .value(id, TextCodePage::Windows1252)?
                .ok_or("missing id value")?
                .kind(),
            &ValueKind::Long(1)
        );
        let payload_value = first
            .value(payload, TextCodePage::Windows1252)?
            .ok_or("missing payload value")?;
        let ValueKind::Text(text) = payload_value.kind() else {
            return Err("payload was not text".into());
        };
        assert_eq!(text.as_str(), "a");
        assert!(
            first
                .value(crate::ColumnOrdinal::new(99), TextCodePage::Windows1252)?
                .is_none()
        );
    }
    {
        let overflow = rows.next_row()?.ok_or("missing overflow row")?;
        assert_eq!(overflow.locator().slot(), 1);
        assert_eq!(
            overflow.storage_locator().page(),
            PageNumber::new(SECOND_DATA as u64)
        );
        assert_eq!(
            overflow.field(id),
            Some(RawField::Bytes(&5_u32.to_le_bytes()))
        );
        assert_eq!(
            overflow.field(payload).and_then(RawField::raw_bytes),
            Some(&[b'O'; 255][..])
        );
        assert_eq!(overflow.raw_bytes().len(), 265);
    }
    assert!(rows.next_row()?.is_none());
    let work = rows.owned.budget_mut().total_work_units();
    assert!(rows.next_row()?.is_none());
    assert_eq!(rows.owned.budget_mut().total_work_units(), work);
    Ok(())
}

#[test]
fn row_errors_expose_display_and_nested_sources() {
    let plain = RowError::RowTooShort {
        length: 0,
        minimum: 1,
    };
    assert!(plain.to_string().contains("row stream failed"));
    assert!(plain.source().is_none());

    let resource = RowError::Resource(Error::Arithmetic {
        operation: "test row source",
    });
    assert!(resource.source().is_some());
    let directory = RowError::Directory(crate::RowDirectoryError::UnexpectedOwner {
        expected: PageNumber::new(1),
        actual: PageNumber::new(2),
    });
    assert!(directory.source().is_some());
}

#[test]
fn skips_owned_long_value_pages_after_primary_rows() -> Result<(), Box<dyn std::error::Error>> {
    let mut bytes = database_bytes([0, SECOND_DATA as u8, 0, 0], 0x8000, ROOT as u32);
    let first = direct_row(1, b"a");
    write_rows(
        &mut bytes[FIRST_DATA * PAGE_BYTES..(FIRST_DATA + 1) * PAGE_BYTES],
        ROOT as u32,
        &[(&first, 0)],
    );
    write_rows(
        &mut bytes[SECOND_DATA * PAGE_BYTES..(SECOND_DATA + 1) * PAGE_BYTES],
        u32::from_le_bytes(*b"LVAL"),
        &[(&[0, 0, 0, 0, b'x'], 0)],
    );

    let mut budget = ResourceBudget::new(limits(&bytes));
    let mut database = open(&bytes, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    assert!(rows.next_row()?.is_some());
    assert!(rows.next_row()?.is_none());
    Ok(())
}

#[test]
fn rejects_self_links_cycles_wrong_owner_and_chain_exhaustion()
-> Result<(), Box<dyn std::error::Error>> {
    let self_link = database_bytes([1, FIRST_DATA as u8, 0, 0], 0x8000, ROOT as u32);
    let mut budget = ResourceBudget::new(limits(&self_link));
    let mut database = open(&self_link, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    let _ = rows.next_row()?.ok_or("missing first")?;
    assert!(matches!(rows.next_row(), Err(RowError::SelfLink { .. })));
    assert!(rows.next_row()?.is_none());

    let cycle = database_bytes([0, SECOND_DATA as u8, 0, 0], 0xc000, ROOT as u32);
    let mut cycle = cycle;
    write_rows(
        &mut cycle[SECOND_DATA * PAGE_BYTES..(SECOND_DATA + 1) * PAGE_BYTES],
        ROOT as u32,
        &[(&[1, FIRST_DATA as u8, 0, 0], 0xc000)],
    );
    let mut budget = ResourceBudget::new(limits(&cycle));
    let mut database = open(&cycle, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    let _ = rows.next_row()?.ok_or("missing first")?;
    assert!(matches!(rows.next_row(), Err(RowError::Cycle { .. })));

    let wrong_owner = database_bytes([0, SECOND_DATA as u8, 0, 0], 0x8000, 99);
    let mut budget = ResourceBudget::new(limits(&wrong_owner));
    let mut database = open(&wrong_owner, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    let _ = rows.next_row()?.ok_or("missing first")?;
    assert!(matches!(rows.next_row(), Err(RowError::Directory(_))));

    let bytes = database_bytes([0, SECOND_DATA as u8, 0, 0], 0x8000, ROOT as u32);
    let mut schema_budget = ResourceBudget::new(limits(&bytes));
    let mut schema_database = open(&bytes, &mut schema_budget)?;
    let definition =
        schema_database.table_definition(PageNumber::new(ROOT as u64), &mut schema_budget)?;
    let mut budget = ResourceBudget::new(limits(&bytes).with_max_chain_depth(0));
    let mut database = open(&bytes, &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    let _ = rows.next_row()?.ok_or("missing first")?;
    assert!(matches!(
        rows.next_row(),
        Err(RowError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::ChainDepth,
            ..
        }))
    ));
    Ok(())
}

#[test]
fn rejects_row_trailer_null_bitmap_and_item_resource_corruption()
-> Result<(), Box<dyn std::error::Error>> {
    let mut bytes = database_bytes([0, SECOND_DATA as u8, 0, 0], 0x8000, ROOT as u32);
    let page = &mut bytes[FIRST_DATA * PAGE_BYTES..(FIRST_DATA + 1) * PAGE_BYTES];
    let start = usize::from(u16::from_le_bytes([page[10], page[11]]) & 0x1fff);
    page[PAGE_BYTES - 2] = 2;
    let mut budget = ResourceBudget::new(limits(&bytes));
    let mut database = open(&bytes, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    assert!(matches!(
        rows.next_row(),
        Err(RowError::VariableCountMismatch { .. })
    ));
    assert!(start < PAGE_BYTES);

    let mut bytes = database_bytes([0, SECOND_DATA as u8, 0, 0], 0x8000, ROOT as u32);
    bytes[FIRST_DATA * PAGE_BYTES + PAGE_BYTES - 1] = 0x83;
    let mut budget = ResourceBudget::new(limits(&bytes));
    let mut database = open(&bytes, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    assert!(matches!(
        rows.next_row(),
        Err(RowError::NonzeroUnusedNullBits { .. })
    ));

    let bytes = database_bytes([0, SECOND_DATA as u8, 0, 0], 0x8000, ROOT as u32);
    let mut observed = ResourceBudget::new(limits(&bytes));
    let mut database = open(&bytes, &mut observed)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut observed)?;
    let before_rows = observed.item_work();
    let mut rows = database.rows(&definition, &mut observed)?;
    let _ = rows.next_row()?.ok_or("missing row")?;
    let row_items = rows.owned.budget_mut().item_work() - before_rows;

    let mut limited =
        ResourceBudget::new(limits(&bytes).with_max_item_work(before_rows + row_items - 1));
    let mut database = open(&bytes, &mut limited)?;
    let definition = database.table_definition(PageNumber::new(ROOT as u64), &mut limited)?;
    let mut rows = database.rows(&definition, &mut limited)?;
    assert!(matches!(
        rows.next_row(),
        Err(RowError::Resource(_)) | Err(RowError::Directory(_))
    ));
    Ok(())
}
