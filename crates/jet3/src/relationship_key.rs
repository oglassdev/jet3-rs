//! EXP-0288/0290 relationship equality uses ordered scalar index encodings.
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
        kinds: &[NumericKeyType],
        values: &[RowValue<'_>],
        budget: &mut ResourceBudget,
    ) -> Result<Option<Self>, UpdateError> {
        if kinds.len() != values.len()
            || !(1..=crate::numeric_index_entry::MAX_FIELDS).contains(&kinds.len())
        {
            return Err(UpdateError::Mismatch("relationship key field count"));
        }
        let mut fields = [NumericIndexField {
            column: 0,
            direction: IndexDirection::Ascending,
            kind: NumericKeyType::Long,
        }; crate::numeric_index_entry::MAX_FIELDS];
        for (ordinal, (field, &kind)) in fields.iter_mut().zip(kinds).enumerate() {
            field.column = ordinal;
            field.kind = kind;
        }
        let entry = NumericIndexEntry::encode(
            &fields[..kinds.len()],
            values,
            IndexNullPolicy::Include,
            RowLocator::new(PageNumber::new(0), 0),
            budget,
        )
        .map_err(|error| match error {
            EntryError::Encoding(error) => UpdateError::Resource(error),
            _ => UpdateError::Mismatch("relationship key value type"),
        })?
        .ok_or(UpdateError::Mismatch("relationship key omitted"))?;
        if kinds
            .iter()
            .zip(values)
            .all(|(&kind, &value)| kind.is_null(value))
        {
            return Ok(None);
        }
        Ok(Some(Self {
            entry,
            long: match values {
                [RowValue::Long(value)] => Some(*value),
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
        if !pair[0].entry.has_null() && pair[0].bytes() == pair[1].bytes() {
            return Ok(false);
        }
    }
    Ok(true)
}

pub(crate) fn key_values<'row>(
    row: &mut crate::RowView<'row, '_>,
    columns: &[crate::ColumnOrdinal],
) -> Result<[RowValue<'row>; crate::numeric_index_entry::MAX_FIELDS], UpdateError> {
    let mut values = [RowValue::Null; crate::numeric_index_entry::MAX_FIELDS];
    if columns.len() > values.len() {
        return Err(UpdateError::Mismatch("relationship key field count"));
    }
    for (&column, value) in columns.iter().zip(&mut values) {
        *value = crate::numeric_row_values::read_column(row, column)?;
    }
    Ok(values)
}
