//! Typed bindings for the closed protocol 1.2 scenario inventory.

use core::fmt;

use crate::{CoverageBranches, RejectedFormatErrorClass, ScenarioId};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ScenarioDefinition {
    identifier: &'static str,
    expectation: ScenarioExpectation,
    required_branches: &'static [&'static str],
    forbidden_branches: &'static [&'static str],
}

impl ScenarioDefinition {
    const fn new(
        identifier: &'static str,
        expectation: ScenarioExpectation,
        required_branches: &'static [&'static str],
        forbidden_branches: &'static [&'static str],
    ) -> Self {
        Self {
            identifier,
            expectation,
            required_branches,
            forbidden_branches,
        }
    }

    fn validate(self) -> Result<Self, ScenarioExpectationError> {
        let ordered_unique = |values: &[&str]| {
            values.windows(2).all(|pair| pair[0] < pair[1])
                && values.iter().all(|value| !value.is_empty())
        };
        if !ordered_unique(self.required_branches)
            || !ordered_unique(self.forbidden_branches)
            || self
                .required_branches
                .iter()
                .chain(self.forbidden_branches)
                .any(|branch| !ProtocolScenario::is_registered_branch(branch))
            || self
                .required_branches
                .iter()
                .any(|branch| self.forbidden_branches.binary_search(branch).is_ok())
        {
            return Err(ScenarioExpectationError::InvalidInventory);
        }
        Ok(self)
    }
}

/// The only protocol outcomes the Rust snapshot producer may publish.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ScenarioExpectation {
    /// Opening and semantic traversal must succeed.
    Success,
    /// Opening must fail with this exact normalized format class.
    OpeningFailure(RejectedFormatErrorClass),
}

/// One closed protocol scenario and its publication expectation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ProtocolScenario {
    scenario_id: ScenarioId,
    definition: ScenarioDefinition,
}

impl ProtocolScenario {
    pub(crate) fn is_registered_branch(value: &str) -> bool {
        REGISTERED_BRANCHES.binary_search(&value).is_ok()
    }

    /// Resolves only identifiers present in the checked protocol 1.2 inventory.
    pub fn resolve(value: &str) -> Result<Self, ScenarioExpectationError> {
        let index = SCENARIOS
            .binary_search_by_key(&value, |scenario| scenario.identifier)
            .map_err(|_| ScenarioExpectationError::UnknownScenarioId)?;
        let definition = SCENARIOS[index].validate()?;
        let scenario_id = ScenarioId::new(definition.identifier)
            .map_err(|_| ScenarioExpectationError::InvalidInventory)?;
        Ok(Self {
            scenario_id,
            definition,
        })
    }

    /// Returns the validated identifier carried into canonical artifacts.
    #[must_use]
    pub const fn scenario_id(&self) -> &ScenarioId {
        &self.scenario_id
    }

    /// Returns the closed expected outcome.
    #[must_use]
    pub const fn expectation(&self) -> ScenarioExpectation {
        self.definition.expectation
    }

    /// Returns the scenario branches every paired receipt must contain.
    #[must_use]
    pub const fn required_branches(&self) -> &'static [&'static str] {
        self.definition.required_branches
    }

    /// Returns the scenario-boundary branches no paired receipt may contain.
    #[must_use]
    pub const fn forbidden_branches(&self) -> &'static [&'static str] {
        self.definition.forbidden_branches
    }

    /// Admits an observed successful open only for a success scenario.
    pub const fn validate_success(&self) -> Result<(), ScenarioExpectationError> {
        match self.definition.expectation {
            ScenarioExpectation::Success => Ok(()),
            ScenarioExpectation::OpeningFailure(expected) => {
                Err(ScenarioExpectationError::ExpectedOpeningFailure { expected })
            }
        }
    }

    /// Admits an observed opening failure only for the exact expected class.
    pub fn validate_opening_failure(
        &self,
        observed: RejectedFormatErrorClass,
    ) -> Result<(), ScenarioExpectationError> {
        match self.definition.expectation {
            ScenarioExpectation::Success => Err(ScenarioExpectationError::ExpectedSuccess),
            ScenarioExpectation::OpeningFailure(expected) if expected == observed => Ok(()),
            ScenarioExpectation::OpeningFailure(expected) => {
                Err(ScenarioExpectationError::ErrorClassMismatch { expected, observed })
            }
        }
    }

    pub(crate) fn validate_artifact(
        scenario_id: &ScenarioId,
        error_class: Option<RejectedFormatErrorClass>,
        branches: &CoverageBranches,
    ) -> Result<(), ScenarioExpectationError> {
        let index = SCENARIOS
            .binary_search_by_key(&scenario_id.as_str(), |scenario| scenario.identifier)
            .map_err(|_| ScenarioExpectationError::UnknownScenarioId)?;
        let definition = SCENARIOS[index].validate()?;
        match (definition.expectation, error_class) {
            (ScenarioExpectation::Success, None)
            | (ScenarioExpectation::OpeningFailure(_), Some(_)) => {}
            (ScenarioExpectation::Success, Some(_)) => {
                return Err(ScenarioExpectationError::ExpectedSuccess);
            }
            (ScenarioExpectation::OpeningFailure(expected), None) => {
                return Err(ScenarioExpectationError::ExpectedOpeningFailure { expected });
            }
        }
        if let (ScenarioExpectation::OpeningFailure(expected), Some(observed)) =
            (definition.expectation, error_class)
            && expected != observed
        {
            return Err(ScenarioExpectationError::ErrorClassMismatch { expected, observed });
        }
        if let Some(branch) = definition
            .required_branches
            .iter()
            .find(|branch| !branches.contains(branch))
        {
            return Err(ScenarioExpectationError::MissingRequiredBranch { branch });
        }
        if let Some(branch) = definition
            .forbidden_branches
            .iter()
            .find(|branch| branches.contains(branch))
        {
            return Err(ScenarioExpectationError::ForbiddenBranchObserved { branch });
        }
        Ok(())
    }
}

/// Structured failure from binding an observed outcome to a closed scenario.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ScenarioExpectationError {
    /// The caller supplied no scenario in the protocol 1.2 inventory.
    UnknownScenarioId,
    /// The generated inventory contains an identifier rejected by `ScenarioId`.
    InvalidInventory,
    /// A success scenario produced a normalized opening failure.
    ExpectedSuccess,
    /// An expected-error scenario opened successfully.
    ExpectedOpeningFailure {
        /// The class that the inventory requires.
        expected: RejectedFormatErrorClass,
    },
    /// An expected-error scenario failed with a different normalized class.
    ErrorClassMismatch {
        /// The class that the inventory requires.
        expected: RejectedFormatErrorClass,
        /// The class returned by structured database opening.
        observed: RejectedFormatErrorClass,
    },
    /// The receipt omitted a branch required by its named scenario.
    MissingRequiredBranch {
        /// First missing branch in canonical order.
        branch: &'static str,
    },
    /// The receipt observed a branch forbidden at its scenario boundary.
    ForbiddenBranchObserved {
        /// First forbidden branch observed in canonical order.
        branch: &'static str,
    },
}

impl fmt::Display for ScenarioExpectationError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnknownScenarioId => formatter.write_str("unknown protocol scenario identifier"),
            Self::InvalidInventory => formatter.write_str("invalid generated scenario inventory"),
            Self::ExpectedSuccess => formatter.write_str("scenario requires a successful open"),
            Self::ExpectedOpeningFailure { .. } => {
                formatter.write_str("scenario requires an opening failure")
            }
            Self::ErrorClassMismatch { .. } => {
                formatter.write_str("opening failure class does not match the scenario")
            }
            Self::MissingRequiredBranch { .. } => {
                formatter.write_str("coverage receipt lacks a required scenario branch")
            }
            Self::ForbiddenBranchObserved { .. } => {
                formatter.write_str("coverage receipt contains a forbidden scenario branch")
            }
        }
    }
}

impl std::error::Error for ScenarioExpectationError {}

include!("scenario_inventory_generated.rs");

#[cfg(test)]
mod tests {
    use super::{
        BRANCH_REGISTRY_SHA256, INVENTORY_SHA256, ProtocolScenario, REGISTERED_BRANCHES, SCENARIOS,
        ScenarioExpectation, ScenarioExpectationError,
    };
    use crate::{RejectedFormatErrorClass, ScenarioId, Sha256Hasher, hex_digest};

    #[test]
    fn generated_inventory_is_bound_to_the_exact_protocol_document()
    -> Result<(), Box<dyn std::error::Error>> {
        let inventory = include_bytes!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../oracle/windows-dao/protocol/v1_2/scenarios.json"
        ));
        let mut hasher = Sha256Hasher::new();
        hasher.update(inventory)?;
        assert_eq!(hex_digest(hasher.finalize()?), INVENTORY_SHA256);

        let registry = include_bytes!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../oracle/windows-dao/protocol/v1_2/branch-registry.json"
        ));
        let mut hasher = Sha256Hasher::new();
        hasher.update(registry)?;
        assert_eq!(hex_digest(hasher.finalize()?), BRANCH_REGISTRY_SHA256);
        assert!(
            REGISTERED_BRANCHES
                .iter()
                .all(|branch| ProtocolScenario::is_registered_branch(branch))
        );

        for definition in SCENARIOS {
            let scenario = ProtocolScenario::resolve(definition.identifier)?;
            assert_eq!(scenario.scenario_id().as_str(), definition.identifier);
            assert_eq!(scenario.expectation(), definition.expectation);
            assert_eq!(scenario.required_branches(), definition.required_branches);
            assert_eq!(scenario.forbidden_branches(), definition.forbidden_branches);
        }
        Ok(())
    }

    #[test]
    fn outcome_validation_is_closed_and_class_exact() -> Result<(), Box<dyn std::error::Error>> {
        assert_eq!(
            ProtocolScenario::resolve("DAO-READ-NOT-IN-INVENTORY"),
            Err(ScenarioExpectationError::UnknownScenarioId)
        );
        let success = ProtocolScenario::resolve("DAO-READ-ROWS-SINGLE")?;
        assert_eq!(success.validate_success(), Ok(()));
        assert_eq!(
            success.validate_opening_failure(RejectedFormatErrorClass::UnsupportedVersion),
            Err(ScenarioExpectationError::ExpectedSuccess)
        );

        let rejected = ProtocolScenario::resolve("DAO-READ-OPEN-REJECT-JET4")?;
        assert_eq!(
            rejected.expectation(),
            ScenarioExpectation::OpeningFailure(RejectedFormatErrorClass::UnsupportedVersion)
        );
        assert!(matches!(
            rejected.validate_success(),
            Err(ScenarioExpectationError::ExpectedOpeningFailure { .. })
        ));
        assert_eq!(
            rejected.validate_opening_failure(RejectedFormatErrorClass::EncryptedDatabase),
            Err(ScenarioExpectationError::ErrorClassMismatch {
                expected: RejectedFormatErrorClass::UnsupportedVersion,
                observed: RejectedFormatErrorClass::EncryptedDatabase,
            })
        );
        assert_eq!(
            rejected.validate_opening_failure(RejectedFormatErrorClass::UnsupportedVersion),
            Ok(())
        );
        Ok(())
    }

    #[test]
    fn required_forbidden_and_overlapping_branches_fail_closed()
    -> Result<(), Box<dyn std::error::Error>> {
        let scenario = ProtocolScenario::resolve("DAO-READ-ROWS-SINGLE")?;
        let missing = scenario.required_branches()[1..]
            .iter()
            .copied()
            .map(str::to_owned)
            .collect();
        assert!(matches!(
            ProtocolScenario::validate_artifact(scenario.scenario_id(), None, &missing),
            Err(ScenarioExpectationError::MissingRequiredBranch { .. })
        ));
        let unknown = ScenarioId::new("DAO-READ-NOT-IN-INVENTORY")?;
        assert_eq!(
            ProtocolScenario::validate_artifact(&unknown, None, &missing),
            Err(ScenarioExpectationError::UnknownScenarioId)
        );

        let boundary = ProtocolScenario::resolve("DAO-READ-ALLOC-EXTENDED-SLOT-1-AT")?;
        let mut observed = boundary
            .required_branches()
            .iter()
            .copied()
            .map(str::to_owned)
            .collect::<crate::CoverageBranches>();
        observed.insert(boundary.forbidden_branches()[0].to_owned());
        assert!(matches!(
            ProtocolScenario::validate_artifact(boundary.scenario_id(), None, &observed),
            Err(ScenarioExpectationError::ForbiddenBranchObserved { .. })
        ));

        let invalid = super::ScenarioDefinition::new(
            "DAO-READ-OVERLAP",
            ScenarioExpectation::Success,
            &["open.header_page"],
            &["open.header_page"],
        );
        assert_eq!(
            invalid.validate(),
            Err(ScenarioExpectationError::InvalidInventory)
        );
        Ok(())
    }
}
