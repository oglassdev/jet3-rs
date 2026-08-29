//! Protocol 1.2 canonical semantic snapshot model and its canonical JSON.
//!
//! Struct fields are declared in Unicode code-point order so `serde_json`
//! emits the same key order as Python's `sort_keys=True`. Every map is a
//! `BTreeMap`, which orders keys the same way.

use std::collections::BTreeMap;
use std::fmt;

use serde::Serialize;
use serde_json::value::RawValue;
use sha2::{Digest, Sha256 as Sha256Hasher};

/// Protocol revision emitted by this crate.
pub const PROTOCOL_VERSION: &str = "1.2.0";

/// A structured snapshot model or serialization error.
#[derive(Debug)]
#[non_exhaustive]
pub enum SnapshotError {
    /// A scenario identifier does not match the protocol grammar.
    InvalidScenarioId(String),
    /// A producer revision is empty or longer than 200 characters.
    InvalidSourceRevision,
    /// A single or double value is not finite.
    NonFiniteNumber,
    /// An OLE Automation date is outside the years 0100–9999.
    DateTimeOutOfRange(f64),
    /// A decoded value has a form the snapshot model does not carry.
    UnsupportedValue(&'static str),
    /// A catalog, column, or index name is not ASCII.
    NonAsciiName(Vec<u8>),
    /// A relationship record has no matching record on the related table.
    UnpairedRelationship(String),
    /// The reader rejected the database.
    Reader(Box<dyn std::error::Error + Send + Sync>),
    /// JSON serialization failed.
    Json(serde_json::Error),
}

impl fmt::Display for SnapshotError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidScenarioId(id) => write!(formatter, "invalid scenario id {id:?}"),
            Self::InvalidSourceRevision => formatter.write_str("invalid producer source revision"),
            Self::NonFiniteNumber => formatter.write_str("non-finite number"),
            Self::DateTimeOutOfRange(days) => {
                write!(formatter, "date/time {days} is outside years 0100-9999")
            }
            Self::UnsupportedValue(form) => write!(formatter, "unsupported {form}"),
            Self::NonAsciiName(raw) => write!(formatter, "non-ASCII name {raw:02x?}"),
            Self::UnpairedRelationship(name) => {
                write!(formatter, "relationship {name:?} has no matching side")
            }
            Self::Reader(error) => write!(formatter, "reader failed: {error}"),
            Self::Json(error) => write!(formatter, "json serialization failed: {error}"),
        }
    }
}

impl std::error::Error for SnapshotError {}

/// Wraps any reader error.
pub fn reader_error(error: impl std::error::Error + Send + Sync + 'static) -> SnapshotError {
    SnapshotError::Reader(Box::new(error))
}

impl From<serde_json::Error> for SnapshotError {
    fn from(error: serde_json::Error) -> Self {
        Self::Json(error)
    }
}

/// Encodes bytes as lowercase hexadecimal text.
#[must_use]
pub fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// Returns the lowercase SHA-256 of `bytes`.
#[must_use]
pub fn sha256_hex(bytes: &[u8]) -> String {
    hex(&Sha256Hasher::digest(bytes))
}

/// Validates a `DAO-(READ|WRITE|UPDATE)-` scenario identifier.
pub fn validate_scenario_id(id: &str) -> Result<(), SnapshotError> {
    let suffix = ["DAO-READ-", "DAO-WRITE-", "DAO-UPDATE-"]
        .into_iter()
        .find_map(|prefix| id.strip_prefix(prefix));
    let valid = suffix.is_some_and(|suffix| {
        (3..=64).contains(&suffix.len())
            && suffix.bytes().all(|byte| {
                byte.is_ascii_uppercase() || byte.is_ascii_digit() || b"_-".contains(&byte)
            })
            && suffix
                .as_bytes()
                .first()
                .is_some_and(u8::is_ascii_alphanumeric)
    });
    if valid {
        Ok(())
    } else {
        Err(SnapshotError::InvalidScenarioId(id.to_owned()))
    }
}

/// The JSON scalar carried by a typed value.
#[derive(Clone, Debug, Serialize)]
#[serde(untagged)]
pub enum Scalar {
    /// JSON `null`.
    Null(()),
    /// A JSON boolean.
    Boolean(bool),
    /// A JSON integer.
    Integer(i64),
    /// A finite JSON number spelled exactly as Python's `repr` would.
    Number(Box<RawValue>),
    /// A JSON string.
    Text(String),
}

impl PartialEq for Scalar {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Null(()), Self::Null(())) => true,
            (Self::Boolean(left), Self::Boolean(right)) => left == right,
            (Self::Integer(left), Self::Integer(right)) => left == right,
            (Self::Number(left), Self::Number(right)) => left.get() == right.get(),
            (Self::Text(left), Self::Text(right)) => left == right,
            _ => false,
        }
    }
}

/// One protocol typed value: a kind, a JSON scalar, and lossless raw bytes.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct TypedValue {
    /// Windows code page for `text` and `memo` values.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code_page: Option<u32>,
    /// Protocol kind name (`null`, `boolean`, `long`, `text`, ...).
    pub kind: &'static str,
    /// Exact physical field bytes; absent only for `null` and `boolean`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub raw_hex: Option<String>,
    /// The converted value.
    pub value: Scalar,
}

impl TypedValue {
    /// A null value, which carries no field bytes.
    #[must_use]
    pub const fn null() -> Self {
        Self {
            code_page: None,
            kind: "null",
            raw_hex: None,
            value: Scalar::Null(()),
        }
    }

    /// A value whose bytes are retained beside its converted scalar.
    #[must_use]
    pub fn with_raw(kind: &'static str, value: Scalar, raw: &[u8]) -> Self {
        Self {
            code_page: None,
            kind,
            raw_hex: Some(hex(raw)),
            value,
        }
    }
}

/// A key-ordered property or value map.
pub type PropertyMap = BTreeMap<String, TypedValue>;

/// Column schema.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Column {
    /// DAO `FieldAttributeEnum` bits.
    pub attributes: i64,
    /// Whether values are generated by the database.
    pub auto_increment: bool,
    /// DAO `DataTypeEnum` constant name.
    pub dao_type: String,
    /// Column name.
    pub name: String,
    /// Zero-based declared position.
    pub ordinal: u64,
    /// Additional canonical properties.
    pub properties: PropertyMap,
    /// Declared size.
    pub size: Option<u64>,
}

/// One indexed field.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct IndexField {
    /// Whether the field sorts descending.
    pub descending: bool,
    /// Indexed column name.
    pub name: String,
}

/// Index schema.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Index {
    /// Fields in key order.
    pub fields: Vec<IndexField>,
    /// Index name.
    pub name: String,
    /// Whether this is the primary index.
    pub primary: bool,
    /// Additional canonical properties.
    pub properties: PropertyMap,
    /// Whether every indexed field is required.
    pub required: bool,
    /// Whether key tuples are unique.
    pub unique: bool,
}

/// One row keyed by the SHA-256 of its canonical `values` object.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Row {
    /// Lowercase SHA-256 of the canonical JSON bytes of `values`.
    pub canonical_key: String,
    /// Zero-based count of earlier byte-identical rows.
    pub duplicate_ordinal: u64,
    /// Values keyed by column name.
    pub values: PropertyMap,
}

/// Snapshot table kind.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum TableKind {
    /// A locally stored user table.
    User,
    /// A Jet system table.
    System,
    /// A linked table.
    Linked,
}

/// Table schema and rows.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Table {
    /// DAO `TableDefAttributeEnum` bits.
    pub attributes: i64,
    /// Columns ordered by ordinal.
    pub columns: Vec<Column>,
    /// Indexes ordered by name.
    pub indexes: Vec<Index>,
    /// Table kind.
    pub kind: TableKind,
    /// Table name.
    pub name: String,
    /// Additional canonical properties.
    pub properties: PropertyMap,
    /// Rows ordered by canonical key then duplicate ordinal.
    pub rows: Vec<Row>,
}

/// A local/foreign column pair.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct RelationshipField {
    /// Column in the primary table.
    pub field: String,
    /// Column in the foreign table.
    pub foreign_field: String,
}

/// Relationship schema.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Relationship {
    /// DAO `RelationAttributeEnum` bits.
    pub attributes: i64,
    /// Column pairs in declared order.
    pub fields: Vec<RelationshipField>,
    /// Foreign table name.
    pub foreign_table: String,
    /// Relationship name.
    pub name: String,
    /// Additional canonical properties.
    pub properties: PropertyMap,
    /// Primary table name.
    pub table: String,
}

/// Raw bytes retained for a preservation comparison.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct RawField {
    /// Why these bytes are compared.
    pub purpose: String,
    /// The bytes as lowercase hexadecimal.
    pub raw_hex: String,
    /// Semantic path of the bytes.
    pub semantic_path: String,
}

/// Snapshot producer.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Producer {
    /// `dao` or `rust`.
    pub kind: &'static str,
    /// Producer revision text.
    pub source_revision: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
struct Ordering {
    columns: &'static str,
    indexes: &'static str,
    object_keys: &'static str,
    objects: &'static str,
    relationships: &'static str,
    rows: &'static str,
}

const ORDERING: Ordering = Ordering {
    columns: "ordinal_ascending",
    indexes: "name_codepoint_ascending",
    object_keys: "unicode_codepoint_ascending",
    objects: "name_codepoint_ascending",
    relationships: "name_codepoint_ascending",
    rows: "values_sha256_then_duplicate_ordinal",
};

/// A protocol 1.2 canonical semantic snapshot.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct SemanticSnapshot {
    /// JSON pointers removed before byte comparison.
    pub comparison_projection: [&'static str; 2],
    /// Database-level properties.
    pub database_properties: PropertyMap,
    /// SHA-256 of the exact database bytes.
    pub database_sha256: String,
    /// Always `canonical_semantic_snapshot`.
    pub document_type: &'static str,
    ordering: Ordering,
    /// Producing implementation.
    pub producer: Producer,
    /// Producer-only facts keyed by JSON pointer.
    pub producer_extensions: PropertyMap,
    /// Always `1.2.0`.
    pub protocol_version: &'static str,
    /// Retained raw byte sequences.
    pub raw_preservation: Vec<RawField>,
    /// Relationships ordered by name.
    pub relationships: Vec<Relationship>,
    /// Scenario that generated the database.
    pub scenario_id: String,
    /// Tables ordered by name.
    pub tables: Vec<Table>,
}

impl SemanticSnapshot {
    /// Constructs an empty Rust-produced snapshot.
    pub fn new(
        scenario_id: &str,
        source_revision: &str,
        database_sha256: String,
    ) -> Result<Self, SnapshotError> {
        validate_scenario_id(scenario_id)?;
        if source_revision.is_empty() || source_revision.chars().count() > 200 {
            return Err(SnapshotError::InvalidSourceRevision);
        }
        Ok(Self {
            comparison_projection: ["/producer", "/producer_extensions"],
            database_properties: PropertyMap::new(),
            database_sha256,
            document_type: "canonical_semantic_snapshot",
            ordering: ORDERING,
            producer: Producer {
                kind: "rust",
                source_revision: source_revision.to_owned(),
            },
            producer_extensions: PropertyMap::new(),
            protocol_version: PROTOCOL_VERSION,
            raw_preservation: Vec::new(),
            relationships: Vec::new(),
            scenario_id: scenario_id.to_owned(),
            tables: Vec::new(),
        })
    }

    /// Sorts every protocol-ordered sequence and numbers duplicate rows.
    pub fn canonicalize(&mut self) {
        self.tables
            .sort_by(|left, right| left.name.cmp(&right.name));
        for table in &mut self.tables {
            table.columns.sort_by_key(|column| column.ordinal);
            table
                .indexes
                .sort_by(|left, right| left.name.cmp(&right.name));
            canonicalize_rows(&mut table.rows);
        }
        self.relationships
            .sort_by(|left, right| left.name.cmp(&right.name));
    }

    /// Emits compact canonical UTF-8 JSON followed by exactly one LF.
    pub fn to_canonical_json(&self) -> Result<Vec<u8>, SnapshotError> {
        canonical_json(self)
    }
}

/// Serializes `value` as compact JSON with sorted keys and one trailing LF.
pub fn canonical_json<T: Serialize>(value: &T) -> Result<Vec<u8>, SnapshotError> {
    let mut bytes = serde_json::to_vec(value)?;
    bytes.push(b'\n');
    Ok(bytes)
}

/// Builds a row from its values, keyed by the canonical JSON digest.
pub fn row_from_values(values: PropertyMap) -> Result<Row, SnapshotError> {
    let canonical_key = sha256_hex(&canonical_json(&values)?);
    Ok(Row {
        canonical_key,
        duplicate_ordinal: 0,
        values,
    })
}

/// Sorts rows built by [`row_from_values`] and numbers byte-identical duplicates.
fn canonicalize_rows(rows: &mut [Row]) {
    rows.sort_by(|left, right| left.canonical_key.cmp(&right.canonical_key));
    let mut previous: Option<(String, u64)> = None;
    for row in rows.iter_mut() {
        row.duplicate_ordinal = match &previous {
            Some((key, ordinal)) if *key == row.canonical_key => ordinal + 1,
            _ => 0,
        };
        previous = Some((row.canonical_key.clone(), row.duplicate_ordinal));
    }
}

#[cfg(test)]
mod tests {
    use super::{
        PropertyMap, Scalar, SemanticSnapshot, Table, TableKind, TypedValue, row_from_values,
    };

    fn table(name: &str, rows: Vec<super::Row>) -> Table {
        Table {
            attributes: 0,
            columns: Vec::new(),
            indexes: Vec::new(),
            kind: TableKind::User,
            name: name.to_owned(),
            properties: PropertyMap::new(),
            rows,
        }
    }

    fn row(id: i64) -> Result<super::Row, super::SnapshotError> {
        let mut values = PropertyMap::new();
        values.insert(
            "Id".to_owned(),
            TypedValue::with_raw("long", Scalar::Integer(id), &(id as i32).to_le_bytes()),
        );
        row_from_values(values)
    }

    #[test]
    fn canonicalize_orders_tables_and_rows_and_numbers_duplicates()
    -> Result<(), Box<dyn std::error::Error>> {
        let mut snapshot =
            SemanticSnapshot::new("DAO-READ-ROWS-DUPLICATES", "test", "0".repeat(64))?;
        snapshot
            .tables
            .push(table("b", vec![row(2)?, row(1)?, row(2)?]));
        snapshot.tables.push(table("a", Vec::new()));
        snapshot.canonicalize();
        assert_eq!(snapshot.tables[0].name, "a");
        let rows = &snapshot.tables[1].rows;
        assert!(rows[0].canonical_key < rows[1].canonical_key);
        assert!(rows[1].canonical_key <= rows[2].canonical_key);
        let ordinals: Vec<_> = rows.iter().map(|row| row.duplicate_ordinal).collect();
        let keys: Vec<_> = rows.iter().map(|row| row.canonical_key.as_str()).collect();
        if keys[0] == keys[1] {
            assert_eq!(ordinals, [0, 1, 0]);
        } else {
            assert_eq!(ordinals, [0, 0, 1]);
        }
        let json = String::from_utf8(snapshot.to_canonical_json()?)?;
        assert!(
            json.starts_with(
                "{\"comparison_projection\":[\"/producer\",\"/producer_extensions\"],"
            )
        );
        assert!(json.ends_with("}\n"));
        Ok(())
    }

    #[test]
    fn every_object_emits_keys_in_codepoint_order() -> Result<(), Box<dyn std::error::Error>> {
        let mut snapshot =
            SemanticSnapshot::new("DAO-READ-SCHEMA-RELATIONSHIP", "test", "0".repeat(64))?;
        let mut table = table("t", vec![row(1)?]);
        table.columns.push(super::Column {
            attributes: 1,
            auto_increment: false,
            dao_type: "dbLong".to_owned(),
            name: "Id".to_owned(),
            ordinal: 0,
            properties: PropertyMap::new(),
            size: Some(4),
        });
        table.indexes.push(super::Index {
            fields: vec![super::IndexField {
                descending: true,
                name: "Id".to_owned(),
            }],
            name: "PK".to_owned(),
            primary: true,
            properties: PropertyMap::new(),
            required: true,
            unique: true,
        });
        snapshot.tables.push(table);
        snapshot.relationships.push(super::Relationship {
            attributes: 0,
            fields: vec![super::RelationshipField {
                field: "Id".to_owned(),
                foreign_field: "Id".to_owned(),
            }],
            foreign_table: "t".to_owned(),
            name: "r".to_owned(),
            properties: PropertyMap::new(),
            table: "t".to_owned(),
        });
        snapshot.raw_preservation.push(super::RawField {
            purpose: "p".to_owned(),
            raw_hex: "00".to_owned(),
            semantic_path: "/tables/0".to_owned(),
        });
        let emitted = snapshot.to_canonical_json()?;
        // `serde_json::Value` stores objects in a BTreeMap, so re-serializing
        // the parsed document yields sorted keys; equality proves ours were.
        let parsed: serde_json::Value = serde_json::from_slice(&emitted)?;
        assert_eq!(emitted, super::canonical_json(&parsed)?);
        Ok(())
    }

    #[test]
    fn row_key_is_sha256_of_canonical_values_with_trailing_newline()
    -> Result<(), Box<dyn std::error::Error>> {
        let row = row(1)?;
        let bytes = b"{\"Id\":{\"kind\":\"long\",\"raw_hex\":\"01000000\",\"value\":1}}\n";
        assert_eq!(row.canonical_key, super::sha256_hex(bytes));
        Ok(())
    }

    #[test]
    fn scenario_ids_follow_the_protocol_grammar() {
        assert!(super::validate_scenario_id("DAO-READ-OPEN-EMPTY").is_ok());
        assert!(super::validate_scenario_id("DAO-GEN-OPEN-EMPTY").is_err());
        assert!(super::validate_scenario_id("DAO-READ-ab").is_err());
        assert!(super::validate_scenario_id("DAO-READ--AB").is_err());
    }
}
