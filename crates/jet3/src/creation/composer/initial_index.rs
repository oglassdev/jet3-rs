//! Uncompressed scalar trees: EXP-0062 branch/leaf/locator
//! grammar, EXP-0126/0150 scalar directions, and EXP-0148 null components/policies.

use super::*;
use crate::numeric_index_entry::{
    EntryError, MAX_FIELDS, NumericIndexEntry as Entry, NumericIndexField, record_capacity,
    sort_cost,
};
use crate::numeric_index_key::NumericKeyType;
use crate::{IndexNullPolicy, IndexTree, RowLocator};

#[path = "initial_index_pages.rs"]
mod pages;
use pages::IndexPages;

const COMPONENT_BYTES: usize = 5;

#[derive(Debug, Clone)]
pub(crate) struct InitialLongIndex {
    fields: [NumericIndexField; MAX_FIELDS],
    field_count: usize,
    unique: bool,
    entries: Vec<Entry>,
    null_policy: IndexNullPolicy,
    next_row: usize,
    distinct: u32,
    pages: IndexPages,
}

impl InitialLongIndex {
    #[cfg(test)]
    pub(crate) fn new(
        spec: &TableSpec<'_>,
        row_count: usize,
        budget: &mut ResourceBudget,
    ) -> Result<Option<Self>, ComposeError> {
        let [index] = spec.indexes else {
            return if spec.indexes.is_empty() {
                Ok(None)
            } else {
                Err(ComposeError::UnsupportedInitialIndexSchema)
            };
        };
        Self::for_index(spec, index, row_count, budget).map(Some)
    }

    pub(crate) fn for_table(
        spec: &TableSpec<'_>,
        row_count: usize,
        budget: &mut ResourceBudget,
    ) -> Result<Vec<Self>, ComposeError> {
        if spec.indexes.len() > crate::creation::schema_plan::MAX_OBSERVED_INDEXES {
            return Err(ComposeError::UnsupportedInitialIndexSchema);
        }
        budget.charge_allocation(ByteCount::new(
            (spec.indexes.len() * size_of::<Self>()) as u64,
        ))?;
        let mut indexes = Vec::new();
        indexes
            .try_reserve_exact(spec.indexes.len())
            .map_err(|_| Error::Io {
                operation: "reserve initial indexes",
                kind: std::io::ErrorKind::OutOfMemory,
            })?;
        for index in spec.indexes {
            indexes.push(Self::for_index(spec, index, row_count, budget)?);
        }
        Ok(indexes)
    }

    fn for_index(
        spec: &TableSpec<'_>,
        index: &crate::IndexSpec<'_>,
        row_count: usize,
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        if !(1..=MAX_FIELDS).contains(&index.fields.len()) {
            return Err(ComposeError::UnsupportedInitialIndexSchema);
        }
        let mut fields = [NumericIndexField {
            column: 0,
            direction: IndexDirection::Ascending,
            kind: NumericKeyType::Long,
        }; MAX_FIELDS];
        for (slot, field) in fields.iter_mut().zip(index.fields) {
            let column = field
                .column
                .resolve(spec.columns)
                .map(usize::from)
                .ok_or(ComposeError::UnsupportedInitialIndexSchema)?;
            let kind = spec
                .columns
                .get(column)
                .and_then(|column| NumericKeyType::from_column(column.column_type()))
                .ok_or(ComposeError::UnsupportedInitialIndexSchema)?;
            *slot = NumericIndexField {
                column,
                direction: field.direction,
                kind,
            };
        }
        u32::try_from(row_count).map_err(|_| Error::IntegerConversion {
            value: row_count as u128,
            target: "u32 initial row count",
        })?;
        let pages = IndexPages::new(&[], budget)?;
        let allocation = row_count
            .checked_mul(size_of::<Entry>())
            .ok_or(Error::Arithmetic {
                operation: "size initial index entries",
            })?;
        budget.charge_allocation(ByteCount::new(allocation as u64))?;
        let mut entries = Vec::new();
        entries
            .try_reserve_exact(row_count)
            .map_err(|_| Error::Io {
                operation: "reserve initial Long index entries",
                kind: std::io::ErrorKind::OutOfMemory,
            })?;
        Ok(Self {
            fields,
            field_count: index.fields.len(),
            unique: index.kind.is_unique(),
            null_policy: index.kind.null_policy(),
            next_row: 0,
            entries,
            distinct: 0,
            pages,
        })
    }

    pub(crate) fn push(
        &mut self,
        values: &[RowValue<'_>],
        locator: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let row = self.next_row;
        self.next_row += 1;
        let entry = Entry::encode(
            &self.fields[..self.field_count],
            values,
            self.null_policy,
            locator,
            budget,
        )
        .map_err(|error| match error {
            EntryError::Encoding(error) => ComposeError::from(error),
            EntryError::NullRequired => ComposeError::NullInitialIndexKey { row },
            EntryError::UnsupportedValue { column, kind } if kind != NumericKeyType::Long => {
                ComposeError::UnsupportedInitialIndexValue { row, column }
            }
            EntryError::FieldCount { .. }
            | EntryError::MissingColumn { .. }
            | EntryError::UnsupportedValue { .. } => ComposeError::UnsupportedInitialIndexSchema,
        })?;
        if let Some(entry) = entry {
            self.entries.push(entry);
        }
        Ok(())
    }

    pub(crate) fn sort(&mut self, budget: &mut ResourceBudget) -> Result<(), ComposeError> {
        let count = self.entries.len() as u64;
        // The unstable sort has O(n log n) worst-case work; charge byte comparisons.
        budget.charge_work_units(
            count
                * u64::from(count.max(1).ilog2() + 1)
                * sort_cost(&self.fields[..self.field_count]),
        )?;
        self.entries
            .sort_unstable_by(|a, b| a.record().cmp(b.record()));
        self.distinct = u32::from(!self.entries.is_empty());
        for pair in self.entries.windows(2) {
            if pair[0].key() == pair[1].key() {
                if self.unique && !pair[1].has_null() {
                    if self.field_count > 2
                        || self.fields[..self.field_count]
                            .iter()
                            .any(|field| field.kind != NumericKeyType::Long)
                    {
                        return Err(ComposeError::DuplicateInitialScalarIndexKey);
                    }
                    let mut values = [0_i32; 2];
                    for (position, value) in values.iter_mut().enumerate().take(self.field_count) {
                        let start = position * COMPONENT_BYTES + 1;
                        let mut raw: [u8; 4] =
                            pair[1].key()[start..start + 4].try_into().map_err(|_| {
                                Error::Arithmetic {
                                    operation: "decode duplicate Long index component",
                                }
                            })?;
                        if self.fields[position].direction == IndexDirection::Descending {
                            for byte in &mut raw {
                                *byte ^= 0xff;
                            }
                        }
                        raw[0] ^= 0x80;
                        *value = i32::from_be_bytes(raw);
                    }
                    return Err(if self.field_count == 1 {
                        ComposeError::DuplicateInitialIndexKey { value: values[0] }
                    } else {
                        ComposeError::DuplicateInitialCompositeIndexKey { values }
                    });
                }
            } else {
                self.distinct += 1;
            }
        }
        self.pages = IndexPages::new(&self.entries, budget)?;
        Ok(())
    }

    pub(crate) const fn distinct_count(&self) -> u32 {
        self.distinct
    }

    pub(super) fn extra_page_count(&self) -> u64 {
        self.pages.extra_count()
    }

    pub(super) fn image(
        &self,
        owner: PageNumber,
        root: PageNumber,
        first_extra: u64,
        ordinal: Option<usize>,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        self.pages
            .image(&self.entries, owner, root, first_extra, ordinal, budget)
    }

    pub(super) fn contains_single_long(
        &self,
        value: i32,
        budget: &mut ResourceBudget,
    ) -> Result<bool, ComposeError> {
        budget.charge_work_units(
            u64::from((self.entries.len().max(1) as u64).ilog2() + 2) * COMPONENT_BYTES as u64,
        )?;
        let key = crate::long_index_key::encode(value, IndexDirection::Ascending);
        Ok(self
            .entries
            .binary_search_by(|entry| entry.key().cmp(&key))
            .is_ok())
    }

    pub(crate) fn matches(
        &self,
        tree: &IndexTree,
        budget: &mut ResourceBudget,
    ) -> Result<bool, ComposeError> {
        budget.charge_work_units(
            self.entries.len() as u64 * record_capacity(&self.fields[..self.field_count]) as u64,
        )?;
        Ok(self.entries.len() == tree.entries().len()
            && self
                .entries
                .iter()
                .zip(tree.entries())
                .all(|(expected, actual)| {
                    actual.key().raw_bytes() == expected.key() && actual.row() == expected.locator()
                }))
    }
}

#[cfg(test)]
mod lookup_tests {
    use super::*;
    use crate::IndexKind;

    #[test]
    fn odd_length_lookup_charges_the_final_comparison() -> Result<(), ComposeError> {
        let table = TableSpec {
            name: b"Keys",
            columns: &[ColumnSpec::new(b"Id", ColumnType::Long)],
            indexes: &[crate::IndexSpec {
                name: b"ById",
                kind: IndexKind::Primary,
                fields: &[crate::IndexColumnSpec::ascending(b"Id")],
            }],
        };
        let mut budget = ResourceBudget::new(crate::ResourceLimits::default());
        let mut index = InitialLongIndex::new(&table, 3, &mut budget)?
            .ok_or(ComposeError::UnsupportedInitialIndexSchema)?;
        for slot in 0..3 {
            index.push(
                &[RowValue::Long(i32::from(slot))],
                RowLocator::new(PageNumber::new(24), slot),
                &mut budget,
            )?;
        }
        index.sort(&mut budget)?;
        let mut insufficient =
            ResourceBudget::new(crate::ResourceLimits::default().with_max_total_work_units(14));
        assert!(index.contains_single_long(1, &mut insufficient).is_err());
        let mut sufficient =
            ResourceBudget::new(crate::ResourceLimits::default().with_max_total_work_units(15));
        assert!(index.contains_single_long(1, &mut sufficient)?);
        assert_eq!(sufficient.total_work_units(), 15);
        Ok(())
    }
}
