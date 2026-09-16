//! Named Boolean field properties from EXP-0208/0266/0270/0277, stored in catalog LvProp.
use crate::{BinaryWriter, ColumnPhysicalType, ColumnSpec, Error, ResourceBudget};

const DICTIONARY_LENGTH: usize = 33;
const FIELD_PREFIX: usize = 12;
const BOOLEAN_RECORD_LENGTH: usize = 9;

pub(crate) const fn has_zero_length_property(kind: ColumnPhysicalType) -> bool {
    matches!(kind, ColumnPhysicalType::Text | ColumnPhysicalType::Memo)
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct ColumnProperties<'a> {
    columns: &'a [ColumnSpec<'a>],
    length: usize,
}

impl<'a> ColumnProperties<'a> {
    pub(crate) fn new(columns: &'a [ColumnSpec<'a>]) -> Option<Self> {
        if columns.len() > u8::MAX as usize || !columns.iter().any(ColumnSpec::allow_zero_length) {
            return None;
        }
        let mut length = 4 + DICTIONARY_LENGTH;
        for column in columns {
            if crate::catalog_name_key::validate_catalog_name(column.name()).is_err() {
                return None;
            }
            if column.column_type() == crate::ColumnType::AutoIncrement {
                continue;
            }
            length += FIELD_PREFIX
                + column.name().len()
                + BOOLEAN_RECORD_LENGTH
                    * (1 + usize::from(has_zero_length_property(column.physical_type())));
        }
        Some(Self { columns, length })
    }

    pub(crate) const fn len(self) -> usize {
        self.length
    }

    pub(crate) fn encode(
        self,
        output: &mut [u8],
        budget: &mut ResourceBudget,
    ) -> Result<usize, Error> {
        let mut writer = BinaryWriter::new(output, budget)?;
        writer.write_exact(b"KKD\0")?;
        writer.write_u32_le(DICTIONARY_LENGTH as u32)?;
        writer.write_u16_le(0x80)?;
        for name in [b"Required".as_slice(), b"AllowZeroLength"] {
            writer.write_u16_le(name.len() as u16)?;
            writer.write_exact(name)?;
        }
        for column in self.columns {
            if column.column_type() == crate::ColumnType::AutoIncrement {
                continue;
            }
            let eligible = has_zero_length_property(column.physical_type());
            let length = FIELD_PREFIX
                + column.name().len()
                + BOOLEAN_RECORD_LENGTH * (1 + usize::from(eligible));
            writer.write_u32_le(length as u32)?;
            writer.write_u16_le(1)?;
            writer.write_u32_le(6 + column.name().len() as u32)?;
            writer.write_u16_le(column.name().len() as u16)?;
            writer.write_exact(column.name())?;
            if eligible {
                boolean(&mut writer, 1, column.allow_zero_length())?;
            }
            boolean(&mut writer, 0, false)?;
        }
        Ok(self.length)
    }
}

fn boolean(writer: &mut BinaryWriter<'_, '_>, ordinal: u16, value: bool) -> Result<(), Error> {
    writer.write_u16_le(BOOLEAN_RECORD_LENGTH as u16)?;
    writer.write_u8(1)?;
    writer.write_u8(1)?;
    writer.write_u16_le(ordinal)?;
    writer.write_u16_le(1)?;
    writer.write_u8(if value { 0xff } else { 0 })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn auto_number_omits_its_default_property_block() -> Result<(), Box<dyn std::error::Error>> {
        let columns = [
            ColumnSpec::new(b"Id", crate::ColumnType::AutoIncrement),
            ColumnSpec::new(b"Body", crate::ColumnType::Memo).with_allow_zero_length(),
        ];
        let all = ColumnProperties::new(&columns).ok_or("properties")?;
        let memo = ColumnProperties::new(&columns[1..]).ok_or("memo properties")?;
        assert_eq!(all.len(), memo.len());
        let mut a = vec![0; all.len()];
        let mut b = vec![0; memo.len()];
        let mut work = ResourceBudget::new(crate::ResourceLimits::default());
        assert_eq!(all.encode(&mut a, &mut work)?, a.len());
        assert_eq!(memo.encode(&mut b, &mut work)?, b.len());
        assert_eq!(a, b);
        Ok(())
    }
}
