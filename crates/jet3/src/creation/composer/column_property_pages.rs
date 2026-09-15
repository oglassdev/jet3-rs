//! EXP-0266: named column properties use ordinary single/chained LVAL storage.
use super::*;
use crate::long_value_writer::{chained_fragments, encode_chained_row};

impl PlannedCreate<'_> {
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
        if count == 1 {
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
