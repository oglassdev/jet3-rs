//! One uncompressed ascending Long leaf: EXP-0062 key and locator grammar,
//! matching the existing catalog Long encoder; EXP-0073 distinct-key counts.

use super::*;
use crate::{IndexKind, IndexTree, RowLocator};

const KEY_BYTES: usize = 5;
const ENTRY_BYTES: usize = KEY_BYTES + 4;
const MAX_ENTRIES: usize = INDEX_ENTRY_AREA_LEN / ENTRY_BYTES;

#[derive(Debug, Clone)]
pub(crate) struct InitialLongIndex {
    column: usize,
    unique: bool,
    entries: Vec<[u8; ENTRY_BYTES]>,
    distinct: u32,
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
        let [field] = index.fields else {
            return Err(ComposeError::UnsupportedInitialIndexSchema);
        };
        let column = field
            .column
            .resolve(spec.columns)
            .map(usize::from)
            .ok_or(ComposeError::UnsupportedInitialIndexSchema)?;
        if field.direction != IndexDirection::Ascending
            || spec
                .columns
                .get(column)
                .is_none_or(|column| column.column_type() != ColumnType::Long)
        {
            return Err(ComposeError::UnsupportedInitialIndexSchema);
        }
        if row_count > MAX_ENTRIES {
            return Err(ComposeError::IndexPageFull {
                needed: row_count.saturating_mul(ENTRY_BYTES),
                available: INDEX_ENTRY_AREA_LEN,
            });
        }
        budget.charge_allocation(ByteCount::new((row_count * ENTRY_BYTES) as u64))?;
        let mut entries = Vec::new();
        entries
            .try_reserve_exact(row_count)
            .map_err(|_| Error::Io {
                operation: "reserve initial Long index entries",
                kind: std::io::ErrorKind::OutOfMemory,
            })?;
        Ok(Some(Self {
            column,
            unique: index.kind != IndexKind::Ordinary,
            entries,
            distinct: 0,
        }))
    }

    pub(crate) fn push(
        &mut self,
        values: &[RowValue<'_>],
        locator: RowLocator,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let Some(RowValue::Long(value)) = values.get(self.column) else {
            return Err(ComposeError::NullInitialIndexKey {
                row: self.entries.len(),
            });
        };
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
        let mut entry = [0_u8; ENTRY_BYTES];
        entry[0] = 0x7f;
        entry[1..KEY_BYTES].copy_from_slice(&value.to_be_bytes());
        entry[1] ^= 0x80;
        entry[KEY_BYTES..KEY_BYTES + 3].copy_from_slice(&page.to_be_bytes()[1..]);
        entry[ENTRY_BYTES - 1] = locator.slot();
        self.entries.push(entry);
        Ok(())
    }

    pub(crate) fn sort(&mut self, budget: &mut ResourceBudget) -> Result<(), ComposeError> {
        // At most 200 entries; charge a conservative quadratic byte-comparison bound.
        let count = self.entries.len() as u64;
        budget.charge_work_units(count * count * ENTRY_BYTES as u64)?;
        self.entries.sort_unstable();
        self.distinct = u32::from(!self.entries.is_empty());
        for pair in self.entries.windows(2) {
            if pair[0][..KEY_BYTES] == pair[1][..KEY_BYTES] {
                if self.unique {
                    let mut raw: [u8; 4] =
                        pair[1][1..KEY_BYTES]
                            .try_into()
                            .map_err(|_| Error::Arithmetic {
                                operation: "decode duplicate Long index key",
                            })?;
                    raw[0] ^= 0x80;
                    return Err(ComposeError::DuplicateInitialIndexKey {
                        value: i32::from_be_bytes(raw),
                    });
                }
            } else {
                self.distinct += 1;
            }
        }
        Ok(())
    }

    pub(crate) const fn distinct_count(&self) -> u32 {
        self.distinct
    }

    pub(super) fn image(
        &self,
        owner: PageNumber,
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        let mut bytes = [0_u8; PAGE_BYTES];
        bytes[0] = 4;
        bytes[1] = 1;
        let used = self.entries.len() * ENTRY_BYTES;
        bytes[2..4].copy_from_slice(&((INDEX_ENTRY_AREA_LEN - used) as u16).to_le_bytes());
        let owner = u32::try_from(owner.get()).map_err(|_| Error::IntegerConversion {
            value: owner.get() as u128,
            target: "u32 index owner",
        })?;
        bytes[4..8].copy_from_slice(&owner.to_le_bytes());
        for (position, entry) in self.entries.iter().enumerate() {
            let start = INDEX_ENTRY_AREA_OFFSET + position * ENTRY_BYTES;
            bytes[start..start + ENTRY_BYTES].copy_from_slice(entry);
            let end = (position + 1) * ENTRY_BYTES;
            bytes[INDEX_BOUNDARY_BITMAP_OFFSET + end / 8] |= 1 << (end % 8);
        }
        let mut image = PageImage::new(PageKind::LeafIndex);
        image.write_at(PageOffset::new(0), &bytes, budget)?;
        Ok(image)
    }

    pub(crate) fn matches(&self, tree: &IndexTree) -> bool {
        self.entries.len() == tree.entries().len()
            && self
                .entries
                .iter()
                .zip(tree.entries())
                .all(|(expected, actual)| {
                    actual.key().raw_bytes() == &expected[..KEY_BYTES]
                        && actual.row().page().get()
                            == u64::from(u32::from_be_bytes([
                                0,
                                expected[5],
                                expected[6],
                                expected[7],
                            ]))
                        && actual.row().slot() == expected[8]
                })
    }
}
