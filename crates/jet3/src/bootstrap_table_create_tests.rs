//! Composition of arbitrary planned user tables, decoded back through the
//! reader to check each `EXP-0093` structure lands where the plan says.

use super::super::tests::{compose_budget, inline_map_bit, read_budget};
use super::super::{ComposeError, compose_table_database};
use crate::column_definition_writer::nz;
use crate::table_schema_plan::{IndexKind, IndexSpec, TableSchemaPlanError, TableSpec};
use crate::{
    ColumnOrdinal, ColumnRef, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec,
    IndexDirection, MapRowLocator, PAGE_BYTES, PageKind, PageNumber, SliceSource, page_tag,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

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

/// Fixed Long columns with ten-byte names: 70 of them encode to the 2,075-byte
/// definition `EXP-0105` observed needing one continuation.
fn wide_names(count: usize) -> Vec<Vec<u8>> {
    (0..count)
        .map(|ordinal| format!("Field{ordinal:05}").into_bytes())
        .collect()
}

fn wide_columns(names: &[Vec<u8>]) -> Vec<ColumnSpec<'_>> {
    names
        .iter()
        .map(|name| ColumnSpec::new(name, ColumnType::Long))
        .collect()
}

#[test]
fn a_created_memo_table_carries_its_long_value_map_groups_on_its_map_page() -> TestResult {
    // EXP-0087's Beta shape as a first create: root, map page, empty LvProp
    // page, and one EXP-0077 map group for the Memo column.
    let columns = [ID, NAME, NOTE];
    let bytes = create_bytes(&TableSpec {
        name: b"Beta",
        columns: &columns,
        indexes: &[],
    })?;
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
    let bytes = create_bytes(&TableSpec {
        name: b"Three",
        columns: &columns,
        indexes: &indexes,
    })?;
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
fn a_definition_needing_a_continuation_is_refused_before_any_page_is_built() {
    // EXP-0105 established the 2,048/2,040 capacities but placed its
    // continuations at pages 68 and 219/218 under an unestablished
    // allocation, so no compact layout can claim to follow it.
    let names = wide_names(70);
    let columns = wide_columns(&names);
    let mut budget = compose_budget();
    assert!(matches!(
        compose_table_database(
            &TableSpec {
                name: b"Wide",
                columns: &columns,
                indexes: &[],
            },
            &mut budget,
        ),
        Err(ComposeError::Schema(
            TableSchemaPlanError::ContinuationPlacementUnestablished {
                length: 2075,
                continuations: 1,
            }
        ))
    ));
}

#[test]
fn a_table_with_both_an_index_and_a_long_value_column_is_refused() {
    // No observed create carried both, so their map-page row order is unobserved.
    let columns = [ID, NOTE];
    let indexes = [IndexSpec {
        name: b"ById",
        fields: &[field(0, IndexDirection::Ascending)],
        kind: IndexKind::Ordinary,
    }];
    let mut budget = compose_budget();
    assert!(matches!(
        compose_table_database(
            &TableSpec {
                name: b"Mixed",
                columns: &columns,
                indexes: &indexes,
            },
            &mut budget,
        ),
        Err(ComposeError::UnobservedMapRowLayout)
    ));
}

#[test]
fn a_create_that_cannot_be_planned_reports_the_schema_error() {
    let columns = [ID];
    let mut budget = compose_budget();
    assert!(matches!(
        compose_table_database(
            &TableSpec {
                name: b"",
                columns: &columns,
                indexes: &[],
            },
            &mut budget,
        ),
        Err(ComposeError::Schema(_))
    ));
}

#[test]
fn a_second_long_value_column_is_refused() {
    // EXP-0087's only long-value create, Beta, carried one Memo column, and
    // the one multi-group layout on record (MSysObjects) is not consecutive.
    let columns = [ID, NOTE, ColumnSpec::new(b"Blob", ColumnType::LongBinary)];
    let mut budget = compose_budget();
    assert!(matches!(
        compose_table_database(
            &TableSpec {
                name: b"Wide",
                columns: &columns,
                indexes: &[],
            },
            &mut budget,
        ),
        Err(ComposeError::UnobservedLongValueColumnCount { observed: 1 })
    ));
}
