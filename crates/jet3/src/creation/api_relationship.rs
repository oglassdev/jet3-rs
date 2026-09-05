//! Atomic publication of the bounded EXP-0118/0122 relationship construction.

use super::*;
use crate::creation::composer::{compose_relationship, compose_relationship_with_rows};
use crate::{
    CatalogObjectKind, IndexColumnSpec, IndexDirection, IndexSpec, RelationshipSide,
    RelationshipSpec,
};

/// Creates two empty tables with one non-cascading Long-to-Long relationship.
///
/// The parent must be the first table and the child the second. The parent's
/// first index must be an ascending, single-column primary index on the
/// referenced Long column. It may have one further ascending, single-Long
/// unique index. The child must initially have no indexes; creation adds its
/// foreign index, named by `relationship.name`. Table and column references
/// may use names or zero-based ordinals.
///
/// AutoIncrement, Memo, LongBinary, continued definitions, and unsupported
/// name bytes are refused with [`CreateDatabaseError::Compose`] before writing.
/// The name and scalar-column bounds of [`create_database`] also apply.
/// The atomic publication and existing-destination guarantees are the same:
/// the written pages, catalog, user definitions, reciprocal relationships,
/// empty rows, and empty index trees are checked before publication. All
/// composition, writing, and checking is charged to `budget`.
///
/// `EXP-0118` and `EXP-0122` observed DAO accept three exact original/renamed
/// images with these one/two-parent-index shapes. Other names and schemas
/// compose from the same bounded encoders; they have no individual DAO
/// result. Referential-integrity mutations, cascades, and hosted write
/// support were not established by those read-only experiments.
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

/// Creates two tables with initial rows and one non-cascading Long relationship.
///
/// Requests must contain parent then child. The parent has exactly one
/// ascending Long primary index on its relationship column; the child starts
/// unindexed and receives an ordinary foreign index. Both relationship keys
/// must be non-null Long values, every child key must occur in the parent,
/// and both index trees grow within the existing inline-map and resource
/// limits. Repeated child keys are allowed. Other scalar columns may span data pages under
/// the existing row bounds. AutoIncrement and long-value columns are refused.
///
/// Existing destinations are preserved, and complete written pages,
/// reciprocal relationship metadata, rows and both index trees are checked
/// before atomic publication. This bounded candidate construction does not
/// establish general DAO compatibility or subsequent integrity enforcement.
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
            let indexes = [IndexSpec {
                name: relationship.name,
                fields: &fields,
                kind: IndexKind::Ordinary,
            }];
            let child = TableRows {
                table: TableSpec {
                    indexes: &indexes,
                    ..requests[1].table
                },
                rows: requests[1].rows,
            };
            let request = if position == 0 { &requests[0] } else { &child };
            check_initial_table_rows(&mut database, request, root, position == 0, budget)?;
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

#[cfg(all(test, unix))]
#[path = "relationship_tests.rs"]
mod tests;
