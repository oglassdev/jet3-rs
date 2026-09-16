//! Read-only checks of EXP-0268/0273/0279 relationship metadata and scalar keys.
use super::*;
use crate::relationship_key::{self, Key};

#[derive(Default)]
pub(crate) struct Summary {
    pub catalog_rows: u64,
    pub verified: u64,
    pub uninterpreted: u64,
    pub inventory_checked: bool,
}

struct Endpoint {
    root: PageNumber,
    record: [u8; 20],
}

pub(crate) fn validate<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    budget: &mut ResourceBudget,
) -> Result<Summary, UpdateError> {
    let records = read_records(database, None, budget)?;
    let mut report = Summary {
        catalog_rows: records.len() as u64,
        ..Summary::default()
    };
    let mut endpoints = Vec::new();
    for record in &records {
        let names = [
            &record.name,
            &record.parent,
            &record.child,
            &record.parent_column,
            &record.child_column,
        ];
        if record.metadata != [0, 1, 0]
            || record.name.len() > 63
            || names
                .iter()
                .any(|name| validate_catalog_name(name).is_err())
        {
            report.uninterpreted += 1;
            continue;
        }
        budget.charge_work_units((records.len() as u64).saturating_mul(512))?;
        if records
            .iter()
            .filter(|other| catalog_names_equal(&other.name, &record.name))
            .count()
            != 1
        {
            return Err(UpdateError::Mismatch("duplicate relationship catalog name"));
        }
        let parent = table(database, &record.parent, budget)?;
        let child = table(database, &record.child, budget)?;
        budget.charge_work_units(
            ((parent.columns().len() + child.columns().len()) as u64).saturating_mul(512),
        )?;
        let mut supported = true;
        for (table, name) in [
            (&parent, &record.parent_column),
            (&child, &record.child_column),
        ] {
            match key_column(table, name) {
                Ok(_) => {}
                Err(UpdateError::Unsupported("relationship scalar column schema")) => {
                    supported = false
                }
                Err(error) => return Err(error),
            }
        }
        if !supported {
            report.uninterpreted += 1;
            continue;
        }
        // Once both scalar endpoints are known, every reciprocal/index mismatch is an error.
        let constraint = resolve_tables(record, parent, child, budget)?;
        check_keys(database, &constraint, budget)?;
        reserve(&mut endpoints, 2, budget)?;
        endpoints.push(Endpoint {
            root: constraint.parent.root(),
            record: constraint.parent_record,
        });
        endpoints.push(Endpoint {
            root: constraint.child.root(),
            record: constraint.child_record,
        });
        report.verified += 1;
    }
    report.inventory_checked = report.uninterpreted == 0;
    check_inventory(database, &endpoints, report.inventory_checked, budget)?;
    Ok(report)
}

fn check_keys<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    constraint: &Constraint,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let mut parent_keys = Vec::new();
    {
        let mut rows = database.rows(&constraint.parent, budget)?;
        while let Some(mut row) = rows.next_row()? {
            let value = crate::numeric_row_values::read_column(&mut row, constraint.parent_column)?;
            let Some(key) = Key::encode(constraint.parent_kind, value, row.budget_mut())? else {
                continue;
            };
            reserve(&mut parent_keys, 1, rows.owned.budget_mut())?;
            parent_keys.push(key);
        }
    }
    relationship_key::sort(&mut parent_keys, budget)?;
    if !relationship_key::unique(&parent_keys, budget)? {
        return Err(UpdateError::Mismatch("duplicate relationship parent key"));
    }
    let mut rows = database.rows(&constraint.child, budget)?;
    while let Some(mut row) = rows.next_row()? {
        let value = crate::numeric_row_values::read_column(&mut row, constraint.child_column)?;
        let Some(key) = Key::encode(constraint.child_kind, value, row.budget_mut())? else {
            continue;
        };
        if !relationship_key::contains(&parent_keys, &key, row.budget_mut())? {
            return Err(key.violation(constraint.parent.root(), constraint.child.root()));
        }
    }
    Ok(())
}

fn check_inventory<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    endpoints: &[Endpoint],
    complete: bool,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let mut roots = Vec::new();
    {
        let mut catalog = database.catalog(budget)?;
        while let Some(record) = catalog.next_record()? {
            if record.class() == CatalogObjectClass::User
                && let Some(root) = record.table_definition()
            {
                reserve(&mut roots, 1, catalog.budget_mut())?;
                roots.push(root);
            }
        }
    }
    let mut seen = Vec::new();
    reserve(&mut seen, endpoints.len(), budget)?;
    seen.resize(endpoints.len(), 0_usize);
    for root in roots {
        let table = database.table_definition(root, budget)?;
        budget.charge_work_units(
            (table.indexes().len() as u64)
                .saturating_mul((endpoints.len() as u64).saturating_mul(20) + 1),
        )?;
        for relation in table.relationships() {
            let mut matches = endpoints.iter().enumerate().filter(|(_, endpoint)| {
                endpoint.root == root && endpoint.record == *relation.raw_record()
            });
            if let Some((position, _)) = matches.next() {
                if matches.next().is_some() || seen[position] != 0 {
                    return Err(UpdateError::Mismatch(
                        "duplicate relationship endpoint record",
                    ));
                }
                seen[position] = 1;
            } else if complete {
                return Err(UpdateError::Mismatch(
                    "unresolved relationship endpoint record",
                ));
            }
        }
    }
    budget.charge_work_units(seen.len() as u64)?;
    if seen.contains(&0) {
        return Err(UpdateError::Mismatch("relationship endpoint inventory"));
    }
    Ok(())
}
