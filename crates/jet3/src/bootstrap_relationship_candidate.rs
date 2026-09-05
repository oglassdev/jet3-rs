//! Bounded relationship composition with caller-supplied schemas.
//!
//! EXP-0118 and EXP-0122 accepted three exact constructions, including the
//! two renamed one/two-parent-index cases. EXP-0087 supplies name-key weights;
//! no general name grammar or integrity-enforcement behavior is established.

#![allow(dead_code, reason = "retained deterministic relationship fixtures")]

use super::*;
use crate::{IndexColumnSpec, IndexFieldSpec, IndexKind, IndexSpec, RelationshipSide};

#[path = "bootstrap_relationship_plan.rs"]
mod planning;
use planning::RelationshipPlan;
pub use planning::{RelationshipColumn, RelationshipSpec, TableRef};

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
    compose_relationship(
        &TABLES,
        &RelationshipSpec {
            name: RELATION.name,
            parent: RelationshipColumn {
                table: TableRef::Ordinal(0),
                column: crate::ColumnRef::Ordinal(0),
            },
            child: RelationshipColumn {
                table: TableRef::Ordinal(1),
                column: crate::ColumnRef::Ordinal(0),
            },
        },
        budget,
    )
}

pub(crate) fn compose_relationship(
    tables: &[TableSpec<'_>],
    relationship: &RelationshipSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    let relation = RelationshipPlan::new(tables, relationship)?;
    let plan = compose_database(tables, budget)?;
    let creates = [
        PlannedCreate::new(&tables[0], relation.parent.definition_root().get(), true)?,
        PlannedCreate::new(&tables[1], relation.child.definition_root().get(), false)?,
    ];
    assemble_relationship(&relation, &creates, plan, None, budget)
}

fn assemble_relationship(
    relation: &RelationshipPlan<'_>,
    creates: &[PlannedCreate<'_>],
    mut plan: WholeFileImagePlan,
    child_index: Option<&InitialLongIndex>,
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    let tables = relation.tables;
    let relationship = relation.spec;
    let seed = CatalogSeed {
        name: relationship.name,
        ..RELATION
    };
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
            global_map_page(
                relation.index_page()
                    + 1
                    + child_index.map_or(0, InitialLongIndex::extra_page_count),
                budget,
            )?,
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
            objects_parent_name_index(creates, Some(seed), budget)?,
        ),
        (
            OBJECTS_ID_ROOT,
            objects_id_index(creates, Some(seed), budget)?,
        ),
        (
            SHARED_MAP_PAGE,
            shared_map_page(&[relation.data_page()], budget)?,
        ),
        (
            ACES_OBJECT_ID_ROOT,
            aces_index(creates, &RELATION_ACES, budget)?,
        ),
        (
            MSYS_OBJECTS_DATA_PAGE,
            objects_data_page(creates, Some(seed), budget)?,
        ),
        (
            MSYS_ACES_DATA_PAGE,
            aces_data_page(creates, &RELATION_ACES, budget)?,
        ),
        (
            RELATIONSHIPS_NAME_ROOT,
            relation_index_name(relationship.name, relation.data_page(), budget)?,
        ),
        (
            RELATIONSHIPS_OBJECT_ROOT,
            relation_index_name(tables[1].name, relation.data_page(), budget)?,
        ),
        (
            RELATIONSHIPS_REFERENCED_ROOT,
            relation_index_name(tables[0].name, relation.data_page(), budget)?,
        ),
        (
            relation.parent.definition_root().get(),
            user_definition(
                relation,
                true,
                creates[0].row_count(),
                creates[0].index_distinct_count(),
                budget,
            )?,
        ),
        (
            relation.child.definition_root().get(),
            user_definition(
                relation,
                false,
                creates[1].row_count(),
                child_index.map_or(0, InitialLongIndex::distinct_count),
                budget,
            )?,
        ),
        (
            relation.child.map_page().get(),
            creates[1].map_page(
                Some((
                    PageNumber::new(relation.index_page()),
                    child_index.map_or(0, InitialLongIndex::extra_page_count),
                )),
                budget,
            )?,
        ),
    ];
    for (page, image) in replacements {
        plan.replace(PageNumber::new(page), image)?;
    }
    let mut global_free = global_map(relation.data_page(), budget)?;
    plan.append(
        relationship_data(relation, budget)?,
        &mut global_free,
        budget,
    )?;
    plan.append(
        match child_index {
            Some(index) => index.image(
                relation.child.definition_root(),
                PageNumber::new(relation.index_page()),
                relation.index_page() + 1,
                None,
                budget,
            )?,
            None => empty_index_page(relation.child.definition_root().get(), budget)?,
        },
        &mut global_free,
        budget,
    )?;
    if let Some(index) = child_index {
        for ordinal in 0..index.extra_page_count() {
            plan.append(
                index.image(
                    relation.child.definition_root(),
                    PageNumber::new(relation.index_page()),
                    relation.index_page() + 1,
                    Some(ordinal as usize),
                    budget,
                )?,
                &mut global_free,
                budget,
            )?;
        }
    }
    Ok(plan)
}

fn user_definition(
    relation: &RelationshipPlan<'_>,
    parent: bool,
    row_count: u32,
    entry_count: u32,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let child_fields = [IndexFieldSpec {
        column: relation.child_column,
        direction: IndexDirection::Ascending,
    }];
    let child_physical = [PhysicalIndexSpec {
        fields: &child_fields,
        usage_map_page: relation.child.map_page(),
        usage_map_row: 2,
        root: PageNumber::new(relation.index_page()),
        flags: PhysicalIndexFlagsSpec::Ordinary,
        entry_count,
    }];
    let mut parent_physical = [child_physical[0]; 2];
    for (slot, (((root, row), index), fields)) in parent_physical.iter_mut().zip(
        relation
            .parent
            .index_placements()
            .zip(relation.tables[0].indexes)
            .zip(relation.parent.index_fields()),
    ) {
        *slot = PhysicalIndexSpec {
            fields,
            usage_map_page: relation.parent.map_page(),
            usage_map_row: row,
            root,
            flags: index.kind.flags(),
            entry_count,
        };
    }
    let relationship_index = LogicalIndexSpec {
        name: if parent {
            relation.hidden_name
        } else {
            relation.spec.name
        },
        physical_index: 0,
        kind: LogicalIndexKindSpec::Relationship {
            side: if parent {
                RelationshipSide::PrimaryTable
            } else {
                RelationshipSide::ForeignTable
            },
            related_table: if parent {
                relation.child.definition_root()
            } else {
                relation.parent.definition_root()
            },
            raw_selector: if parent { relation.selector } else { 0 },
            relation_ordinal: if parent { 0 } else { relation.selector },
            cascade_updates: false,
            cascade_deletes: false,
        },
    };
    let mut logical = [relationship_index; 3];
    let logical_count = if parent {
        relation.tables[0].indexes.len() + 1
    } else {
        1
    };
    if parent {
        for (ordinal, index) in relation.tables[0].indexes.iter().enumerate() {
            logical[ordinal + 1] = LogicalIndexSpec {
                name: index.name,
                physical_index: ordinal as u16,
                kind: index.kind.logical_kind(),
            };
        }
        logical[..logical_count].sort_unstable_by(|left, right| left.name.cmp(right.name));
    }
    let table = if parent {
        &relation.tables[0]
    } else {
        &relation.tables[1]
    };
    let map = if parent {
        relation.parent.map_page()
    } else {
        relation.child.map_page()
    };
    definition_page(
        &TableDefinitionSpec {
            kind: TableDefinitionKind::User,
            columns: table.columns,
            system_column_classes: &[],
            physical_indexes: if parent {
                &parent_physical[..relation.tables[0].indexes.len()]
            } else {
                &child_physical
            },
            indexes: &logical[..logical_count],
            owned_map: MapRowLocator::new(map, 0),
            available_map: MapRowLocator::new(map, 1),
            row_count,
            long_value_maps: &[],
        },
        budget,
    )
}

fn relationship_data(
    relation: &RelationshipPlan<'_>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
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
        RowValue::Text(relation.spec.name),
        RowValue::Long(0),
        RowValue::Long(1),
        RowValue::Long(0),
        RowValue::Text(relation.tables[1].name),
        RowValue::Text(relation.tables[1].columns[usize::from(relation.child_column)].name()),
        RowValue::Text(relation.tables[0].name),
        RowValue::Text(relation.tables[0].columns[usize::from(relation.parent_column)].name()),
    ];
    let mut row = [0_u8; PAGE_BYTES];
    let length = encode_row(&layout, &values, &mut row, budget)?.get() as usize;
    data_page(MSYS_RELATIONSHIPS_ROOT, &[&row[..length]], budget)
}

fn relation_index_name(
    name: &[u8],
    row_page: u64,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    // EXP-0118/0122 accepted the original and two renamed constructions
    // using the EXP-0087 text component as these standalone keys.
    let mut entry = OwnedIndexEntry::EMPTY;
    let length = encode_catalog_name_key(0, name, &mut entry.key)?;
    let prefix = crate::catalog_name_key::LONG_COMPONENT_LEN;
    entry.key.copy_within(prefix..length, 0);
    entry.len = length - prefix;
    index_page(MSYS_RELATIONSHIPS_ROOT, row_page, &[entry], budget)
}

#[cfg(test)]
#[path = "bootstrap_relationship_candidate_tests.rs"]
mod tests;

#[cfg(test)]
#[path = "bootstrap_relationship_parameterized_tests.rs"]
mod parameterized_tests;

#[path = "bootstrap_relationship_rows.rs"]
mod initial_rows;
pub(crate) use initial_rows::compose_relationship_with_rows;
