//! EXP-0294/0295: compute the complete related-row result before staging any writes.
use crate::page_edits::reserve;
use crate::relationship_catalog::Constraint;
use crate::relationship_key;
use crate::relationship_mutation::Change;
use crate::{DatabaseReader, FileSource, ResourceBudget, RowValue, TableDefinition, UpdateError};

#[path = "cascade_publish.rs"]
mod publication;
#[path = "cascade_rows.rs"]
mod rows;

pub(crate) struct Plan<'a> {
    rows: Vec<rows::Row>,
    selected: usize,
    request: Change<'a>,
    table: &'a [u8],
}

pub(crate) fn prepare<'a>(
    database: &mut DatabaseReader<FileSource>,
    target: &TableDefinition,
    table: &'a [u8],
    change: Change<'a>,
    budget: &mut ResourceBudget,
) -> Result<Option<Plan<'a>>, UpdateError> {
    if !target
        .relationships()
        .any(|relation| relation.cascade_updates() || relation.cascade_deletes())
    {
        return Ok(None);
    }
    let locator = match change {
        Change::Field(row, _, _) | Change::Replace(row, _) | Change::Delete(row) => row,
        Change::Insert(_) => return Ok(None),
    };
    let constraints = crate::relationship_catalog::component(database, target, table, budget)?;
    let mut rows = rows::load(database, &constraints, budget)?;
    validate(&rows, &constraints, false, budget)?;
    let selected = rows
        .iter()
        .position(|row| row.table == target.root() && row.locator == locator)
        .ok_or(UpdateError::NotFound("cascade target row"))?;
    let row = &mut rows[selected];
    row.deleted = matches!(change, Change::Delete(_));
    for field in &mut row.fields {
        let replacement = match change {
            Change::Replace(_, values) => Some(
                *values
                    .get(usize::from(field.column.get()))
                    .ok_or(UpdateError::Mismatch("replacement column count"))?,
            ),
            Change::Field(_, column, value) if column == field.column => Some(value),
            _ => None,
        };
        if let Some(value) = replacement {
            let value = if matches!(value, RowValue::AutoIncrement) {
                let column = target
                    .columns()
                    .get(usize::from(field.column.get()))
                    .ok_or(UpdateError::NotFound("cascade assignment column"))?;
                if !matches!(change, Change::Replace(_, _)) || !column.auto_increment() {
                    return Err(UpdateError::Unsupported(
                        "AutoIncrement requires an AutoNumber row replacement",
                    ));
                }
                field.before.value()
            } else {
                value
            };
            field.after = Some(rows::Value::copy(value, budget)?);
            field.explicit = true;
        }
    }
    propagate(&mut rows, &constraints, selected, budget)?;
    guards(&rows, &constraints, selected, change, budget)?;
    validate(&rows, &constraints, true, budget)?;
    Ok(Some(Plan {
        rows,
        selected,
        request: change,
        table,
    }))
}

fn propagate(
    rows: &mut [rows::Row],
    constraints: &[Constraint],
    selected: usize,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let mut queue = Vec::new();
    reserve(&mut queue, 1, budget)?;
    queue.push(selected);
    let mut next = 0;
    while let Some(&position) = queue.get(next) {
        next += 1;
        for constraint in constraints {
            budget.charge_work_units(
                constraint.parent_columns.len() as u64 * rows[position].fields.len() as u64 + 1,
            )?;
            let parent = &rows[position];
            if parent.table != constraint.parent.root()
                || !parent.assigned(&constraint.parent_columns)
                || !(if parent.deleted {
                    constraint.flags.deletes
                } else {
                    constraint.flags.updates
                })
            {
                continue;
            }
            let deleted = parent.deleted;
            let before = parent.key(
                &constraint.parent_columns,
                &constraint.parent_kinds,
                false,
                budget,
            )?;
            let mut replacements = Vec::new();
            if !deleted {
                let values = parent.values(&constraint.parent_columns, true)?;
                reserve(&mut replacements, constraint.parent_columns.len(), budget)?;
                for value in &values[..constraint.parent_columns.len()] {
                    replacements.push(rows::Value::copy(*value, budget)?);
                }
            }
            for (child_position, child) in rows.iter_mut().enumerate() {
                budget.charge_work_units(1)?;
                if child.table != constraint.child.root() || child.deleted {
                    continue;
                }
                let key = child.key(
                    &constraint.child_columns,
                    &constraint.child_kinds,
                    false,
                    budget,
                )?;
                if !rows::equal(&before, &key, budget)? {
                    continue;
                }
                let mut changed = deleted;
                if deleted {
                    child.deleted = true;
                } else {
                    for (&column, value) in constraint.child_columns.iter().zip(&replacements) {
                        changed |= child.assign(column, value.value(), false, budget)?;
                    }
                }
                if changed {
                    reserve(&mut queue, 1, budget)?;
                    queue.push(child_position);
                }
            }
        }
    }
    Ok(())
}

fn guards(
    rows: &[rows::Row],
    constraints: &[Constraint],
    selected: usize,
    change: Change<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    for constraint in constraints {
        for (position, parent) in rows.iter().enumerate() {
            budget.charge_work_units(
                (parent.fields.len() * constraint.parent_columns.len()) as u64 + 1,
            )?;
            if parent.table != constraint.parent.root()
                || !parent.assigned(&constraint.parent_columns)
                || (if parent.deleted {
                    constraint.flags.deletes
                } else {
                    constraint.flags.updates
                })
            {
                continue;
            }
            let key = parent.key(
                &constraint.parent_columns,
                &constraint.parent_kinds,
                false,
                budget,
            )?;
            for (child_position, child) in rows.iter().enumerate() {
                budget.charge_work_units(1)?;
                if child.table != constraint.child.root() || child.deleted {
                    continue;
                }
                if position == selected
                    && child_position == selected
                    && matches!(change, Change::Replace(_, _))
                    && !constraint.self_reference_requires_existing_parent
                {
                    continue;
                }
                let after = child.key(
                    &constraint.child_columns,
                    &constraint.child_kinds,
                    true,
                    budget,
                )?;
                if rows::equal(&key, &after, budget)? {
                    return Err(key.as_ref().map_or(
                        UpdateError::NullRelationshipConstraint {
                            parent: constraint.parent.root(),
                            child: constraint.child.root(),
                        },
                        |key| key.violation(constraint.parent.root(), constraint.child.root()),
                    ));
                }
            }
        }
    }
    Ok(())
}

fn validate(
    rows: &[rows::Row],
    constraints: &[Constraint],
    after: bool,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    for constraint in constraints {
        let mut parents = Vec::new();
        let mut previous = Vec::new();
        for row in rows {
            budget.charge_work_units(1)?;
            if row.table != constraint.parent.root() {
                continue;
            }
            if after
                && constraint.self_reference_requires_existing_parent
                && let Some(key) = row.key(
                    &constraint.parent_columns,
                    &constraint.parent_kinds,
                    false,
                    budget,
                )?
            {
                reserve(&mut previous, 1, budget)?;
                previous.push(key);
            }
            if after && row.deleted {
                continue;
            }
            if let Some(key) = row.key(
                &constraint.parent_columns,
                &constraint.parent_kinds,
                after,
                budget,
            )? {
                reserve(&mut parents, 1, budget)?;
                parents.push(key);
            }
        }
        relationship_key::sort(&mut parents, budget)?;
        relationship_key::sort(&mut previous, budget)?;
        if !relationship_key::unique(&parents, budget)? {
            return Err(UpdateError::Mismatch("duplicate cascade parent key"));
        }
        for row in rows {
            budget.charge_work_units(1)?;
            if row.table != constraint.child.root() || (after && row.deleted) {
                continue;
            }
            if let Some(key) = row.key(
                &constraint.child_columns,
                &constraint.child_kinds,
                after,
                budget,
            )? && (!relationship_key::contains(&parents, &key, budget)?
                || (after
                    && constraint.self_reference_requires_existing_parent
                    && !relationship_key::contains(&previous, &key, budget)?))
            {
                return Err(key.violation(constraint.parent.root(), constraint.child.root()));
            }
        }
    }
    Ok(())
}

#[cfg(all(test, any(unix, windows)))]
#[path = "cascade_tests.rs"]
mod tests;
