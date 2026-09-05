//! Uncompressed trees of one/two Long components: EXP-0062 branch/leaf/locator
//! grammar, EXP-0126 direction/composition and full-key distinct counts.

use super::*;
use crate::{IndexKind, IndexTree, RowLocator};

#[path = "bootstrap_initial_index_pages.rs"]
mod pages;
use pages::IndexPages;

const COMPONENT_BYTES: usize = 5;
const MAX_FIELDS: usize = 2;
const LOCATOR_BYTES: usize = 4;
const ENTRY_CAPACITY: usize = MAX_FIELDS * COMPONENT_BYTES + LOCATOR_BYTES;

#[derive(Debug, Clone, Copy)]
struct LongField {
    column: usize,
    direction: IndexDirection,
}

#[derive(Debug, Clone)]
pub(crate) struct InitialLongIndex {
    fields: [LongField; MAX_FIELDS],
    field_count: usize,
    unique: bool,
    entries: Vec<[u8; ENTRY_CAPACITY]>,
    distinct: u32,
    pages: IndexPages,
}

impl InitialLongIndex {
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
        if !(1..=MAX_FIELDS).contains(&index.fields.len()) {
            return Err(ComposeError::UnsupportedInitialIndexSchema);
        }
        let mut fields = [LongField {
            column: 0,
            direction: IndexDirection::Ascending,
        }; MAX_FIELDS];
        for (slot, field) in fields.iter_mut().zip(index.fields) {
            let column = field
                .column
                .resolve(spec.columns)
                .map(usize::from)
                .ok_or(ComposeError::UnsupportedInitialIndexSchema)?;
            if spec.columns.get(column).is_none_or(|column| {
                !matches!(
                    column.column_type(),
                    ColumnType::Long | ColumnType::AutoIncrement
                )
            }) {
                return Err(ComposeError::UnsupportedInitialIndexSchema);
            }
            *slot = LongField {
                column,
                direction: field.direction,
            };
        }
        let entry_bytes = index.fields.len() * COMPONENT_BYTES + LOCATOR_BYTES;
        let pages = IndexPages::new(row_count, entry_bytes, budget)?;
        budget.charge_allocation(ByteCount::new((row_count * ENTRY_CAPACITY) as u64))?;
        let mut entries = Vec::new();
        entries
            .try_reserve_exact(row_count)
            .map_err(|_| Error::Io {
                operation: "reserve initial Long index entries",
                kind: std::io::ErrorKind::OutOfMemory,
            })?;
        Ok(Some(Self {
            fields,
            field_count: index.fields.len(),
            unique: index.kind != IndexKind::Ordinary,
            entries,
            distinct: 0,
            pages,
        }))
    }

    pub(crate) fn push(
        &mut self,
        values: &[RowValue<'_>],
        locator: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        budget.charge_items(1)?;
        let page = u32::try_from(locator.page().get()).map_err(|_| Error::IntegerConversion {
            value: locator.page().get() as u128,
            target: "24-bit index row page",
        })?;
        if page > 0x00ff_ffff {
            return Err(Error::IntegerConversion {
                value: page as u128,
                target: "24-bit index row page",
            }
            .into());
        }
        let mut entry = [0_u8; ENTRY_CAPACITY];
        for (position, field) in self.fields[..self.field_count].iter().enumerate() {
            let Some(RowValue::Long(value)) = values.get(field.column) else {
                return Err(ComposeError::NullInitialIndexKey {
                    row: self.entries.len(),
                });
            };
            let component =
                &mut entry[position * COMPONENT_BYTES..(position + 1) * COMPONENT_BYTES];
            component[0] = 0x7f;
            component[1..].copy_from_slice(&value.to_be_bytes());
            component[1] ^= 0x80;
            if field.direction == IndexDirection::Descending {
                for byte in component {
                    *byte ^= 0xff;
                }
            }
        }
        let key_bytes = self.key_bytes();
        entry[key_bytes..key_bytes + 3].copy_from_slice(&page.to_be_bytes()[1..]);
        entry[key_bytes + LOCATOR_BYTES - 1] = locator.slot();
        self.entries.push(entry);
        Ok(())
    }

    pub(crate) fn sort(&mut self, budget: &mut ResourceBudget) -> Result<(), ComposeError> {
        let count = self.entries.len() as u64;
        // The unstable sort has O(n log n) worst-case work; charge byte comparisons.
        budget.charge_work_units(
            count * u64::from(count.max(1).ilog2() + 1) * 4 * ENTRY_CAPACITY as u64,
        )?;
        self.entries.sort_unstable();
        self.distinct = u32::from(!self.entries.is_empty());
        let key_bytes = self.key_bytes();
        for pair in self.entries.windows(2) {
            if pair[0][..key_bytes] == pair[1][..key_bytes] {
                if self.unique {
                    let mut values = [0_i32; MAX_FIELDS];
                    for (position, value) in values.iter_mut().enumerate().take(self.field_count) {
                        let start = position * COMPONENT_BYTES + 1;
                        let mut raw: [u8; 4] =
                            pair[1][start..start + 4].try_into().map_err(|_| {
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
        Ok(())
    }

    const fn key_bytes(&self) -> usize {
        self.field_count * COMPONENT_BYTES
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
        let mut key = [0x7f, 0, 0, 0, 0];
        key[1..].copy_from_slice(&value.to_be_bytes());
        key[1] ^= 0x80;
        Ok(self
            .entries
            .binary_search_by(|entry| entry[..COMPONENT_BYTES].cmp(&key))
            .is_ok())
    }

    pub(crate) fn matches(&self, tree: &IndexTree) -> bool {
        let key_bytes = self.key_bytes();
        self.entries.len() == tree.entries().len()
            && self
                .entries
                .iter()
                .zip(tree.entries())
                .all(|(expected, actual)| {
                    actual.key().raw_bytes() == &expected[..key_bytes]
                        && actual.row().page().get()
                            == u64::from(u32::from_be_bytes([
                                0,
                                expected[key_bytes],
                                expected[key_bytes + 1],
                                expected[key_bytes + 2],
                            ]))
                        && actual.row().slot() == expected[key_bytes + 3]
                })
    }
}

#[cfg(test)]
mod lookup_tests {
    use super::*;

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
