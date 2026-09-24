//! EXP-0273 multiple/self endpoints, composed with ordinary table and catalog pages.
use super::{
    relationship_graph_plan::{GraphRelation, push},
    *,
};
use crate::{IndexColumnSpec, IndexSpec, RelationshipSpec, TableRows};

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
    let edges = super::relationship_graph_plan::resolve(requests, relationships, budget)?;
    let mut fields: Vec<[Vec<IndexColumnSpec<'_>>; 2]> = Vec::new();
    for edge in &edges {
        push(
            &mut fields,
            [edge.fields(false, budget)?, edge.fields(true, budget)?],
            budget,
        )?;
    }
    let mut indexes: Vec<Vec<IndexSpec<'_>>> = Vec::new();
    let mut overlays: Vec<Vec<LogicalIndexSpec<'_>>> = Vec::new();
    for (table, request) in requests.iter().enumerate() {
        budget.charge_work_units(edges.len() as u64)?;
        let mut physical = Vec::new();
        crate::format::resource::reserve(&mut physical, request.table.indexes.len(), budget)?;
        physical.extend_from_slice(request.table.indexes);
        let mut logical = Vec::new();
        for (edge, fields) in edges.iter().zip(&fields) {
            if edge.child == table {
                if usize::from(edge.physical) == physical.len() {
                    push(&mut physical, edge.foreign_index(&fields[0]), budget)?;
                }
                push(&mut logical, edge.logical(false), budget)?;
            }
            if edge.parent == table {
                if usize::from(edge.parent_physical) == physical.len() {
                    push(&mut physical, edge.parent_index(&fields[1]), budget)?;
                }
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
        let create = PlannedCreate::new_with_relationships(
            spec,
            next_page,
            position == 0,
            logical,
            request.table.indexes.len(),
            budget,
        )?
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
        budget.charge_items(requests[edge.child].rows.len() as u64)?;
        for (row, values) in requests[edge.child].rows.iter().enumerate() {
            let mut key_values = [RowValue::Null; crate::index::entry::MAX_FIELDS];
            let mut all_null = true;
            for ((&column, &kind), target) in edge
                .child_columns
                .iter()
                .zip(&edge.child_kinds)
                .zip(&mut key_values)
            {
                *target = *values.get(usize::from(column)).ok_or(
                    super::relationship_graph_plan::invalid("foreign key column absent"),
                )?;
                all_null &= kind.is_null(*target);
            }
            let key_values = &key_values[..edge.child_columns.len()];
            if all_null
                || creates[edge.parent].contains_initial_key(
                    edge.parent_physical,
                    &edge.child_kinds,
                    key_values,
                    budget,
                )?
            {
                continue;
            }
            return Err(match key_values {
                [RowValue::Long(value)] => {
                    ComposeError::OrphanInitialRelationshipKey { row, value: *value }
                }
                _ => ComposeError::OrphanInitialScalarRelationshipKey { row },
            });
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
    let relationship_pages = RelationshipPages::new(
        edges.iter().flat_map(|edge| {
            let child = &requests[edge.child].table;
            let parent = &requests[edge.parent].table;
            edge.parent_columns
                .iter()
                .zip(&edge.child_columns)
                .enumerate()
                .map(move |(ordinal, (&parent_column, &child_column))| {
                    relationship_pages::RelationshipRow {
                        name: edge.name,
                        flags: edge.flags,
                        child_table: child.name,
                        child_column: child.columns[usize::from(child_column)].name(),
                        parent_table: parent.name,
                        parent_column: parent.columns[usize::from(parent_column)].name(),
                        field_count: edge.parent_columns.len() as u16,
                        field_ordinal: ordinal as u16,
                    }
                })
        }),
        catalog.page_count(),
        budget,
    )?;
    let mut maps = AllocationMaps::new(relationship_pages.page_count());
    let images = compose_existing_pages_with_relationships(
        creates,
        &catalog,
        relationship_pages.maps(),
        &mut maps,
        budget,
    )?;
    let mut image = WholeFileImagePlan::from_existing_pages(images, budget)?;
    relationship_pages.replace_existing(&mut image, budget)?;
    for create in creates {
        create.append_pages(&mut image, &mut maps, budget)?;
    }
    catalog.append(&mut image, budget)?;
    relationship_pages.append(&mut image, budget)?;
    maps.finish(&mut image, budget)?;
    Ok(image)
}
