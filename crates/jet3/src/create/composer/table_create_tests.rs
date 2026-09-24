//! Composition of arbitrary planned user tables, decoded back through the
//! reader to check each `EXP-0093` structure lands where the plan says.
use crate::testkit::table;

use super::{
    ComposeError, catalog_row_number, compose_database, compose_table_database, creation_counter,
    tests::{compose_budget, inline_map_bit, read_budget},
};
use crate::{
    ColumnOrdinal, ColumnRef, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec,
    IndexDirection, MapRowLocator, PAGE_BYTES, PageKind, PageNumber, SliceSource,
    create::schema_plan::{IndexKind, IndexSpec, TableSpec},
    definition::column_writer::nz,
    format::page_kind::page_tag,
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
const CODE: ColumnSpec<'static> = ColumnSpec::new(b"Code", ColumnType::Text { max_len: nz(8) });
const SEQUENCE: ColumnSpec<'static> = ColumnSpec::new(b"Sequence", ColumnType::Long);
const NOTE: ColumnSpec<'static> = ColumnSpec::new(b"Note", ColumnType::Memo);

const fn field(column: u16, direction: IndexDirection) -> IndexColumnSpec<'static> {
    IndexColumnSpec {
        column: ColumnRef::Ordinal(column),
        direction,
    }
}

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
fn a_three_index_first_create_follows_the_observed_page_and_record_order() -> TestResult {
    // EXP-0093's `three` arm shape: primary, unique, and ordinary indexes
    // appended in that order, the last one composite with a descending field.
    let columns = [ID, CODE, SEQUENCE];
    let indexes = [
        IndexSpec {
            name: b"ZPrimary",
            fields: &[field(0, IndexDirection::Ascending)],
            kind: IndexKind::Primary,
        },
        IndexSpec {
            name: b"MUniqueX",
            fields: &[field(1, IndexDirection::Ascending)],
            kind: IndexKind::Unique,
        },
        IndexSpec {
            name: b"ASecondx",
            fields: &[
                field(1, IndexDirection::Descending),
                field(2, IndexDirection::Ascending),
            ],
            kind: IndexKind::Ordinary,
        },
    ];
    let bytes = create_bytes(&table(b"Three", &columns, &indexes))?;
    assert_eq!(bytes.len(), 26 * PAGE_BYTES);
    assert_eq!(page(&bytes, 20)[0], page_tag(PageKind::TableDefinition));
    assert_eq!(page(&bytes, 21)[0], page_tag(PageKind::Data));
    assert_eq!(&page(&bytes, 22)[4..8], b"LVAL");
    for root in 23..26 {
        assert_eq!(page(&bytes, root)[0], page_tag(PageKind::LeafIndex));
        assert!(!inline_map_bit(&bytes, 1, 0, root as u64)?);
    }
    assert!(inline_map_bit(&bytes, 1, 0, 26)?);
    assert!(inline_map_bit(&bytes, 6, 10, 22)?);
    // Map rows 2 through 4 each map exactly their own root.
    assert_eq!(
        u16::from_le_bytes([page(&bytes, 21)[8], page(&bytes, 21)[9]]),
        5
    );
    for (row, root) in [(2, 23), (3, 24), (4, 25)] {
        for candidate in 23..26 {
            assert_eq!(
                inline_map_bit(&bytes, 21, row, candidate)?,
                candidate == root
            );
        }
    }

    let mut budget = read_budget(bytes.len());
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    let physical = definition.physical_indexes();
    assert_eq!(physical.len(), 3);
    for (ordinal, (root, flags)) in [(23, 0x09), (24, 0x01), (25, 0x00)].into_iter().enumerate() {
        assert_eq!(physical[ordinal].root(), PageNumber::new(root));
        assert_eq!(physical[ordinal].usage_map().page(), PageNumber::new(21));
        assert_eq!(physical[ordinal].usage_map().row(), 2 + ordinal as u8);
        assert_eq!(physical[ordinal].raw_flags(), flags);
        assert!(
            database
                .index_tree(&definition, ordinal as u16, &mut budget)?
                .entries()
                .is_empty()
        );
    }
    let composite = physical[2].fields();
    assert_eq!(composite.len(), 2);
    assert_eq!(composite[0].column(), ColumnOrdinal::new(1));
    assert_eq!(composite[0].direction(), IndexDirection::Descending);
    assert_eq!(composite[1].column(), ColumnOrdinal::new(2));
    assert_eq!(composite[1].direction(), IndexDirection::Ascending);
    // Logical records in name order, referring back to physical ordinals.
    let logical = definition
        .indexes()
        .iter()
        .map(|index| (index.name().raw_bytes(), index.physical_index()))
        .collect::<Vec<_>>();
    assert_eq!(
        logical,
        [
            (b"ASecondx".as_slice(), 2),
            (b"MUniqueX".as_slice(), 1),
            (b"ZPrimary".as_slice(), 0),
        ]
    );
    Ok(())
}

#[test]
fn a_case_folded_duplicate_name_is_refused() {
    let mut budget = compose_budget();
    let duplicate = [
        table(b"Alpha", &[ID], &[]),
        table(b"Beta", &[ID], &[]),
        table(b"ALPHA", &[ID], &[]),
    ];
    assert!(matches!(
        compose_database(&duplicate, &mut budget),
        Err(ComposeError::DuplicateTableName {
            first: 0,
            second: 2
        })
    ));
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
