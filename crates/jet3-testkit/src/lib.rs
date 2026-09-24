#![forbid(unsafe_code)]
#![doc = "Test-only support for reproducible fixtures and independent checks."]

mod coverage;
mod scenarios;
mod semantic_reader;
mod semantic_snapshot;
pub mod semantic_values;
pub mod synthetic;
mod write_fixture;

pub use coverage::{
    Boundary, CoverageReceipt, Operation, PROTOCOL_SCENARIOS, Scenario, ScenarioCoverage, coverage,
    parse_scenarios,
};
pub use scenarios::{
    CREATION_INDEX_SCENARIOS, INDEXED_UPDATE_SCENARIOS, ROW_ALLOCATION_SCENARIOS,
    ROW_REPLACEMENT_SCENARIOS, ROW_UPDATE_SCENARIOS, UPDATE_SCENARIOS,
};
pub use semantic_reader::{Branches, SnapshotOptions, SnapshotOutcome, snapshot_bytes};
pub use semantic_snapshot::{
    Column, Index, IndexField, PROTOCOL_VERSION, Producer, PropertyMap, RawField, Relationship,
    RelationshipField, Row, Scalar, SemanticSnapshot, SnapshotError, Table, TableKind, TypedValue,
    canonical_json, hex, reader_error, row_from_values, sha256_hex, validate_scenario_id,
    validate_source_revision,
};
pub use write_fixture::{WRITE_SCENARIOS, write_fixture};
