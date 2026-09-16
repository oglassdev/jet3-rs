//! Atomic publication of the bounded EXP-0118/0122 relationship construction.

use super::*;
use crate::creation::composer::{compose_relationship, compose_relationship_with_rows};
use crate::{
    CatalogObjectKind, IndexColumnSpec, IndexDirection, IndexSpec, RelationshipSide,
    RelationshipSpec,
};

/// Creates two empty tables with one enforced, non-cascading Long relationship.
///
/// The parent is first and its first index is an ascending primary on the
/// referenced Long column. Empty creation also admits one additional ascending
/// unique Long index on the parent. The child may start with one ascending
/// Long/AutoIncrement primary on a different column; creation appends the foreign
/// physical index. Names or zero-based ordinals resolve table/column references.
///
/// Other columns retain the normal table planner's properties, AutoIncrement,
/// Memo/OLE maps and definition chains. Keys of other types, additional child
/// indexes, cascades, self-references and more than two tables are refused.
/// Written pages and reciprocal relationships are checked before atomic
/// publication. The budget and existing-destination guarantees of
/// [`create_database`] apply. Format encoders use EXP-0059/0114/0268; combining
/// these schemas is a candidate construction, not a general compatibility claim.
pub fn create_database_with_relationship(
    path: impl AsRef<Path>,
    tables: &[TableSpec<'_>],
    relationship: &RelationshipSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), CreateDatabaseError> {
    let pages = compose_relationship(tables, relationship, budget)
        .map_err(CreateDatabaseError::Compose)?
        .into_pages();
    budget
        .charge_work_units((pages.len() as u64).saturating_mul(crate::PAGE_BYTES as u64))
        .map_err(|error| CreateDatabaseError::Compose(ComposeError::Encoding(error)))?;
    atomic_create(
        path,
        |file| write_pages(file, &pages),
        |candidate| check_relationship_candidate(candidate, tables, relationship, &pages, budget),
    )
    .map_err(CreateDatabaseError::Publish)
}

/// Creates two related tables with initial rows, indexes and Memo/OLE payloads.
///
/// Requests contain parent then child, with one ascending Long primary on the
/// parent's referenced column. The child may have one separate ascending
/// Long/AutoIncrement primary. Null foreign keys are admitted; every non-null
/// Long foreign key must exist in the parent. Duplicate child keys are allowed.
/// Other columns support generated IDs, column options and independent Memo/OLE
/// storage under the normal table, definition-chain and allocation-map bounds.
///
/// Both index inventories, reciprocal metadata, complete rows and payloads are
/// checked before atomic publication. Existing destinations are preserved.
/// Unsupported relationship forms have the same restrictions as
/// [`create_database_with_relationship`]. DAO evidence covers only its recorded
/// finite comparisons; Rust candidate checking alone does not establish it.
pub fn create_database_with_relationship_rows(
    path: impl AsRef<Path>,
    requests: &[TableRows<'_>],
    relationship: &RelationshipSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), CreateDatabaseError> {
    let [parent, child] = requests else {
        return Err(CreateDatabaseError::Compose(
            ComposeError::UnsupportedRelationship {
                detail: "exactly two tables required",
            },
        ));
    };
    let tables = [parent.table, child.table];
    let pages = compose_relationship_with_rows(requests, relationship, budget)
        .map_err(CreateDatabaseError::Compose)?
        .into_pages();
    budget
        .charge_work_units((pages.len() as u64).saturating_mul(crate::PAGE_BYTES as u64))
        .map_err(|error| CreateDatabaseError::Compose(error.into()))?;
    atomic_create(
        path,
        |file| write_pages(file, &pages),
        |candidate| {
            check_relationship_contents(
                candidate,
                &tables,
                relationship,
                &pages,
                Some(requests),
                budget,
            )
        },
    )
    .map_err(CreateDatabaseError::Publish)
}

fn check_relationship_candidate(
    candidate: &Path,
    tables: &[TableSpec<'_>],
    relationship: &RelationshipSpec<'_>,
    pages: &[PlannedPage],
    budget: &mut ResourceBudget,
) -> Result<(), CandidateCheckError> {
    check_relationship_contents(candidate, tables, relationship, pages, None, budget)
}

fn check_relationship_contents(
    candidate: &Path,
    tables: &[TableSpec<'_>],
    relationship: &RelationshipSpec<'_>,
    pages: &[PlannedPage],
    requests: Option<&[TableRows<'_>]>,
    budget: &mut ResourceBudget,
) -> Result<(), CandidateCheckError> {
    let mismatch = |detail| CandidateCheckError::Mismatch { detail };
    let mut database =
        DatabaseReader::open(candidate, budget).map_err(CandidateCheckError::Open)?;
    if tables.len() != 2
        || requests.is_some_and(|rows| rows.len() != 2)
        || database.geometry().page_count() != pages.len() as u64
    {
        return Err(mismatch("relationship geometry"));
    }
    let mut bytes = [0_u8; crate::PAGE_BYTES];
    for page in pages {
        database
            .read_raw_page(page.number(), &mut bytes, budget)
            .map_err(CandidateCheckError::Read)?;
        budget
            .charge_work_units(crate::PAGE_BYTES as u64)
            .map_err(CandidateCheckError::Read)?;
        if &bytes != page.image().as_bytes() {
            return Err(mismatch("relationship written page"));
        }
    }
    let mut roots = [None; 2];
    {
        let mut catalog = database
            .catalog(budget)
            .map_err(CandidateCheckError::Catalog)?;
        while let Some(record) = catalog
            .next_record()
            .map_err(CandidateCheckError::Catalog)?
        {
            if record.class() != CatalogObjectClass::User
                || record.kind() != CatalogObjectKind::Table
            {
                continue;
            }
            let position = tables
                .iter()
                .position(|table| table.name == record.name().raw_bytes())
                .ok_or(mismatch("relationship catalog table"))?;
            if roots[position].is_some() {
                return Err(mismatch("relationship duplicate table"));
            }
            roots[position] = record.table_definition();
        }
    }
    for (position, table) in tables.iter().enumerate() {
        let root = roots[position].ok_or(mismatch("relationship catalog table"))?;
        let other = roots[1 - position].ok_or(mismatch("relationship catalog table"))?;
        let definition = database
            .table_definition(root, budget)
            .map_err(CandidateCheckError::Definition)?;
        let mut relations = definition.relationships();
        let relation = relations.next().ok_or(mismatch("relationship record"))?;
        let endpoint = if position == 0 {
            relationship.parent
        } else {
            relationship.child
        };
        let fields = definition
            .physical_indexes()
            .get(usize::from(relation.physical_index()))
            .ok_or(mismatch("relationship physical index"))?
            .fields();
        if relations.next().is_some()
            || relation.related_table() != other
            || relation.side()
                != if position == 0 {
                    RelationshipSide::PrimaryTable
                } else {
                    RelationshipSide::ForeignTable
                }
            || relation.cascade_updates()
            || relation.cascade_deletes()
            || (position == 1 && relation.name().raw_bytes() != relationship.name)
            || fields.len() != 1
            || endpoint.column.resolve(table.columns) != Some(fields[0].column().get())
        {
            return Err(mismatch("relationship endpoint"));
        }
        if let Some(requests) = requests {
            let fields = [IndexColumnSpec {
                column: relationship.child.column,
                direction: IndexDirection::Ascending,
            }];
            let foreign = IndexSpec {
                name: relationship.name,
                fields: &fields,
                kind: IndexKind::Ordinary,
            };
            let indexes = [
                requests[1]
                    .table
                    .indexes
                    .first()
                    .copied()
                    .unwrap_or(foreign),
                foreign,
            ];
            let child = TableRows {
                table: TableSpec {
                    indexes: if requests[1].table.indexes.is_empty() {
                        &indexes[1..]
                    } else {
                        &indexes
                    },
                    ..requests[1].table
                },
                rows: requests[1].rows,
            };
            let request = if position == 0 { &requests[0] } else { &child };
            let plan = crate::creation::schema_plan::plan_table_schema_with_logical_index(
                &request.table,
                root.get(),
                position == 0,
                (position == 0).then_some(relation.name().raw_bytes()),
                budget,
            )
            .map_err(|error| CandidateCheckError::RowEncoding(ComposeError::Schema(error)))?;
            check_initial_table_rows_from(
                &mut database,
                request,
                root,
                root.get() + plan.appended_page_count(),
                budget,
            )?;
        } else {
            for ordinal in 0..definition.physical_indexes().len() {
                let ordinal =
                    u16::try_from(ordinal).map_err(|_| mismatch("relationship index count"))?;
                if !database
                    .index_tree(&definition, ordinal, budget)
                    .map_err(CandidateCheckError::Index)?
                    .entries()
                    .is_empty()
                {
                    return Err(mismatch("relationship index not empty"));
                }
            }
            if database
                .rows(&definition, budget)
                .map_err(CandidateCheckError::Rows)?
                .next_row()
                .map_err(CandidateCheckError::Rows)?
                .is_some()
            {
                return Err(mismatch("relationship rows not empty"));
            }
        }
    }
    Ok(())
}

#[cfg(all(test, any(unix, windows)))]
#[path = "relationship_tests.rs"]
mod tests;
