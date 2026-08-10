//! Operation-wide resource limits and cumulative accounting.

use crate::limits::{ReadBudget, ReadLimits};
use crate::{ByteCount, Error, ResourceLimitKind};

/// Default cumulative allocation ceiling: 256 MiB.
pub const DEFAULT_MAX_ALLOCATION_BYTES: ByteCount = ByteCount::new(256 * 1024 * 1024);
/// Default maximum size of one decoded value: 16 MiB.
pub const DEFAULT_MAX_DECODED_VALUE_BYTES: ByteCount = ByteCount::new(16 * 1024 * 1024);
/// Default cumulative decoded-value ceiling: 256 MiB.
pub const DEFAULT_MAX_TOTAL_DECODED_BYTES: ByteCount = ByteCount::new(256 * 1024 * 1024);
/// Default cumulative encoded-output ceiling: 256 MiB.
pub const DEFAULT_MAX_ENCODED_BYTES: ByteCount = ByteCount::new(256 * 1024 * 1024);
/// Default cumulative item/count work ceiling: ten million.
pub const DEFAULT_MAX_ITEM_WORK: u64 = 10_000_000;
/// Default cumulative page-visit ceiling: ten million.
pub const DEFAULT_MAX_PAGE_VISITS: u64 = 10_000_000;
/// Default maximum depth of one followed chain: 4,096 links.
pub const DEFAULT_MAX_CHAIN_DEPTH: u64 = 4_096;
/// Default aggregate non-I/O work ceiling: one billion units.
pub const DEFAULT_MAX_TOTAL_WORK_UNITS: u64 = 1_000_000_000;

/// Immutable policy for one complete untrusted-input operation.
///
/// [`ReadLimits`] remains the byte-I/O sub-policy. The other ceilings bound
/// memory reservations, decoded and encoded output, count-driven loops, page
/// traversal, chain following, and aggregate non-I/O work. Defaults are
/// format-neutral:
///
/// - 256 MiB cumulative allocation prevents input from driving process-scale
///   memory growth;
/// - 16 MiB per decoded value and 256 MiB each for cumulative decoded and
///   encoded output bound transformation work;
/// - ten million items and page visits permit large workloads while bounding
///   count-controlled loops;
/// - 4,096 chain links reject pathological or cyclic traversals promptly; and
/// - one billion aggregate work units provide a final ceiling across charged
///   allocation, decoding, item, page, and explicit algorithm work.
///
/// Applications should lower these defaults for latency-sensitive or
/// memory-constrained environments. Raising them requires accepting
/// proportionally greater CPU or memory exposure.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ResourceLimits {
    read: ReadLimits,
    max_allocation_bytes: ByteCount,
    max_decoded_value_bytes: ByteCount,
    max_total_decoded_bytes: ByteCount,
    max_encoded_bytes: ByteCount,
    max_item_work: u64,
    max_page_visits: u64,
    max_chain_depth: u64,
    max_total_work_units: u64,
}

impl ResourceLimits {
    /// Creates a policy with explicit read limits and documented defaults for
    /// all other dimensions.
    #[must_use]
    pub const fn new(read: ReadLimits) -> Self {
        Self {
            read,
            max_allocation_bytes: DEFAULT_MAX_ALLOCATION_BYTES,
            max_decoded_value_bytes: DEFAULT_MAX_DECODED_VALUE_BYTES,
            max_total_decoded_bytes: DEFAULT_MAX_TOTAL_DECODED_BYTES,
            max_encoded_bytes: DEFAULT_MAX_ENCODED_BYTES,
            max_item_work: DEFAULT_MAX_ITEM_WORK,
            max_page_visits: DEFAULT_MAX_PAGE_VISITS,
            max_chain_depth: DEFAULT_MAX_CHAIN_DEPTH,
            max_total_work_units: DEFAULT_MAX_TOTAL_WORK_UNITS,
        }
    }

    /// Replaces the cumulative allocation ceiling.
    #[must_use]
    pub const fn with_max_allocation_bytes(mut self, maximum: ByteCount) -> Self {
        self.max_allocation_bytes = maximum;
        self
    }

    /// Replaces the maximum size of one decoded value.
    #[must_use]
    pub const fn with_max_decoded_value_bytes(mut self, maximum: ByteCount) -> Self {
        self.max_decoded_value_bytes = maximum;
        self
    }

    /// Replaces the cumulative decoded-value ceiling.
    #[must_use]
    pub const fn with_max_total_decoded_bytes(mut self, maximum: ByteCount) -> Self {
        self.max_total_decoded_bytes = maximum;
        self
    }

    /// Replaces the cumulative encoded-output ceiling.
    #[must_use]
    pub const fn with_max_encoded_bytes(mut self, maximum: ByteCount) -> Self {
        self.max_encoded_bytes = maximum;
        self
    }

    /// Replaces the cumulative item/count work ceiling.
    #[must_use]
    pub const fn with_max_item_work(mut self, maximum: u64) -> Self {
        self.max_item_work = maximum;
        self
    }

    /// Replaces the cumulative page-visit ceiling.
    #[must_use]
    pub const fn with_max_page_visits(mut self, maximum: u64) -> Self {
        self.max_page_visits = maximum;
        self
    }

    /// Replaces the maximum permitted depth of one chain.
    #[must_use]
    pub const fn with_max_chain_depth(mut self, maximum: u64) -> Self {
        self.max_chain_depth = maximum;
        self
    }

    /// Replaces the cumulative aggregate work ceiling.
    #[must_use]
    pub const fn with_max_total_work_units(mut self, maximum: u64) -> Self {
        self.max_total_work_units = maximum;
        self
    }

    /// Returns the byte-I/O sub-policy.
    #[must_use]
    pub const fn read(self) -> ReadLimits {
        self.read
    }

    /// Returns the cumulative allocation ceiling.
    #[must_use]
    pub const fn max_allocation_bytes(self) -> ByteCount {
        self.max_allocation_bytes
    }

    /// Returns the maximum size of one decoded value.
    #[must_use]
    pub const fn max_decoded_value_bytes(self) -> ByteCount {
        self.max_decoded_value_bytes
    }

    /// Returns the cumulative decoded-value ceiling.
    #[must_use]
    pub const fn max_total_decoded_bytes(self) -> ByteCount {
        self.max_total_decoded_bytes
    }

    /// Returns the cumulative encoded-output ceiling.
    #[must_use]
    pub const fn max_encoded_bytes(self) -> ByteCount {
        self.max_encoded_bytes
    }

    /// Returns the cumulative item/count work ceiling.
    #[must_use]
    pub const fn max_item_work(self) -> u64 {
        self.max_item_work
    }

    /// Returns the cumulative page-visit ceiling.
    #[must_use]
    pub const fn max_page_visits(self) -> u64 {
        self.max_page_visits
    }

    /// Returns the maximum permitted depth of one chain.
    #[must_use]
    pub const fn max_chain_depth(self) -> u64 {
        self.max_chain_depth
    }

    /// Returns the cumulative aggregate work ceiling.
    #[must_use]
    pub const fn max_total_work_units(self) -> u64 {
        self.max_total_work_units
    }
}

impl Default for ResourceLimits {
    fn default() -> Self {
        Self::new(ReadLimits::default())
    }
}

/// Non-copy cumulative accounting for one complete operation.
///
/// Create exactly one budget at the public operation boundary and pass mutable
/// borrows through all participating parsers, sources, and writers. Successful
/// dimension-specific charges also consume the same number of aggregate work
/// units. Encoded bytes include rewrites after seeking. Read bytes remain
/// governed by the embedded [`ReadBudget`] and are not charged a second time as
/// aggregate non-I/O work.
#[derive(Debug)]
pub struct ResourceBudget {
    limits: ResourceLimits,
    read: ReadBudget,
    allocation_bytes: ByteCount,
    decoded_bytes: ByteCount,
    encoded_bytes: ByteCount,
    item_work: u64,
    page_visits: u64,
    total_work_units: u64,
}

impl ResourceBudget {
    /// Starts an unused operation budget.
    #[must_use]
    pub const fn new(limits: ResourceLimits) -> Self {
        Self {
            limits,
            read: ReadBudget::new(limits.read()),
            allocation_bytes: ByteCount::new(0),
            decoded_bytes: ByteCount::new(0),
            encoded_bytes: ByteCount::new(0),
            item_work: 0,
            page_visits: 0,
            total_work_units: 0,
        }
    }

    /// Returns the immutable policy.
    #[must_use]
    pub const fn limits(&self) -> ResourceLimits {
        self.limits
    }

    /// Borrows the operation's persistent byte-I/O budget.
    pub const fn read_budget(&mut self) -> &mut ReadBudget {
        &mut self.read
    }

    /// Returns cumulative allocation bytes charged.
    #[must_use]
    pub const fn allocation_bytes(&self) -> ByteCount {
        self.allocation_bytes
    }

    /// Returns cumulative decoded bytes charged.
    #[must_use]
    pub const fn decoded_bytes(&self) -> ByteCount {
        self.decoded_bytes
    }

    /// Returns cumulative bytes encoded into caller-provided output.
    #[must_use]
    pub const fn encoded_bytes(&self) -> ByteCount {
        self.encoded_bytes
    }

    /// Returns cumulative item/count work charged.
    #[must_use]
    pub const fn item_work(&self) -> u64 {
        self.item_work
    }

    /// Returns cumulative page visits charged.
    #[must_use]
    pub const fn page_visits(&self) -> u64 {
        self.page_visits
    }

    /// Returns cumulative aggregate non-I/O work charged.
    #[must_use]
    pub const fn total_work_units(&self) -> u64 {
        self.total_work_units
    }

    /// Checks a single decoded value size without changing any counter.
    pub fn check_decoded_value(&self, bytes: ByteCount) -> Result<(), Error> {
        check_limit(
            ResourceLimitKind::DecodedValueBytes,
            bytes.get(),
            self.limits.max_decoded_value_bytes().get(),
        )
    }

    /// Checks a chain depth without changing any counter.
    pub fn check_chain_depth(&self, depth: u64) -> Result<(), Error> {
        check_limit(
            ResourceLimitKind::ChainDepth,
            depth,
            self.limits.max_chain_depth(),
        )
    }

    /// Charges cumulative allocation bytes and aggregate work before allocation.
    pub fn charge_allocation(&mut self, bytes: ByteCount) -> Result<(), Error> {
        let next = checked_byte_total(
            self.allocation_bytes,
            bytes,
            ResourceLimitKind::AllocationBytes,
            self.limits.max_allocation_bytes(),
            "accumulate allocation bytes",
        )?;
        let next_work = self.checked_work_add(bytes.get())?;
        self.allocation_bytes = next;
        self.total_work_units = next_work;
        Ok(())
    }

    /// Charges one decoded value and aggregate work before decoding.
    pub fn charge_decoded_value(&mut self, bytes: ByteCount) -> Result<(), Error> {
        self.check_decoded_value(bytes)?;
        let next = checked_byte_total(
            self.decoded_bytes,
            bytes,
            ResourceLimitKind::TotalDecodedBytes,
            self.limits.max_total_decoded_bytes(),
            "accumulate decoded bytes",
        )?;
        let next_work = self.checked_work_add(bytes.get())?;
        self.decoded_bytes = next;
        self.total_work_units = next_work;
        Ok(())
    }

    /// Charges encoded output and aggregate work before modifying output.
    ///
    /// Every successful encoding is charged, including a rewrite after
    /// seeking backward.
    pub fn charge_encoded_bytes(&mut self, bytes: ByteCount) -> Result<(), Error> {
        let next = checked_byte_total(
            self.encoded_bytes,
            bytes,
            ResourceLimitKind::EncodedBytes,
            self.limits.max_encoded_bytes(),
            "accumulate encoded bytes",
        )?;
        let next_work = self.checked_work_add(bytes.get())?;
        self.encoded_bytes = next;
        self.total_work_units = next_work;
        Ok(())
    }

    /// Charges item/count-driven work and aggregate work before iteration.
    pub fn charge_items(&mut self, count: u64) -> Result<(), Error> {
        let next = checked_total(
            self.item_work,
            count,
            ResourceLimitKind::ItemWork,
            self.limits.max_item_work(),
            "accumulate item work",
        )?;
        let next_work = self.checked_work_add(count)?;
        self.item_work = next;
        self.total_work_units = next_work;
        Ok(())
    }

    /// Charges page visits and aggregate work before traversal.
    pub fn charge_page_visits(&mut self, count: u64) -> Result<(), Error> {
        let next = checked_total(
            self.page_visits,
            count,
            ResourceLimitKind::PageVisits,
            self.limits.max_page_visits(),
            "accumulate page visits",
        )?;
        let next_work = self.checked_work_add(count)?;
        self.page_visits = next;
        self.total_work_units = next_work;
        Ok(())
    }

    /// Charges explicit algorithm work not covered by another charge method.
    ///
    /// Copy loops can use copied bytes as units. Callers must not separately
    /// charge work already represented by allocation, decoding, items, or page
    /// visits because those methods charge aggregate work automatically.
    pub fn charge_work_units(&mut self, units: u64) -> Result<(), Error> {
        let next = self.checked_work_add(units)?;
        self.total_work_units = next;
        Ok(())
    }

    fn checked_work_add(&self, units: u64) -> Result<u64, Error> {
        checked_total(
            self.total_work_units,
            units,
            ResourceLimitKind::TotalWorkUnits,
            self.limits.max_total_work_units(),
            "accumulate total work units",
        )
    }
}

fn checked_byte_total(
    current: ByteCount,
    amount: ByteCount,
    kind: ResourceLimitKind,
    maximum: ByteCount,
    operation: &'static str,
) -> Result<ByteCount, Error> {
    checked_total(current.get(), amount.get(), kind, maximum.get(), operation).map(ByteCount::new)
}

fn checked_total(
    current: u64,
    amount: u64,
    kind: ResourceLimitKind,
    maximum: u64,
    operation: &'static str,
) -> Result<u64, Error> {
    let requested = current
        .checked_add(amount)
        .ok_or(Error::Arithmetic { operation })?;
    check_limit(kind, requested, maximum)?;
    Ok(requested)
}

fn check_limit(kind: ResourceLimitKind, requested: u64, maximum: u64) -> Result<(), Error> {
    if requested > maximum {
        return Err(Error::ResourceLimitExceeded {
            kind,
            requested,
            maximum,
        });
    }
    Ok(())
}

#[cfg(test)]
#[path = "resource_tests.rs"]
mod tests;
