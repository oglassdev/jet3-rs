#![forbid(unsafe_code)]
#![doc = "Test-only support for reproducible fixtures and independent checks."]

mod classifier_snapshot;
mod coverage;
mod semantic_reader;
mod semantic_snapshot;
pub mod semantic_values;
pub mod synthetic;

pub use classifier_snapshot::{
    ClassifiedFixture, ClassifierSnapshot, ClassifierSnapshotError, CommitId, PageKindHistogram,
    Sha256, classify_fixture,
};
pub use coverage::{
    Boundary, CoverageReceipt, Operation, PROTOCOL_SCENARIOS, Scenario, ScenarioCoverage, coverage,
    parse_scenarios,
};
pub use semantic_reader::{Branches, SnapshotOptions, SnapshotOutcome, snapshot_bytes};
pub use semantic_snapshot::{
    Column, Index, IndexField, PROTOCOL_VERSION, Producer, PropertyMap, RawField, Relationship,
    RelationshipField, Row, Scalar, SemanticSnapshot, SnapshotError, Table, TableKind, TypedValue,
    canonical_json, hex, reader_error, row_from_values, sha256_hex, validate_scenario_id,
    validate_source_revision,
};

/// Returns the format name used in fixture metadata.
#[must_use]
pub const fn fixture_format_name() -> &'static str {
    jet3::FORMAT_NAME
}

mod write_fixture;
pub use write_fixture::{WRITE_SCENARIOS, write_fixture};
