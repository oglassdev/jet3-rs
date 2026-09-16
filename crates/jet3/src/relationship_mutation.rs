//! Referential integrity over the catalog's checked single-Long relationships.
use crate::{
    ColumnOrdinal, DatabaseReader, FileSource, ResourceBudget, RowLocator, RowValue,
    TableDefinition, TextCodePage, UpdateError, ValueKind, page_edits::reserve,
};

#[derive(Clone, Copy)]
pub(crate) enum Change<'a> {
    Insert(&'a [RowValue<'a>]),
    Replace(RowLocator, &'a [RowValue<'a>]),
    Field(RowLocator, ColumnOrdinal, RowValue<'a>),
    Delete(RowLocator),
}

fn value(value: RowValue<'_>) -> Result<Option<i32>, UpdateError> {
    match value {
        RowValue::Long(value) => Ok(Some(value)),
        RowValue::Null => Ok(None),
        _ => Err(UpdateError::Unsupported(
            "relationship key value must be Long or null",
        )),
    }
}

fn column_value(
    values: &[RowValue<'_>],
    column: ColumnOrdinal,
) -> Result<Option<i32>, UpdateError> {
    value(
        *values
            .get(usize::from(column.get()))
            .ok_or(UpdateError::Mismatch(
                "relationship key absent from replacement",
            ))?,
    )
}

impl Change<'_> {
    fn existing(
        self,
        row: RowLocator,
        column: ColumnOrdinal,
        before: Option<i32>,
    ) -> Result<Option<Option<i32>>, UpdateError> {
        Ok(match self {
            Self::Delete(selected) if selected == row => None,
            Self::Replace(selected, values) if selected == row => {
                Some(column_value(values, column)?)
            }
            Self::Field(selected, changed, replacement) if selected == row && changed == column => {
                Some(value(replacement)?)
            }
            _ => Some(before),
        })
    }
}

struct Keys {
    before: Vec<i32>,
    after: Vec<i32>,
    has_null_after: bool,
    removed_null: bool,
}
fn push(
    keys: &mut Vec<i32>,
    value: Option<i32>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    if let Some(value) = value {
        reserve(keys, 1, budget)?;
        keys.push(value);
    }
    Ok(())
}

fn keys(
    database: &mut DatabaseReader<FileSource>,
    table: &TableDefinition,
    column: ColumnOrdinal,
    change: Option<Change<'_>>,
    budget: &mut ResourceBudget,
) -> Result<Keys, UpdateError> {
    let mut result = Keys {
        before: Vec::new(),
        after: Vec::new(),
        has_null_after: false,
        removed_null: false,
    };
    let mut rows = database.rows(table, budget)?;
    let mut count = 0_u32;
    while let Some(mut row) = rows.next_row()? {
        count = count.checked_add(1).ok_or(UpdateError::Mismatch(
            "relationship table row count overflow",
        ))?;
        let locator = row.locator();
        let before = match row
            .value(column, TextCodePage::Windows1252)?
            .ok_or(UpdateError::Mismatch("relationship key absent"))?
            .kind()
        {
            ValueKind::Null => None,
            ValueKind::Long(value) => Some(*value),
            _ => return Err(UpdateError::Mismatch("relationship stored key type")),
        };
        let after = match change {
            Some(change) => change.existing(locator, column, before)?,
            None => Some(before),
        };
        result.has_null_after |= after == Some(None);
        result.removed_null |= before.is_none() && after != Some(None);
        push(&mut result.before, before, rows.owned.budget_mut())?;
        if let Some(after) = after {
            push(&mut result.after, after, rows.owned.budget_mut())?;
        }
    }
    if count != table.row_count() {
        return Err(UpdateError::Mismatch("relationship table row count"));
    }
    if let Some(Change::Insert(values)) = change {
        let inserted = column_value(values, column)?;
        result.has_null_after |= inserted.is_none();
        push(&mut result.after, inserted, rows.owned.budget_mut())?;
    }
    Ok(result)
}

fn sort(keys: &mut [i32], budget: &mut ResourceBudget) -> Result<(), UpdateError> {
    budget.charge_work_units(
        (keys.len() as u64).saturating_mul((keys.len().max(1).ilog2() + 1) as u64),
    )?;
    keys.sort_unstable();
    Ok(())
}

fn missing(
    parent: &[i32],
    child: &[i32],
    budget: &mut ResourceBudget,
) -> Result<Option<i32>, UpdateError> {
    for &value in child {
        budget.charge_work_units((parent.len().max(1).ilog2() + 1) as u64)?;
        if parent.binary_search(&value).is_err() {
            return Ok(Some(value));
        }
    }
    Ok(None)
}

pub(crate) fn check(
    database: &mut DatabaseReader<FileSource>,
    target: &TableDefinition,
    name: &[u8],
    change: Change<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let constraints = crate::relationship_catalog::load(database, target, name, budget)?;
    for constraint in constraints {
        // Both relation indexes must agree with their rows before relying on the keys.
        crate::index_mutation::load(database, &constraint.parent, budget)?;
        crate::index_mutation::load(database, &constraint.child, budget)?;
        let mut parent = keys(
            database,
            &constraint.parent,
            constraint.parent_column,
            (constraint.parent.root() == target.root()).then_some(change),
            budget,
        )?;
        let child = keys(
            database,
            &constraint.child,
            constraint.child_column,
            (constraint.child.root() == target.root()).then_some(change),
            budget,
        )?;
        sort(&mut parent.before, budget)?;
        sort(&mut parent.after, budget)?;
        budget.charge_work_units(parent.before.len() as u64)?;
        if parent.before.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(UpdateError::Mismatch("duplicate relationship parent key"));
        }
        if missing(&parent.before, &child.before, budget)?.is_some() {
            return Err(UpdateError::Mismatch("orphan relationship key"));
        }
        // EXP-0286: a null child blocks changing/removing a null parent, even
        // when another null parent remains. Null child insertion is exempt.
        if parent.removed_null && child.has_null_after {
            return Err(UpdateError::NullRelationshipConstraint {
                parent: constraint.parent.root(),
                child: constraint.child.root(),
            });
        }
        let missing_after = missing(&parent.after, &child.after, budget)?;
        // EXP-0286: a foreign tree preceding its parent tree cannot reference
        // a self key established by the same insertion/replacement.
        let missing_before = if constraint.self_reference_requires_existing_parent {
            missing(&parent.before, &child.after, budget)?
        } else {
            None
        };
        if let Some(value) = missing_after.or(missing_before) {
            return Err(UpdateError::RelationshipConstraint {
                parent: constraint.parent.root(),
                child: constraint.child.root(),
                value,
            });
        }
    }
    Ok(())
}
