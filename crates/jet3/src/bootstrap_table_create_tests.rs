//! Composition of arbitrary planned user tables, decoded back through the
//! reader to check each `EXP-0087` structure lands where the plan says.

use super::super::tests::{compose_budget, inline_map_bit, read_budget};
use super::super::{BootstrapComposeError, TableCreate, compose_table_database};
use crate::table_schema_plan::{PlannedIndex, PlannedIndexKind, TableSchemaSpec};
use crate::{
    ColumnOrdinal, ColumnPhysicalType, ColumnSpec, ColumnStorageKind, DatabaseReader,
    IndexDirection, IndexFieldSpec, MapRowLocator, PageKind, PageNumber, SliceSource, page_tag,
};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn create_bytes(create: TableCreate<'_>) -> Result<Vec<u8>, BootstrapComposeError> {
    let mut budget = compose_budget();
    let plan = compose_table_database(create, &mut budget)?;
    let mut bytes = Vec::with_capacity(plan.pages().len() * crate::PAGE_BYTES);
    for page in plan.pages() {
        bytes.extend_from_slice(page.image().as_bytes());
    }
    Ok(bytes)
}

const ID: ColumnSpec<'static> =
    ColumnSpec::new(b"Id", ColumnPhysicalType::Long, ColumnStorageKind::Fixed, 4);
const NAME: ColumnSpec<'static> = ColumnSpec::new(
    b"Name",
    ColumnPhysicalType::Text,
    ColumnStorageKind::Variable,
    50,
);
const NOTE: ColumnSpec<'static> = ColumnSpec::new(
    b"Note",
    ColumnPhysicalType::Memo,
    ColumnStorageKind::Variable,
    0,
);
/// Builds a payload with the EXP-0087 framing and empty chunk bodies: the
/// magic, one names chunk, then one chunk per column. Only Alpha's recorded
/// payload is established; this exercises the framing check alone.
fn framed_properties(columns: usize) -> Vec<u8> {
    let mut payload = b"KKD\x00".to_vec();
    payload.extend_from_slice(&[6, 0, 0, 0, 0x80, 0x00]);
    for _ in 0..columns {
        payload.extend_from_slice(&[6, 0, 0, 0, 0x01, 0x00]);
    }
    payload
}

#[test]
fn a_created_memo_table_carries_its_long_value_map_groups_on_its_map_page() -> TestResult {
    // EXP-0087's Beta shape: two appended pages plus the first-create
    // long-value page, and one EXP-0077 map group for the Memo column.
    let columns = [ID, NAME, NOTE];
    let bytes = create_bytes(TableCreate {
        spec: &TableSchemaSpec {
            name: b"Beta",
            columns: &columns,
            indexes: &[],
        },
        properties: &framed_properties(3),
    })?;
    assert_eq!(bytes.len(), 23 * crate::PAGE_BYTES);
    assert_eq!(bytes[1538], 2);
    assert_eq!(
        &bytes[22 * crate::PAGE_BYTES + 4..22 * crate::PAGE_BYTES + 8],
        b"LVAL"
    );
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
        u16::from_le_bytes([
            bytes[21 * crate::PAGE_BYTES + 8],
            bytes[21 * crate::PAGE_BYTES + 9]
        ]),
        4
    );
    Ok(())
}

#[test]
fn an_indexed_first_create_places_its_index_root_before_the_long_value_page() -> TestResult {
    // EXP-0087's Gamma shape as a first create, which no run observed: the
    // index root was observed directly after the map page, so the
    // first-create long-value page can only follow it.
    let columns = [ID];
    let indexes = [PlannedIndex {
        name: b"PrimaryKey",
        fields: &[IndexFieldSpec {
            column: 0,
            direction: IndexDirection::Ascending,
        }],
        kind: PlannedIndexKind::Primary,
    }];
    let bytes = create_bytes(TableCreate {
        spec: &TableSchemaSpec {
            name: b"Gamma",
            columns: &columns,
            indexes: &indexes,
        },
        properties: &framed_properties(1),
    })?;
    assert_eq!(bytes.len(), 24 * crate::PAGE_BYTES);
    assert_eq!(bytes[22 * crate::PAGE_BYTES], page_tag(PageKind::LeafIndex));
    assert_eq!(
        &bytes[23 * crate::PAGE_BYTES + 4..23 * crate::PAGE_BYTES + 8],
        b"LVAL"
    );
    assert!(!inline_map_bit(&bytes, 1, 0, 23)?);
    assert!(inline_map_bit(&bytes, 1, 0, 24)?);
    assert!(inline_map_bit(&bytes, 6, 10, 23)?);

    let mut budget = read_budget(bytes.len());
    let source = SliceSource::new(&bytes, budget.read_budget())?;
    let mut database = DatabaseReader::from_source(source, &mut budget)?;
    let definition = database.table_definition(PageNumber::new(20), &mut budget)?;
    let [physical] = definition.physical_indexes() else {
        return Err("expected exactly one physical index".into());
    };
    assert_eq!(physical.root(), PageNumber::new(22));
    assert_eq!(physical.usage_map().page(), PageNumber::new(21));
    assert_eq!(physical.usage_map().row(), 2);
    assert!(physical.unique());
    assert!(physical.required());
    assert_eq!(definition.indexes()[0].name().raw_bytes(), b"PrimaryKey");
    assert!(definition.long_value_maps().is_empty());
    let root = database.index_tree(&definition, 0, &mut budget)?;
    assert!(root.entries().is_empty());
    Ok(())
}

#[test]
fn a_table_with_both_an_index_and_a_long_value_column_is_refused() {
    // No EXP-0087 create carried both, so their map-page row order is unobserved.
    let columns = [ID, NOTE];
    let indexes = [PlannedIndex {
        name: b"ById",
        fields: &[IndexFieldSpec {
            column: 0,
            direction: IndexDirection::Ascending,
        }],
        kind: PlannedIndexKind::Ordinary,
    }];
    let mut budget = compose_budget();
    assert!(matches!(
        compose_table_database(
            TableCreate {
                spec: &TableSchemaSpec {
                    name: b"Mixed",
                    columns: &columns,
                    indexes: &indexes,
                },
                properties: &framed_properties(2),
            },
            &mut budget,
        ),
        Err(BootstrapComposeError::UnobservedMapRowLayout)
    ));
}

#[test]
fn a_create_that_cannot_be_planned_reports_the_schema_error() {
    let columns = [ID];
    let mut budget = compose_budget();
    assert!(matches!(
        compose_table_database(
            TableCreate {
                spec: &TableSchemaSpec {
                    name: b"",
                    columns: &columns,
                    indexes: &[],
                },
                properties: &framed_properties(1),
            },
            &mut budget,
        ),
        Err(BootstrapComposeError::Schema(_))
    ));
    assert!(matches!(
        compose_table_database(
            TableCreate {
                spec: &TableSchemaSpec {
                    name: b"Empty",
                    columns: &columns,
                    indexes: &[],
                },
                properties: b"",
            },
            &mut budget,
        ),
        Err(BootstrapComposeError::LongValue(_))
    ));
}

#[test]
fn a_second_long_value_column_is_refused() {
    // EXP-0087's only long-value create, Beta, carried one Memo column, and
    // the one multi-group layout on record (MSysObjects) is not consecutive.
    let columns = [
        ID,
        NOTE,
        ColumnSpec::new(
            b"Blob",
            ColumnPhysicalType::LongBinary,
            ColumnStorageKind::Variable,
            0,
        ),
    ];
    let mut budget = compose_budget();
    assert!(matches!(
        compose_table_database(
            TableCreate {
                spec: &TableSchemaSpec {
                    name: b"Wide",
                    columns: &columns,
                    indexes: &[],
                },
                properties: &framed_properties(3),
            },
            &mut budget,
        ),
        Err(BootstrapComposeError::UnobservedLongValueColumnCount { observed: 1 })
    ));
}

#[test]
fn a_property_payload_outside_the_pinned_framing_is_refused() {
    let columns = [ID];
    let mut budget = compose_budget();
    let compose = |properties: &[u8], budget: &mut _| {
        compose_table_database(
            TableCreate {
                spec: &TableSchemaSpec {
                    name: b"Alpha",
                    columns: &columns,
                    indexes: &[],
                },
                properties,
            },
            budget,
        )
    };
    // Wrong magic.
    assert!(matches!(
        compose(b"KKE\x00", &mut budget),
        Err(BootstrapComposeError::PropertyFraming { offset: 0 })
    ));
    // A chunk length running past the payload.
    assert!(matches!(
        compose(b"KKD\x00\x09\x00\x00\x00\x80\x00", &mut budget),
        Err(BootstrapComposeError::PropertyFraming { offset: 4 })
    ));
    // A chunk shorter than its own header.
    assert!(matches!(
        compose(b"KKD\x00\x05\x00\x00\x00\x80\x00", &mut budget),
        Err(BootstrapComposeError::PropertyFraming { offset: 4 })
    ));
    // A leading chunk that is not the names chunk.
    assert!(matches!(
        compose(b"KKD\x00\x06\x00\x00\x00\x01\x00", &mut budget),
        Err(BootstrapComposeError::PropertyFraming { offset: 4 })
    ));
    // Chunk count not matching one names chunk plus one per column.
    assert!(matches!(
        compose(&framed_properties(2), &mut budget),
        Err(BootstrapComposeError::PropertyChunkCount {
            chunks: 3,
            expected: 2
        })
    ));
    // Alpha's recorded payload passes the framing for its one column.
    assert!(compose(super::super::ALPHA_LVPROP_PAYLOAD, &mut budget).is_ok());
}
