//! EXP-0266: named column properties use ordinary single/chained LVAL storage;
//! EXP-0300 fixes the single-page limit for property payloads.
use super::table_create::*;
use crate::{
    PAGE_BYTES,
    create::composer::*,
    long_value::writer::{chained_fragments, encode_chained_row},
};

impl PlannedCreate<'_> {
    pub(in crate::create) fn property_header(&self) -> Result<Option<[u8; 12]>, ComposeError> {
        self.column_properties()
            .map(|property| {
                let page = self
                    .plan
                    .property_page()
                    .ok_or(ComposeError::UnsupportedMemoOption)?;
                crate::long_value::writer::external_long_value_header(
                    property.len(),
                    if self.plan.property_chained() {
                        crate::ExternalLongValueStorage::Chained
                    } else {
                        crate::ExternalLongValueStorage::SinglePage
                    },
                    crate::RowLocator::new(page, 0),
                )
                .map_err(|_| ComposeError::UnsupportedMemoOption)
            })
            .transpose()
    }

    pub(super) fn column_properties(
        &self,
    ) -> Option<&crate::properties::column::CreationProperties> {
        self.properties.as_ref()
    }

    pub(in crate::create) fn property_pages(
        &self,
        available: bool,
    ) -> impl Iterator<Item = u64> + Clone {
        self.plan
            .property_page()
            .filter(|_| !available || !self.plan.property_chained())
            .into_iter()
            .flat_map(|page| page.get()..page.get() + self.plan.property_page_count() as u64)
    }

    pub(super) fn append_property_pages(
        &self,
        plan: &mut WholeFileImagePlan,
        budget: &mut ResourceBudget,
    ) -> Result<(), ComposeError> {
        let Some(first) = self.plan.property_page() else {
            return Ok(());
        };
        let Some(property) = self.column_properties() else {
            let page = DataPageBuilder::new_long_value(budget)?;
            plan.append_image(finish_data_builder(page, budget)?, budget)?;
            return Ok(());
        };
        let count = self.plan.property_page_count();
        if count > 1 {
            budget.check_chain_depth(count as u64)?;
        }
        budget.charge_allocation(ByteCount::new(property.len() as u64))?;
        let mut payload = Vec::new();
        payload
            .try_reserve_exact(property.len())
            .map_err(|_| Error::Io {
                operation: "reserve column properties",
                kind: std::io::ErrorKind::OutOfMemory,
            })?;
        payload.resize(property.len(), 0);
        property.encode(&mut payload, budget)?;
        if !self.plan.property_chained() {
            let mut page = DataPageBuilder::new_long_value(budget)?;
            page.append_row(&payload, budget)?;
            plan.append_image(finish_data_builder(page, budget)?, budget)?;
        } else {
            let mut encoded = [0; PAGE_BYTES];
            for (ordinal, fragment) in chained_fragments(&payload).enumerate() {
                let next = (ordinal + 1 < count).then(|| {
                    crate::RowLocator::new(PageNumber::new(first.get() + ordinal as u64 + 1), 0)
                });
                let length = encode_chained_row(fragment, next, &mut encoded)
                    .map_err(|_| ComposeError::UnsupportedMemoOption)?;
                let mut page = DataPageBuilder::new_long_value(budget)?;
                page.append_row(&encoded[..length], budget)?;
                plan.append_image(finish_data_builder(page, budget)?, budget)?;
            }
        }
        Ok(())
    }
}
