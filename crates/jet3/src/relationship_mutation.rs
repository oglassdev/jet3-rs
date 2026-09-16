//! Referential integrity over the catalog's checked scalar relationships.
use crate::{
    ColumnOrdinal, DatabaseReader, FileSource, ResourceBudget, RowLocator, RowValue,
    TableDefinition, UpdateError, page_edits::reserve,
};

use crate::numeric_index_key::NumericKeyType;
use crate::relationship_key::{self, Key, key_values};

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
        kinds: &[NumericKeyType],
        budget: &mut ResourceBudget,
    ) -> Result<Option<Option<Key>>, UpdateError> {
        if matches!(self, Self::Delete(selected) if selected == row) {
            return Ok(None);
        }
        let mut values = [RowValue::Null; crate::numeric_index_entry::MAX_FIELDS];
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
    has_null_after: bool,
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
    kinds: &[NumericKeyType],
    change: Option<Change<'_>>,
    budget: &mut ResourceBudget,
) -> Result<Keys, UpdateError> {
    let mut result = Keys {
        before: Vec::new(),
        after: Vec::new(),
        has_null_after: false,
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
        result.has_null_after |= matches!(after, Some(None));
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
            push(&mut result.after, after, rows.owned.budget_mut())?;
        }
    }
    if count != table.row_count() {
        return Err(UpdateError::Mismatch("relationship table row count"));
    }
    if let Some(Change::Insert(values)) = change {
        let mut key = [RowValue::Null; crate::numeric_index_entry::MAX_FIELDS];
        for (&column, value) in columns.iter().zip(&mut key) {
            *value = column_value(values, column)?;
        }
        let inserted = Key::encode(kinds, &key[..columns.len()], rows.owned.budget_mut())?;
        result.has_null_after |= inserted.is_none();
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
    let constraints = crate::relationship_catalog::load(database, target, name, budget)?;
    for constraint in constraints {
        // Both relation indexes must agree with their rows before relying on the keys.
        crate::index_mutation::load(database, &constraint.parent, budget)?;
        crate::index_mutation::load(database, &constraint.child, budget)?;
        let mut parent = keys(
            database,
            &constraint.parent,
            &constraint.parent_columns,
            &constraint.parent_kinds,
            (constraint.parent.root() == target.root()).then_some(change),
            budget,
        )?;
        let mut child = keys(
            database,
            &constraint.child,
            &constraint.child_columns,
            &constraint.child_kinds,
            (constraint.child.root() == target.root()).then_some(change),
            budget,
        )?;
        relationship_key::sort(&mut parent.before, budget)?;
        relationship_key::sort(&mut parent.after, budget)?;
        if !relationship_key::unique(&parent.before, budget)? {
            return Err(UpdateError::Mismatch("duplicate relationship parent key"));
        }
        if relationship_key::missing(&parent.before, &child.before, budget)?.is_some() {
            return Err(UpdateError::Mismatch("orphan relationship key"));
        }
        // EXP-0286/0289/0290: parent assignments are checked even if the key is
        // unchanged or another nullable parent has the same tuple.
        if parent.assigned_null && child.has_null_after {
            return Err(UpdateError::NullRelationshipConstraint {
                parent: constraint.parent.root(),
                child: constraint.child.root(),
            });
        }
        relationship_key::sort(&mut child.after, budget)?;
        for key in &parent.assigned {
            if relationship_key::contains(&child.after, key, budget)? {
                return Err(key.violation(constraint.parent.root(), constraint.child.root()));
            }
        }
        let missing_after = relationship_key::missing(&parent.after, &child.after, budget)?;
        // EXP-0286: a foreign tree preceding its parent tree cannot reference
        // a self key established by the same insertion/replacement.
        let missing_before = if constraint.self_reference_requires_existing_parent {
            relationship_key::missing(&parent.before, &child.after, budget)?
        } else {
            None
        };
        if let Some(value) = missing_after.or(missing_before) {
            return Err(value.violation(constraint.parent.root(), constraint.child.root()));
        }
    }
    Ok(())
}
