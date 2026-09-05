//! Populated relationship candidate using EXP-0114 reciprocal metadata,
//! EXP-0062 leaf locators and EXP-0120 ordinary duplicate-key observations.
//! Initial rows retain the bounded writer's maps/counts. Foreign keys are
//! non-null Long values; no nullable-key grammar or insertion counter is guessed.

use super::*;
use crate::table_schema_plan::plan_table_schema;
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
    let tables = [parent.table, child.table];
    let mut relation = RelationshipPlan::new(&tables, relationship)?;
    if tables[0].indexes.len() != 1 {
        return Err(ComposeError::UnsupportedRelationship {
            detail: "initial relationship rows require one parent primary index",
        });
    }
    let parent_create =
        PlannedCreate::new(&tables[0], relation.parent.definition_root().get(), true)?
            .with_rows(parent.rows, budget)?;
    relation.child = plan_table_schema(&tables[1], parent_create.page_count(), false)?;
    let child_create =
        PlannedCreate::new(&tables[1], relation.child.definition_root().get(), false)?
            .with_rows(child.rows, budget)?;
    relation.end_of_tables = child_create.page_count();
    let fields = [IndexColumnSpec {
        column: ColumnRef::Ordinal(relation.child_column),
        direction: IndexDirection::Ascending,
    }];
    let indexes = [IndexSpec {
        name: relationship.name,
        fields: &fields,
        kind: IndexKind::Ordinary,
    }];
    let indexed_child = TableSpec {
        indexes: &indexes,
        ..tables[1]
    };
    let mut foreign = InitialLongIndex::new(&indexed_child, child.rows.len(), budget)?
        .ok_or(ComposeError::UnsupportedInitialIndexSchema)?;
    for (row, locator) in child.rows.iter().zip(child_create.initial_row_locators()) {
        foreign.push(row, locator, budget)?;
    }
    foreign.sort(budget)?;
    for (row, values) in child.rows.iter().enumerate() {
        let Some(RowValue::Long(value)) = values.get(usize::from(relation.child_column)) else {
            return Err(ComposeError::NullInitialIndexKey { row });
        };
        if !parent_create.contains_initial_long(*value, budget)? {
            return Err(ComposeError::OrphanInitialRelationshipKey { row, value: *value });
        }
    }
    let creates = [parent_create, child_create];
    let plan = compose_planned_creates(&creates, budget)?;
    assemble_relationship(&relation, &creates, plan, Some(&foreign), budget)
}
