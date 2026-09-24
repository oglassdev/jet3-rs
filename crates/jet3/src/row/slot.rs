//! EXP-0060 directory flags and four-byte overflow links.
use crate::{UpdateError, row::directory::RowEntry};

pub(crate) fn pointer(row: crate::RowLocator) -> Result<[u8; 4], UpdateError> {
    if row.page().get() > 0x00ff_ffff {
        return Err(UpdateError::Unsupported("overflow page reference width"));
    }
    let bytes = row.page().get().to_le_bytes();
    Ok([row.slot(), bytes[0], bytes[1], bytes[2]])
}

pub(crate) fn hide_first(
    image: &mut crate::PageImage,
    budget: &mut crate::ResourceBudget,
) -> Result<(), UpdateError> {
    let bytes = image.as_bytes();
    if u16::from_le_bytes([bytes[8], bytes[9]]) != 1 {
        return Err(UpdateError::Mismatch("new overflow page slot count"));
    }
    let word = u16::from_le_bytes([bytes[10], bytes[11]]) | RowSlot::Storage.flags();
    image.write_at(crate::PageOffset::new(10), &word.to_le_bytes(), budget)?;
    Ok(())
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum RowSlot {
    Ordinary,
    Link,
    Storage,
    StorageLink,
    Deleted,
}

impl RowSlot {
    pub fn read(entry: &RowEntry) -> Result<Self, UpdateError> {
        let length = entry.range().len();
        let slot = match (entry.hidden(), entry.overflow(), length) {
            (true, true, 0) => Self::Deleted,
            (true, true, _) => Self::StorageLink,
            (true, false, _) => Self::Storage,
            (false, true, _) => Self::Link,
            (false, false, _) => Self::Ordinary,
        };
        slot.check_length(length)?;
        Ok(slot)
    }

    pub fn check_length(self, length: usize) -> Result<(), UpdateError> {
        let valid = match self {
            Self::Deleted => length == 0,
            Self::Link | Self::StorageLink => length == 4,
            Self::Ordinary | Self::Storage => length != 0,
        };
        if valid {
            Ok(())
        } else {
            Err(UpdateError::Mismatch("physical row slot length"))
        }
    }

    pub const fn flags(self) -> u16 {
        match self {
            Self::Ordinary => 0,
            Self::Link => 0x4000,
            Self::Storage => 0x8000,
            Self::StorageLink | Self::Deleted => 0xc000,
        }
    }
}
