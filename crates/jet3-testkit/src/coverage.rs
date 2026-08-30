//! Coverage receipt: which protocol scenarios one reader run satisfies.

use serde::{Deserialize, Serialize};

use crate::{Branches, PROTOCOL_VERSION, Producer, SnapshotOutcome};

/// The checked-in protocol 1.2 scenario inventory.
pub const PROTOCOL_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/scenarios.json");

/// One scenario's read expectations from `scenarios.json`.
#[derive(Clone, Debug, Deserialize, PartialEq)]
pub struct Scenario {
    /// Scenario identifier.
    pub id: String,
    /// Expected reader outcome.
    pub operation: Operation,
    /// Reader branches the scenario must exercise.
    pub required_branches: Vec<String>,
    /// Boundary declaration, when the scenario sits at a physical threshold.
    pub boundary: Option<Boundary>,
}

/// Expected outcome of the reader on a scenario database.
#[derive(Clone, Debug, Deserialize, PartialEq)]
pub struct Operation {
    /// `success` or `expected_error`.
    pub expected_outcome: String,
    /// Required error class for `expected_error`.
    pub error_class: Option<String>,
}

/// Branches a boundary scenario forbids.
#[derive(Clone, Debug, Deserialize, PartialEq)]
pub struct Boundary {
    /// Branches that must not be observed.
    pub forbidden_branches: Vec<String>,
}

#[derive(Deserialize)]
struct Inventory {
    scenarios: Vec<Scenario>,
}

/// Parses a scenario inventory document.
pub fn parse_scenarios(json: &str) -> Result<Vec<Scenario>, serde_json::Error> {
    serde_json::from_str::<Inventory>(json).map(|inventory| inventory.scenarios)
}

/// Coverage verdict for one scenario.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ScenarioCoverage {
    /// Forbidden branches that were observed.
    pub forbidden_observed: Vec<String>,
    /// Scenario identifier.
    pub id: String,
    /// Required branches that were not observed.
    pub missing_branches: Vec<String>,
    /// Whether the reader outcome matched the expected outcome.
    pub outcome_matches: bool,
    /// Whether every check above passed.
    pub satisfied: bool,
}

/// The `coverage.json` document written beside a snapshot.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct CoverageReceipt {
    /// Every reader branch observed.
    pub branches: Vec<String>,
    /// SHA-256 of the exact database bytes.
    pub database_sha256: String,
    /// Always `coverage_receipt`.
    pub document_type: &'static str,
    /// Error class when the reader rejected the database.
    pub error_class: Option<String>,
    /// `success` or `opening_failure`.
    pub outcome: &'static str,
    /// Producing implementation.
    pub producer: Producer,
    /// Always `1.2.0`.
    pub protocol_version: &'static str,
    /// Scenario the database was generated for.
    pub scenario_id: String,
    /// Verdict for every inventory scenario.
    pub scenarios: Vec<ScenarioCoverage>,
}

/// Evaluates one reader run against every scenario.
#[must_use]
pub fn coverage(
    scenario_id: &str,
    producer: Producer,
    database_sha256: String,
    outcome: &SnapshotOutcome,
    scenarios: &[Scenario],
) -> CoverageReceipt {
    let (branches, error_class) = match outcome {
        SnapshotOutcome::Snapshot { branches, .. } => (branches, None),
        SnapshotOutcome::OpeningFailure {
            branches,
            error_class,
            ..
        } => (branches, Some(*error_class)),
    };
    let scenarios = scenarios
        .iter()
        .map(|scenario| evaluate(scenario, branches, error_class))
        .collect();
    CoverageReceipt {
        branches: branches.iter().cloned().collect(),
        database_sha256,
        document_type: "coverage_receipt",
        error_class: error_class.map(str::to_owned),
        outcome: if error_class.is_some() {
            "opening_failure"
        } else {
            "success"
        },
        producer,
        protocol_version: PROTOCOL_VERSION,
        scenario_id: scenario_id.to_owned(),
        scenarios,
    }
}

fn evaluate(
    scenario: &Scenario,
    branches: &Branches,
    error_class: Option<&str>,
) -> ScenarioCoverage {
    let outcome_matches = match error_class {
        None => scenario.operation.expected_outcome == "success",
        Some(class) => scenario.operation.error_class.as_deref() == Some(class),
    };
    let missing_branches: Vec<String> = scenario
        .required_branches
        .iter()
        .filter(|branch| !branches.contains(*branch))
        .cloned()
        .collect();
    let forbidden_observed: Vec<String> = scenario
        .boundary
        .iter()
        .flat_map(|boundary| &boundary.forbidden_branches)
        .filter(|branch| branches.contains(*branch))
        .cloned()
        .collect();
    ScenarioCoverage {
        satisfied: outcome_matches && missing_branches.is_empty() && forbidden_observed.is_empty(),
        forbidden_observed,
        id: scenario.id.clone(),
        missing_branches,
        outcome_matches,
    }
}

#[cfg(test)]
mod tests {
    use super::{PROTOCOL_SCENARIOS, coverage, parse_scenarios};
    use crate::{Branches, Producer, SnapshotOutcome};

    #[test]
    fn opening_failures_satisfy_only_their_error_class() -> Result<(), serde_json::Error> {
        let scenarios = parse_scenarios(PROTOCOL_SCENARIOS)?;
        assert!(scenarios.len() > 90);
        let outcome = SnapshotOutcome::OpeningFailure {
            error_class: "unsupported_version",
            database_sha256: "0".repeat(64),
            branches: [
                "open.signature_geometry",
                "open.rejected_format",
                "open.header_page",
            ]
            .into_iter()
            .map(str::to_owned)
            .collect::<Branches>(),
        };
        let producer = Producer {
            kind: "rust",
            source_revision: "test".to_owned(),
        };
        let receipt = coverage(
            "DAO-READ-OPEN-REJECT-JET4",
            producer,
            "0".repeat(64),
            &outcome,
            &scenarios,
        );
        let satisfied: Vec<_> = receipt
            .scenarios
            .iter()
            .filter(|scenario| scenario.satisfied)
            .map(|scenario| scenario.id.as_str())
            .collect();
        assert_eq!(satisfied, ["DAO-READ-OPEN-REJECT-JET4"]);
        assert_eq!(receipt.outcome, "opening_failure");
        Ok(())
    }
}
