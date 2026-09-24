//! Composition of arbitrary planned user tables, decoded back through the
//! reader to check each `EXP-0093` structure lands where the plan says.
use crate::testkit::table;

use super::{
    ComposeError, catalog_row_number, compose_table_database, creation_counter,
    tests::{compose_budget, inline_map_bit, read_budget},
};
use crate::{
    ColumnOrdinal, ColumnSpec, ColumnType, DatabaseReader, MapRowLocator, PAGE_BYTES, PageNumber,
    SliceSource, create::schema_plan::TableSpec, definition::column_writer::nz,
};

use crate::testkit::TestResult;

fn create_bytes(spec: &TableSpec<'_>) -> Result<Vec<u8>, ComposeError> {
    let mut budget = compose_budget();
    let plan = compose_table_database(spec, &mut budget)?;
    let mut bytes = Vec::with_capacity(plan.pages().len() * PAGE_BYTES);
    for page in plan.pages() {
        bytes.extend_from_slice(page.image().as_bytes());
    }
    Ok(bytes)
}

fn page(bytes: &[u8], number: usize) -> &[u8] {
    &bytes[number * PAGE_BYTES..(number + 1) * PAGE_BYTES]
}

const ID: ColumnSpec<'static> = ColumnSpec::new(b"Id", ColumnType::Long);
const NAME: ColumnSpec<'static> = ColumnSpec::new(b"Name", ColumnType::Text { max_len: nz(50) });
const NOTE: ColumnSpec<'static> = ColumnSpec::new(b"Note", ColumnType::Memo);

#[test]
fn a_created_memo_table_carries_its_long_value_map_groups_on_its_map_page() -> TestResult {
    // EXP-0087's Beta shape as a first create: root, map page, empty LvProp
    // page, and one EXP-0077 map group for the Memo column.
    let columns = [ID, NAME, NOTE];
    let bytes = create_bytes(&table(b"Beta", &columns, &[]))?;
    assert_eq!(bytes.len(), 23 * PAGE_BYTES);
    assert_eq!(bytes[1538], 2);
    assert_eq!(&page(&bytes, 22)[4..8], b"LVAL");
    assert!(inline_map_bit(&bytes, 6, 10, 22)?);

    let mut budget = read_budget(bytes.len());
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let mut beta = None;
    {
        let mut catalog = database.catalog(&mut budget)?;
        while let Some(record) = catalog.next_record()? {
            if record.name().raw_bytes() == b"Beta" {
                beta = Some((record.id().get(), record.table_definition()));
            }
        }
    }
    assert_eq!(beta, Some((20, Some(PageNumber::new(20)))));
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    assert_eq!(definition.columns().len(), 3);
    assert!(definition.physical_indexes().is_empty());
    let [group] = definition.long_value_maps() else {
        return Err("expected exactly one long-value map group".into());
    };
    assert_eq!(group.column(), ColumnOrdinal::new(2));
    assert_eq!(group.owned(), MapRowLocator::new(PageNumber::new(21), 2));
    assert_eq!(
        group.available(),
        MapRowLocator::new(PageNumber::new(21), 3)
    );
    // The map page holds the table's two maps and the group's two.
    assert_eq!(
        u16::from_le_bytes([page(&bytes, 21)[8], page(&bytes, 21)[9]]),
        4
    );
    Ok(())
}

#[test]
fn creation_counter_and_catalog_locators_reject_overflow() -> Result<(), ComposeError> {
    for (count, expected) in [
        (0, 0x0100),
        (127, 0x01fe),
        (128, 0x0200),
        (255, 0x02fe),
        (256, 0x0300),
        (32639, 0xfffe),
    ] {
        assert_eq!(creation_counter(count)?, expected);
    }
    assert!(matches!(
        creation_counter(32640),
        Err(ComposeError::TableCountOverflow {
            count: 32640,
            maximum: 32639
        })
    ));
    assert_eq!(catalog_row_number(255)?, 255);
    assert!(matches!(
        catalog_row_number(256),
        Err(ComposeError::Encoding(crate::Error::IntegerConversion {
            value: 256,
            target: "u8"
        }))
    ));
    Ok(())
}
