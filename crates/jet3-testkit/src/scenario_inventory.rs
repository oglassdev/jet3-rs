//! Typed bindings for the closed protocol 1.2 scenario inventory.

use core::fmt;

use crate::{RejectedFormatErrorClass, ScenarioId};

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
    expectation: ScenarioExpectation,
}

impl ProtocolScenario {
    /// Resolves only identifiers present in the checked protocol 1.2 inventory.
    pub fn resolve(value: &str) -> Result<Self, ScenarioExpectationError> {
        let index = SCENARIOS
            .binary_search_by_key(&value, |(identifier, _)| identifier)
            .map_err(|_| ScenarioExpectationError::UnknownScenarioId)?;
        let (identifier, expectation) = SCENARIOS[index];
        let scenario_id =
            ScenarioId::new(identifier).map_err(|_| ScenarioExpectationError::InvalidInventory)?;
        Ok(Self {
            scenario_id,
            expectation,
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
        self.expectation
    }

    /// Admits an observed successful open only for a success scenario.
    pub const fn validate_success(&self) -> Result<(), ScenarioExpectationError> {
        match self.expectation {
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
        match self.expectation {
            ScenarioExpectation::Success => Err(ScenarioExpectationError::ExpectedSuccess),
            ScenarioExpectation::OpeningFailure(expected) if expected == observed => Ok(()),
            ScenarioExpectation::OpeningFailure(expected) => {
                Err(ScenarioExpectationError::ErrorClassMismatch { expected, observed })
            }
        }
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
        }
    }
}

impl std::error::Error for ScenarioExpectationError {}

include!("scenario_inventory_generated.rs");

#[cfg(test)]
mod tests {
    use super::{
        INVENTORY_SHA256, ProtocolScenario, SCENARIOS, ScenarioExpectation,
        ScenarioExpectationError,
    };
    use crate::{RejectedFormatErrorClass, Sha256Hasher, hex_digest};

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

        for (identifier, expectation) in SCENARIOS {
            let scenario = ProtocolScenario::resolve(identifier)?;
            assert_eq!(scenario.scenario_id().as_str(), *identifier);
            assert_eq!(scenario.expectation(), *expectation);
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
}
