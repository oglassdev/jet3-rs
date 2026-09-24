//! Atomic relationship graph creation using EXP-0273/0279 endpoint records.
use super::api::*;
use crate::{
    CatalogObjectClass, CatalogObjectKind, DatabaseReader, PageNumber, RelationshipSpec,
    ResourceBudget, TableRef, TableSpec, TextCodePage,
    create::{
        composer::{GraphImage, compose_relationship_graph},
        page_append_plan::PlannedPage,
    },
    write::atomic::atomic_create,
};
use std::path::Path;

/// Creates empty tables joined by enforced relationships of one to ten scalar
/// fields each.
///
/// Table order is independent of relationship direction; chains, multiple
/// endpoints and self-references are admitted. Missing parent or child
/// indexes are generated. The result is checked and published like
/// [`create_database`].
///
/// # Errors
///
/// As [`create_database`]; unsupported relationship forms fail before writing.
/// See `docs/plans/V1_SCOPE.md` for the supported scope.
pub fn create_database_with_relationships(
    path: impl AsRef<Path>,
    tables: &[TableSpec<'_>],
    relationships: &[RelationshipSpec<'_>],
    budget: &mut ResourceBudget,
) -> Result<(), CreateDatabaseError> {
    crate::create::composer::table_count_limit(tables.len())
        .map_err(CreateDatabaseError::Compose)?;
    let mut requests = Vec::new();
    crate::format::resource::reserve(&mut requests, tables.len(), budget)
        .map_err(|error| CreateDatabaseError::Compose(error.into()))?;
    requests.extend(tables.iter().map(|&table| TableRows { table, rows: &[] }));
    create_database_with_relationships_and_rows(path, &requests, relationships, budget)
}

/// Creates tables, their initial rows and relationships with one to ten scalar fields.
///
/// The schema restrictions of [`create_database_with_relationships`] apply.
/// Each foreign key with any non-null component must occur in its parent's initial rows, including
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

pub(super) fn check_graph(
    candidate: &Path,
    requests: &[TableRows<'_>],
    relationships: &[RelationshipSpec<'_>],
    tables: &[(PageNumber, u64)],
    pages: &[PlannedPage],
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    let mismatch = |detail| ImageCheckError::Mismatch { detail };
    let mut database = DatabaseReader::open(candidate, budget).map_err(ImageCheckError::Open)?;
    if database.geometry().page_count() != pages.len() as u64 || requests.len() != tables.len() {
        return Err(mismatch("relationship graph geometry"));
    }
    let mut bytes = [0; crate::PAGE_BYTES];
    for page in pages {
        database
            .read_raw_page(page.number(), &mut bytes, budget)
            .map_err(ImageCheckError::Read)?;
        budget
            .charge_work_units(crate::PAGE_BYTES as u64)
            .map_err(ImageCheckError::Read)?;
        if &bytes != page.image().as_bytes() {
            return Err(mismatch("relationship graph written page"));
        }
    }
    let mut seen = Vec::new();
    crate::format::resource::reserve(&mut seen, requests.len(), budget)
        .map_err(ImageCheckError::Read)?;
    seen.resize(requests.len(), false);
    let mut catalog = database.catalog(budget).map_err(ImageCheckError::Catalog)?;
    while let Some(record) = catalog.next_record().map_err(ImageCheckError::Catalog)? {
        if record.class() != CatalogObjectClass::User || record.kind() != CatalogObjectKind::Table {
            continue;
        }
        catalog
            .budget_mut()
            .charge_work_units((requests.len() as u64).saturating_mul(64))
            .map_err(ImageCheckError::Read)?;
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
            .map_err(ImageCheckError::Definition)?;
        check_columns(&definition, &request.table)?;
        super::graph_schema_check::check(
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
            .map_err(ImageCheckError::Read)?;
        let expected = relationships
            .iter()
            .map(|r| {
                usize::from(resolve(r.parent) == Some(position))
                    + usize::from(resolve(r.child) == Some(position))
            })
            .sum::<usize>();
        if definition.relationships().count() != expected {
            return Err(mismatch("relationship graph endpoint count"));
        }
    }
    let report = database
        .validate(TextCodePage::Windows1252, budget)
        .map_err(|error| ImageCheckError::Validation(Box::new(error)))?;
    let expected_rows = relationships
        .iter()
        .try_fold(0_u64, |count, relationship| {
            count
                .checked_add(relationship.fields.len() as u64)
                .ok_or(mismatch("relationship catalog row count"))
        })?;
    if report.relationship_catalog_rows != expected_rows
        || report.relationships_with_verified_keys != relationships.len() as u64
        || report.uninterpreted_relationship_rows != 0
        || !report.relationship_inventory_checked
    {
        return Err(mismatch("relationship graph complete validation"));
    }
    Ok(())
}
