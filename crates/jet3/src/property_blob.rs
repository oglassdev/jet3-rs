//! Lossless catalog `LvProp` blobs.
//!
//! Framing follows EXP-0208/0266/0283: `KKD\0`, a length-prefixed name
//! dictionary of kind `0x80`, then length-prefixed named blocks whose records
//! reference dictionary ordinals. EXP-0297/0299 establish the edit behavior.
//! Every byte of a parsed blob, including unknown block kinds, record flags,
//! value types and unused dictionary names, is retained and re-encoded exactly.
use crate::{BinaryWriter, ColumnPropertyError, Error, ResourceBudget, resource::reserve};

const SIGNATURE: &[u8; 4] = b"KKD\0";
const DICTIONARY_KIND: u16 = 0x80;
const DICTIONARY_HEADER: usize = 10;
const BLOCK_HEADER: usize = 12;
const RECORD_HEADER: usize = 6;

/// EXP-0266 field blocks and EXP-0299 table blocks.
pub(crate) const FIELD_BLOCK: u16 = 1;
pub(crate) const TABLE_BLOCK: u16 = 0;
/// EXP-0266 Boolean and EXP-0299 Text/Memo record value types.
pub(crate) const BOOLEAN: u8 = 1;
pub(crate) const TEXT: u8 = 10;
pub(crate) const MEMO: u8 = 12;

fn invalid(detail: &'static str) -> ColumnPropertyError {
    ColumnPropertyError::Invalid(detail)
}

pub(crate) fn owned(bytes: &[u8], budget: &mut ResourceBudget) -> Result<Vec<u8>, Error> {
    let mut output = Vec::new();
    reserve(&mut output, bytes.len(), budget)?;
    output.extend_from_slice(bytes);
    Ok(output)
}

/// One named property value; `data` holds everything after the ordinal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Record {
    flag: u8,
    kind: u8,
    name: u16,
    data: Vec<u8>,
}

impl Record {
    pub(crate) fn new(
        flag: u8,
        kind: u8,
        name: u16,
        value: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<Self, ColumnPropertyError> {
        let length = u16::try_from(value.len())
            .ok()
            .filter(|length| usize::from(*length) + 2 + RECORD_HEADER <= usize::from(u16::MAX))
            .ok_or(invalid("property value length"))?;
        let mut data = Vec::new();
        reserve(&mut data, value.len() + 2, budget)?;
        data.extend_from_slice(&length.to_le_bytes());
        data.extend_from_slice(value);
        Ok(Self {
            flag,
            kind,
            name,
            data,
        })
    }

    pub(crate) const fn flag(&self) -> u8 {
        self.flag
    }

    pub(crate) const fn kind(&self) -> u8 {
        self.kind
    }

    pub(crate) const fn name(&self) -> u16 {
        self.name
    }

    /// The length-prefixed value, when the record uses that framing.
    pub(crate) fn value(&self) -> Option<&[u8]> {
        let (length, value) = self.data.split_first_chunk::<2>()?;
        (usize::from(u16::from_le_bytes(*length)) == value.len()).then_some(value)
    }

    fn len(&self) -> usize {
        RECORD_HEADER + self.data.len()
    }
}

/// One block of records; kind 1 names a field, and EXP-0299 kind 0 with an empty name is the table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Block {
    kind: u16,
    name: Vec<u8>,
    records: Vec<Record>,
}

impl Block {
    pub(crate) fn new(
        kind: u16,
        name: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<Self, ColumnPropertyError> {
        if u16::try_from(name.len()).is_err() {
            return Err(invalid("property block name length"));
        }
        Ok(Self {
            kind,
            name: owned(name, budget)?,
            records: Vec::new(),
        })
    }

    pub(crate) const fn kind(&self) -> u16 {
        self.kind
    }

    pub(crate) fn name(&self) -> &[u8] {
        &self.name
    }

    pub(crate) fn records(&self) -> &[Record] {
        &self.records
    }

    pub(crate) fn record(&self, name: u16) -> Option<&Record> {
        self.records.iter().find(|record| record.name == name)
    }

    pub(crate) fn rename(
        &mut self,
        name: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<(), ColumnPropertyError> {
        *self = Self {
            name: Self::new(self.kind, name, budget)?.name,
            kind: self.kind,
            records: std::mem::take(&mut self.records),
        };
        Ok(())
    }

    /// Replaces the record with the same dictionary ordinal in place, or appends it.
    pub(crate) fn set(
        &mut self,
        record: Record,
        budget: &mut ResourceBudget,
    ) -> Result<(), ColumnPropertyError> {
        if let Some(existing) = self.records.iter_mut().find(|r| r.name == record.name) {
            *existing = record;
        } else {
            reserve(&mut self.records, 1, budget)?;
            self.records.push(record);
        }
        Ok(())
    }

    pub(crate) fn remove(&mut self, name: u16) {
        self.records.retain(|record| record.name != name);
    }

    fn len(&self) -> usize {
        BLOCK_HEADER + self.name.len() + self.records.iter().map(Record::len).sum::<usize>()
    }
}

/// A complete property payload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct PropertyBlob {
    dictionary_kind: u16,
    names: Vec<Vec<u8>>,
    blocks: Vec<Block>,
}

impl PropertyBlob {
    pub(crate) const fn empty() -> Self {
        Self {
            dictionary_kind: DICTIONARY_KIND,
            names: Vec::new(),
            blocks: Vec::new(),
        }
    }

    pub(crate) fn parse(
        data: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<Self, ColumnPropertyError> {
        budget.charge_work_units(data.len() as u64)?;
        if data.get(..4) != Some(SIGNATURE.as_slice()) {
            return Err(invalid("property signature"));
        }
        let dictionary_end = u32_at(data, 4)
            .and_then(|length| usize::try_from(length).ok())
            .and_then(|length| length.checked_add(4))
            .filter(|end| (DICTIONARY_HEADER..=data.len()).contains(end))
            .ok_or(invalid("property block bounds"))?;
        let dictionary_kind = u16_at(data, 8).ok_or(invalid("property block bounds"))?;
        if dictionary_kind != DICTIONARY_KIND {
            return Err(invalid("property dictionary kind"));
        }
        let mut names = Vec::new();
        let mut position = DICTIONARY_HEADER;
        while position < dictionary_end {
            let length = u16_at(data, position).map(usize::from);
            let name = length
                .and_then(|length| data.get(position + 2..position + 2 + length))
                .filter(|name| !name.is_empty() && position + 2 + name.len() <= dictionary_end)
                .ok_or(invalid("property dictionary name bounds"))?;
            if names.len() > usize::from(u16::MAX) {
                return Err(invalid("property dictionary name bounds"));
            }
            reserve(&mut names, 1, budget)?;
            names.push(owned(name, budget)?);
            position += 2 + name.len();
        }
        let mut blocks = Vec::new();
        while position < data.len() {
            let end = u32_at(data, position)
                .and_then(|length| usize::try_from(length).ok())
                .filter(|length| *length >= BLOCK_HEADER)
                .and_then(|length| position.checked_add(length))
                .filter(|end| *end <= data.len())
                .ok_or(invalid("property block bounds"))?;
            let block = &data[position..end];
            let kind = u16::from_le_bytes([block[4], block[5]]);
            let name_frame = u32_at(block, 6).ok_or(invalid("property field-name framing"))?;
            let name_length = usize::from(u16_at(block, 10).unwrap_or(0));
            if name_frame as usize != name_length + 6 {
                return Err(invalid("property field-name framing"));
            }
            let name = block
                .get(BLOCK_HEADER..BLOCK_HEADER + name_length)
                .ok_or(invalid("property field-name bounds"))?;
            let mut parsed = Block::new(kind, name, budget)?;
            let mut at = BLOCK_HEADER + name_length;
            while at < block.len() {
                let length = usize::from(u16_at(block, at).unwrap_or(0));
                let record = block
                    .get(at..at + length)
                    .filter(|record| record.len() >= RECORD_HEADER + 2)
                    .ok_or(invalid("property record bounds"))?;
                let name = u16::from_le_bytes([record[4], record[5]]);
                if usize::from(name) >= names.len() {
                    return Err(invalid("property dictionary reference"));
                }
                reserve(&mut parsed.records, 1, budget)?;
                parsed.records.push(Record {
                    flag: record[2],
                    kind: record[3],
                    name,
                    data: owned(&record[RECORD_HEADER..], budget)?,
                });
                at += length;
            }
            reserve(&mut blocks, 1, budget)?;
            blocks.push(parsed);
            position = end;
        }
        Ok(Self {
            dictionary_kind,
            names,
            blocks,
        })
    }

    pub(crate) fn names(&self) -> &[Vec<u8>] {
        &self.names
    }

    pub(crate) fn ordinal(&self, name: &[u8]) -> Option<u16> {
        self.names
            .iter()
            .position(|existing| existing == name)
            .and_then(|position| u16::try_from(position).ok())
    }

    /// Returns the dictionary ordinal of `name`, appending it when absent (EXP-0297/0299).
    pub(crate) fn intern(
        &mut self,
        name: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<u16, ColumnPropertyError> {
        if let Some(ordinal) = self.ordinal(name) {
            return Ok(ordinal);
        }
        let ordinal =
            u16::try_from(self.names.len()).map_err(|_| invalid("property dictionary capacity"))?;
        reserve(&mut self.names, 1, budget)?;
        self.names.push(owned(name, budget)?);
        Ok(ordinal)
    }

    pub(crate) fn blocks(&self) -> &[Block] {
        &self.blocks
    }

    pub(crate) fn block_at(&mut self, position: usize) -> Result<&mut Block, ColumnPropertyError> {
        self.blocks
            .get_mut(position)
            .ok_or(invalid("property block position"))
    }

    pub(crate) fn block_mut(&mut self, kind: u16, name: &[u8]) -> Option<&mut Block> {
        self.blocks
            .iter_mut()
            .find(|block| block.kind == kind && block.name == name)
    }

    /// Returns the named block, appending an empty one when absent.
    pub(crate) fn ensure_block(
        &mut self,
        kind: u16,
        name: &[u8],
        budget: &mut ResourceBudget,
    ) -> Result<&mut Block, ColumnPropertyError> {
        let position = match self
            .blocks
            .iter()
            .position(|block| block.kind == kind && block.name == name)
        {
            Some(position) => position,
            None => {
                let block = Block::new(kind, name, budget)?;
                self.push(block, budget)?;
                self.blocks.len() - 1
            }
        };
        Ok(&mut self.blocks[position])
    }

    pub(crate) fn push(
        &mut self,
        block: Block,
        budget: &mut ResourceBudget,
    ) -> Result<(), ColumnPropertyError> {
        reserve(&mut self.blocks, 1, budget)?;
        self.blocks.push(block);
        Ok(())
    }

    pub(crate) fn insert(
        &mut self,
        position: usize,
        block: Block,
        budget: &mut ResourceBudget,
    ) -> Result<(), ColumnPropertyError> {
        reserve(&mut self.blocks, 1, budget)?;
        self.blocks.insert(position.min(self.blocks.len()), block);
        Ok(())
    }

    pub(crate) fn blocks_mut(&mut self) -> impl Iterator<Item = &mut Block> {
        self.blocks.iter_mut()
    }

    pub(crate) fn retain_blocks(&mut self, keep: impl FnMut(&Block) -> bool) {
        self.blocks.retain(keep);
    }

    pub(crate) fn len(&self) -> usize {
        DICTIONARY_HEADER
            + self.names.iter().map(|name| 2 + name.len()).sum::<usize>()
            + self.blocks.iter().map(Block::len).sum::<usize>()
    }

    pub(crate) fn encode(&self, budget: &mut ResourceBudget) -> Result<Vec<u8>, Error> {
        let mut output = Vec::new();
        reserve(&mut output, self.len(), budget)?;
        output.resize(self.len(), 0);
        self.write(&mut BinaryWriter::new(&mut output, budget)?)?;
        Ok(output)
    }

    pub(crate) fn write(&self, writer: &mut BinaryWriter<'_, '_>) -> Result<(), Error> {
        let too_long = Error::Arithmetic {
            operation: "property blob length",
        };
        writer.write_exact(SIGNATURE)?;
        let dictionary =
            DICTIONARY_HEADER - 4 + self.names.iter().map(|name| 2 + name.len()).sum::<usize>();
        writer.write_u32_le(u32::try_from(dictionary).map_err(|_| too_long.clone())?)?;
        writer.write_u16_le(self.dictionary_kind)?;
        for name in &self.names {
            writer.write_u16_le(u16::try_from(name.len()).map_err(|_| too_long.clone())?)?;
            writer.write_exact(name)?;
        }
        for block in &self.blocks {
            writer.write_u32_le(u32::try_from(block.len()).map_err(|_| too_long.clone())?)?;
            writer.write_u16_le(block.kind)?;
            writer.write_u32_le(block.name.len() as u32 + 6)?;
            writer.write_u16_le(block.name.len() as u16)?;
            writer.write_exact(&block.name)?;
            for record in &block.records {
                writer.write_u16_le(u16::try_from(record.len()).map_err(|_| too_long.clone())?)?;
                writer.write_u8(record.flag)?;
                writer.write_u8(record.kind)?;
                writer.write_u16_le(record.name)?;
                writer.write_exact(&record.data)?;
            }
        }
        Ok(())
    }
}

fn u16_at(data: &[u8], offset: usize) -> Option<u16> {
    Some(u16::from_le_bytes(
        *data.get(offset..offset.checked_add(2)?)?.first_chunk()?,
    ))
}

fn u32_at(data: &[u8], offset: usize) -> Option<u32> {
    Some(u32::from_le_bytes(
        *data.get(offset..offset.checked_add(4)?)?.first_chunk()?,
    ))
}
