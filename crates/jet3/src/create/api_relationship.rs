//! Atomic publication of the bounded EXP-0118/0122 relationship construction.

use super::{
    api::{CreateDatabaseError, TableRows, write_pages},
    check::{ImageCheckError, check_initial_table_rows_from},
};
use crate::{
    CatalogObjectClass, CatalogObjectKind, DatabaseReader, IndexColumnSpec, IndexDirection,
    IndexKind, IndexSpec, RelationshipSide, RelationshipSpec, ResourceBudget, TableSpec,
    create::{
        composer::{ComposeError, compose_relationship, compose_relationship_with_rows},
        page_append_plan::PlannedPage,
    },
    write::atomic::atomic_create,
};
use std::path::Path;

pub(super) fn create(
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
        |candidate| check_relationship_image(candidate, tables, relationship, &pages, budget),
    )
    .map_err(CreateDatabaseError::Publish)
}

pub(super) fn create_with_rows(
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

pub(super) fn check_relationship_image(
    candidate: &Path,
    tables: &[TableSpec<'_>],
    relationship: &RelationshipSpec<'_>,
    pages: &[PlannedPage],
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    check_relationship_contents(candidate, tables, relationship, pages, None, budget)
}

pub(super) fn check_relationship_contents(
    candidate: &Path,
    tables: &[TableSpec<'_>],
    relationship: &RelationshipSpec<'_>,
    pages: &[PlannedPage],
    requests: Option<&[TableRows<'_>]>,
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    let mismatch = |detail| ImageCheckError::Mismatch { detail };
    let mut database = DatabaseReader::open(candidate, budget).map_err(ImageCheckError::Open)?;
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
            .map_err(ImageCheckError::Read)?;
        budget
            .charge_work_units(crate::PAGE_BYTES as u64)
            .map_err(ImageCheckError::Read)?;
        if &bytes != page.image().as_bytes() {
            return Err(mismatch("relationship written page"));
        }
    }
    let mut roots = [None; 2];
    {
        let mut catalog = database.catalog(budget).map_err(ImageCheckError::Catalog)?;
        while let Some(record) = catalog.next_record().map_err(ImageCheckError::Catalog)? {
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
            .map_err(ImageCheckError::Definition)?;
        let mut relations = definition.relationships();
        let relation = relations.next().ok_or(mismatch("relationship record"))?;
        let [field] = relationship.fields else {
            return Err(mismatch("singular relationship field count"));
        };
        let column = if position == 0 {
            field.parent
        } else {
            field.child
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
            || column.resolve(table.columns) != Some(fields[0].column().get())
        {
            return Err(mismatch("relationship endpoint"));
        }
        if let Some(requests) = requests {
            let fields = [IndexColumnSpec {
                column: field.child,
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
            let plan = crate::create::schema_plan::plan_table_schema_for_order(
                &request.table,
                root.get(),
                position == 0,
                (position == 0)
                    .then_some(relation.name().raw_bytes())
                    .as_slice(),
                request.table.indexes.len(),
                crate::SortOrder::General,
                budget,
            )
            .map_err(|error| ImageCheckError::RowEncoding(ComposeError::Schema(error)))?;
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
                    .map_err(ImageCheckError::Index)?
                    .entries()
                    .is_empty()
                {
                    return Err(mismatch("relationship index not empty"));
                }
            }
            if database
                .rows(&definition, budget)
                .map_err(ImageCheckError::Rows)?
                .next_row()
                .map_err(ImageCheckError::Rows)?
                .is_some()
            {
                return Err(mismatch("relationship rows not empty"));
            }
        }
    }
    Ok(())
}
