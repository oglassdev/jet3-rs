//! Bounded relationship composition with caller-supplied schemas.
//!
//! EXP-0118 and EXP-0122 accepted three exact constructions, including the
//! two renamed one/two-parent-index cases. EXP-0087 supplies name-key weights;
//! no general name grammar or integrity-enforcement behavior is established.
use crate::{IndexColumnSpec, IndexKind, IndexSpec, PAGE_BYTES, RelationshipSpec};

use super::{relationship_plan::RelationshipPlan, *};
#[cfg(test)]
use crate::{RelationshipField, TableRef};

// EXP-0114 base and first checkpoints.
#[cfg(test)]
pub(super) const RELATION_DATA: u64 = 27;
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
pub(super) const TABLES: [TableSpec<'static>; 2] = [
    TableSpec {
        validation: crate::TableValidation::NONE,
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
        validation: crate::TableValidation::NONE,
        name: b"Child",
        columns: &CHILD_COLUMNS,
        indexes: &[],
    },
];
const RELATION: ObjectRow<'static> =
    ObjectRow::relationship(RELATION_ID, RELATIONSHIPS_ID, b"ParentChild");
const RELATION_ACES: [AceRow; 2] = AceRow::relationship_grants(RELATION_ID);

#[cfg(test)]
pub(super) fn compose_parent_child(
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    compose_relationship(
        &TABLES,
        &RelationshipSpec {
            unique: false,
            enforce: true,
            join: crate::RelationshipJoin::Inner,
            cascade_updates: false,
            cascade_deletes: false,
            name: RELATION.name,
            parent: TableRef::Ordinal(0),
            child: TableRef::Ordinal(1),
            fields: &[RelationshipField {
                parent: crate::ColumnRef::Ordinal(0),
                child: crate::ColumnRef::Ordinal(0),
            }],
        },
        budget,
    )
}

pub(crate) fn compose_relationship(
    tables: &[TableSpec<'_>],
    relationship: &RelationshipSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    super::relationship_rows::compose_with_rows(tables, [&[], &[]], relationship, budget)
}

pub(super) fn assemble_relationship(
    relation: &RelationshipPlan<'_>,
    creates: &[PlannedCreate<'_>],
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    let catalog = CatalogPages::new(creates, budget)?;
    if catalog.page_count() != relation.data_page() {
        return Err(ComposeError::CatalogLayout {
            detail: "relationship system catalog requires extra pages",
        });
    }
    let mut maps = AllocationMaps::new(relation.data_page() + 1);
    let images = compose_existing_pages(creates, &catalog, &mut maps, budget)?;
    let mut plan = WholeFileImagePlan::from_existing_pages(images, budget)?;
    for create in creates {
        create.append_pages(&mut plan, &mut maps, budget)?;
    }
    let tables = relation.tables;
    let relationship = relation.spec;
    let seed = ObjectRow {
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
            shared_map_page_with_aces(
                RelationshipMaps::single(&[relation.data_page()]),
                &[MSYS_ACES_DATA_PAGE],
                &[MSYS_ACES_DATA_PAGE],
                &[ACES_OBJECT_ID_ROOT],
                &mut maps,
                budget,
            )?,
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
    ];
    for (page, image) in replacements {
        plan.replace(PageNumber::new(page), image)?;
    }
    plan.append_image(relationship_data(relation, budget)?, budget)?;
    maps.finish(&mut plan, budget)?;
    Ok(plan)
}

fn relationship_data(
    relation: &RelationshipPlan<'_>,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    // EXP-0114 first relationship's exact values.
    let values = RelationshipRow {
        name: relation.spec.name,
        flags: 0,
        field_count: 1,
        field_ordinal: 0,
        child_table: relation.tables[1].name,
        child_column: relation.tables[1].columns[usize::from(relation.child_column)].name(),
        parent_table: relation.tables[0].name,
        parent_column: relation.tables[0].columns[usize::from(relation.parent_column)].name(),
    };
    let mut row = [0_u8; PAGE_BYTES];
    let length = super::relationship_pages::encode_relationship_row(values, &mut row, budget)?;
    data_page(MSYS_RELATIONSHIPS_ROOT, &[&row[..length]], budget)
}

pub(super) fn relation_index_name(
    name: &[u8],
    row_page: u64,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    // EXP-0118/0122 accepted the original and two renamed constructions
    // using the EXP-0087 text component as these standalone keys.
    let mut entry = OwnedIndexEntry::EMPTY;
    let length = encode_catalog_name_key(0, name, &mut entry.key)?;
    let prefix = crate::catalog::name_key::LONG_COMPONENT_LEN;
    entry.key.copy_within(prefix..length, 0);
    entry.len = length - prefix;
    index_page(MSYS_RELATIONSHIPS_ROOT, row_page, &[entry], budget)
}

pub(crate) use super::relationship_rows::compose_relationship_with_rows;
