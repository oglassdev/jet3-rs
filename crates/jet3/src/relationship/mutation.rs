//! Referential integrity over the catalog's checked scalar relationships.
use crate::{
    ColumnOrdinal, DatabaseReader, FileSource, ResourceBudget, RowLocator, RowValue,
    TableDefinition, UpdateError, index::key::scalar::ScalarKeyType, write::page_edits::reserve,
};

use super::key::{self, Key, key_values};

#[derive(Clone, Copy)]
pub(crate) enum Change<'a> {
    Insert(&'a [RowValue<'a>]),
    Replace(RowLocator, &'a [RowValue<'a>]),
    Field(RowLocator, ColumnOrdinal, RowValue<'a>),
    Delete(RowLocator),
}

fn column_value<'a>(
    values: &[RowValue<'a>],
    column: ColumnOrdinal,
) -> Result<RowValue<'a>, UpdateError> {
    values
        .get(usize::from(column.get()))
        .copied()
        .ok_or(UpdateError::Mismatch(
            "relationship key absent from replacement",
        ))
}

impl Change<'_> {
    fn existing(
        self,
        row: RowLocator,
        columns: &[ColumnOrdinal],
        before: &[RowValue<'_>],
        kinds: &[ScalarKeyType],
        budget: &mut ResourceBudget,
    ) -> Result<Option<Option<Key>>, UpdateError> {
        if matches!(self, Self::Delete(selected) if selected == row) {
            return Ok(None);
        }
        let mut values = [RowValue::Null; crate::index::entry::MAX_FIELDS];
        for ((&column, &before), value) in columns.iter().zip(before).zip(&mut values) {
            *value = match self {
                Self::Replace(selected, values) if selected == row => column_value(values, column)?,
                Self::Field(selected, changed, replacement)
                    if selected == row && changed == column =>
                {
                    replacement
                }
                _ => before,
            };
        }
        Ok(Some(Key::encode(kinds, &values[..columns.len()], budget)?))
    }
}

struct Keys {
    before: Vec<Key>,
    after: Vec<Key>,
    has_other_null_after: bool,
    replaced_after: Option<Key>,
    assigned_null: bool,
    assigned: Vec<Key>,
}
fn push(
    keys: &mut Vec<Key>,
    value: Option<Key>,
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
    columns: &[ColumnOrdinal],
    kinds: &[ScalarKeyType],
    change: Option<Change<'_>>,
    exclude_replacement: bool,
    budget: &mut ResourceBudget,
) -> Result<Keys, UpdateError> {
    let mut result = Keys {
        before: Vec::new(),
        after: Vec::new(),
        has_other_null_after: false,
        replaced_after: None,
        assigned_null: false,
        assigned: Vec::new(),
    };
    let mut rows = database.rows(table, budget)?;
    let mut count = 0_u32;
    while let Some(mut row) = rows.next_row()? {
        count = count.checked_add(1).ok_or(UpdateError::Mismatch(
            "relationship table row count overflow",
        ))?;
        let locator = row.locator();
        let values = key_values(&mut row, columns)?;
        let values = &values[..columns.len()];
        let before = Key::encode(kinds, values, row.budget_mut())?;
        let after = match change {
            Some(change) => change.existing(locator, columns, values, kinds, row.budget_mut())?,
            None => Some(Key::encode(kinds, values, row.budget_mut())?),
        };
        let replaced = exclude_replacement
            && matches!(change, Some(Change::Replace(selected, _)) if selected == locator);
        result.has_other_null_after |= !replaced && matches!(after, Some(None));
        let assigned = match change {
            Some(Change::Delete(selected) | Change::Replace(selected, _)) => selected == locator,
            Some(Change::Field(selected, column, _)) => {
                selected == locator && columns.contains(&column)
            }
            _ => false,
        };
        if assigned {
            result.assigned_null |= before.is_none();
            push(
                &mut result.assigned,
                Key::encode(kinds, values, row.budget_mut())?,
                row.budget_mut(),
            )?;
        }
        push(&mut result.before, before, rows.owned.budget_mut())?;
        if let Some(after) = after {
            if replaced {
                result.replaced_after = after;
            } else {
                push(&mut result.after, after, rows.owned.budget_mut())?;
            }
        }
    }
    if count != table.row_count() {
        return Err(UpdateError::Mismatch("relationship table row count"));
    }
    if let Some(Change::Insert(values)) = change {
        let mut key = [RowValue::Null; crate::index::entry::MAX_FIELDS];
        for (&column, value) in columns.iter().zip(&mut key) {
            *value = column_value(values, column)?;
        }
        let inserted = Key::encode(kinds, &key[..columns.len()], rows.owned.budget_mut())?;
        result.has_other_null_after |= inserted.is_none();
        push(&mut result.after, inserted, rows.owned.budget_mut())?;
    }
    Ok(result)
}

pub(crate) fn check(
    database: &mut DatabaseReader<FileSource>,
    target: &TableDefinition,
    name: &[u8],
    change: Change<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let constraints = crate::relationship::catalog::load(database, target, name, budget)?;
    for constraint in constraints {
        // Both relation indexes must agree with their rows before relying on the keys.
        crate::index::mutation::load(database, &constraint.parent, budget)?;
        crate::index::mutation::load(database, &constraint.child, budget)?;
        let mut parent = keys(
            database,
            &constraint.parent,
            &constraint.parent_columns,
            &constraint.parent_kinds,
            (constraint.parent.root() == target.root()).then_some(change),
            false,
            budget,
        )?;
        let mut child = keys(
            database,
            &constraint.child,
            &constraint.child_columns,
            &constraint.child_kinds,
            (constraint.child.root() == target.root()).then_some(change),
            constraint.parent.root() == constraint.child.root()
                && !constraint.self_reference_requires_existing_parent,
            budget,
        )?;
        key::sort(&mut parent.before, budget)?;
        key::sort(&mut parent.after, budget)?;
        if !key::unique(&parent.before, budget)? {
            return Err(UpdateError::Mismatch("duplicate relationship parent key"));
        }
        if key::missing(&parent.before, &child.before, budget)?.is_some() {
            return Err(UpdateError::Mismatch("orphan relationship key"));
        }
        // EXP-0286/0289/0290: parent assignments are checked even if the key is
        // unchanged or another nullable parent has the same tuple.
        if parent.assigned_null && child.has_other_null_after {
            return Err(UpdateError::NullRelationshipConstraint {
                parent: constraint.parent.root(),
                child: constraint.child.root(),
            });
        }
        key::sort(&mut child.after, budget)?;
        for key in &parent.assigned {
            if key::contains(&child.after, key, budget)? {
                return Err(key.violation(constraint.parent.root(), constraint.child.root()));
            }
        }
        // EXP-0286/0292: when the parent tree precedes the foreign tree, full
        // replacement checks its own child row after the parent assignment guard.
        push(&mut child.after, child.replaced_after.take(), budget)?;
        let missing_after = key::missing(&parent.after, &child.after, budget)?;
        // EXP-0286: a foreign tree preceding its parent tree cannot reference
        // a self key established by the same insertion/replacement.
        let missing_before = if constraint.self_reference_requires_existing_parent {
            key::missing(&parent.before, &child.after, budget)?
        } else {
            None
        };
        if let Some(value) = missing_after.or(missing_before) {
            return Err(value.violation(constraint.parent.root(), constraint.child.root()));
        }
    }
    Ok(())
}
