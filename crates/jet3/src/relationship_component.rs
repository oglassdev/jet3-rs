//! Resolve the complete connected constraint set before a multi-table mutation.
use super::*;

pub(crate) fn component<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    target: &TableDefinition,
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<Vec<Constraint>, UpdateError> {
    let records = read_records(database, None, budget)?;
    let groups = groups(&records, budget)?;
    let mut selected = Vec::new();
    reserve(&mut selected, groups.len(), budget)?;
    selected.resize(groups.len(), false);
    let mut names = Vec::new();
    reserve(&mut names, 1, budget)?;
    names.push(name);
    let mut constraints = Vec::new();
    loop {
        let mut advanced = false;
        for (position, group) in groups.iter().enumerate() {
            if selected[position] {
                continue;
            }
            let first = group
                .first()
                .ok_or(UpdateError::Mismatch("empty relationship"))?;
            budget.charge_work_units((names.len() as u64).saturating_mul(1024))?;
            if !names.iter().any(|name| {
                catalog_names_equal(name, &first.parent) || catalog_names_equal(name, &first.child)
            }) {
                continue;
            }
            if interpreted(&first.metadata).is_none() {
                return Err(UpdateError::Unsupported("relationship catalog flags"));
            }
            let ordered = ordered(group, budget)?;
            for record in &ordered {
                if record.name.len() > 63
                    || [
                        &record.name,
                        &record.parent,
                        &record.child,
                        &record.parent_column,
                        &record.child_column,
                    ]
                    .iter()
                    .any(|name| validate_catalog_name(name).is_err())
                {
                    return Err(UpdateError::Unsupported("unresolved relationship name"));
                }
            }
            selected[position] = true;
            if !enforced(&ordered) {
                continue;
            }
            let constraint = resolve(database, &ordered, budget)?;
            reserve(&mut constraints, 1, budget)?;
            constraints.push(constraint);
            reserve(&mut names, 2, budget)?;
            names.extend([first.parent.as_slice(), first.child.as_slice()]);
            advanced = true;
        }
        if !advanced {
            break;
        }
    }
    check_target(target, &constraints, budget)?;
    incoming(database, target.root(), &constraints, budget)?;
    let mut checked = Vec::new();
    reserve(&mut checked, 1, budget)?;
    checked.push(target.root());
    for constraint in &constraints {
        for table in [&constraint.parent, &constraint.child] {
            budget.charge_work_units(checked.len() as u64)?;
            if !checked.contains(&table.root()) {
                check_target(table, &constraints, budget)?;
                incoming(database, table.root(), &constraints, budget)?;
                reserve(&mut checked, 1, budget)?;
                checked.push(table.root());
            }
        }
    }
    Ok(constraints)
}
