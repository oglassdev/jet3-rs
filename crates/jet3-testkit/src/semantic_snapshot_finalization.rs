//! Budgeted canonical ordering and validation for collected semantic snapshots.

use crate::{SemanticSnapshot, SemanticSnapshotError};
use jet3::{Error, ResourceBudget};

pub(super) fn finalize_semantic_snapshot(
    snapshot: &mut SemanticSnapshot,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    let algorithm_work = finalization_work(snapshot)?;
    crate::semantic_json::preflight_canonicalization_measurement(snapshot, budget, algorithm_work)?;
    let allocation = crate::semantic_json::canonicalization_allocation_bound(snapshot)
        .map_err(SemanticSnapshotError::Resource)?;
    crate::semantic_json::preflight_canonicalization_charge(allocation, algorithm_work, budget)?;
    budget
        .charge_allocation(allocation)
        .map_err(SemanticSnapshotError::Resource)?;
    budget
        .charge_work_units(algorithm_work)
        .map_err(SemanticSnapshotError::Resource)?;
    snapshot.canonicalize_precomputed_rows()?;
    Ok(())
}

fn finalization_work(snapshot: &SemanticSnapshot) -> Result<u64, SemanticSnapshotError> {
    let table_count = count(snapshot.tables.len())?;
    let mut work = ordering_work(
        snapshot.tables.len(),
        "size semantic snapshot finalization work",
    )?;
    let mut maximum_columns = 0;
    for table in &snapshot.tables {
        let columns = count(table.columns.len())?;
        maximum_columns = maximum_columns.max(columns);
        add(
            &mut work,
            ordering_work(
                table.columns.len(),
                "size semantic snapshot finalization work",
            )?,
        )?;
        add(&mut work, square(columns)?)?;
        add(
            &mut work,
            ordering_work(
                table.indexes.len(),
                "size semantic snapshot finalization work",
            )?,
        )?;
        for index in &table.indexes {
            let fields = count(index.fields.len())?;
            add(&mut work, product(fields, columns)?)?;
            add(&mut work, square(fields)?)?;
        }
        let row_lookup = product(columns, ordering_passes(columns)?)?
            .checked_add(1)
            .ok_or_else(arithmetic)?;
        add(&mut work, product(count(table.rows.len())?, row_lookup)?)?;
    }

    add(
        &mut work,
        ordering_work(
            snapshot.relationships.len(),
            "size semantic snapshot finalization work",
        )?,
    )?;
    let table_lookup = ordering_passes(table_count)?;
    for relationship in &snapshot.relationships {
        let fields = count(relationship.fields.len())?;
        add(&mut work, product(2, table_lookup)?)?;
        add(&mut work, product(product(2, fields)?, maximum_columns)?)?;
        add(&mut work, square(fields)?)?;
    }
    add(
        &mut work,
        ordering_work(
            snapshot.raw_preservation.len(),
            "size semantic snapshot finalization work",
        )?,
    )?;
    Ok(work)
}

pub(super) fn ordering_work(
    items: usize,
    operation: &'static str,
) -> Result<u64, SemanticSnapshotError> {
    let items = count(items)?;
    items
        .checked_mul(ordering_passes(items)?)
        .ok_or(SemanticSnapshotError::Resource(Error::Arithmetic {
            operation,
        }))
}

fn ordering_passes(items: u64) -> Result<u64, SemanticSnapshotError> {
    let levels = if items <= 1 {
        0
    } else {
        u64::from(u64::BITS - (items - 1).leading_zeros())
    };
    levels.checked_add(1).ok_or_else(arithmetic)
}

fn square(value: u64) -> Result<u64, SemanticSnapshotError> {
    product(value, value)
}

fn product(left: u64, right: u64) -> Result<u64, SemanticSnapshotError> {
    left.checked_mul(right).ok_or_else(arithmetic)
}

fn add(total: &mut u64, value: u64) -> Result<(), SemanticSnapshotError> {
    *total = total.checked_add(value).ok_or_else(arithmetic)?;
    Ok(())
}

fn count(value: usize) -> Result<u64, SemanticSnapshotError> {
    u64::try_from(value).map_err(|_| {
        SemanticSnapshotError::Resource(Error::IntegerConversion {
            value: value as u128,
            target: "u64",
        })
    })
}

fn arithmetic() -> SemanticSnapshotError {
    SemanticSnapshotError::Resource(Error::Arithmetic {
        operation: "size semantic snapshot finalization work",
    })
}

#[cfg(test)]
mod tests {
    use super::{finalization_work, finalize_semantic_snapshot};
    use crate::{
        HexString, Producer, ProducerKind, PropertyMap, ScenarioId, SemanticSnapshot,
        SemanticSnapshotError, SemanticTable, Sha256, TableKind, TypedValue,
    };
    use jet3::{ByteCount, Error, ResourceBudget, ResourceLimitKind, ResourceLimits};

    fn unordered_snapshot() -> Result<SemanticSnapshot, Box<dyn std::error::Error>> {
        let mut snapshot = SemanticSnapshot::new(
            ScenarioId::new("DAO-READ-ROWS-SINGLE")?,
            Producer::new(ProducerKind::Rust, "test")?,
            Sha256::new("ab".repeat(32))?,
        );
        for name in ["Zulu", "Alpha"] {
            snapshot.tables.push(SemanticTable {
                name: name.to_owned(),
                kind: TableKind::User,
                attributes: 0,
                columns: Vec::new(),
                indexes: Vec::new(),
                properties: PropertyMap::new(),
                rows: Vec::new(),
            });
        }
        Ok(snapshot)
    }

    #[test]
    fn semantic_finalization_has_exact_total_work_boundary()
    -> Result<(), Box<dyn std::error::Error>> {
        let run = |maximum| {
            let mut snapshot = unordered_snapshot()?;
            let mut budget =
                ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(maximum));
            let result = finalize_semantic_snapshot(&mut snapshot, &mut budget);
            Ok::<_, Box<dyn std::error::Error>>((result, snapshot, budget.total_work_units()))
        };

        let (measured, _, required) = run(u64::MAX)?;
        measured?;
        let allocation_work =
            crate::semantic_json::canonicalization_allocation_bound(&unordered_snapshot()?)?.get();
        assert!(required > allocation_work);
        let (exact, snapshot, charged) = run(required)?;
        exact?;
        assert_eq!(charged, required);
        assert_eq!(snapshot.tables[0].name, "Alpha");

        let maximum = required.checked_sub(1).ok_or("zero finalization work")?;
        let (rejected, unchanged, charged) = run(maximum)?;
        assert!(matches!(
            rejected,
            Err(SemanticSnapshotError::Resource(
                Error::ResourceLimitExceeded {
                    kind: ResourceLimitKind::TotalWorkUnits,
                    ..
                }
            ))
        ));
        assert!(charged <= maximum);
        assert_eq!(unchanged.tables[0].name, "Zulu");
        Ok(())
    }

    #[test]
    fn finalization_preflights_large_strings_before_exact_measurement()
    -> Result<(), Box<dyn std::error::Error>> {
        let repetitions = 4_096;
        let mut source = unordered_snapshot()?;
        source.database_properties.insert(
            "LargeMemo".to_owned(),
            TypedValue::Memo {
                value: "\\\n".repeat(repetitions),
                raw_hex: Some(HexString::new("5c0a".repeat(repetitions))?),
                code_page: Some(1252),
            },
        );
        let algorithm_work = finalization_work(&source)?;
        let exact_allocation = crate::semantic_json::canonicalization_allocation_bound(&source)?;

        let mut exhausted = ResourceBudget::new(
            ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(0)),
        );
        let mut candidate = source.clone();
        let Err(error) = finalize_semantic_snapshot(&mut candidate, &mut exhausted) else {
            return Err(std::io::Error::other(
                "zero allocation ceiling accepted the retained model",
            )
            .into());
        };
        let SemanticSnapshotError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            requested: minimum_allocation,
            maximum: 0,
        }) = error
        else {
            return Err(std::io::Error::other("unexpected exhausted-budget error").into());
        };
        assert!(exact_allocation.get() > minimum_allocation);
        assert_eq!(candidate, source);
        assert_eq!(exhausted.allocation_bytes(), ByteCount::new(0));
        assert_eq!(exhausted.total_work_units(), 0);

        let minimum_work = minimum_allocation
            .checked_add(algorithm_work)
            .ok_or("minimum finalization work")?;
        for limits in [
            ResourceLimits::default()
                .with_max_allocation_bytes(ByteCount::new(minimum_allocation - 1)),
            ResourceLimits::default().with_max_total_work_units(minimum_work - 1),
            ResourceLimits::default().with_max_total_work_units(0),
        ] {
            let mut candidate = source.clone();
            let mut budget = ResourceBudget::new(limits);
            assert!(matches!(
                finalize_semantic_snapshot(&mut candidate, &mut budget),
                Err(SemanticSnapshotError::Resource(
                    Error::ResourceLimitExceeded { .. }
                ))
            ));
            assert_eq!(candidate, source);
            assert_eq!(budget.allocation_bytes(), ByteCount::new(0));
            assert_eq!(budget.total_work_units(), 0);
        }

        let exact_work = exact_allocation
            .get()
            .checked_add(algorithm_work)
            .ok_or("exact finalization work")?;
        let mut candidate = source.clone();
        let mut exact_budget = ResourceBudget::new(
            ResourceLimits::default()
                .with_max_allocation_bytes(exact_allocation)
                .with_max_total_work_units(exact_work),
        );
        finalize_semantic_snapshot(&mut candidate, &mut exact_budget)?;
        assert_eq!(candidate.tables[0].name, "Alpha");
        assert_eq!(exact_budget.allocation_bytes(), exact_allocation);
        assert_eq!(exact_budget.total_work_units(), exact_work);
        Ok(())
    }
}
