//! Budget-aware retention of snapshot state.
//!
//! Reader cursors hold the operation `ResourceBudget` exclusively while they
//! stream, so the adapter cannot charge the same budget for every object it
//! retains at the moment of allocation. [`RetainedLedger`] mirrors the
//! budget's allocation ceiling instead: it charges before each retained
//! allocation against the ceiling minus the budget's last observed usage, and
//! [`RetainedLedger::sync`] transfers those charges into the real budget at
//! every point where no cursor holds it, so reader-side allocations that
//! occurred in between are reconciled and rejected fail-closed.

use std::mem::size_of;

use jet3::{ByteCount, Error, PageNumber, ResourceBudget, ResourceLimitKind};

use super::SemanticSnapshotError;
use crate::{HexString, PropertyMap, TypedValue};

/// Approximate per-entry overhead of one ordered-map node beyond its payload.
const MAP_NODE_OVERHEAD: usize = 32;

pub(super) struct RetainedLedger {
    ceiling: u64,
    used: u64,
    unsynced: u64,
}

impl RetainedLedger {
    pub(super) fn new(budget: &ResourceBudget) -> Self {
        Self {
            ceiling: budget.limits().max_allocation_bytes().get(),
            used: budget.allocation_bytes().get(),
            unsynced: 0,
        }
    }

    /// Charges `bytes` before the caller allocates them.
    pub(super) fn charge(&mut self, bytes: usize) -> Result<(), SemanticSnapshotError> {
        let bytes = u64::try_from(bytes).map_err(|_| {
            SemanticSnapshotError::Resource(Error::IntegerConversion {
                value: bytes as u128,
                target: "u64",
            })
        })?;
        let requested = self
            .used
            .checked_add(bytes)
            .ok_or(SemanticSnapshotError::Resource(Error::Arithmetic {
                operation: "accumulate retained snapshot bytes",
            }))?;
        if requested > self.ceiling {
            return Err(SemanticSnapshotError::Resource(
                Error::ResourceLimitExceeded {
                    kind: ResourceLimitKind::AllocationBytes,
                    requested,
                    maximum: self.ceiling,
                },
            ));
        }
        self.used = requested;
        self.unsynced = self.unsynced.saturating_add(bytes);
        Ok(())
    }

    /// Transfers pending charges into `budget` and re-reads its usage.
    pub(super) fn sync(
        &mut self,
        budget: &mut ResourceBudget,
    ) -> Result<(), SemanticSnapshotError> {
        budget
            .charge_allocation(ByteCount::new(self.unsynced))
            .map_err(SemanticSnapshotError::Resource)?;
        self.unsynced = 0;
        self.used = budget.allocation_bytes().get();
        Ok(())
    }

    /// Retains an ASCII definition or catalog name as an owned string.
    pub(super) fn ascii_name(
        &mut self,
        raw: &[u8],
        table: Option<PageNumber>,
    ) -> Result<String, SemanticSnapshotError> {
        if !raw.is_ascii() {
            return Err(SemanticSnapshotError::NonAsciiName { table });
        }
        self.charge(raw.len())?;
        let mut owned = String::new();
        owned
            .try_reserve_exact(raw.len())
            .map_err(|_| out_of_memory())?;
        for byte in raw {
            owned.push(char::from(*byte));
        }
        Ok(owned)
    }

    /// Retains a copy of already decoded text.
    pub(super) fn text(&mut self, value: &str) -> Result<String, SemanticSnapshotError> {
        self.charge(value.len())?;
        let mut owned = String::new();
        owned
            .try_reserve_exact(value.len())
            .map_err(|_| out_of_memory())?;
        owned.push_str(value);
        Ok(owned)
    }

    /// Appends decoded text after charging and reserving its exact byte length.
    pub(super) fn append_text(
        &mut self,
        output: &mut String,
        value: &str,
    ) -> Result<(), SemanticSnapshotError> {
        self.charge(value.len())?;
        output
            .try_reserve(value.len())
            .map_err(|_| out_of_memory())?;
        output.push_str(value);
        Ok(())
    }

    /// Appends lowercase hexadecimal text for sourced bytes.
    pub(super) fn append_hex(
        &mut self,
        output: &mut String,
        bytes: &[u8],
    ) -> Result<(), SemanticSnapshotError> {
        const DIGITS: &[u8; 16] = b"0123456789abcdef";
        let length = bytes.len().saturating_mul(2);
        self.charge(length)?;
        output.try_reserve(length).map_err(|_| out_of_memory())?;
        for byte in bytes {
            output.push(char::from(DIGITS[usize::from(byte >> 4)]));
            output.push(char::from(DIGITS[usize::from(byte & 0x0f)]));
        }
        Ok(())
    }

    /// Retains bytes as lowercase hexadecimal text.
    pub(super) fn hex(&mut self, bytes: &[u8]) -> Result<HexString, SemanticSnapshotError> {
        self.charge(bytes.len().saturating_mul(2))?;
        Ok(HexString::from_bytes(bytes))
    }

    /// Retains one element in a vector after charging and reserving for it.
    pub(super) fn push<T>(
        &mut self,
        vector: &mut Vec<T>,
        item: T,
    ) -> Result<(), SemanticSnapshotError> {
        self.charge(size_of::<T>())?;
        vector.try_reserve(1).map_err(|_| out_of_memory())?;
        vector.push(item);
        Ok(())
    }

    /// Retains one property after charging for its map node.
    pub(super) fn insert(
        &mut self,
        map: &mut PropertyMap,
        key: String,
        value: TypedValue,
    ) -> Result<(), SemanticSnapshotError> {
        self.charge(size_of::<String>() + size_of::<TypedValue>() + MAP_NODE_OVERHEAD)?;
        map.insert(key, value);
        Ok(())
    }
}

fn out_of_memory() -> SemanticSnapshotError {
    SemanticSnapshotError::Resource(Error::Io {
        operation: "reserve retained snapshot state",
        kind: std::io::ErrorKind::OutOfMemory,
    })
}
