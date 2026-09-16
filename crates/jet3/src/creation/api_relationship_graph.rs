//! Atomic creation of the bounded EXP-0273 relationship graphs.
use super::*;
use crate::creation::composer::{GraphImage, compose_relationship_graph};
use crate::{CatalogObjectKind, RelationshipSide, RelationshipSpec, TableRef, TextCodePage};

/// Creates empty tables with up to two enforced, non-cascading Long relationships.
///
/// Table order is independent of relationship direction. Multiple endpoints,
/// chains, self-references and two parents sharing a child FK column are admitted.
/// Each parent needs an ascending Long/AutoIncrement primary as its first index. A child FK
/// must not already have a declared single-column index; the composer appends
/// its foreign index, sharing it between relationships on that column.
/// Other columns retain the normal creation planner's bounds. Generated foreign
/// indexes need free slots within the 32-physical-index limit. Parent logical
/// ordinals, including earlier relationship records, must fit the current
/// hidden-name construction policy through `.rZ` (ordinal 25).
///
/// This uses the EXP-0273 reciprocal grammar and existing creation primitives.
/// It is a candidate construction; only recorded DAO comparisons establish
/// compatibility. Existing destinations and the budget guarantees of
/// [`create_database`] apply.
pub fn create_database_with_relationships(
    path: impl AsRef<Path>,
    tables: &[TableSpec<'_>],
    relationships: &[RelationshipSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), CreateDatabaseError> {
    crate::creation::composer::table_count_limit(tables.len())
        .map_err(CreateDatabaseError::Compose)?;
    let mut requests = Vec::new();
    crate::resource::reserve(&mut requests, tables.len(), budget)
        .map_err(|error| CreateDatabaseError::Compose(error.into()))?;
    requests.extend(tables.iter().map(|&table| TableRows { table, rows: &[] }));
    create_database_with_relationships_and_rows(path, &requests, relationships, budget)
}

/// Creates tables, their initial rows and up to two Long relationships.
///
/// The schema restrictions of [`create_database_with_relationships`] apply.
/// Each non-null foreign key must occur in its parent's initial rows, including
/// self-references and keys shared by two parents. Complete rows, Memo/OLE
/// payloads, indexes, reciprocal metadata and allocation ownership are checked
/// before atomic publication. An empty relationship slice creates ordinary tables.
pub fn create_database_with_relationships_and_rows(
    path: impl AsRef<Path>,
    requests: &[TableRows<'_>],
    relationships: &[RelationshipSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), CreateDatabaseError> {
    let GraphImage { image, tables } = compose_relationship_graph(requests, relationships, budget)
        .map_err(CreateDatabaseError::Compose)?;
    let pages = image.into_pages();
    budget
        .charge_work_units((pages.len() as u64).saturating_mul(crate::PAGE_BYTES as u64))
        .map_err(|error| CreateDatabaseError::Compose(error.into()))?;
    atomic_create(
        path,
        |file| write_pages(file, &pages),
        |candidate| check_graph(candidate, requests, relationships, &tables, &pages, budget),
    )
    .map_err(CreateDatabaseError::Publish)
}

fn check_graph(
    candidate: &Path,
    requests: &[TableRows<'_>],
    relationships: &[RelationshipSpec<'_>],
    tables: &[(PageNumber, u64)],
    pages: &[PlannedPage],
    budget: &mut ResourceBudget,
) -> Result<(), CandidateCheckError> {
    let mismatch = |detail| CandidateCheckError::Mismatch { detail };
    let mut database =
        DatabaseReader::open(candidate, budget).map_err(CandidateCheckError::Open)?;
    if database.geometry().page_count() != pages.len() as u64 || requests.len() != tables.len() {
        return Err(mismatch("relationship graph geometry"));
    }
    let mut bytes = [0; crate::PAGE_BYTES];
    for page in pages {
        database
            .read_raw_page(page.number(), &mut bytes, budget)
            .map_err(CandidateCheckError::Read)?;
        budget
            .charge_work_units(crate::PAGE_BYTES as u64)
            .map_err(CandidateCheckError::Read)?;
        if &bytes != page.image().as_bytes() {
            return Err(mismatch("relationship graph written page"));
        }
    }
    let mut seen = Vec::new();
    crate::resource::reserve(&mut seen, requests.len(), budget)
        .map_err(CandidateCheckError::Read)?;
    seen.resize(requests.len(), false);
    let mut catalog = database
        .catalog(budget)
        .map_err(CandidateCheckError::Catalog)?;
    while let Some(record) = catalog
        .next_record()
        .map_err(CandidateCheckError::Catalog)?
    {
        if record.class() != CatalogObjectClass::User || record.kind() != CatalogObjectKind::Table {
            continue;
        }
        catalog
            .budget_mut()
            .charge_work_units((requests.len() as u64).saturating_mul(64))
            .map_err(CandidateCheckError::Read)?;
        let position = requests
            .iter()
            .position(|r| r.table.name == record.name().raw_bytes())
            .ok_or(mismatch("relationship graph catalog table"))?;
        if seen[position] || record.table_definition() != Some(tables[position].0) {
            return Err(mismatch("relationship graph catalog root"));
        }
        seen[position] = true;
    }
    drop(catalog);
    if seen.iter().any(|&present| !present) {
        return Err(mismatch("relationship graph missing table"));
    }
    let resolve = |reference| match reference {
        TableRef::Ordinal(position) => (position < requests.len()).then_some(position),
        TableRef::Name(name) => requests.iter().position(|r| r.table.name == name),
    };
    for (position, (request, &(root, payload))) in requests.iter().zip(tables).enumerate() {
        check_initial_table_rows_from(&mut database, request, root, payload, budget)?;
        let definition = database
            .table_definition(root, budget)
            .map_err(CandidateCheckError::Definition)?;
        check_columns(&definition, &request.table)?;
        schema_check::check(
            &definition,
            requests,
            relationships,
            tables,
            position,
            budget,
        )?;
        let expected = relationships
            .iter()
            .map(|r| {
                usize::from(resolve(r.parent.table) == Some(position))
                    + usize::from(resolve(r.child.table) == Some(position))
            })
            .sum::<usize>();
        if definition.relationships().count() != expected {
            return Err(mismatch("relationship graph endpoint count"));
        }
        // Loading checks exact central/reciprocal coverage, including incoming records.
        crate::relationship_catalog::load(&mut database, &definition, request.table.name, budget)
            .map_err(CandidateCheckError::Relationships)?;
        for spec in relationships
            .iter()
            .filter(|r| resolve(r.child.table) == Some(position))
        {
            let parent = resolve(spec.parent.table).ok_or(mismatch("relationship graph parent"))?;
            let parent_definition = database
                .table_definition(tables[parent].0, budget)
                .map_err(CandidateCheckError::Definition)?;
            let parent_key = parent_definition
                .physical_indexes()
                .first()
                .ok_or(mismatch("relationship graph parent index"))?;
            if parent_key.fields().len() != 1
                || Some(parent_key.fields()[0].column().get())
                    != spec.parent.column.resolve(requests[parent].table.columns)
            {
                return Err(mismatch("relationship graph parent column"));
            }
            let mut matching = definition.relationships().filter(|r| {
                r.side() == RelationshipSide::ForeignTable && r.name().raw_bytes() == spec.name
            });
            let relation = matching
                .next()
                .ok_or(mismatch("relationship graph foreign record"))?;
            if matching.next().is_some() || relation.related_table() != tables[parent].0 {
                return Err(mismatch("relationship graph target"));
            }
            let index = definition
                .physical_indexes()
                .get(usize::from(relation.physical_index()))
                .ok_or(mismatch("relationship graph physical index"))?;
            if index.fields().len() != 1
                || Some(index.fields()[0].column().get())
                    != spec.child.column.resolve(request.table.columns)
            {
                return Err(mismatch("relationship graph child column"));
            }
        }
    }
    database
        .validate(TextCodePage::Windows1252, budget)
        .map_err(|error| CandidateCheckError::Validation(Box::new(error)))?;
    Ok(())
}

#[cfg(all(test, any(unix, windows)))]
#[path = "relationship_graph_tests.rs"]
mod tests;

#[path = "graph_schema_check.rs"]
mod schema_check;
