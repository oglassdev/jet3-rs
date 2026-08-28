//! Versioned protocol 1.2 semantic snapshot and Rust coverage receipt.

use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::fmt;

use crate::{
    Producer, PropertyMap, RawPreservation, Relationship, ScenarioId, Sha256, SnapshotError,
    TableKind, TypedValue,
};

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
    /// SHA-256 binding the traversed allocated-page sets.
    pub allocated_set_sha256: Sha256,
    /// Closed registry branch identifiers observed by the producer.
    pub branches: BTreeSet<String>,
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
        self.tables
            .sort_by(|left, right| left.name.cmp(&right.name));
        ensure_unique(
            self.tables.iter().map(|table| table.name.as_str()),
            "$.tables",
        )?;
        for table in &mut self.tables {
            table.columns.sort_by_key(|column| column.ordinal);
            ensure_unique(
                table.columns.iter().map(|column| column.name.as_str()),
                "$.tables[].columns",
            )?;
            for (expected, column) in table.columns.iter().enumerate() {
                if column.ordinal != expected as u64 {
                    return Err(invalid(
                        "$.tables[].columns",
                        "ordinals must be contiguous from zero",
                    ));
                }
            }
            table
                .indexes
                .sort_by(|left, right| left.name.cmp(&right.name));
            ensure_unique(
                table.indexes.iter().map(|index| index.name.as_str()),
                "$.tables[].indexes",
            )?;
            let columns = table
                .columns
                .iter()
                .map(|column| column.name.clone())
                .collect::<BTreeSet<_>>();
            for index in &table.indexes {
                if index.fields.is_empty()
                    || index
                        .fields
                        .iter()
                        .any(|field| !columns.contains(&field.name))
                {
                    return Err(invalid(
                        "$.tables[].indexes[].fields",
                        "unknown or empty field list",
                    ));
                }
            }
            canonicalize_rows(table, &columns)?;
        }
        self.relationships
            .sort_by(|left, right| left.name.cmp(&right.name));
        ensure_unique(
            self.relationships.iter().map(|value| value.name.as_str()),
            "$.relationships",
        )?;
        validate_relationships(&self.tables, &self.relationships)?;
        self.raw_preservation
            .sort_by(|left, right| left.semantic_path.cmp(&right.semantic_path));
        ensure_unique(
            self.raw_preservation
                .iter()
                .map(|value| value.semantic_path.as_str()),
            "$.raw_preservation",
        )?;
        Ok(())
    }

    /// Returns compact canonical UTF-8 JSON with one trailing newline.
    pub fn to_canonical_json(&self) -> Result<Vec<u8>, SemanticProtocolError> {
        crate::semantic_json::write_snapshot(self)
    }
}

impl CoverageReceipt {
    /// Returns compact canonical UTF-8 JSON with one trailing newline.
    pub fn to_canonical_json(&self) -> Result<Vec<u8>, SemanticProtocolError> {
        crate::semantic_json::write_receipt(self)
    }
}

pub(crate) fn sha256(bytes: &[u8]) -> Result<Sha256, SemanticProtocolError> {
    // `SRC-0027` binds the protocol digest name to FIPS SHA-256.
    let text = crate::sha256_hex(bytes)
        .map_err(|_| invalid("SHA-256", "input length is not representable"))?;
    Sha256::new(text).map_err(Into::into)
}

fn canonicalize_rows(
    table: &mut SemanticTable,
    columns: &BTreeSet<String>,
) -> Result<(), SemanticProtocolError> {
    let mut keyed = Vec::with_capacity(table.rows.len());
    for mut row in table.rows.drain(..) {
        if row.values.keys().cloned().collect::<BTreeSet<_>>() != *columns {
            return Err(invalid(
                "$.tables[].rows[].values",
                "keys must equal declared columns",
            ));
        }
        let bytes =
            crate::semantic_json::write_properties(&row.values, "$.tables[].rows[].values")?;
        row.canonical_key = sha256(&bytes)?;
        keyed.push((row.canonical_key.clone(), bytes, row));
    }
    keyed.sort_by(|left, right| left.0.cmp(&right.0).then_with(|| left.1.cmp(&right.1)));
    let mut previous: Option<(Sha256, Vec<u8>, u64)> = None;
    for (digest, bytes, mut row) in keyed {
        row.duplicate_ordinal = match &previous {
            Some((prior_digest, prior_bytes, ordinal)) if prior_digest == &digest => {
                if prior_bytes != &bytes {
                    return Err(SemanticProtocolError::RowHashCollision {
                        table: table.name.clone(),
                    });
                }
                ordinal + 1
            }
            _ => 0,
        };
        previous = Some((digest, bytes, row.duplicate_ordinal));
        table.rows.push(row);
    }
    Ok(())
}

fn validate_relationships(
    tables: &[SemanticTable],
    relationships: &[Relationship],
) -> Result<(), SemanticProtocolError> {
    let known = tables
        .iter()
        .map(|table| {
            (
                table.name.as_str(),
                table
                    .columns
                    .iter()
                    .map(|column| column.name.as_str())
                    .collect::<BTreeSet<_>>(),
            )
        })
        .collect::<BTreeMap<_, _>>();
    for relationship in relationships {
        let local = known
            .get(relationship.table.as_str())
            .ok_or_else(|| invalid("$.relationships[].table", "unknown table"))?;
        let foreign = known
            .get(relationship.foreign_table.as_str())
            .ok_or_else(|| invalid("$.relationships[].foreign_table", "unknown table"))?;
        if relationship.fields.is_empty()
            || relationship.fields.iter().any(|pair| {
                !local.contains(pair.field.as_str())
                    || !foreign.contains(pair.foreign_field.as_str())
            })
        {
            return Err(invalid(
                "$.relationships[].fields",
                "unknown or empty field list",
            ));
        }
    }
    Ok(())
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
    Ok(())
}

fn ensure_unique<'a>(
    values: impl Iterator<Item = &'a str>,
    path: &str,
) -> Result<(), SemanticProtocolError> {
    let mut seen = BTreeSet::new();
    for value in values {
        if value.is_empty() || !seen.insert(value) {
            return Err(invalid(path, "names must be non-empty and unique"));
        }
    }
    Ok(())
}

fn invalid(path: impl Into<String>, reason: &'static str) -> SemanticProtocolError {
    SemanticProtocolError::InvalidModel {
        path: path.into(),
        reason,
    }
}
