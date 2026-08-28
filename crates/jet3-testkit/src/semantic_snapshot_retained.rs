//! Budget-aware retention of snapshot state.
//!
//! Reader cursors expose their operation-wide `ResourceBudget` narrowly so
//! every retained reserve is charged against the live authority immediately.
//! No ceiling or usage counter is mirrored here.

use std::mem::size_of;

use jet3::{ByteCount, Error, PageNumber, ResourceBudget};

use super::SemanticSnapshotError;
use crate::{HexString, PropertyMap, TypedValue};

/// Approximate per-entry overhead of one ordered-map node beyond its payload.
const MAP_NODE_OVERHEAD: usize = 32;

pub(super) struct RetainedLedger;

impl RetainedLedger {
    pub(super) const fn new() -> Self {
        Self
    }

    /// Charges `bytes` before the caller allocates them.
    pub(super) fn charge(
        &mut self,
        budget: &mut ResourceBudget,
        bytes: usize,
    ) -> Result<(), SemanticSnapshotError> {
        let bytes = u64::try_from(bytes).map_err(|_| {
            SemanticSnapshotError::Resource(Error::IntegerConversion {
                value: bytes as u128,
                target: "u64",
            })
        })?;
        budget
            .charge_allocation(ByteCount::new(bytes))
            .map_err(SemanticSnapshotError::Resource)
    }

    /// Retains an ASCII definition or catalog name as an owned string.
    pub(super) fn ascii_name(
        &mut self,
        budget: &mut ResourceBudget,
        raw: &[u8],
        table: Option<PageNumber>,
    ) -> Result<String, SemanticSnapshotError> {
        if !raw.is_ascii() {
            return Err(SemanticSnapshotError::NonAsciiName { table });
        }
        self.charge(budget, raw.len())?;
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
    pub(super) fn text(
        &mut self,
        budget: &mut ResourceBudget,
        value: &str,
    ) -> Result<String, SemanticSnapshotError> {
        self.charge(budget, value.len())?;
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
        budget: &mut ResourceBudget,
        output: &mut String,
        value: &str,
    ) -> Result<(), SemanticSnapshotError> {
        self.charge(budget, value.len())?;
        output
            .try_reserve(value.len())
            .map_err(|_| out_of_memory())?;
        output.push_str(value);
        Ok(())
    }

    /// Retains bytes as lowercase hexadecimal text.
    pub(super) fn hex(
        &mut self,
        budget: &mut ResourceBudget,
        bytes: &[u8],
    ) -> Result<HexString, SemanticSnapshotError> {
        self.charge(budget, bytes.len().saturating_mul(2))?;
        Ok(HexString::from_bytes(bytes))
    }

    /// Retains one element in a vector after charging and reserving for it.
    pub(super) fn push<T>(
        &mut self,
        budget: &mut ResourceBudget,
        vector: &mut Vec<T>,
        item: T,
    ) -> Result<(), SemanticSnapshotError> {
        self.charge(budget, size_of::<T>())?;
        vector.try_reserve(1).map_err(|_| out_of_memory())?;
        vector.push(item);
        Ok(())
    }

    /// Retains one property after charging for its map node.
    pub(super) fn insert(
        &mut self,
        budget: &mut ResourceBudget,
        map: &mut PropertyMap,
        key: String,
        value: TypedValue,
    ) -> Result<(), SemanticSnapshotError> {
        self.charge(
            budget,
            size_of::<String>() + size_of::<TypedValue>() + MAP_NODE_OVERHEAD,
        )?;
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

#[cfg(test)]
mod tests {
    use super::RetainedLedger;
    use jet3::{ByteCount, ReadLimits, ResourceBudget, ResourceLimits};

    fn budget(maximum: u64) -> ResourceBudget {
        ResourceBudget::new(
            ResourceLimits::new(ReadLimits::default())
                .with_max_allocation_bytes(ByteCount::new(maximum)),
        )
    }

    #[test]
    fn live_reader_charges_are_seen_before_retained_growth()
    -> Result<(), Box<dyn std::error::Error>> {
        let mut budget = budget(10);
        let mut ledger = RetainedLedger::new();
        ledger.charge(&mut budget, 4)?;
        budget.charge_allocation(ByteCount::new(5))?;
        let mut destination = String::new();
        let capacity = destination.capacity();
        assert!(
            ledger
                .append_text(&mut budget, &mut destination, "xx")
                .is_err()
        );
        assert_eq!(destination.capacity(), capacity);
        assert!(destination.is_empty());
        assert_eq!(budget.allocation_bytes(), ByteCount::new(9));
        Ok(())
    }

    #[test]
    fn exact_live_boundary_succeeds() -> Result<(), Box<dyn std::error::Error>> {
        let mut budget = budget(11);
        let mut ledger = RetainedLedger::new();
        ledger.charge(&mut budget, 4)?;
        budget.charge_allocation(ByteCount::new(5))?;
        let mut destination = String::new();
        ledger.append_text(&mut budget, &mut destination, "xx")?;
        assert_eq!(destination, "xx");
        assert_eq!(budget.allocation_bytes(), ByteCount::new(11));
        Ok(())
    }
}
