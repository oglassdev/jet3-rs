//! Initial relationships combine EXP-0134 rows, EXP-0268 nullable foreign keys,
//! and the normal scalar-index, property and long-value planners.
use super::*;
use crate::{ColumnRef, TableRows};

pub(crate) fn compose_relationship_with_rows(
    requests: &[TableRows<'_>],
    relationship: &RelationshipSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    let [parent, child] = requests else {
        return Err(ComposeError::UnsupportedRelationship {
            detail: "exactly two tables required",
        });
    };
    if parent.table.indexes.len() != 1 {
        return Err(ComposeError::UnsupportedRelationship {
            detail: "initial relationship rows require one parent primary index",
        });
    }
    compose_with_rows(
        &[parent.table, child.table],
        [parent.rows, child.rows],
        relationship,
        budget,
    )
}

pub(super) fn compose_with_rows(
    tables: &[TableSpec<'_>],
    rows: [&[&[RowValue<'_>]]; 2],
    relationship: &RelationshipSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<WholeFileImagePlan, ComposeError> {
    let mut relation = RelationshipPlan::new(tables, relationship, budget)?;
    let mut parent = PlannedCreate::new_with_relationship(
        &tables[0],
        EMPTY_DATABASE_PAGE_COUNT,
        true,
        Some(relation.logical(true, PageNumber::new(0))),
        budget,
    )?
    .with_rows(rows[0], budget)?;
    let fields = [IndexColumnSpec {
        column: ColumnRef::Ordinal(relation.child_column),
        direction: IndexDirection::Ascending,
    }];
    // The child primary precedes the generated foreign physical index (EXP-0268).
    let foreign = IndexSpec {
        name: relationship.name,
        fields: &fields,
        kind: IndexKind::Ordinary,
    };
    let indexes = [
        tables[1].indexes.first().copied().unwrap_or(foreign),
        foreign,
    ];
    let child_spec = TableSpec {
        indexes: if tables[1].indexes.is_empty() {
            &indexes[1..]
        } else {
            &indexes
        },
        ..tables[1]
    };
    let child = PlannedCreate::new_with_relationship(
        &child_spec,
        parent.page_count(),
        false,
        Some(relation.logical(false, parent.schema().definition_root())),
        budget,
    )?
    .with_rows(rows[1], budget)?;
    parent.set_relationship_target(child.schema().definition_root())?;
    relation.parent = parent.schema().clone();
    relation.child = child.schema().clone();
    relation.end_of_tables = child.page_count();
    for (row, values) in rows[1].iter().enumerate() {
        match values.get(usize::from(relation.child_column)) {
            Some(RowValue::Null) => {}
            Some(RowValue::Long(value)) if parent.contains_initial_long(*value, budget)? => {}
            Some(RowValue::Long(value)) => {
                return Err(ComposeError::OrphanInitialRelationshipKey { row, value: *value });
            }
            _ => {
                return Err(ComposeError::UnsupportedRelationship {
                    detail: "foreign key value must be Long or null",
                });
            }
        }
    }
    assemble_relationship(&relation, &[parent, child], budget)
}
