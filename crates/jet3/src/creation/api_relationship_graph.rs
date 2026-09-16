//! Atomic relationship graph creation using EXP-0273/0279 endpoint records.
use super::*;
use crate::creation::composer::{GraphImage, compose_relationship_graph};
use crate::{CatalogObjectKind, RelationshipSpec, TableRef, TextCodePage};

/// Creates empty tables with enforced, non-cascading Long relationships.
///
/// Table order is independent of relationship direction. Multiple endpoints,
/// chains, self-references and parents sharing a child FK column are admitted.
/// Each parent needs a unique Long/AutoIncrement index. The composer selects
/// an ascending index first, in logical name order. If only a descending index
/// qualifies, it generates an ascending tree with the same null policy, shared
/// by relationships on that parent column (EXP-0286). An ordinary ascending
/// child index on the FK column is reused, retaining its declared name; otherwise
/// the composer adds a foreign index. Relationships on the same child column
/// share its physical index. Each reciprocal relationship record consumes one
/// of the table's 32 logical index slots; a self-reference consumes two.
/// Other columns retain the normal
/// creation planner's bounds.
///
/// This uses the EXP-0273/0279 reciprocal grammar and existing creation primitives.
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

/// Creates tables, their initial rows and Long relationships.
///
/// The schema restrictions of [`create_database_with_relationships`] apply.
/// Each non-null foreign key must occur in its parent's initial rows, including
/// self-references and keys shared by multiple parents. Complete rows, Memo/OLE
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
        budget
            .charge_work_units(
                (relationships.len() as u64)
                    .saturating_mul(requests.len() as u64)
                    .saturating_mul(2 * 64),
            )
            .map_err(CandidateCheckError::Read)?;
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
    }
    let report = database
        .validate(TextCodePage::Windows1252, budget)
        .map_err(|error| CandidateCheckError::Validation(Box::new(error)))?;
    if report.relationship_catalog_rows != relationships.len() as u64
        || report.relationships_with_verified_keys != relationships.len() as u64
        || report.uninterpreted_relationship_rows != 0
        || !report.relationship_inventory_checked
    {
        return Err(mismatch("relationship graph complete validation"));
    }
    Ok(())
}

#[cfg(all(test, any(unix, windows)))]
#[path = "relationship_graph_tests.rs"]
mod tests;

#[path = "graph_schema_check.rs"]
mod schema_check;
