//! Operation-wide resource limits and cumulative accounting.

use crate::limits::{ReadBudget, ReadLimits};
use crate::{ByteCount, Error, ResourceLimitKind};

/// Default cumulative allocation ceiling: 256 MiB.
pub const DEFAULT_MAX_ALLOCATION_BYTES: ByteCount = ByteCount::new(256 * 1024 * 1024);
/// Default maximum size of one decoded value: 16 MiB.
pub const DEFAULT_MAX_DECODED_VALUE_BYTES: ByteCount = ByteCount::new(16 * 1024 * 1024);
/// Default cumulative decoded-value ceiling: 256 MiB.
pub const DEFAULT_MAX_TOTAL_DECODED_BYTES: ByteCount = ByteCount::new(256 * 1024 * 1024);
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
/// memory reservations, decoded output, count-driven loops, page traversal,
/// chain following, and aggregate non-I/O work. Defaults are format-neutral:
///
/// - 256 MiB cumulative allocation prevents input from driving process-scale
///   memory growth;
/// - 16 MiB per decoded value and 256 MiB total decoded output bound expansion;
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
/// units. Read bytes remain governed by the embedded [`ReadBudget`] and are not
/// charged a second time as aggregate non-I/O work.
#[derive(Debug)]
pub struct ResourceBudget {
    limits: ResourceLimits,
    read: ReadBudget,
    allocation_bytes: ByteCount,
    decoded_bytes: ByteCount,
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
mod tests {
    use super::{ResourceBudget, ResourceLimits};
    use crate::limits::ReadLimits;
    use crate::{ByteCount, Error, ResourceLimitKind};

    fn limits(
        allocation: u64,
        single_decoded: u64,
        total_decoded: u64,
        items: u64,
        pages: u64,
        depth: u64,
        work: u64,
    ) -> ResourceLimits {
        ResourceLimits::new(ReadLimits::new(
            ByteCount::new(u64::MAX),
            ByteCount::new(u64::MAX),
            ByteCount::new(u64::MAX),
        ))
        .with_max_allocation_bytes(ByteCount::new(allocation))
        .with_max_decoded_value_bytes(ByteCount::new(single_decoded))
        .with_max_total_decoded_bytes(ByteCount::new(total_decoded))
        .with_max_item_work(items)
        .with_max_page_visits(pages)
        .with_max_chain_depth(depth)
        .with_max_total_work_units(work)
    }

    #[test]
    fn defaults_match_documented_policy() {
        let defaults = ResourceLimits::default();
        assert_eq!(defaults.read(), ReadLimits::default());
        assert_eq!(
            defaults.max_allocation_bytes(),
            super::DEFAULT_MAX_ALLOCATION_BYTES
        );
        assert_eq!(
            defaults.max_decoded_value_bytes(),
            super::DEFAULT_MAX_DECODED_VALUE_BYTES
        );
        assert_eq!(
            defaults.max_total_decoded_bytes(),
            super::DEFAULT_MAX_TOTAL_DECODED_BYTES
        );
        assert_eq!(defaults.max_item_work(), super::DEFAULT_MAX_ITEM_WORK);
        assert_eq!(defaults.max_page_visits(), super::DEFAULT_MAX_PAGE_VISITS);
        assert_eq!(defaults.max_chain_depth(), super::DEFAULT_MAX_CHAIN_DEPTH);
        assert_eq!(
            defaults.max_total_work_units(),
            super::DEFAULT_MAX_TOTAL_WORK_UNITS
        );
    }

    #[test]
    fn read_budget_is_persistent_operation_sub_budget() -> Result<(), Error> {
        let policy = ResourceLimits::new(ReadLimits::new(
            ByteCount::new(2),
            ByteCount::new(1),
            ByteCount::new(1),
        ))
        .with_max_allocation_bytes(ByteCount::new(0))
        .with_max_decoded_value_bytes(ByteCount::new(0))
        .with_max_total_decoded_bytes(ByteCount::new(0))
        .with_max_item_work(0)
        .with_max_page_visits(0)
        .with_max_chain_depth(0)
        .with_max_total_work_units(0);
        let mut budget = ResourceBudget::new(policy);
        budget.read_budget().check_input(ByteCount::new(2))?;
        budget
            .read_budget()
            .charge_read_attempt(ByteCount::new(1))?;
        assert_eq!(
            budget.read_budget().charge_read_attempt(ByteCount::new(1)),
            Err(Error::LimitExceeded {
                kind: crate::LimitKind::TotalReadBytes,
                requested: ByteCount::new(2),
                maximum: ByteCount::new(1),
            })
        );
        Ok(())
    }

    #[test]
    fn allocation_exact_one_over_and_aggregate_rejection_are_atomic() -> Result<(), Error> {
        let mut exact = ResourceBudget::new(limits(3, 0, 0, 0, 0, 0, 3));
        exact.charge_allocation(ByteCount::new(1))?;
        exact.charge_allocation(ByteCount::new(2))?;
        assert_eq!(exact.allocation_bytes(), ByteCount::new(3));
        assert_eq!(exact.total_work_units(), 3);
        assert_eq!(
            exact.charge_allocation(ByteCount::new(1)),
            resource_error(ResourceLimitKind::AllocationBytes, 4, 3)
        );
        assert_eq!(exact.allocation_bytes(), ByteCount::new(3));
        assert_eq!(exact.total_work_units(), 3);

        let mut one_over = ResourceBudget::new(limits(3, 0, 0, 0, 0, 0, 4));
        assert_eq!(
            one_over.charge_allocation(ByteCount::new(4)),
            resource_error(ResourceLimitKind::AllocationBytes, 4, 3)
        );
        assert_eq!(one_over.allocation_bytes(), ByteCount::new(0));
        assert_eq!(one_over.total_work_units(), 0);
        Ok(())
    }

    #[test]
    fn decoded_value_checks_single_and_cumulative_boundaries() -> Result<(), Error> {
        let mut budget = ResourceBudget::new(limits(0, 2, 3, 0, 0, 0, 3));
        assert_eq!(budget.check_decoded_value(ByteCount::new(2)), Ok(()));
        assert_eq!(
            budget.check_decoded_value(ByteCount::new(3)),
            resource_error(ResourceLimitKind::DecodedValueBytes, 3, 2)
        );
        budget.charge_decoded_value(ByteCount::new(1))?;
        budget.charge_decoded_value(ByteCount::new(2))?;
        assert_eq!(budget.decoded_bytes(), ByteCount::new(3));
        assert_eq!(
            budget.charge_decoded_value(ByteCount::new(1)),
            resource_error(ResourceLimitKind::TotalDecodedBytes, 4, 3)
        );
        assert_eq!(budget.decoded_bytes(), ByteCount::new(3));
        assert_eq!(budget.total_work_units(), 3);
        Ok(())
    }

    #[test]
    fn item_work_exact_one_over_and_aggregate_rejection_are_atomic() -> Result<(), Error> {
        let mut budget = ResourceBudget::new(limits(0, 0, 0, 3, 0, 0, 3));
        budget.charge_items(1)?;
        budget.charge_items(2)?;
        assert_eq!(budget.item_work(), 3);
        assert_eq!(
            budget.charge_items(1),
            resource_error(ResourceLimitKind::ItemWork, 4, 3)
        );
        assert_eq!(budget.item_work(), 3);

        let mut one_over = ResourceBudget::new(limits(0, 0, 0, 3, 0, 0, 4));
        assert_eq!(
            one_over.charge_items(4),
            resource_error(ResourceLimitKind::ItemWork, 4, 3)
        );
        assert_eq!(one_over.item_work(), 0);
        Ok(())
    }

    #[test]
    fn page_visits_exact_one_over_and_aggregate_rejection_are_atomic() -> Result<(), Error> {
        let mut budget = ResourceBudget::new(limits(0, 0, 0, 0, 3, 0, 3));
        budget.charge_page_visits(1)?;
        budget.charge_page_visits(2)?;
        assert_eq!(budget.page_visits(), 3);
        assert_eq!(
            budget.charge_page_visits(1),
            resource_error(ResourceLimitKind::PageVisits, 4, 3)
        );
        assert_eq!(budget.page_visits(), 3);

        let mut one_over = ResourceBudget::new(limits(0, 0, 0, 0, 3, 0, 4));
        assert_eq!(
            one_over.charge_page_visits(4),
            resource_error(ResourceLimitKind::PageVisits, 4, 3)
        );
        assert_eq!(one_over.page_visits(), 0);
        Ok(())
    }

    #[test]
    fn chain_depth_accepts_exact_and_rejects_one_over_without_mutation() {
        let budget = ResourceBudget::new(limits(0, 0, 0, 0, 0, 3, 0));
        assert_eq!(budget.check_chain_depth(3), Ok(()));
        assert_eq!(
            budget.check_chain_depth(4),
            resource_error(ResourceLimitKind::ChainDepth, 4, 3)
        );
        assert_eq!(budget.total_work_units(), 0);
    }

    #[test]
    fn total_work_exact_one_over_and_aggregate_rejection_are_atomic() -> Result<(), Error> {
        let mut budget = ResourceBudget::new(limits(4, 0, 0, 0, 0, 0, 3));
        budget.charge_work_units(1)?;
        budget.charge_work_units(2)?;
        assert_eq!(budget.total_work_units(), 3);
        assert_eq!(
            budget.charge_work_units(1),
            resource_error(ResourceLimitKind::TotalWorkUnits, 4, 3)
        );
        assert_eq!(budget.total_work_units(), 3);

        let mut allocation = ResourceBudget::new(limits(4, 0, 0, 0, 0, 0, 3));
        assert_eq!(
            allocation.charge_allocation(ByteCount::new(4)),
            resource_error(ResourceLimitKind::TotalWorkUnits, 4, 3)
        );
        assert_eq!(allocation.allocation_bytes(), ByteCount::new(0));
        assert_eq!(allocation.total_work_units(), 0);
        Ok(())
    }

    #[test]
    fn aggregate_work_rejection_preserves_each_dimension_counter() {
        let mut allocation = ResourceBudget::new(limits(1, 0, 0, 0, 0, 0, 0));
        assert_eq!(
            allocation.charge_allocation(ByteCount::new(1)),
            resource_error(ResourceLimitKind::TotalWorkUnits, 1, 0)
        );
        assert_eq!(allocation.allocation_bytes(), ByteCount::new(0));

        let mut decoded = ResourceBudget::new(limits(0, 1, 1, 0, 0, 0, 0));
        assert_eq!(
            decoded.charge_decoded_value(ByteCount::new(1)),
            resource_error(ResourceLimitKind::TotalWorkUnits, 1, 0)
        );
        assert_eq!(decoded.decoded_bytes(), ByteCount::new(0));

        let mut items = ResourceBudget::new(limits(0, 0, 0, 1, 0, 0, 0));
        assert_eq!(
            items.charge_items(1),
            resource_error(ResourceLimitKind::TotalWorkUnits, 1, 0)
        );
        assert_eq!(items.item_work(), 0);

        let mut pages = ResourceBudget::new(limits(0, 0, 0, 0, 1, 0, 0));
        assert_eq!(
            pages.charge_page_visits(1),
            resource_error(ResourceLimitKind::TotalWorkUnits, 1, 0)
        );
        assert_eq!(pages.page_visits(), 0);
    }

    #[test]
    fn every_cumulative_counter_rejects_u64_overflow_without_mutation() -> Result<(), Error> {
        let mut allocation = ResourceBudget::new(limits(u64::MAX, 0, 0, 0, 0, 0, u64::MAX));
        allocation.charge_allocation(ByteCount::new(u64::MAX))?;
        assert_arithmetic(
            allocation.charge_allocation(ByteCount::new(1)),
            "accumulate allocation bytes",
        );
        assert_eq!(allocation.allocation_bytes(), ByteCount::new(u64::MAX));

        let mut decoded = ResourceBudget::new(limits(0, u64::MAX, u64::MAX, 0, 0, 0, u64::MAX));
        decoded.charge_decoded_value(ByteCount::new(u64::MAX))?;
        assert_arithmetic(
            decoded.charge_decoded_value(ByteCount::new(1)),
            "accumulate decoded bytes",
        );
        assert_eq!(decoded.decoded_bytes(), ByteCount::new(u64::MAX));

        let mut items = ResourceBudget::new(limits(0, 0, 0, u64::MAX, 0, 0, u64::MAX));
        items.charge_items(u64::MAX)?;
        assert_arithmetic(items.charge_items(1), "accumulate item work");
        assert_eq!(items.item_work(), u64::MAX);

        let mut pages = ResourceBudget::new(limits(0, 0, 0, 0, u64::MAX, 0, u64::MAX));
        pages.charge_page_visits(u64::MAX)?;
        assert_arithmetic(pages.charge_page_visits(1), "accumulate page visits");
        assert_eq!(pages.page_visits(), u64::MAX);

        let mut work = ResourceBudget::new(limits(0, 0, 0, 0, 0, 0, u64::MAX));
        work.charge_work_units(u64::MAX)?;
        assert_arithmetic(work.charge_work_units(1), "accumulate total work units");
        assert_eq!(work.total_work_units(), u64::MAX);
        Ok(())
    }

    fn resource_error(kind: ResourceLimitKind, requested: u64, maximum: u64) -> Result<(), Error> {
        Err(Error::ResourceLimitExceeded {
            kind,
            requested,
            maximum,
        })
    }

    fn assert_arithmetic(result: Result<(), Error>, operation: &'static str) {
        assert_eq!(result, Err(Error::Arithmetic { operation }));
    }
}
