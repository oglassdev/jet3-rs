//! Explicit resource policy for work driven by untrusted input.

use crate::{ByteCount, Error, LimitKind};

/// Default maximum input retained by one format-neutral operation: 256 MiB.
pub const DEFAULT_MAX_INPUT_BYTES: ByteCount = ByteCount::new(256 * 1024 * 1024);
/// Default maximum contiguous byte range returned by one read: 16 MiB.
pub const DEFAULT_MAX_SINGLE_READ_BYTES: ByteCount = ByteCount::new(16 * 1024 * 1024);
/// Default cumulative read budget for one cursor: 512 MiB.
pub const DEFAULT_MAX_TOTAL_READ_BYTES: ByteCount = ByteCount::new(512 * 1024 * 1024);

/// Bounds memory exposure and repeated work caused by untrusted input.
///
/// The defaults are deliberately independent of any Jet format assertion.
/// A 256 MiB input ceiling prevents an API call from retaining arbitrarily
/// large attacker-controlled buffers. A 16 MiB single-read ceiling prevents a
/// corrupt length from turning into one unexpectedly large value. The 512 MiB
/// cumulative budget permits two complete passes over a maximum-sized input
/// while bounding work from repeated seeks. Applications with a different
/// trust or memory model should construct an explicit policy.
///
/// Zero is valid for every field and denies the corresponding operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ResourceLimits {
    max_input_bytes: ByteCount,
    max_single_read_bytes: ByteCount,
    max_total_read_bytes: ByteCount,
}

impl ResourceLimits {
    /// Creates a policy from explicit byte ceilings.
    #[must_use]
    pub const fn new(
        max_input_bytes: ByteCount,
        max_single_read_bytes: ByteCount,
        max_total_read_bytes: ByteCount,
    ) -> Self {
        Self {
            max_input_bytes,
            max_single_read_bytes,
            max_total_read_bytes,
        }
    }

    /// Returns the maximum accepted input length.
    #[must_use]
    pub const fn max_input_bytes(self) -> ByteCount {
        self.max_input_bytes
    }

    /// Returns the maximum byte range accepted by one read.
    #[must_use]
    pub const fn max_single_read_bytes(self) -> ByteCount {
        self.max_single_read_bytes
    }

    /// Returns the maximum cumulative bytes read by one cursor.
    #[must_use]
    pub const fn max_total_read_bytes(self) -> ByteCount {
        self.max_total_read_bytes
    }
}

impl Default for ResourceLimits {
    fn default() -> Self {
        Self::new(
            DEFAULT_MAX_INPUT_BYTES,
            DEFAULT_MAX_SINGLE_READ_BYTES,
            DEFAULT_MAX_TOTAL_READ_BYTES,
        )
    }
}

/// Mutable accounting for input-driven read work.
///
/// File-backed readers should call [`Self::check_input`] when the source length
/// becomes known and [`Self::charge_read`] before issuing each I/O request.
/// Failed and short I/O remains charged intentionally: an unreliable source
/// must not permit unbounded retries to evade the total-work ceiling.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WorkBudget {
    limits: ResourceLimits,
    total_read: ByteCount,
}

impl WorkBudget {
    /// Starts an unused budget governed by `limits`.
    #[must_use]
    pub const fn new(limits: ResourceLimits) -> Self {
        Self {
            limits,
            total_read: ByteCount::new(0),
        }
    }

    /// Returns the governing policy.
    #[must_use]
    pub const fn limits(&self) -> ResourceLimits {
        self.limits
    }

    /// Returns cumulative bytes charged to this budget.
    #[must_use]
    pub const fn total_read(&self) -> ByteCount {
        self.total_read
    }

    /// Checks a known input length without consuming read work.
    pub fn check_input(&self, input_len: ByteCount) -> Result<(), Error> {
        if input_len > self.limits.max_input_bytes() {
            return Err(Error::LimitExceeded {
                kind: LimitKind::InputBytes,
                requested: input_len,
                maximum: self.limits.max_input_bytes(),
            });
        }
        Ok(())
    }

    /// Charges one read request before it is attempted.
    ///
    /// The budget is unchanged if a single-read, cumulative, or arithmetic
    /// boundary rejects the request.
    pub fn charge_read(&mut self, count: ByteCount) -> Result<(), Error> {
        if count > self.limits.max_single_read_bytes() {
            return Err(Error::LimitExceeded {
                kind: LimitKind::SingleReadBytes,
                requested: count,
                maximum: self.limits.max_single_read_bytes(),
            });
        }

        let next_total = self.total_read.checked_add(count)?;
        if next_total > self.limits.max_total_read_bytes() {
            return Err(Error::LimitExceeded {
                kind: LimitKind::TotalReadBytes,
                requested: next_total,
                maximum: self.limits.max_total_read_bytes(),
            });
        }
        self.total_read = next_total;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::{
        DEFAULT_MAX_INPUT_BYTES, DEFAULT_MAX_SINGLE_READ_BYTES, DEFAULT_MAX_TOTAL_READ_BYTES,
        ResourceLimits, WorkBudget,
    };
    use crate::{ByteCount, Error, LimitKind};

    #[test]
    fn defaults_match_documented_security_policy() {
        let limits = ResourceLimits::default();
        assert_eq!(limits.max_input_bytes(), DEFAULT_MAX_INPUT_BYTES);
        assert_eq!(
            limits.max_single_read_bytes(),
            DEFAULT_MAX_SINGLE_READ_BYTES
        );
        assert_eq!(limits.max_total_read_bytes(), DEFAULT_MAX_TOTAL_READ_BYTES);
    }

    #[test]
    fn explicit_policy_preserves_zero_and_boundary_values() {
        let limits = ResourceLimits::new(
            ByteCount::new(0),
            ByteCount::new(1),
            ByteCount::new(u64::MAX),
        );
        assert_eq!(limits.max_input_bytes(), ByteCount::new(0));
        assert_eq!(limits.max_single_read_bytes(), ByteCount::new(1));
        assert_eq!(limits.max_total_read_bytes(), ByteCount::new(u64::MAX));
    }

    #[test]
    fn work_budget_checks_input_at_and_above_boundary() {
        let budget = WorkBudget::new(ResourceLimits::new(
            ByteCount::new(3),
            ByteCount::new(3),
            ByteCount::new(3),
        ));
        assert_eq!(budget.check_input(ByteCount::new(3)), Ok(()));
        assert_eq!(
            budget.check_input(ByteCount::new(4)),
            Err(Error::LimitExceeded {
                kind: LimitKind::InputBytes,
                requested: ByteCount::new(4),
                maximum: ByteCount::new(3),
            })
        );
        assert_eq!(budget.total_read(), ByteCount::new(0));
    }

    #[test]
    fn work_budget_charges_exact_boundaries_and_zero() {
        let policy = ResourceLimits::new(ByteCount::new(9), ByteCount::new(2), ByteCount::new(3));
        let mut budget = WorkBudget::new(policy);
        assert_eq!(budget.limits(), policy);
        assert_eq!(budget.charge_read(ByteCount::new(0)), Ok(()));
        assert_eq!(budget.charge_read(ByteCount::new(2)), Ok(()));
        assert_eq!(budget.charge_read(ByteCount::new(1)), Ok(()));
        assert_eq!(budget.total_read(), ByteCount::new(3));
    }

    #[test]
    fn work_budget_rejects_single_and_total_limit_without_charging() {
        let mut budget = WorkBudget::new(ResourceLimits::new(
            ByteCount::new(9),
            ByteCount::new(2),
            ByteCount::new(3),
        ));
        assert_eq!(
            budget.charge_read(ByteCount::new(3)),
            Err(Error::LimitExceeded {
                kind: LimitKind::SingleReadBytes,
                requested: ByteCount::new(3),
                maximum: ByteCount::new(2),
            })
        );
        assert_eq!(budget.total_read(), ByteCount::new(0));
        assert_eq!(budget.charge_read(ByteCount::new(2)), Ok(()));
        assert_eq!(
            budget.charge_read(ByteCount::new(2)),
            Err(Error::LimitExceeded {
                kind: LimitKind::TotalReadBytes,
                requested: ByteCount::new(4),
                maximum: ByteCount::new(3),
            })
        );
        assert_eq!(budget.total_read(), ByteCount::new(2));
    }

    #[test]
    fn work_budget_rejects_counter_overflow_without_wrapping() {
        let mut budget = WorkBudget::new(ResourceLimits::new(
            ByteCount::new(u64::MAX),
            ByteCount::new(u64::MAX),
            ByteCount::new(u64::MAX),
        ));
        assert_eq!(budget.charge_read(ByteCount::new(u64::MAX)), Ok(()));
        assert_eq!(
            budget.charge_read(ByteCount::new(1)),
            Err(Error::Arithmetic {
                operation: "byte-count addition"
            })
        );
        assert_eq!(budget.total_read(), ByteCount::new(u64::MAX));
    }
}
