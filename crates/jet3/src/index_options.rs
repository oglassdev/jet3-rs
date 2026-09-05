//! Typed index options using EXP-0093 classes and EXP-0148 null policies.

use crate::{LogicalIndexKindSpec, PhysicalIndexFlagsSpec};

/// Treatment of nullable key components (`EXP-0148`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum IndexNullPolicy {
    /// Store null-bearing keys; uniqueness applies only to fully present keys.
    Include,
    /// Omit a row only when every indexed component is null.
    IgnoreAllNull,
    /// Reject any row containing a null indexed component.
    Required,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum Class {
    Primary,
    Unique,
    Ordinary,
}

/// Primary/uniqueness class and null policy of an index in a table specification.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct IndexKind {
    class: Class,
    null_policy: IndexNullPolicy,
}

#[allow(non_upper_case_globals)]
impl IndexKind {
    /// Primary unique index requiring every key component.
    pub const Primary: Self = Self {
        class: Class::Primary,
        null_policy: IndexNullPolicy::Required,
    };
    /// Unique non-primary index, allowing repeated null-bearing keys.
    pub const Unique: Self = Self {
        class: Class::Unique,
        null_policy: IndexNullPolicy::Include,
    };
    /// Non-unique index including null-bearing keys.
    pub const Ordinary: Self = Self {
        class: Class::Ordinary,
        null_policy: IndexNullPolicy::Include,
    };

    /// Selects the null policy. Primary indexes must retain `Required`;
    /// incompatible options are rejected during schema planning.
    #[must_use]
    pub const fn with_null_policy(mut self, policy: IndexNullPolicy) -> Self {
        self.null_policy = policy;
        self
    }

    /// Returns the declared null policy.
    #[must_use]
    pub const fn null_policy(self) -> IndexNullPolicy {
        self.null_policy
    }

    /// Reports whether this is the table's primary index.
    #[must_use]
    pub const fn is_primary(self) -> bool {
        matches!(self.class, Class::Primary)
    }

    /// Reports whether fully present keys must be unique.
    #[must_use]
    pub const fn is_unique(self) -> bool {
        !matches!(self.class, Class::Ordinary)
    }

    pub(crate) const fn flags(self) -> PhysicalIndexFlagsSpec {
        match (self.is_unique(), self.null_policy) {
            (false, IndexNullPolicy::Include) => PhysicalIndexFlagsSpec::Ordinary,
            (true, IndexNullPolicy::Include) => PhysicalIndexFlagsSpec::Unique,
            (false, IndexNullPolicy::IgnoreAllNull) => PhysicalIndexFlagsSpec::IgnoreNulls,
            (true, IndexNullPolicy::IgnoreAllNull) => PhysicalIndexFlagsSpec::UniqueIgnoreNulls,
            (false, IndexNullPolicy::Required) => PhysicalIndexFlagsSpec::Required,
            (true, IndexNullPolicy::Required) => PhysicalIndexFlagsSpec::UniqueRequired,
        }
    }

    pub(crate) const fn logical_kind(self) -> LogicalIndexKindSpec {
        if self.is_primary() {
            LogicalIndexKindSpec::Primary
        } else {
            LogicalIndexKindSpec::Ordinary
        }
    }
}
