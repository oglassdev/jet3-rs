//! EXP-0273 multiple/self endpoints, composed with ordinary table and catalog pages.
use super::*;
use crate::{IndexColumnSpec, IndexSpec, RelationshipSpec, TableRows};

#[path = "relationship_graph_plan.rs"]
mod planning;
use planning::{GraphRelation, push};

pub(crate) struct GraphImage {
    pub image: WholeFileImagePlan,
    pub tables: Vec<(PageNumber, u64)>,
}

pub(crate) fn compose_relationship_graph(
    requests: &[TableRows<'_>],
    relationships: &[RelationshipSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<GraphImage, ComposeError> {
    let mut creates = reserve_creates(requests.len(), budget)?;
    let edges = planning::resolve(requests, relationships, budget)?;
    let mut fields: Vec<[IndexColumnSpec<'_>; 1]> = Vec::new();
    for edge in &edges {
        push(&mut fields, edge.field(), budget)?;
    }
    let mut indexes: Vec<Vec<IndexSpec<'_>>> = Vec::new();
    let mut overlays: Vec<Vec<LogicalIndexSpec<'_>>> = Vec::new();
    for (table, request) in requests.iter().enumerate() {
        let mut physical = Vec::new();
        crate::resource::reserve(&mut physical, request.table.indexes.len(), budget)?;
        physical.extend_from_slice(request.table.indexes);
        let mut logical = Vec::new();
        for (edge, fields) in edges.iter().zip(&fields) {
            if edge.child == table {
                if usize::from(edge.physical) == physical.len() {
                    push(&mut physical, edge.foreign_index(fields), budget)?;
                }
                push(&mut logical, edge.logical(false), budget)?;
            }
            if edge.parent == table {
                push(&mut logical, edge.logical(true), budget)?;
            }
        }
        push(&mut indexes, physical, budget)?;
        push(&mut overlays, logical, budget)?;
    }
    let mut specs = Vec::new();
    for (request, indexes) in requests.iter().zip(&indexes) {
        push(
            &mut specs,
            TableSpec {
                indexes,
                ..request.table
            },
            budget,
        )?;
    }
    let mut next_page = EMPTY_DATABASE_PAGE_COUNT;
    let mut tables = Vec::new();
    let mut roots = Vec::new();
    for (position, ((spec, request), logical)) in
        specs.iter().zip(requests).zip(&overlays).enumerate()
    {
        let create =
            PlannedCreate::new_with_relationships(spec, next_page, position == 0, logical, budget)?
                .with_rows(request.rows, budget)?;
        let root = create.schema().definition_root();
        push(
            &mut tables,
            (root, root.get() + create.schema().appended_page_count()),
            budget,
        )?;
        push(&mut roots, root, budget)?;
        next_page = create.page_count();
        creates.push(create);
    }
    for create in &mut creates {
        create.resolve_relationship_targets(&roots)?;
    }
    for edge in &edges {
        for (row, values) in requests[edge.child].rows.iter().enumerate() {
            match values.get(usize::from(edge.child_column)) {
                Some(RowValue::Null) => {}
                Some(RowValue::Long(value))
                    if creates[edge.parent].contains_initial_long(*value, budget)? => {}
                Some(RowValue::Long(value)) => {
                    return Err(ComposeError::OrphanInitialRelationshipKey { row, value: *value });
                }
                _ => return Err(planning::invalid("foreign key value must be Long or null")),
            }
        }
    }
    let image = if edges.is_empty() {
        compose_planned_creates(&creates, budget)?
    } else {
        assemble(requests, &edges, &creates, budget)?
    };
    Ok(GraphImage { image, tables })
}

fn assemble(
    requests: &[TableRows<'_>],
    edges: &[GraphRelation<'_>],
    creates: &[PlannedCreate<'_>],
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    let mut objects = Vec::new();
    let mut aces = Vec::new();
    for (position, edge) in edges.iter().enumerate() {
        let id = i32::MIN + position as i32;
        push(
            &mut objects,
            CatalogSeed {
                id,
                parent: RELATIONSHIPS_ID,
                name: edge.name,
                kind: 8,
                owner: CATALOG_OWNER_0301,
                flags: 0,
            },
            budget,
        )?;
        push(&mut aces, ace(id, b"\x03\x01", 983294, false), budget)?;
        push(&mut aces, ace(id, b"\x02\x01", 1048575, false), budget)?;
    }
    let catalog = CatalogPages::new_with_extras(creates, &objects, &aces, budget)?;
    let data_page = catalog.page_count();
    let mut maps = AllocationMaps::new(data_page + 1);
    let images = compose_existing_pages_with_relationships(
        creates,
        &catalog,
        &[data_page],
        &mut maps,
        budget,
    )?;
    let mut image = WholeFileImagePlan::from_existing_pages(images, budget)?;
    let mut counts = [0; 3];
    for (ordinal, root) in [
        RELATIONSHIPS_NAME_ROOT,
        RELATIONSHIPS_OBJECT_ROOT,
        RELATIONSHIPS_REFERENCED_ROOT,
    ]
    .into_iter()
    .enumerate()
    {
        let mut entries = Vec::new();
        for (row, edge) in edges.iter().enumerate() {
            let name = match ordinal {
                0 => edge.name,
                1 => requests[edge.child].table.name,
                _ => requests[edge.parent].table.name,
            };
            let mut entry = OwnedIndexEntry::name(0, name, catalog_row_number(row)?)?;
            let prefix = crate::catalog_name_key::LONG_COMPONENT_LEN;
            entry.key.copy_within(prefix..entry.len, 0);
            entry.len -= prefix;
            push(&mut entries, entry, budget)?;
        }
        sort_index_entries(&mut entries);
        counts[ordinal] = 1 + entries
            .windows(2)
            .filter(|pair| pair[0].key[..pair[0].len] != pair[1].key[..pair[1].len])
            .count() as u32;
        image.replace(
            PageNumber::new(root),
            index_page(MSYS_RELATIONSHIPS_ROOT, data_page, &entries, budget)?,
        )?;
    }
    image.replace(
        PageNumber::new(MSYS_RELATIONSHIPS_ROOT),
        msys_relationships_definition(edges.len() as u32, counts, budget)?,
    )?;
    for create in creates {
        create.append_pages(&mut image, &mut maps, budget)?;
    }
    catalog.append(&mut image, budget)?;
    image.append_image(relationship_rows(requests, edges, budget)?, budget)?;
    maps.finish(&mut image, budget)?;
    Ok(image)
}

fn relationship_rows(
    requests: &[TableRows<'_>],
    edges: &[GraphRelation<'_>],
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
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
    let mut builder = DataPageBuilder::new(PageNumber::new(MSYS_RELATIONSHIPS_ROOT), budget)?;
    let mut row = [0; PAGE_BYTES];
    for edge in edges {
        let child = &requests[edge.child].table;
        let parent = &requests[edge.parent].table;
        let values = [
            RowValue::Text(edge.name),
            RowValue::Long(0),
            RowValue::Long(1),
            RowValue::Long(0),
            RowValue::Text(child.name),
            RowValue::Text(child.columns[usize::from(edge.child_column)].name()),
            RowValue::Text(parent.name),
            RowValue::Text(parent.columns[usize::from(edge.parent_column)].name()),
        ];
        let length = encode_row(&layout, &values, &mut row, budget)?.get() as usize;
        builder.append_row(&row[..length], budget)?;
    }
    finish_data_builder(builder, budget)
}
