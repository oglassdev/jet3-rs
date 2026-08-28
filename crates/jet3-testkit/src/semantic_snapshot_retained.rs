//! Budget-aware retention of snapshot state.
//!
//! Reader cursors expose their operation-wide `ResourceBudget` narrowly so
//! every retained reserve is charged against the live authority immediately.
//! No ceiling or usage counter is mirrored here.

use std::mem::size_of;

use jet3::{ByteCount, Error, PageNumber, ResourceBudget};

use super::SemanticSnapshotError;
use crate::{CoverageBranches, HexString, PropertyMap, TypedValue};

pub(super) struct RetainedLedger;

pub(super) struct RetainedPropertyBatch {
    entries: Vec<(String, TypedValue)>,
}

impl RetainedPropertyBatch {
    pub(super) const fn new() -> Self {
        Self {
            entries: Vec::new(),
        }
    }

    pub(super) fn reserve(
        &mut self,
        budget: &mut ResourceBudget,
        ledger: &mut RetainedLedger,
        additional: usize,
    ) -> Result<(), SemanticSnapshotError> {
        let required = self
            .entries
            .len()
            .checked_add(additional)
            .ok_or_else(allocation_overflow)?;
        if required <= self.entries.capacity() {
            return Ok(());
        }
        let doubled = self
            .entries
            .capacity()
            .checked_mul(2)
            .ok_or_else(allocation_overflow)?;
        let target = required.max(doubled).max(1);
        let relocation_work = u64::try_from(self.entries.len()).map_err(|_| {
            SemanticSnapshotError::Resource(Error::IntegerConversion {
                value: self.entries.len() as u128,
                target: "u64",
            })
        })?;
        budget
            .charge_work_units(relocation_work)
            .map_err(SemanticSnapshotError::Resource)?;
        let additional_capacity = target
            .checked_sub(self.entries.len())
            .ok_or_else(allocation_overflow)?;
        ledger.reserve_vec(budget, &mut self.entries, additional_capacity)
    }

    pub(super) fn push(
        &mut self,
        budget: &mut ResourceBudget,
        ledger: &mut RetainedLedger,
        key: String,
        value: TypedValue,
    ) -> Result<(), SemanticSnapshotError> {
        self.reserve(budget, ledger, 1)?;
        self.entries.push((key, value));
        Ok(())
    }

    pub(super) fn finish(
        mut self,
        budget: &mut ResourceBudget,
    ) -> Result<PropertyMap, SemanticSnapshotError> {
        budget
            .charge_work_units(canonical_property_order_work(self.entries.len())?)
            .map_err(SemanticSnapshotError::Resource)?;
        self.entries
            .sort_unstable_by(|left, right| left.0.cmp(&right.0));
        PropertyMap::from_sorted_unique_entries(self.entries).ok_or_else(|| {
            crate::SemanticProtocolError::InvalidModel {
                path: "$.producer_extensions".to_owned(),
                reason: "keys must be unique",
            }
            .into()
        })
    }
}

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
        let mut owned = String::new();
        self.reserve_string(budget, &mut owned, raw.len())?;
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
        let mut owned = String::new();
        self.reserve_string(budget, &mut owned, value.len())?;
        owned.push_str(value);
        Ok(owned)
    }

    /// Appends decoded text after charging and reserving its exact byte length.
    #[cfg(test)]
    pub(super) fn append_text(
        &mut self,
        budget: &mut ResourceBudget,
        output: &mut String,
        value: &str,
    ) -> Result<(), SemanticSnapshotError> {
        self.reserve_string(budget, output, value.len())?;
        output.push_str(value);
        Ok(())
    }

    /// Precharges and reserves the exact additional string capacity required.
    pub(super) fn reserve_string(
        &mut self,
        budget: &mut ResourceBudget,
        output: &mut String,
        additional: usize,
    ) -> Result<(), SemanticSnapshotError> {
        let required = output
            .len()
            .checked_add(additional)
            .ok_or_else(allocation_overflow)?;
        let growth = required.saturating_sub(output.capacity());
        if growth == 0 {
            return Ok(());
        }
        self.charge(budget, growth)?;
        output
            .try_reserve_exact(required - output.len())
            .map_err(|_| out_of_memory())?;
        debug_assert!(output.capacity() >= required);
        Ok(())
    }

    /// Retains bytes as lowercase hexadecimal text.
    pub(super) fn hex(
        &mut self,
        budget: &mut ResourceBudget,
        bytes: &[u8],
    ) -> Result<HexString, SemanticSnapshotError> {
        const DIGITS: &[u8; 16] = b"0123456789abcdef";
        let length = bytes.len().checked_mul(2).ok_or_else(allocation_overflow)?;
        let mut output = String::new();
        self.reserve_string(budget, &mut output, length)?;
        for byte in bytes {
            output.push(char::from(DIGITS[usize::from(byte >> 4)]));
            output.push(char::from(DIGITS[usize::from(byte & 0x0f)]));
        }
        Ok(HexString::new(output)?)
    }

    /// Retains one element in a vector after charging and reserving for it.
    pub(super) fn push<T>(
        &mut self,
        budget: &mut ResourceBudget,
        vector: &mut Vec<T>,
        item: T,
    ) -> Result<(), SemanticSnapshotError> {
        self.reserve_vec(budget, vector, 1)?;
        vector.push(item);
        Ok(())
    }

    /// Precharges and reserves the exact additional vector capacity required.
    pub(super) fn reserve_vec<T>(
        &mut self,
        budget: &mut ResourceBudget,
        vector: &mut Vec<T>,
        additional: usize,
    ) -> Result<(), SemanticSnapshotError> {
        if size_of::<T>() == 0 {
            return Ok(());
        }
        let required = vector
            .len()
            .checked_add(additional)
            .ok_or_else(allocation_overflow)?;
        let elements = required.saturating_sub(vector.capacity());
        if elements == 0 {
            return Ok(());
        }
        let bytes = elements
            .checked_mul(size_of::<T>())
            .ok_or_else(allocation_overflow)?;
        self.charge(budget, bytes)?;
        vector
            .try_reserve_exact(required - vector.len())
            .map_err(|_| out_of_memory())?;
        debug_assert!(vector.capacity() >= required);
        Ok(())
    }

    /// Precharges exact contiguous property capacity before keys are built.
    pub(super) fn reserve_properties(
        &mut self,
        budget: &mut ResourceBudget,
        map: &mut PropertyMap,
        additional: usize,
    ) -> Result<(), SemanticSnapshotError> {
        let required = map
            .len()
            .checked_add(additional)
            .ok_or_else(allocation_overflow)?;
        let elements = required.saturating_sub(map.capacity());
        if elements == 0 {
            return Ok(());
        }
        let bytes = elements
            .checked_mul(size_of::<(String, TypedValue)>())
            .ok_or_else(allocation_overflow)?;
        self.charge(budget, bytes)?;
        map.try_reserve_exact(required - map.len())
            .map_err(|_| out_of_memory())?;
        debug_assert!(map.capacity() >= required);
        Ok(())
    }

    /// Retains one unique coverage branch in chargeable contiguous storage.
    pub(super) fn branch(
        &mut self,
        budget: &mut ResourceBudget,
        branches: &mut CoverageBranches,
        value: &'static str,
    ) -> Result<(), SemanticSnapshotError> {
        if branches.contains(value) {
            return Ok(());
        }
        let required = branches
            .len()
            .checked_add(1)
            .ok_or_else(allocation_overflow)?;
        let elements = required.saturating_sub(branches.capacity());
        if elements != 0 {
            let bytes = elements
                .checked_mul(size_of::<String>())
                .ok_or_else(allocation_overflow)?;
            self.charge(budget, bytes)?;
            branches
                .try_reserve_exact(required - branches.len())
                .map_err(|_| out_of_memory())?;
        }
        let value = self.text(budget, value)?;
        let inserted = branches.insert(value);
        debug_assert!(inserted);
        Ok(())
    }

    /// Retains one property in the map's chargeable contiguous storage.
    pub(super) fn insert(
        &mut self,
        budget: &mut ResourceBudget,
        map: &mut PropertyMap,
        key: String,
        value: TypedValue,
    ) -> Result<(), SemanticSnapshotError> {
        let map_entries = u64::try_from(map.len()).map_err(|_| {
            SemanticSnapshotError::Resource(Error::IntegerConversion {
                value: map.len() as u128,
                target: "u64",
            })
        })?;
        let map_work = map_entries
            .checked_mul(4)
            .and_then(|work| work.checked_add(1))
            .ok_or(SemanticSnapshotError::Resource(Error::Arithmetic {
                operation: "size retained property insertion work",
            }))?;
        budget
            .charge_work_units(map_work)
            .map_err(SemanticSnapshotError::Resource)?;
        let present = map.get(&key).is_some();
        if !present {
            self.reserve_properties(budget, map, 1)?;
        }
        map.insert(key, value);
        Ok(())
    }
}

fn canonical_property_order_work(entry_count: usize) -> Result<u64, SemanticSnapshotError> {
    let entries = u64::try_from(entry_count).map_err(|_| {
        SemanticSnapshotError::Resource(Error::IntegerConversion {
            value: entry_count as u128,
            target: "u64",
        })
    })?;
    let levels = if entries <= 1 {
        0
    } else {
        u64::from(u64::BITS - (entries - 1).leading_zeros())
    };
    let passes = levels
        .checked_add(1)
        .ok_or(SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "size retained property ordering work",
        }))?;
    entries
        .checked_mul(passes)
        .ok_or(SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "size retained property ordering work",
        }))
}

fn allocation_overflow() -> SemanticSnapshotError {
    SemanticSnapshotError::Resource(Error::Arithmetic {
        operation: "size retained snapshot allocation",
    })
}

fn out_of_memory() -> SemanticSnapshotError {
    SemanticSnapshotError::Resource(Error::Io {
        operation: "reserve retained snapshot state",
        kind: std::io::ErrorKind::OutOfMemory,
    })
}

#[cfg(test)]
mod tests {
    use super::{RetainedLedger, RetainedPropertyBatch};
    use jet3::{ByteCount, ReadLimits, ResourceBudget, ResourceLimits};
    use std::mem::size_of;

    use crate::{PropertyMap, TypedValue};

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

    #[test]
    fn string_growth_charges_only_the_actual_capacity_delta()
    -> Result<(), Box<dyn std::error::Error>> {
        let mut exact = budget(5);
        let mut ledger = RetainedLedger::new();
        let mut destination = String::new();
        ledger.reserve_string(&mut exact, &mut destination, 4)?;
        destination.push_str("ab");
        ledger.append_text(&mut exact, &mut destination, "cde")?;
        assert_eq!(destination, "abcde");
        assert_eq!(destination.capacity(), 5);
        assert_eq!(exact.allocation_bytes(), ByteCount::new(5));

        let mut rejected = budget(4);
        let mut destination = String::new();
        ledger.reserve_string(&mut rejected, &mut destination, 4)?;
        destination.push_str("ab");
        assert!(
            ledger
                .append_text(&mut rejected, &mut destination, "cde")
                .is_err()
        );
        assert_eq!(destination, "ab");
        assert_eq!(destination.capacity(), 4);
        Ok(())
    }

    #[test]
    fn vector_capacity_growth_matches_the_precharged_bytes()
    -> Result<(), Box<dyn std::error::Error>> {
        let item_bytes = size_of::<u64>() as u64;
        let mut exact = budget(item_bytes);
        let mut ledger = RetainedLedger::new();
        let mut values = Vec::new();
        ledger.push(&mut exact, &mut values, 7_u64)?;
        assert_eq!(values.capacity(), 1);
        assert_eq!(exact.allocation_bytes(), ByteCount::new(item_bytes));

        let mut rejected = budget(item_bytes - 1);
        let mut values = Vec::new();
        assert!(ledger.push(&mut rejected, &mut values, 7_u64).is_err());
        assert_eq!(values.capacity(), 0);
        assert_eq!(rejected.allocation_bytes(), ByteCount::new(0));
        Ok(())
    }

    #[test]
    fn property_capacity_has_an_exact_success_and_failure_boundary()
    -> Result<(), Box<dyn std::error::Error>> {
        let entry_bytes = size_of::<(String, TypedValue)>() as u64;
        let value = || TypedValue::Null { raw_hex: None };
        let mut exact = budget(entry_bytes);
        let mut ledger = RetainedLedger::new();
        let mut properties = PropertyMap::new();
        ledger.insert(&mut exact, &mut properties, String::new(), value())?;
        assert_eq!(properties.capacity(), 1);
        assert_eq!(exact.allocation_bytes(), ByteCount::new(entry_bytes));

        let mut rejected = budget(entry_bytes - 1);
        let mut properties = PropertyMap::new();
        assert!(
            ledger
                .insert(&mut rejected, &mut properties, String::new(), value())
                .is_err()
        );
        assert_eq!(properties.capacity(), 0);
        assert!(properties.is_empty());
        Ok(())
    }

    fn finish_batch(
        count: usize,
        limits: ResourceLimits,
    ) -> (
        Result<PropertyMap, super::SemanticSnapshotError>,
        ResourceBudget,
    ) {
        let mut budget = ResourceBudget::new(limits);
        let mut ledger = RetainedLedger::new();
        let mut batch = RetainedPropertyBatch::new();
        for index in (0..count).rev() {
            let result = batch.push(
                &mut budget,
                &mut ledger,
                format!("/tables/{index:05}/rows/0/values/Memo/header"),
                TypedValue::Null { raw_hex: None },
            );
            if let Err(error) = result {
                return (Err(error), budget);
            }
        }
        (batch.finish(&mut budget), budget)
    }

    #[test]
    fn producer_extension_batch_has_an_exact_total_work_boundary()
    -> Result<(), Box<dyn std::error::Error>> {
        let (measured, measured_budget) = finish_batch(1_024, ResourceLimits::default());
        let measured = measured?;
        assert_eq!(measured.len(), 1_024);
        assert!(measured.keys().is_sorted());
        let required = measured_budget.total_work_units();

        let (exact, exact_budget) = finish_batch(
            1_024,
            ResourceLimits::default().with_max_total_work_units(required),
        );
        exact?;
        assert_eq!(exact_budget.total_work_units(), required);

        let (one_below, _) = finish_batch(
            1_024,
            ResourceLimits::default().with_max_total_work_units(required - 1),
        );
        assert!(one_below.is_err());
        Ok(())
    }

    #[test]
    fn producer_extension_batch_work_grows_subquadratically()
    -> Result<(), Box<dyn std::error::Error>> {
        let (small, small_budget) = finish_batch(4_096, ResourceLimits::default());
        let (doubled, doubled_budget) = finish_batch(8_192, ResourceLimits::default());
        assert!(small.is_ok());
        assert!(doubled.is_ok());
        let upper_bound = small_budget
            .total_work_units()
            .checked_mul(3)
            .ok_or("bounded test work")?;
        assert!(doubled_budget.total_work_units() < upper_bound);
        Ok(())
    }

    #[test]
    fn producer_extension_batch_rejects_duplicate_keys() -> Result<(), Box<dyn std::error::Error>> {
        let mut budget = ResourceBudget::new(ResourceLimits::default());
        let mut ledger = RetainedLedger::new();
        let mut batch = RetainedPropertyBatch::new();
        for marker in 0..2 {
            batch.push(
                &mut budget,
                &mut ledger,
                "/tables/0/rows/0/values/Memo/header".to_owned(),
                TypedValue::Byte {
                    value: marker,
                    raw_hex: None,
                },
            )?;
        }
        assert!(batch.finish(&mut budget).is_err());
        Ok(())
    }
}
