//! Versioned protocol 1.2 semantic snapshot and Rust coverage receipt.

use std::error::Error;
use std::fmt;

use crate::{
    CoverageReceiptOutcome, HexString, Producer, PropertyMap, RawPreservation, Relationship,
    ScenarioId, Sha256, SnapshotError, TableKind, TypedValue,
};

#[path = "semantic_protocol_rows.rs"]
mod rows;
#[path = "semantic_protocol_validation.rs"]
mod validation;

use rows::{canonical_row_bytes, canonicalize_rows};

/// A protocol 1.2 column, containing only facts in the comparison projection.
#[derive(Clone, Debug, PartialEq)]
pub struct SemanticColumn {
    /// Canonical column name.
    pub name: String,
    /// Zero-based column ordinal.
    pub ordinal: u64,
    /// DAO `DataTypeEnum` constant name.
    pub dao_type: String,
    /// Whether the column generates values automatically.
    pub auto_increment: bool,
    /// Declared column size when the producer can establish it.
    pub size: Option<u64>,
    /// Losslessly retained numeric column attributes.
    pub attributes: i64,
    /// Canonically ordered semantic properties.
    pub properties: PropertyMap,
}

/// A protocol 1.2 index, containing only independently decoded facts.
#[derive(Clone, Debug, PartialEq)]
pub struct SemanticIndex {
    /// Canonical index name.
    pub name: String,
    /// Whether this is the table's primary index.
    pub primary: bool,
    /// Whether indexed keys must be unique.
    pub unique: bool,
    /// Whether every indexed field is required.
    pub required: bool,
    /// Declared index fields in key order.
    pub fields: Vec<crate::IndexField>,
    /// Canonically ordered semantic properties.
    pub properties: PropertyMap,
}

/// A protocol 1.2 row with content-derived identity.
#[derive(Clone, Debug, PartialEq)]
pub struct SemanticRow {
    /// SHA-256 of the row's canonical value map.
    pub canonical_key: Sha256,
    /// Zero-based ordinal among byte-identical rows sharing the key.
    pub duplicate_ordinal: u64,
    /// Canonically ordered values keyed by column name.
    pub values: PropertyMap,
}

/// A protocol 1.2 table.
#[derive(Clone, Debug, PartialEq)]
pub struct SemanticTable {
    /// Canonical table name.
    pub name: String,
    /// Semantic table kind.
    pub kind: TableKind,
    /// Losslessly retained numeric table attributes.
    pub attributes: i64,
    /// Columns in ordinal order.
    pub columns: Vec<SemanticColumn>,
    /// Indexes in canonical name order.
    pub indexes: Vec<SemanticIndex>,
    /// Canonically ordered semantic properties.
    pub properties: PropertyMap,
    /// Rows in content-derived canonical order.
    pub rows: Vec<SemanticRow>,
}

/// Canonical semantic snapshot emitted by the Rust protocol 1.2 producer.
#[derive(Clone, Debug, PartialEq)]
pub struct SemanticSnapshot {
    /// Closed protocol scenario identifier.
    pub scenario_id: ScenarioId,
    /// Producer identity and exact source revision.
    pub producer: Producer,
    /// SHA-256 of the exact database input bytes.
    pub database_sha256: Sha256,
    /// Canonically ordered database properties.
    pub database_properties: PropertyMap,
    /// User tables in canonical name order.
    pub tables: Vec<SemanticTable>,
    /// Relationships in canonical name order.
    pub relationships: Vec<Relationship>,
    /// Lossless raw facts not represented beside a semantic value.
    pub raw_preservation: Vec<RawPreservation>,
    /// Producer-specific facts excluded from the comparison projection.
    pub producer_extensions: PropertyMap,
}

/// Commit-bound coverage evidence emitted beside a Rust snapshot.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CoverageReceipt {
    /// Closed protocol scenario identifier.
    pub scenario_id: ScenarioId,
    /// Exact producer source revision.
    pub source_revision: String,
    /// SHA-256 of the exact database input bytes.
    pub database_sha256: Sha256,
    /// Whether traversal completed or opening failed before allocation evidence applied.
    pub outcome: CoverageReceiptOutcome,
    /// Closed registry branch identifiers observed by the producer.
    pub branches: CoverageBranches,
}

/// A canonically ordered set of observed coverage branch identifiers.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct CoverageBranches {
    values: Vec<String>,
}

impl CoverageBranches {
    /// Constructs an empty branch set.
    #[must_use]
    pub const fn new() -> Self {
        Self { values: Vec::new() }
    }

    /// Inserts a branch while preserving canonical ordering.
    pub fn insert(&mut self, value: String) -> bool {
        match self.values.binary_search(&value) {
            Ok(_) => false,
            Err(index) => {
                self.values.insert(index, value);
                true
            }
        }
    }

    /// Iterates over branches in canonical order.
    pub fn iter(&self) -> impl ExactSizeIterator<Item = &String> {
        self.values.iter()
    }

    /// Returns whether `value` was observed.
    #[must_use]
    pub fn contains(&self, value: &str) -> bool {
        self.values
            .binary_search_by(|candidate| candidate.as_str().cmp(value))
            .is_ok()
    }

    /// Returns the number of observed branches.
    #[must_use]
    pub const fn len(&self) -> usize {
        self.values.len()
    }

    /// Returns whether no branches were observed.
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.values.is_empty()
    }

    pub(crate) const fn capacity(&self) -> usize {
        self.values.capacity()
    }

    pub(crate) fn try_reserve_exact(
        &mut self,
        additional: usize,
    ) -> Result<(), std::collections::TryReserveError> {
        self.values.try_reserve_exact(additional)
    }
}

impl FromIterator<String> for CoverageBranches {
    fn from_iter<T: IntoIterator<Item = String>>(values: T) -> Self {
        let mut branches = Self::new();
        for value in values {
            branches.insert(value);
        }
        branches
    }
}

impl<'a> IntoIterator for &'a CoverageBranches {
    type Item = &'a String;
    type IntoIter = std::slice::Iter<'a, String>;

    fn into_iter(self) -> Self::IntoIter {
        self.values.iter()
    }
}

/// A protocol 1.2 model or canonical serialization failure.
#[derive(Clone, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub enum SemanticProtocolError {
    /// A shared canonical value failed validation.
    Snapshot(SnapshotError),
    /// A lossless value lacks its required raw bytes.
    MissingRaw {
        /// JSON-style path to the invalid value.
        path: String,
    },
    /// A null or Boolean value incorrectly carries raw bytes.
    UnexpectedRaw {
        /// JSON-style path to the invalid value.
        path: String,
    },
    /// A text value lacks its explicit decoding code page.
    MissingCodePage {
        /// JSON-style path to the invalid value.
        path: String,
    },
    /// The model violates a protocol 1.2 structural invariant.
    InvalidModel {
        /// JSON-style path to the invalid object.
        path: String,
        /// Stable description of the violated invariant.
        reason: &'static str,
    },
    /// Distinct canonical rows produced the same SHA-256 identity.
    RowHashCollision {
        /// Table containing the colliding rows.
        table: String,
    },
}

impl fmt::Display for SemanticProtocolError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Snapshot(source) => write!(formatter, "snapshot value: {source}"),
            Self::MissingRaw { path } => write!(formatter, "{path} lacks required raw_hex"),
            Self::UnexpectedRaw { path } => write!(formatter, "{path} must not carry raw_hex"),
            Self::MissingCodePage { path } => write!(formatter, "{path} lacks required code_page"),
            Self::InvalidModel { path, reason } => write!(formatter, "{path}: {reason}"),
            Self::RowHashCollision { table } => {
                write!(
                    formatter,
                    "table {table:?} has distinct rows with the same SHA-256"
                )
            }
        }
    }
}

impl Error for SemanticProtocolError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Snapshot(source) => Some(source),
            _ => None,
        }
    }
}

impl From<SnapshotError> for SemanticProtocolError {
    fn from(source: SnapshotError) -> Self {
        Self::Snapshot(source)
    }
}

impl SemanticSnapshot {
    pub(crate) fn new(
        scenario_id: ScenarioId,
        producer: Producer,
        database_sha256: Sha256,
    ) -> Self {
        Self {
            scenario_id,
            producer,
            database_sha256,
            database_properties: PropertyMap::new(),
            tables: Vec::new(),
            relationships: Vec::new(),
            raw_preservation: Vec::new(),
            producer_extensions: PropertyMap::new(),
        }
    }

    /// Sorts the model, validates cross-references, and derives row identities.
    pub fn canonicalize(&mut self) -> Result<(), SemanticProtocolError> {
        self.canonicalize_model(true)
    }

    pub(crate) fn canonicalize_precomputed_rows(&mut self) -> Result<(), SemanticProtocolError> {
        self.canonicalize_model(false)
    }

    fn canonicalize_model(
        &mut self,
        derive_row_identities: bool,
    ) -> Result<(), SemanticProtocolError> {
        self.tables
            .sort_by(|left, right| left.name.cmp(&right.name));
        for table in &mut self.tables {
            table.columns.sort_by_key(|column| column.ordinal);
            table
                .indexes
                .sort_by(|left, right| left.name.cmp(&right.name));
            if derive_row_identities {
                canonicalize_rows(table)?;
            }
        }
        self.relationships
            .sort_by(|left, right| left.name.cmp(&right.name));
        self.raw_preservation
            .sort_by(|left, right| left.semantic_path.cmp(&right.semantic_path));
        validation::validate_snapshot(self)
    }

    /// Returns compact canonical UTF-8 JSON with one trailing newline.
    pub fn to_canonical_json(&self) -> Result<Vec<u8>, SemanticProtocolError> {
        self.validate()?;
        crate::semantic_json::write_snapshot(self)
    }

    pub(crate) fn validate(&self) -> Result<(), SemanticProtocolError> {
        validation::validate_snapshot(self)
    }
}

impl CoverageReceipt {
    /// Returns compact canonical UTF-8 JSON with one trailing newline.
    pub fn to_canonical_json(&self) -> Result<Vec<u8>, SemanticProtocolError> {
        self.validate()?;
        crate::semantic_json::write_receipt(self)
    }

    pub(crate) fn validate(&self) -> Result<(), SemanticProtocolError> {
        if !crate::canonical_snapshot::source_revision_is_valid(&self.source_revision) {
            return Err(invalid(
                "$.source_revision",
                "source revision must contain 1 through 200 Unicode scalar values",
            ));
        }
        let rejected = self.branches.contains("open.rejected_format");
        match &self.outcome {
            CoverageReceiptOutcome::Success { .. } if rejected => Err(invalid(
                "$.branches",
                "successful traversal must not claim rejected-format coverage",
            )),
            CoverageReceiptOutcome::OpeningFailure { .. }
                if self.branches.iter().map(String::as_str).eq([
                    "open.header_page",
                    "open.rejected_format",
                    "open.signature_geometry",
                ]) =>
            {
                Ok(())
            }
            CoverageReceiptOutcome::OpeningFailure { .. } => Err(invalid(
                "$.branches",
                "opening failure must contain exactly the rejected-format opening branches",
            )),
            CoverageReceiptOutcome::Success { .. } => Ok(()),
        }
    }
}

pub(crate) fn sha256(bytes: &[u8]) -> Result<Sha256, SemanticProtocolError> {
    // `SRC-0027` binds the protocol digest name to FIPS SHA-256.
    let text = crate::sha256_hex(bytes)
        .map_err(|_| invalid("SHA-256", "input length is not representable"))?;
    Sha256::new(text).map_err(Into::into)
}

pub(super) fn validate_property_map(
    values: &PropertyMap,
    path: &str,
) -> Result<(), SemanticProtocolError> {
    for (name, value) in values {
        validate_typed_value(value, &format!("{path}/{name}"))?;
    }
    Ok(())
}

fn validate_typed_value(value: &TypedValue, path: &str) -> Result<(), SemanticProtocolError> {
    let (raw, code_page, raw_exempt, needs_code_page) = match value {
        TypedValue::Null { raw_hex } => (raw_hex, None, true, false),
        TypedValue::Boolean { raw_hex, .. } => (raw_hex, None, true, false),
        TypedValue::Text {
            raw_hex, code_page, ..
        }
        | TypedValue::Memo {
            raw_hex, code_page, ..
        } => (raw_hex, *code_page, false, true),
        TypedValue::Byte { raw_hex, .. }
        | TypedValue::Integer { raw_hex, .. }
        | TypedValue::Long { raw_hex, .. }
        | TypedValue::Single { raw_hex, .. }
        | TypedValue::Double { raw_hex, .. }
        | TypedValue::Decimal { raw_hex, .. }
        | TypedValue::Currency { raw_hex, .. }
        | TypedValue::DateTime { raw_hex, .. }
        | TypedValue::Binary { raw_hex, .. }
        | TypedValue::Guid { raw_hex, .. }
        | TypedValue::Ole { raw_hex, .. } => (raw_hex, None, false, false),
    };
    if raw_exempt && raw.is_some() {
        return Err(SemanticProtocolError::UnexpectedRaw { path: path.into() });
    }
    if !raw_exempt && raw.is_none() {
        return Err(SemanticProtocolError::MissingRaw { path: path.into() });
    }
    if needs_code_page && code_page.is_none() {
        return Err(SemanticProtocolError::MissingCodePage { path: path.into() });
    }
    if let TypedValue::Text {
        value,
        raw_hex: Some(raw),
        code_page: Some(code_page),
    }
    | TypedValue::Memo {
        value,
        raw_hex: Some(raw),
        code_page: Some(code_page),
    } = value
    {
        validate_text_payload(value, raw, *code_page, path)?;
    }
    if let TypedValue::Ole {
        value,
        raw_hex: Some(raw),
    } = value
        && value != raw
    {
        return Err(invalid(
            path,
            "OLE raw_hex must equal logical payload bytes",
        ));
    }
    Ok(())
}

fn validate_text_payload(
    value: &str,
    raw_hex: &HexString,
    code_page: u32,
    path: &str,
) -> Result<(), SemanticProtocolError> {
    let code_page = match code_page {
        1251 => jet3::TextCodePage::Windows1251,
        1252 => jet3::TextCodePage::Windows1252,
        _ => {
            return Err(invalid(
                path,
                "text code_page must be Windows-1251 or Windows-1252",
            ));
        }
    };
    let mut raw = Vec::new();
    raw.try_reserve_exact(raw_hex.as_str().len() / 2)
        .map_err(|_| {
            invalid(
                path,
                "text raw_hex cannot be decoded within resource limits",
            )
        })?;
    for pair in raw_hex.as_str().as_bytes().chunks_exact(2) {
        let digits = std::str::from_utf8(pair)
            .map_err(|_| invalid(path, "text raw_hex must be valid lowercase hexadecimal"))?;
        raw.push(
            u8::from_str_radix(digits, 16)
                .map_err(|_| invalid(path, "text raw_hex must be valid lowercase hexadecimal"))?,
        );
    }
    let mut budget = jet3::ResourceBudget::new(jet3::ResourceLimits::default());
    let decoded = jet3::decode_text(&raw, code_page, &mut budget)
        .map_err(|_| invalid(path, "text raw_hex contains an undefined code-page byte"))?;
    if decoded.as_str() != value {
        return Err(invalid(path, "text raw_hex must decode exactly to value"));
    }
    Ok(())
}

fn invalid(path: impl Into<String>, reason: &'static str) -> SemanticProtocolError {
    SemanticProtocolError::InvalidModel {
        path: path.into(),
        reason,
    }
}

#[cfg(test)]
mod row_key_tests {
    use super::{canonical_row_bytes, sha256};
    use crate::{HexString, PropertyMap, TypedValue};

    const FIXTURES: &str =
        include_str!("../../../oracle/windows-dao/protocol/v1_2/fixtures/row-key-vectors.tsv");
    const LONG_VALUE_FIXTURES: &str = include_str!(
        "../../../oracle/windows-dao/protocol/v1_2/fixtures/long-value-comparison-vectors.tsv"
    );

    fn hex(value: &str) -> Result<HexString, Box<dyn std::error::Error>> {
        Ok(HexString::new(value)?)
    }

    fn values_for(case: &str) -> Result<PropertyMap, Box<dyn std::error::Error>> {
        Ok(match case {
            "reordered_null_boolean" => PropertyMap::from([
                ("Zulu".into(), TypedValue::Null { raw_hex: None }),
                (
                    "Alpha".into(),
                    TypedValue::Boolean {
                        value: true,
                        raw_hex: None,
                    },
                ),
            ]),
            "unicode_and_escapes" => PropertyMap::from([
                (
                    "説明".into(),
                    TypedValue::Text {
                        value: "café ??".into(),
                        raw_hex: Some(hex("636166e9203f3f")?),
                        code_page: Some(1252),
                    },
                ),
                (
                    "Escapes".into(),
                    TypedValue::Memo {
                        value: "quote \" slash \\ newline\n tab\t".into(),
                        raw_hex: Some(hex(
                            "71756f7465202220736c617368205c206e65776c696e650a2074616209",
                        )?),
                        code_page: Some(1252),
                    },
                ),
            ]),
            "raw_hex_and_typed_shapes" => PropertyMap::from([
                ("Nothing".into(), TypedValue::Null { raw_hex: None }),
                (
                    "Raw".into(),
                    TypedValue::Binary {
                        value: hex("00ff10")?,
                        raw_hex: Some(hex("00ff10")?),
                    },
                ),
                (
                    "Count".into(),
                    TypedValue::Long {
                        value: -7,
                        raw_hex: Some(hex("f9ffffff")?),
                    },
                ),
                (
                    "Enabled".into(),
                    TypedValue::Boolean {
                        value: false,
                        raw_hex: None,
                    },
                ),
            ]),
            other => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    format!("unknown row-key fixture {other}"),
                )
                .into());
            }
        })
    }

    fn fixture_field<'a>(
        value: Option<&'a str>,
        name: &str,
        line: usize,
    ) -> Result<&'a str, std::io::Error> {
        value.ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                format!("row-key fixture line {line} is missing {name}"),
            )
        })
    }

    #[test]
    fn row_keys_match_shared_canonical_vectors() -> Result<(), Box<dyn std::error::Error>> {
        let mut seen = 0;
        for (line_index, line) in FIXTURES
            .lines()
            .enumerate()
            .filter(|(_, line)| !line.starts_with('#'))
        {
            let mut fields = line.split('\t');
            let case = fixture_field(fields.next(), "case", line_index + 1)?;
            let input_json = fixture_field(fields.next(), "input JSON", line_index + 1)?;
            let canonical_json = fixture_field(fields.next(), "canonical JSON", line_index + 1)?;
            let expected = fixture_field(fields.next(), "expected SHA-256", line_index + 1)?;
            assert!(fields.next().is_none(), "extra field in fixture {case}");
            assert_ne!(
                input_json, canonical_json,
                "fixture must exercise reordering"
            );

            let bytes = canonical_row_bytes(&values_for(case)?)?;
            assert_eq!(bytes, format!("{canonical_json}\n").as_bytes(), "{case}");
            assert_eq!(sha256(&bytes)?.as_str(), expected, "{case}");
            seen += 1;
        }
        assert_eq!(seen, 3);
        Ok(())
    }

    #[test]
    fn long_value_row_keys_depend_only_on_shared_logical_payload_vectors()
    -> Result<(), Box<dyn std::error::Error>> {
        let mut seen = 0;
        for (line_index, line) in LONG_VALUE_FIXTURES
            .lines()
            .enumerate()
            .filter(|(_, line)| !line.starts_with('#'))
        {
            let mut fields = line.split('\t');
            let case = fixture_field(fields.next(), "case", line_index + 1)?;
            let kind = fixture_field(fields.next(), "kind", line_index + 1)?;
            let _storage = fixture_field(fields.next(), "storage", line_index + 1)?;
            let semantic = fixture_field(fields.next(), "semantic value", line_index + 1)?;
            let payload = fixture_field(fields.next(), "logical payload", line_index + 1)?;
            let header = fixture_field(fields.next(), "Jet header", line_index + 1)?;
            let expected = fixture_field(fields.next(), "row SHA-256", line_index + 1)?;
            assert!(fields.next().is_none(), "extra field in fixture {case}");
            assert_eq!(header.len(), 24, "{case}");
            let value = match kind {
                "memo" => TypedValue::Memo {
                    value: semantic.into(),
                    raw_hex: Some(hex(payload)?),
                    code_page: Some(1252),
                },
                "ole" => TypedValue::Ole {
                    value: hex(semantic)?,
                    raw_hex: Some(hex(payload)?),
                },
                other => return Err(format!("unknown long-value kind {other}").into()),
            };
            let bytes = canonical_row_bytes(&PropertyMap::from([("Value".into(), value)]))?;
            assert_eq!(sha256(&bytes)?.as_str(), expected, "{case}");
            seen += 1;
        }
        assert_eq!(seen, 8);
        Ok(())
    }
}
