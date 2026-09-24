//! Inline and indirect long-value ownership from EXP-0051/0057/0077.
pub(super) use crate::alloc::mutation_map::MapBits as Bitmap;
use crate::{ByteCount, ResourceBudget, UpdateError};

pub(super) fn payload_budget(
    length: usize,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    budget.check_decoded_value(ByteCount::new(length as u64))?;
    Ok(())
}
