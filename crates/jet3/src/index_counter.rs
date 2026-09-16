//! EXP-0059 prefixes, EXP-0230 ordinary counters, and EXP-0268/0286 relationship-key edits.
use crate::{PageImage, PageOffset, ResourceBudget, UpdateError};

#[derive(Clone, Copy)]
pub(crate) enum Change {
    Increment,
    RemoveRelationshipEntry,
}

pub(crate) fn change(
    image: &mut PageImage,
    ordinal: u16,
    change: Change,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let offset = 43 + usize::from(ordinal) * 8;
    let raw: [u8; 8] = image
        .as_bytes()
        .get(offset..offset + 8)
        .ok_or(UpdateError::Mismatch("index counter offset"))?
        .try_into()
        .map_err(|_| UpdateError::Mismatch("index counter width"))?;
    let mut first = u32::from_le_bytes([raw[0], raw[1], raw[2], raw[3]]);
    let mut second = u32::from_le_bytes([raw[4], raw[5], raw[6], raw[7]]);
    match change {
        Change::Increment => {
            second = second
                .checked_add(1)
                .ok_or(UpdateError::Unsupported("index counter overflow"))?
        }
        Change::RemoveRelationshipEntry if first > 0 => {
            first -= 1;
            second = second.min(first);
        }
        Change::RemoveRelationshipEntry => {}
    }
    let mut next = [0; 8];
    next[..4].copy_from_slice(&first.to_le_bytes());
    next[4..].copy_from_slice(&second.to_le_bytes());
    image.write_at(PageOffset::new(offset as u64), &next, budget)?;
    Ok(())
}
