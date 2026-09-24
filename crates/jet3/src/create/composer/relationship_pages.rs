//! EXP-0073/0114 relationship rows, EXP-0241 catalog packing and index trees.
use super::{
    catalog_pages::{CatalogData, CatalogIndex},
    *,
};

pub(super) struct RelationshipMaps<'a> {
    pub owned: &'a [u64],
    pub available: &'a [u64],
    pub indexes: [&'a [u64]; 3],
}

impl<'a> RelationshipMaps<'a> {
    pub fn single(data: &'a [u64]) -> Self {
        Self {
            owned: data,
            available: data,
            indexes: [
                &[RELATIONSHIPS_NAME_ROOT],
                &[RELATIONSHIPS_OBJECT_ROOT],
                &[RELATIONSHIPS_REFERENCED_ROOT],
            ],
        }
    }
}

#[derive(Clone, Copy)]
pub(super) struct RelationshipRow<'a> {
    pub name: &'a [u8],
    pub flags: crate::relationship::flags::RelationshipFlags,
    pub child_table: &'a [u8],
    pub child_column: &'a [u8],
    pub parent_table: &'a [u8],
    pub parent_column: &'a [u8],
    pub field_count: u16,
    pub field_ordinal: u16,
}

impl RelationshipRow<'_> {
    fn encode(self, output: &mut [u8], budget: &mut ResourceBudget) -> Result<usize, ComposeError> {
        let layout = [
            variable(ColumnPhysicalType::Text, 0, 255),
            fixed(ColumnPhysicalType::Long, 0, 4),
            fixed(ColumnPhysicalType::Long, 4, 4),
            fixed(ColumnPhysicalType::Long, 8, 4),
            variable(ColumnPhysicalType::Text, 1, 255),
            variable(ColumnPhysicalType::Text, 2, 255),
            variable(ColumnPhysicalType::Text, 3, 255),
            variable(ColumnPhysicalType::Text, 4, 255),
        ];
        let values = [
            RowValue::Text(self.name),
            RowValue::Long(self.flags.raw()),
            RowValue::Long(i32::from(self.field_count)),
            RowValue::Long(i32::from(self.field_ordinal)),
            RowValue::Text(self.child_table),
            RowValue::Text(self.child_column),
            RowValue::Text(self.parent_table),
            RowValue::Text(self.parent_column),
        ];
        Ok(encode_row(&layout, &values, output, budget)?.get() as usize)
    }

    fn key(self, ordinal: usize) -> Result<OwnedIndexEntry, ComposeError> {
        let name = match ordinal {
            0 => self.name,
            1 => self.child_table,
            _ => self.parent_table,
        };
        let mut entry = OwnedIndexEntry::name(0, name, 0)?;
        let prefix = crate::catalog::name_key::LONG_COMPONENT_LEN;
        entry.key.copy_within(prefix..entry.len, 0);
        entry.len -= prefix;
        Ok(entry)
    }
}

pub(super) struct RelationshipPages {
    data: CatalogData,
    indexes: [CatalogIndex; 3],
    page_count: u64,
}

impl RelationshipPages {
    pub fn new<'a>(
        rows: impl Iterator<Item = RelationshipRow<'a>> + Clone,
        first_page: u64,
        budget: &mut ResourceBudget,
    ) -> Result<Self, ComposeError> {
        allocation_maps::check_page(first_page)?;
        let mut next_page = first_page + 1;
        let data = CatalogData::build(
            MSYS_RELATIONSHIPS_ROOT,
            first_page,
            rows.clone(),
            &mut next_page,
            budget,
            RelationshipRow::encode,
        )?;
        let mut index = |ordinal, root| {
            CatalogIndex::build(
                MSYS_RELATIONSHIPS_ROOT,
                root,
                &data.locators,
                rows.clone().map(|row| row.key(ordinal)),
                &mut next_page,
                budget,
            )
        };
        let indexes = [
            index(0, RELATIONSHIPS_NAME_ROOT)?,
            index(1, RELATIONSHIPS_OBJECT_ROOT)?,
            index(2, RELATIONSHIPS_REFERENCED_ROOT)?,
        ];
        Ok(Self {
            data,
            indexes,
            page_count: next_page,
        })
    }

    pub fn page_count(&self) -> u64 {
        self.page_count
    }

    pub fn maps(&self) -> RelationshipMaps<'_> {
        RelationshipMaps {
            owned: &self.data.owned,
            available: &self.data.available,
            indexes: [
                &self.indexes[0].owned,
                &self.indexes[1].owned,
                &self.indexes[2].owned,
            ],
        }
    }

    pub fn replace_existing(
        &self,
        image: &mut WholeFileImagePlan,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let count =
            u32::try_from(self.data.locators.len()).map_err(|_| ComposeError::CatalogLayout {
                detail: "relationship catalog row count",
            })?;
        let counts = self.indexes.each_ref().map(|index| index.distinct_count);
        image.replace(
            PageNumber::new(MSYS_RELATIONSHIPS_ROOT),
            msys_relationships_definition(count, counts, budget)?,
        )?;
        for (index, root) in self.indexes.iter().zip([
            RELATIONSHIPS_NAME_ROOT,
            RELATIONSHIPS_OBJECT_ROOT,
            RELATIONSHIPS_REFERENCED_ROOT,
        ]) {
            image.replace(PageNumber::new(root), index.root()?)?;
        }
        Ok(())
    }

    pub fn append(
        self,
        image: &mut WholeFileImagePlan,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        for (number, page) in self
            .data
            .images
            .into_iter()
            .chain(self.indexes.into_iter().flat_map(|index| index.images))
        {
            if number < EMPTY_DATABASE_PAGE_COUNT {
                continue;
            }
            if number != image.page_count() {
                return Err(ComposeError::CatalogLayout {
                    detail: "relationship append page order",
                });
            }
            image.append_image(page, budget)?;
        }
        Ok(())
    }
}
