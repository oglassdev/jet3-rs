use super::resource::{ResourceBudget, ResourceLimits};
use crate::{ByteCount, Error, ResourceLimitKind, format::limits::ReadLimits};

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

type Charge = fn(&mut ResourceBudget, u64) -> Result<(), Error>;

struct Dimension {
    kind: ResourceLimitKind,
    limits: fn(u64, u64) -> ResourceLimits,
    charge: Charge,
    counter: fn(&ResourceBudget) -> u64,
    overflow: &'static str,
}

const DIMENSIONS: [Dimension; 5] = [
    Dimension {
        kind: ResourceLimitKind::AllocationBytes,
        limits: |limit, work| limits(limit, 0, 0, 0, 0, 0, work),
        charge: |budget, count| budget.charge_allocation(ByteCount::new(count)),
        counter: |budget| budget.allocation_bytes().get(),
        overflow: "accumulate allocation bytes",
    },
    Dimension {
        kind: ResourceLimitKind::EncodedBytes,
        limits: encoded_limits,
        charge: |budget, count| budget.charge_encoded_bytes(ByteCount::new(count)),
        counter: |budget| budget.encoded_bytes().get(),
        overflow: "accumulate encoded bytes",
    },
    Dimension {
        kind: ResourceLimitKind::ItemWork,
        limits: |limit, work| limits(0, 0, 0, limit, 0, 0, work),
        charge: ResourceBudget::charge_items,
        counter: ResourceBudget::item_work,
        overflow: "accumulate item work",
    },
    Dimension {
        kind: ResourceLimitKind::PageVisits,
        limits: |limit, work| limits(0, 0, 0, 0, limit, 0, work),
        charge: ResourceBudget::charge_page_visits,
        counter: ResourceBudget::page_visits,
        overflow: "accumulate page visits",
    },
    Dimension {
        kind: ResourceLimitKind::TotalWorkUnits,
        limits: |_, work| limits(0, 0, 0, 0, 0, 0, work),
        charge: ResourceBudget::charge_work_units,
        counter: ResourceBudget::total_work_units,
        overflow: "accumulate total work units",
    },
];

#[test]
fn each_counter_accepts_exact_and_rejects_one_over_atomically() -> Result<(), Error> {
    for dimension in DIMENSIONS {
        let label = dimension.overflow;
        let mut exact = ResourceBudget::new((dimension.limits)(3, 3));
        (dimension.charge)(&mut exact, 1)?;
        (dimension.charge)(&mut exact, 2)?;
        assert_eq!((dimension.counter)(&exact), 3, "{label}");
        assert_eq!(exact.total_work_units(), 3, "{label}");
        assert_eq!(
            (dimension.charge)(&mut exact, 1),
            resource_error(dimension.kind, 4, 3),
            "{label}"
        );
        assert_eq!((dimension.counter)(&exact), 3, "{label}");
        assert_eq!(exact.total_work_units(), 3, "{label}");

        let mut one_over = ResourceBudget::new((dimension.limits)(3, 3));
        assert_eq!(
            (dimension.charge)(&mut one_over, 4),
            resource_error(dimension.kind, 4, 3),
            "{label}"
        );
        assert_eq!((dimension.counter)(&one_over), 0, "{label}");
        assert_eq!(one_over.total_work_units(), 0, "{label}");
    }
    Ok(())
}

#[test]
fn aggregate_work_rejection_preserves_each_dimension_counter() {
    for dimension in &DIMENSIONS[..4] {
        let label = dimension.overflow;
        let mut budget = ResourceBudget::new((dimension.limits)(4, 3));
        assert_eq!(
            (dimension.charge)(&mut budget, 4),
            resource_error(ResourceLimitKind::TotalWorkUnits, 4, 3),
            "{label}"
        );
        assert_eq!((dimension.counter)(&budget), 0, "{label}");
    }

    let mut decoded = ResourceBudget::new(limits(0, 1, 1, 0, 0, 0, 0));
    assert_eq!(
        decoded.charge_decoded_value(ByteCount::new(1)),
        resource_error(ResourceLimitKind::TotalWorkUnits, 1, 0)
    );
    assert_eq!(decoded.decoded_bytes(), ByteCount::new(0));
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
fn every_cumulative_counter_rejects_u64_overflow_without_mutation() -> Result<(), Error> {
    for dimension in DIMENSIONS {
        let mut budget = ResourceBudget::new((dimension.limits)(u64::MAX, u64::MAX));
        (dimension.charge)(&mut budget, u64::MAX)?;
        assert_arithmetic((dimension.charge)(&mut budget, 1), dimension.overflow);
        assert_eq!((dimension.counter)(&budget), u64::MAX);
    }

    let mut decoded = ResourceBudget::new(limits(0, u64::MAX, u64::MAX, 0, 0, 0, u64::MAX));
    decoded.charge_decoded_value(ByteCount::new(u64::MAX))?;
    assert_arithmetic(
        decoded.charge_decoded_value(ByteCount::new(1)),
        "accumulate decoded bytes",
    );
    assert_eq!(decoded.decoded_bytes(), ByteCount::new(u64::MAX));
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
