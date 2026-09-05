//! Private candidate for exactly EXP-0114's first ParentChild relationship.
//!
//! All schemas, names, selectors, map placements, catalog IDs/ACEs, and
//! logical-record fields are bounded to that observation. Bootstrap null
//! LvProp construction still follows EXP-0091/0110; no DAO result establishes
//! acceptance of this combined image. No generalized relationship API lives
//! here, and the second relationship is not composed.

#![allow(
    dead_code,
    reason = "private relationship candidate awaiting DAO validation"
)]

use super::*;
use crate::{IndexColumnSpec, IndexFieldSpec, IndexKind, IndexSpec, RelationshipSide};

// EXP-0114 base and first checkpoints.
const PARENT_ROOT: u64 = 20;
const PARENT_MAP: u64 = 21;
const PARENT_ID_ROOT: u64 = 23;
const PARENT_ALTERNATE_ROOT: u64 = 24;
const CHILD_ROOT: u64 = 25;
const CHILD_MAP: u64 = 26;
const RELATION_DATA: u64 = 27;
const CHILD_INDEX_ROOT: u64 = 28;
const RELATION_ID: i32 = i32::MIN;
const PAGE0_TRANSITION_OFFSET: u64 = 1538;
const PAGE0_TRANSITION_BYTE: u8 = 2;
const PARENT_COLUMNS: [ColumnSpec<'static>; 2] = [
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"Alternate", ColumnType::Long),
];
const CHILD_COLUMNS: [ColumnSpec<'static>; 2] = [
    ColumnSpec::new(b"ParentId", ColumnType::Long),
    ColumnSpec::new(b"Alternate", ColumnType::Long),
];
const TABLES: [TableSpec<'static>; 2] = [
    TableSpec {
        name: b"Parent",
        columns: &PARENT_COLUMNS,
        indexes: &[
            IndexSpec {
                name: b"ById",
                fields: &[IndexColumnSpec {
                    column: crate::ColumnRef::Ordinal(0),
                    direction: IndexDirection::Ascending,
                }],
                kind: IndexKind::Primary,
            },
            IndexSpec {
                name: b"ByAlternate",
                fields: &[IndexColumnSpec {
                    column: crate::ColumnRef::Ordinal(1),
                    direction: IndexDirection::Ascending,
                }],
                kind: IndexKind::Unique,
            },
        ],
    },
    TableSpec {
        name: b"Child",
        columns: &CHILD_COLUMNS,
        indexes: &[],
    },
];
const RELATION: CatalogSeed<'static> = CatalogSeed {
    id: RELATION_ID,
    parent: RELATIONSHIPS_ID,
    name: b"ParentChild",
    kind: 8,
    owner: CATALOG_OWNER_0301,
    flags: 0,
};
const RELATION_ACES: [AceSeed; 2] = [
    ace(RELATION_ID, b"\x03\x01", 983294, false),
    ace(RELATION_ID, b"\x02\x01", 1048575, false),
];

fn compose_parent_child(budget: &mut ResourceBudget) -> Result<WholeFileImagePlan, ComposeError> {
    let mut plan = compose_database(&TABLES, budget)?;
    let creates = [
        PlannedCreate::new(&TABLES[0], PARENT_ROOT, true)?,
        PlannedCreate::new(&TABLES[1], CHILD_ROOT, false)?,
    ];
    let object_count = (SYSTEM_OBJECT_COUNT + TABLES.len() + 1) as u32;
    let ace_count = (SYSTEM_ACE_COUNT + 2 * TABLES.len() + RELATION_ACES.len()) as u32;
    let mut header = header_page(TABLES.len(), budget)?;
    // EXP-0114 observed this raw value; its meaning is not generalized.
    header.write_at(
        PageOffset::new(PAGE0_TRANSITION_OFFSET),
        &[PAGE0_TRANSITION_BYTE],
        budget,
    )?;
    let replacements = [
        (HEADER_PAGE, header),
        (
            GLOBAL_MAP_PAGE,
            global_map_page(CHILD_INDEX_ROOT + 1, budget)?,
        ),
        (
            MSYS_OBJECTS_ROOT,
            msys_objects_definition(object_count, budget)?,
        ),
        (
            MSYS_ACES_ROOT,
            msys_aces_definition(ace_count, object_count, budget)?,
        ),
        (
            MSYS_RELATIONSHIPS_ROOT,
            msys_relationships_definition(1, [1; 3], budget)?,
        ),
        (
            OBJECTS_PARENT_NAME_ROOT,
            objects_parent_name_index(&creates, Some(RELATION), budget)?,
        ),
        (
            OBJECTS_ID_ROOT,
            objects_id_index(&creates, Some(RELATION), budget)?,
        ),
        (SHARED_MAP_PAGE, shared_map_page(&[RELATION_DATA], budget)?),
        (
            ACES_OBJECT_ID_ROOT,
            aces_index(&creates, &RELATION_ACES, budget)?,
        ),
        (
            MSYS_OBJECTS_DATA_PAGE,
            objects_data_page(&creates, Some(RELATION), budget)?,
        ),
        (
            MSYS_ACES_DATA_PAGE,
            aces_data_page(&creates, &RELATION_ACES, budget)?,
        ),
        (
            RELATIONSHIPS_NAME_ROOT,
            relation_index(
                b"\x7f\x73\x60\x75\x66\x70\x77\x62\x69\x6a\x6d\x64\x00",
                budget,
            )?,
        ),
        (
            RELATIONSHIPS_OBJECT_ROOT,
            relation_index(b"\x7f\x62\x69\x6a\x6d\x64\x00", budget)?,
        ),
        (
            RELATIONSHIPS_REFERENCED_ROOT,
            relation_index(b"\x7f\x73\x60\x75\x66\x70\x77\x00", budget)?,
        ),
        (PARENT_ROOT, user_definition(true, budget)?),
        (CHILD_ROOT, user_definition(false, budget)?),
        (CHILD_MAP, child_map(budget)?),
    ];
    for (page, image) in replacements {
        plan.replace(PageNumber::new(page), image)?;
    }
    let mut global_free = global_map(RELATION_DATA, budget)?;
    plan.append(relationship_data(budget)?, &mut global_free, budget)?;
    plan.append(
        empty_index_page(CHILD_ROOT, budget)?,
        &mut global_free,
        budget,
    )?;
    Ok(plan)
}

fn user_definition(parent: bool, budget: &mut ResourceBudget) -> Result<PageImage, ComposeError> {
    let id = [IndexFieldSpec {
        column: 0,
        direction: IndexDirection::Ascending,
    }];
    let alternate = [IndexFieldSpec {
        column: 1,
        direction: IndexDirection::Ascending,
    }];
    let parent_physical = [
        physical(
            &id,
            PARENT_MAP,
            2,
            PARENT_ID_ROOT,
            PhysicalIndexFlagsSpec::UniqueRequired,
        ),
        physical(
            &alternate,
            PARENT_MAP,
            3,
            PARENT_ALTERNATE_ROOT,
            PhysicalIndexFlagsSpec::Unique,
        ),
    ];
    let child_physical = [physical(
        &id,
        CHILD_MAP,
        2,
        CHILD_INDEX_ROOT,
        PhysicalIndexFlagsSpec::Ordinary,
    )];
    let parent_logical = [
        LogicalIndexSpec {
            name: b".rC",
            physical_index: 0,
            kind: LogicalIndexKindSpec::Relationship {
                side: RelationshipSide::PrimaryTable,
                related_table: PageNumber::new(CHILD_ROOT),
                raw_selector: 2,
                relation_ordinal: 0,
                cascade_updates: false,
                cascade_deletes: false,
            },
        },
        LogicalIndexSpec {
            name: b"ByAlternate",
            physical_index: 1,
            kind: LogicalIndexKindSpec::Ordinary,
        },
        LogicalIndexSpec {
            name: b"ById",
            physical_index: 0,
            kind: LogicalIndexKindSpec::Primary,
        },
    ];
    let child_logical = [LogicalIndexSpec {
        name: b"ParentChild",
        physical_index: 0,
        kind: LogicalIndexKindSpec::Relationship {
            side: RelationshipSide::ForeignTable,
            related_table: PageNumber::new(PARENT_ROOT),
            raw_selector: 0,
            relation_ordinal: 2,
            cascade_updates: false,
            cascade_deletes: false,
        },
    }];
    let map = if parent { PARENT_MAP } else { CHILD_MAP };
    definition_page(
        &TableDefinitionSpec {
            kind: TableDefinitionKind::User,
            columns: if parent {
                &PARENT_COLUMNS
            } else {
                &CHILD_COLUMNS
            },
            system_column_classes: &[],
            physical_indexes: if parent {
                &parent_physical
            } else {
                &child_physical
            },
            indexes: if parent {
                &parent_logical
            } else {
                &child_logical
            },
            owned_map: MapRowLocator::new(PageNumber::new(map), 0),
            available_map: MapRowLocator::new(PageNumber::new(map), 1),
            row_count: 0,
            long_value_maps: &[],
        },
        budget,
    )
}

fn physical<'a>(
    fields: &'a [IndexFieldSpec],
    map: u64,
    row: u8,
    root: u64,
    flags: PhysicalIndexFlagsSpec,
) -> PhysicalIndexSpec<'a> {
    PhysicalIndexSpec {
        fields,
        usage_map_page: PageNumber::new(map),
        usage_map_row: row,
        root: PageNumber::new(root),
        flags,
        entry_count: 0,
    }
}

fn child_map(budget: &mut ResourceBudget) -> Result<PageImage, ComposeError> {
    let empty = inline_map_row(&[], budget)?;
    let index = inline_map_row(&[CHILD_INDEX_ROOT], budget)?;
    data_page(HEADER_PAGE, &[&empty, &empty, &index], budget)
}

fn relationship_data(budget: &mut ResourceBudget) -> Result<PageImage, ComposeError> {
    // EXP-0073 column layout; EXP-0114 first relationship's exact values.
    let layout = [
        variable(ColumnPhysicalType::Text, 0, 255),
        fixed(ColumnPhysicalType::Long, 0, 4),
        fixed(ColumnPhysicalType::Long, 4, 4),
        fixed(ColumnPhysicalType::Long, 8, 4),
        variable(ColumnPhysicalType::Text, 1, 255),
        variable(ColumnPhysicalType::Text, 2, 255),
        variable(ColumnPhysicalType::Text, 3, 255),
        variable(ColumnPhysicalType::Text, 4, 255),
    ];
    let values = [
        RowValue::Text(b"ParentChild"),
        RowValue::Long(0),
        RowValue::Long(1),
        RowValue::Long(0),
        RowValue::Text(b"Child"),
        RowValue::Text(b"ParentId"),
        RowValue::Text(b"Parent"),
        RowValue::Text(b"Id"),
    ];
    let mut row = [0_u8; PAGE_BYTES];
    let length = encode_row(&layout, &values, &mut row, budget)?.get() as usize;
    data_page(MSYS_RELATIONSHIPS_ROOT, &[&row[..length]], budget)
}

fn relation_index(key: &[u8], budget: &mut ResourceBudget) -> Result<PageImage, ComposeError> {
    // Only the three exact EXP-0114 keys above are supplied; no text grammar.
    let mut entry = OwnedIndexEntry::EMPTY;
    let available = entry.key.len();
    entry
        .key
        .get_mut(..key.len())
        .ok_or(ComposeError::IndexPageFull {
            needed: key.len(),
            available,
        })?
        .copy_from_slice(key);
    entry.len = key.len();
    index_page(MSYS_RELATIONSHIPS_ROOT, RELATION_DATA, &[entry], budget)
}

#[cfg(test)]
#[path = "bootstrap_relationship_candidate_tests.rs"]
mod tests;
