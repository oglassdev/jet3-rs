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

fn encoded_limits(encoded: u64, work: u64) -> ResourceLimits {
    limits(0, 0, 0, 0, 0, 0, work).with_max_encoded_bytes(ByteCount::new(encoded))
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
    assert_eq!(
        defaults.max_encoded_bytes(),
        super::DEFAULT_MAX_ENCODED_BYTES
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
fn encoded_bytes_exact_one_over_and_aggregate_rejection_are_atomic() -> Result<(), Error> {
    let mut exact = ResourceBudget::new(encoded_limits(3, 3));
    exact.charge_encoded_bytes(ByteCount::new(1))?;
    exact.charge_encoded_bytes(ByteCount::new(2))?;
    assert_eq!(exact.encoded_bytes(), ByteCount::new(3));
    assert_eq!(exact.total_work_units(), 3);
    assert_eq!(
        exact.charge_encoded_bytes(ByteCount::new(1)),
        resource_error(ResourceLimitKind::EncodedBytes, 4, 3)
    );
    assert_eq!(exact.encoded_bytes(), ByteCount::new(3));
    assert_eq!(exact.total_work_units(), 3);

    let mut aggregate = ResourceBudget::new(encoded_limits(4, 3));
    assert_eq!(
        aggregate.charge_encoded_bytes(ByteCount::new(4)),
        resource_error(ResourceLimitKind::TotalWorkUnits, 4, 3)
    );
    assert_eq!(aggregate.encoded_bytes(), ByteCount::new(0));
    assert_eq!(aggregate.total_work_units(), 0);
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

    let mut encoded = ResourceBudget::new(encoded_limits(1, 0));
    assert_eq!(
        encoded.charge_encoded_bytes(ByteCount::new(1)),
        resource_error(ResourceLimitKind::TotalWorkUnits, 1, 0)
    );
    assert_eq!(encoded.encoded_bytes(), ByteCount::new(0));

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

    let mut encoded = ResourceBudget::new(encoded_limits(u64::MAX, u64::MAX));
    encoded.charge_encoded_bytes(ByteCount::new(u64::MAX))?;
    assert_arithmetic(
        encoded.charge_encoded_bytes(ByteCount::new(1)),
        "accumulate encoded bytes",
    );
    assert_eq!(encoded.encoded_bytes(), ByteCount::new(u64::MAX));

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
