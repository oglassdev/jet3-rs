//! Lossless definition names shared by column and index metadata from
//! `EXP-0059`.

use crate::{ByteCount, Error, ResourceBudget};

/// Encoding context attached to raw definition names.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum DefinitionNameEncoding {
    DatabaseCodePage,
}

/// Owned raw column/index name with its sourced encoding context.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DefinitionName {
    raw: Vec<u8>,
    encoding: DefinitionNameEncoding,
}

impl DefinitionName {
    pub(crate) fn from_raw(raw: &[u8], budget: &mut ResourceBudget) -> Result<Self, Error> {
        budget.charge_allocation(ByteCount::from_usize(raw.len())?)?;
        let mut owned = Vec::new();
        owned.try_reserve_exact(raw.len()).map_err(|_| Error::Io {
            operation: "reserve raw definition name",
            kind: std::io::ErrorKind::OutOfMemory,
        })?;
        owned.extend_from_slice(raw);
        Ok(Self {
            raw: owned,
            encoding: DefinitionNameEncoding::DatabaseCodePage,
        })
    }

    #[must_use]
    pub fn raw_bytes(&self) -> &[u8] {
        &self.raw
    }

    #[must_use]
    pub const fn encoding(&self) -> DefinitionNameEncoding {
        self.encoding
    }

    #[must_use]
    pub fn decoded_ascii(&self) -> Option<&str> {
        self.raw
            .is_ascii()
            .then(|| std::str::from_utf8(&self.raw).ok())
            .flatten()
    }
}

pub(crate) fn contains_name<'name>(
    names: impl IntoIterator<Item = &'name [u8]>,
    name_count: usize,
    candidate: &[u8],
    budget: &mut ResourceBudget,
) -> Result<bool, Error> {
    let units_per_name = u64::try_from(candidate.len())
        .map_err(|_| Error::IntegerConversion {
            value: candidate.len() as u128,
            target: "u64",
        })?
        .checked_add(1)
        .ok_or(Error::Arithmetic {
            operation: "count definition-name comparison work",
        })?;
    let name_count = u64::try_from(name_count).map_err(|_| Error::IntegerConversion {
        value: name_count as u128,
        target: "u64",
    })?;
    budget.charge_work_units(units_per_name.checked_mul(name_count).ok_or(
        Error::Arithmetic {
            operation: "count definition-name comparison work",
        },
    )?)?;
    Ok(names.into_iter().any(|name| name == candidate))
}

#[cfg(test)]
mod tests {
    use super::{DefinitionName, contains_name};
    use crate::{ResourceBudget, ResourceLimits};

    #[test]
    fn duplicate_checks_charge_worst_case_comparison_work() -> Result<(), crate::Error> {
        let mut names_budget = ResourceBudget::new(ResourceLimits::default());
        let names = [
            DefinitionName::from_raw(b"one", &mut names_budget)?,
            DefinitionName::from_raw(b"two", &mut names_budget)?,
        ];

        let mut exact = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(8));
        assert!(contains_name(
            names.iter().map(DefinitionName::raw_bytes),
            names.len(),
            b"one",
            &mut exact,
        )?);
        assert_eq!(exact.total_work_units(), 8);

        let mut one_below =
            ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(7));
        assert!(
            contains_name(
                names.iter().map(DefinitionName::raw_bytes),
                names.len(),
                b"one",
                &mut one_below,
            )
            .is_err()
        );
        Ok(())
    }
}
