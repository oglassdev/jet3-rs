#![forbid(unsafe_code)]
#![doc = "Reproducible fixtures and versioned semantic comparison support."]

mod canonical_json;
mod canonical_snapshot;
mod classifier_snapshot;
mod semantic_json;
mod semantic_protocol;
mod semantic_snapshot;
mod sha256;

pub use canonical_snapshot::{
    CanonicalSnapshot, Column, FiniteF32, FiniteF64, Guid, HexString, Index, IndexField,
    InvariantDateTime, InvariantDecimal, Producer, ProducerKind, PropertyMap, RawPreservation,
    Relationship, RelationshipField, Row, ScenarioId, Sha256, SnapshotError, Table, TableKind,
    TypedValue,
};
pub use classifier_snapshot::{
    ClassifiedFixture, ClassifierSnapshot, ClassifierSnapshotError, CommitId, PageKindHistogram,
    classify_fixture,
};
pub use semantic_protocol::{
    CoverageBranches, CoverageReceipt, SemanticColumn, SemanticIndex, SemanticProtocolError,
    SemanticRow, SemanticSnapshot, SemanticTable,
};
pub use semantic_snapshot::{
    SemanticSnapshotArtifacts, SemanticSnapshotError, SemanticSnapshotOptions,
    UnsupportedValueForm, snapshot_database, snapshot_database_with_receipt,
};
pub use sha256::{Sha256Hasher, Sha256LengthError, hex_digest, sha256_hex};

/// Returns the format name used in fixture metadata.
#[must_use]
pub const fn fixture_format_name() -> &'static str {
    jet3::FORMAT_NAME
}

#[cfg(test)]
mod canonical_order_tests;
#[cfg(test)]
mod canonical_value_tests;
#[cfg(test)]
mod semantic_protocol_validation_tests;
#[cfg(test)]
mod semantic_snapshot_tests;

#[cfg(test)]
mod tests {
    use super::fixture_format_name;

    #[test]
    fn fixture_metadata_uses_the_library_scope() {
        assert_eq!(fixture_format_name(), "Access 97 / Jet 3");
    }
}
