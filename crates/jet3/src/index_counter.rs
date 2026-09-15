//! EXP-0059 prefixes and EXP-0230 retained counter changes for numeric indexes.
use crate::{PageImage, PageOffset, ResourceBudget, UpdateError};

pub(crate) fn increment(
    image: &mut PageImage,
    ordinal: u16,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let offset = 43 + usize::from(ordinal) * 8 + 4;
    let raw: [u8; 4] = image
        .as_bytes()
        .get(offset..offset + 4)
        .ok_or(UpdateError::Mismatch("index counter offset"))?
        .try_into()
        .map_err(|_| UpdateError::Mismatch("index counter width"))?;
    let next = u32::from_le_bytes(raw)
        .checked_add(1)
        .ok_or(UpdateError::Unsupported("index counter overflow"))?;
    image.write_at(PageOffset::new(offset as u64), &next.to_le_bytes(), budget)?;
    Ok(())
}
