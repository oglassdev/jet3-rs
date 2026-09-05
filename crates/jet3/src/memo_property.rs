//! Dedicated Memo AllowZeroLength payload from EXP-0208's named property blocks.
//! Name-length interpolation is a bounded candidate, not a general property grammar.
use crate::{BinaryWriter, Error, ResourceBudget};

pub(crate) const MAX_PAYLOAD: usize = 154;

#[derive(Clone, Copy)]
pub(crate) struct MemoProperty<'a> {
    name: &'a [u8],
}

impl<'a> MemoProperty<'a> {
    pub(crate) fn new(name: &'a [u8]) -> Option<Self> {
        if name.is_empty() || name.len() > 64 || !name.iter().all(u8::is_ascii_alphanumeric) {
            return None;
        }
        Some(Self { name })
    }

    pub(crate) fn len(self) -> usize {
        90 + self.name.len()
    }

    pub(crate) fn encode(
        self,
        output: &mut [u8],
        budget: &mut ResourceBudget,
    ) -> Result<usize, Error> {
        let mut writer = BinaryWriter::new(output, budget)?;
        writer.write_exact(b"KKD\0")?;
        writer.write_u32_le(33)?;
        writer.write_u16_le(0x80)?;
        writer.write_u16_le(8)?;
        writer.write_exact(b"Required")?;
        writer.write_u16_le(15)?;
        writer.write_exact(b"AllowZeroLength")?;
        writer.write_u32_le(23)?;
        writer.write_u16_le(1)?;
        writer.write_u32_le(8)?;
        writer.write_u16_le(2)?;
        writer.write_exact(b"Id")?;
        writer.write_exact(&[9, 0, 1, 1, 0, 0, 1, 0, 0])?;
        writer.write_u32_le(30 + self.name.len() as u32)?;
        writer.write_u16_le(1)?;
        writer.write_u32_le(6 + self.name.len() as u32)?;
        writer.write_u16_le(self.name.len() as u16)?;
        writer.write_exact(self.name)?;
        writer.write_exact(&[9, 0, 1, 1, 1, 0, 1, 0, 0xff])?;
        writer.write_exact(&[9, 0, 1, 1, 0, 0, 1, 0, 0])?;
        Ok(self.len())
    }
}
