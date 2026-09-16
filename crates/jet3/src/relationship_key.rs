//! EXP-0288 relationship equality uses the observed scalar index encodings.
use crate::numeric_index_entry::{EntryError, NumericIndexEntry, NumericIndexField};
use crate::numeric_index_key::NumericKeyType;
use crate::{
    IndexDirection, IndexNullPolicy, PageNumber, ResourceBudget, RowLocator, RowValue, UpdateError,
};

pub(crate) fn compatible(parent: NumericKeyType, child: NumericKeyType) -> bool {
    matches!(
        (parent, child),
        (NumericKeyType::Text { .. }, NumericKeyType::Text { .. })
            | (NumericKeyType::Binary { .. }, NumericKeyType::Binary { .. })
    ) || parent == child
}

pub(crate) struct Key {
    entry: NumericIndexEntry,
    long: Option<i32>,
}

impl Key {
    pub(crate) fn encode(
        kind: NumericKeyType,
        value: RowValue<'_>,
        budget: &mut ResourceBudget,
    ) -> Result<Option<Self>, UpdateError> {
        let entry = NumericIndexEntry::encode(
            &[NumericIndexField {
                column: 0,
                direction: IndexDirection::Ascending,
                kind,
            }],
            &[value],
            IndexNullPolicy::Include,
            RowLocator::new(PageNumber::new(0), 0),
            budget,
        )
        .map_err(|error| match error {
            EntryError::Encoding(error) => UpdateError::Resource(error),
            _ => UpdateError::Mismatch("relationship key value type"),
        })?
        .ok_or(UpdateError::Mismatch("relationship key omitted"))?;
        if entry.has_null() {
            return Ok(None);
        }
        Ok(Some(Self {
            entry,
            long: match value {
                RowValue::Long(value) => Some(value),
                _ => None,
            },
        }))
    }

    pub(crate) fn bytes(&self) -> &[u8] {
        self.entry.key()
    }

    pub(crate) fn violation(&self, parent: PageNumber, child: PageNumber) -> UpdateError {
        if let Some(value) = self.long {
            UpdateError::RelationshipConstraint {
                parent,
                child,
                value,
            }
        } else {
            UpdateError::ScalarRelationshipConstraint { parent, child }
        }
    }
}

pub(crate) fn sort(keys: &mut [Key], budget: &mut ResourceBudget) -> Result<(), UpdateError> {
    budget.charge_work_units(keys.len() as u64)?;
    let width = keys.iter().map(|key| key.bytes().len()).max().unwrap_or(0);
    budget.charge_work_units(
        (keys.len() as u64)
            .saturating_mul(u64::from(keys.len().max(1).ilog2()) + 1)
            .saturating_mul((width + size_of::<Key>()) as u64),
    )?;
    keys.sort_unstable_by(|left, right| left.bytes().cmp(right.bytes()));
    Ok(())
}

pub(crate) fn contains(
    parent: &[Key],
    child: &Key,
    budget: &mut ResourceBudget,
) -> Result<bool, UpdateError> {
    budget.charge_work_units(
        (u64::from(parent.len().max(1).ilog2()) + 2).saturating_mul(child.bytes().len() as u64),
    )?;
    Ok(parent
        .binary_search_by(|key| key.bytes().cmp(child.bytes()))
        .is_ok())
}

pub(crate) fn missing<'a>(
    parent: &[Key],
    child: &'a [Key],
    budget: &mut ResourceBudget,
) -> Result<Option<&'a Key>, UpdateError> {
    for key in child {
        if !contains(parent, key, budget)? {
            return Ok(Some(key));
        }
    }
    Ok(None)
}

pub(crate) fn unique(keys: &[Key], budget: &mut ResourceBudget) -> Result<bool, UpdateError> {
    for pair in keys.windows(2) {
        budget.charge_work_units(pair[0].bytes().len() as u64)?;
        if pair[0].bytes() == pair[1].bytes() {
            return Ok(false);
        }
    }
    Ok(true)
}
