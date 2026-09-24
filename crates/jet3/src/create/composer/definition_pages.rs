//! EXP-0059/0105 payload geometry and EXP-0247 empty terminal pages.

use super::ComposeError;
use crate::{
    ByteCount, Error, PAGE_BYTES, PageImage, PageNumber, ResourceBudget,
    create::schema_plan::CONTINUATION_CAPACITY,
};

pub(super) struct DefinitionPages {
    logical: Vec<u8>,
    continuation_count: usize,
    empty_terminal: bool,
}

impl DefinitionPages {
    pub(super) fn new(length: usize, budget: &mut ResourceBudget) -> Result<Self, ComposeError> {
        budget
            .check_chain_depth(1 + crate::create::schema_plan::continuation_count(length) as u64)?;
        let continuation_count = crate::create::schema_plan::continuation_count(length);
        let empty_terminal =
            length >= PAGE_BYTES && (length - PAGE_BYTES).is_multiple_of(CONTINUATION_CAPACITY);
        let length = length.max(PAGE_BYTES);
        budget.charge_allocation(ByteCount::from_usize(length)?)?;
        let mut logical = Vec::new();
        logical.try_reserve_exact(length).map_err(|_| Error::Io {
            operation: "reserve logical table definition",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
        logical.resize(length, 0);
        Ok(Self {
            logical,
            continuation_count,
            empty_terminal,
        })
    }

    pub(super) fn logical_mut(&mut self) -> &mut [u8] {
        &mut self.logical
    }

    pub(super) fn root(
        &self,
        next: Option<PageNumber>,
        budget: &mut ResourceBudget,
    ) -> Result<[u8; PAGE_BYTES], ComposeError> {
        budget.charge_encoded_bytes(ByteCount::new(PAGE_BYTES as u64))?;
        let mut image = [0; PAGE_BYTES];
        image.copy_from_slice(&self.logical[..PAGE_BYTES]);
        image[4..8].copy_from_slice(&next_reference(next)?);
        Ok(image)
    }

    pub(super) fn continuations(&self) -> impl Iterator<Item = &[u8]> {
        self.logical[PAGE_BYTES..]
            .chunks(CONTINUATION_CAPACITY)
            .chain(self.empty_terminal.then_some([].as_slice()))
    }

    pub(super) fn continuation(
        &self,
        first: PageNumber,
        ordinal: usize,
        payload: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<PageImage, ComposeError> {
        let next = if ordinal + 1 < self.continuation_count {
            Some(PageNumber::new(
                first
                    .get()
                    .checked_add(ordinal as u64 + 1)
                    .ok_or(Error::Arithmetic {
                        operation: "place definition continuation",
                    })?,
            ))
        } else {
            None
        };
        let mut image = PageImage::new(crate::PageKind::TableDefinition);
        image.write_at(crate::PageOffset::new(0), &self.logical[..4], budget)?;
        image.write_at(crate::PageOffset::new(4), &next_reference(next)?, budget)?;
        image.write_at(crate::PageOffset::new(8), payload, budget)?;
        Ok(image)
    }
}

fn next_reference(next: Option<PageNumber>) -> Result<[u8; 4], ComposeError> {
    let next = next.map_or(0, PageNumber::get);
    Ok(u32::try_from(next)
        .map_err(|_| Error::IntegerConversion {
            value: u128::from(next),
            target: "u32 definition continuation",
        })?
        .to_le_bytes())
}
