//! EXP-0290 groups ordered component rows into one relationship.
use super::*;

pub(super) fn groups<'a>(
    records: &'a [Record],
    budget: &mut ResourceBudget,
) -> Result<Vec<Vec<&'a Record>>, UpdateError> {
    let mut groups: Vec<Vec<&Record>> = Vec::new();
    for record in records {
        budget.charge_work_units((groups.len() as u64).saturating_mul(512))?;
        if let Some(group) = groups.iter_mut().find(|group| {
            group
                .first()
                .is_some_and(|first| catalog_names_equal(&first.name, &record.name))
        }) {
            reserve(group, 1, budget)?;
            group.push(record);
        } else {
            let mut group = Vec::new();
            reserve(&mut group, 1, budget)?;
            group.push(record);
            reserve(&mut groups, 1, budget)?;
            groups.push(group);
        }
    }
    Ok(groups)
}

pub(super) fn ordered<'a>(
    group: &[&'a Record],
    budget: &mut ResourceBudget,
) -> Result<Vec<&'a Record>, UpdateError> {
    let first = group
        .first()
        .ok_or(UpdateError::Mismatch("empty relationship catalog group"))?;
    if crate::relationship_flags::RelationshipFlags::decode(first.metadata[0]).is_none()
        || !(1..=crate::numeric_index_entry::MAX_FIELDS as i32).contains(&first.metadata[1])
    {
        return Err(UpdateError::Unsupported(
            "relationship catalog flags or field count",
        ));
    }
    if group.len() != first.metadata[1] as usize {
        return Err(UpdateError::Mismatch(
            "relationship catalog component count",
        ));
    }
    let mut slots = [None; crate::numeric_index_entry::MAX_FIELDS];
    for &record in group {
        budget.charge_work_units(1024)?;
        if record.metadata[..2] != first.metadata[..2]
            || !catalog_names_equal(&record.parent, &first.parent)
            || !catalog_names_equal(&record.child, &first.child)
        {
            return Err(UpdateError::Mismatch(
                "relationship component metadata differs",
            ));
        }
        let ordinal = usize::try_from(record.metadata[2])
            .map_err(|_| UpdateError::Mismatch("relationship component ordinal"))?;
        let slot = slots[..group.len()]
            .get_mut(ordinal)
            .ok_or(UpdateError::Mismatch("relationship component ordinal"))?;
        if slot.replace(record).is_some() {
            return Err(UpdateError::Mismatch(
                "duplicate relationship component ordinal",
            ));
        }
    }
    let mut result = Vec::new();
    reserve(&mut result, group.len(), budget)?;
    for record in &slots[..group.len()] {
        result.push(record.ok_or(UpdateError::Mismatch(
            "missing relationship component ordinal",
        ))?);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn record(count: i32, ordinal: i32) -> Record {
        Record {
            name: b"Relation".to_vec(),
            parent: b"Parent".to_vec(),
            child: b"Child".to_vec(),
            parent_column: b"Key".to_vec(),
            child_column: b"Key".to_vec(),
            metadata: [0, count, ordinal],
        }
    }
    fn budget() -> ResourceBudget {
        ResourceBudget::new(crate::ResourceLimits::default())
    }
    #[test]
    fn component_ordinals_define_order_and_must_form_a_complete_unique_set()
    -> Result<(), UpdateError> {
        let first = record(2, 0);
        let second = record(2, 1);
        let sorted = ordered(&[&second, &first], &mut budget())?;
        assert_eq!(
            sorted
                .iter()
                .map(|record| record.metadata[2])
                .collect::<Vec<_>>(),
            [0, 1]
        );
        for ordinal in [-1, 0, 2] {
            let bad = record(2, ordinal);
            assert!(ordered(&[&first, &bad], &mut budget()).is_err());
        }
        assert!(ordered(&[&first], &mut budget()).is_err());
        assert!(ordered(&[], &mut budget()).is_err());
        let mut bad = second;
        bad.parent = b"Other".to_vec();
        assert!(ordered(&[&first, &bad], &mut budget()).is_err());
        bad.parent = b"Parent".to_vec();
        bad.metadata[0] = 0x100;
        assert!(ordered(&[&first, &bad], &mut budget()).is_err());
        for count in [0, 11] {
            assert!(ordered(&[&record(count, 0)], &mut budget()).is_err());
        }
        Ok(())
    }
}
