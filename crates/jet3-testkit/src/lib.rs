#![forbid(unsafe_code)]
#![doc = "Test-only support for reproducible fixtures and independent checks."]

mod canonical_json;
mod canonical_snapshot;
mod classifier_snapshot;

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
mod tests {
    use super::fixture_format_name;

    #[test]
    fn fixture_metadata_uses_the_library_scope() {
        assert_eq!(fixture_format_name(), "Access 97 / Jet 3");
    }
}
