//! Typed canonical semantic snapshots for DAO protocol 1.0.0.
//!
//! This module models and serializes the protocol document. It does not read
//! MDB bytes and does not provide compatibility evidence.

use std::collections::{BTreeMap, BTreeSet};
use std::error::Error;
use std::fmt;

/// A structured canonical snapshot construction or serialization error.
#[derive(Clone, Debug, Eq, PartialEq)]
#[non_exhaustive]
pub enum SnapshotError {
    /// A scenario identifier does not match the protocol grammar.
    InvalidScenarioId,
    /// A producer revision is empty or exceeds 200 Unicode scalar values.
    InvalidSourceRevision,
    /// A SHA-256 value is not exactly 64 lowercase hexadecimal digits.
    InvalidSha256,
    /// A hexadecimal value is not lowercase, even-length hexadecimal text.
    InvalidHex,
    /// A GUID is not lowercase hyphenated text in the protocol shape.
    InvalidGuid,
    /// A decimal or currency string is not in invariant decimal form.
    InvalidDecimal,
    /// A date/time string is not in the protocol's timezone-free ISO shape.
    InvalidDateTime,
    /// A single or double value is not finite.
    NonFiniteNumber,
    /// A required string is empty.
    EmptyString {
        /// Semantic location of the empty string.
        path: String,
    },
    /// A required collection is empty.
    EmptyCollection {
        /// Semantic location of the empty collection.
        path: String,
    },
    /// A protocol-declared sequence is not in canonical ascending order.
    NonCanonicalOrder {
        /// Semantic location of the unordered sequence.
        path: String,
    },
    /// A value that must be unique is duplicated.
    Duplicate {
        /// Semantic location of the uniqueness constraint.
        path: String,
        /// Duplicated value rendered for diagnostics.
        value: String,
    },
    /// Rust's finite-number formatter returned an unexpected internal shape.
    NumberFormatting,
}

impl fmt::Display for SnapshotError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidScenarioId => formatter.write_str("invalid DAO scenario identifier"),
            Self::InvalidSourceRevision => formatter.write_str("invalid producer source revision"),
            Self::InvalidSha256 => formatter.write_str("invalid lowercase SHA-256 text"),
            Self::InvalidHex => formatter.write_str("invalid lowercase hexadecimal text"),
            Self::InvalidGuid => formatter.write_str("invalid lowercase hyphenated GUID"),
            Self::InvalidDecimal => formatter.write_str("invalid invariant decimal string"),
            Self::InvalidDateTime => formatter.write_str("invalid invariant date/time string"),
            Self::NonFiniteNumber => formatter.write_str("non-finite JSON number"),
            Self::EmptyString { path } => write!(formatter, "{path} must not be empty"),
            Self::EmptyCollection { path } => write!(formatter, "{path} must not be empty"),
            Self::NonCanonicalOrder { path } => {
                write!(formatter, "{path} is not in canonical order")
            }
            Self::Duplicate { path, value } => {
                write!(formatter, "{path} contains duplicate value {value:?}")
            }
            Self::NumberFormatting => formatter.write_str("unexpected finite-number formatting"),
        }
    }
}

impl Error for SnapshotError {}

macro_rules! string_value {
    ($name:ident) => {
        impl $name {
            /// Returns the validated string representation.
            #[must_use]
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl AsRef<str> for $name {
            fn as_ref(&self) -> &str {
                self.as_str()
            }
        }
    };
}

/// A validated protocol scenario identifier.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ScenarioId(String);

impl ScenarioId {
    /// Validates and constructs a DAO protocol scenario identifier.
    pub fn new(value: impl Into<String>) -> Result<Self, SnapshotError> {
        let value = value.into();
        let suffix = ["DAO-GEN-", "DAO-READ-", "DAO-WRITE-", "DAO-UPDATE-"]
            .into_iter()
            .find_map(|prefix| value.strip_prefix(prefix));
        let valid = suffix.is_some_and(|candidate| {
            (3..=64).contains(&candidate.len())
                && candidate.bytes().all(|byte| {
                    byte.is_ascii_uppercase() || byte.is_ascii_digit() || b"_-".contains(&byte)
                })
                && candidate
                    .as_bytes()
                    .first()
                    .is_some_and(u8::is_ascii_alphanumeric)
        });
        if !valid {
            return Err(SnapshotError::InvalidScenarioId);
        }
        Ok(Self(value))
    }
}

string_value!(ScenarioId);

/// A validated lowercase SHA-256 value.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Sha256(String);

impl Sha256 {
    /// Validates and constructs lowercase SHA-256 text.
    pub fn new(value: impl Into<String>) -> Result<Self, SnapshotError> {
        let value = value.into();
        if value.len() != 64 || !value.bytes().all(is_lower_hex_digit) {
            return Err(SnapshotError::InvalidSha256);
        }
        Ok(Self(value))
    }
}

string_value!(Sha256);

/// Validated lowercase, even-length hexadecimal text.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct HexString(String);

impl HexString {
    /// Validates lowercase, even-length hexadecimal text.
    pub fn new(value: impl Into<String>) -> Result<Self, SnapshotError> {
        let value = value.into();
        if value.len() % 2 != 0 || !value.bytes().all(is_lower_hex_digit) {
            return Err(SnapshotError::InvalidHex);
        }
        Ok(Self(value))
    }

    /// Encodes bytes as lowercase hexadecimal text.
    #[must_use]
    pub fn from_bytes(bytes: &[u8]) -> Self {
        const DIGITS: &[u8; 16] = b"0123456789abcdef";
        let mut output = String::with_capacity(bytes.len().saturating_mul(2));
        for byte in bytes {
            output.push(char::from(DIGITS[usize::from(byte >> 4)]));
            output.push(char::from(DIGITS[usize::from(byte & 0x0f)]));
        }
        Self(output)
    }
}

string_value!(HexString);

/// A validated lowercase hyphenated GUID.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct Guid(String);

impl Guid {
    /// Validates the protocol's lowercase hyphenated GUID shape.
    pub fn new(value: impl Into<String>) -> Result<Self, SnapshotError> {
        let value = value.into();
        let valid = value.len() == 36
            && value.bytes().enumerate().all(|(index, byte)| {
                if matches!(index, 8 | 13 | 18 | 23) {
                    byte == b'-'
                } else {
                    is_lower_hex_digit(byte)
                }
            });
        if !valid {
            return Err(SnapshotError::InvalidGuid);
        }
        Ok(Self(value))
    }
}

string_value!(Guid);

/// A validated invariant decimal or currency string.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct InvariantDecimal(String);

impl InvariantDecimal {
    /// Validates the protocol's invariant decimal grammar.
    pub fn new(value: impl Into<String>) -> Result<Self, SnapshotError> {
        let value = value.into();
        if !is_invariant_decimal(&value) {
            return Err(SnapshotError::InvalidDecimal);
        }
        Ok(Self(value))
    }
}

string_value!(InvariantDecimal);

/// A validated timezone-free invariant date/time string.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct InvariantDateTime(String);

impl InvariantDateTime {
    /// Validates the protocol's timezone-free ISO date/time shape.
    pub fn new(value: impl Into<String>) -> Result<Self, SnapshotError> {
        let value = value.into();
        if !is_invariant_datetime(&value) {
            return Err(SnapshotError::InvalidDateTime);
        }
        Ok(Self(value))
    }
}

string_value!(InvariantDateTime);

/// A finite IEEE-754 single-precision value.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FiniteF32(f32);

impl FiniteF32 {
    /// Rejects non-finite values and constructs a canonical numeric value.
    pub fn new(value: f32) -> Result<Self, SnapshotError> {
        if !value.is_finite() {
            return Err(SnapshotError::NonFiniteNumber);
        }
        Ok(Self(value))
    }

    /// Returns the finite value.
    #[must_use]
    pub const fn get(self) -> f32 {
        self.0
    }
}

/// A finite IEEE-754 double-precision value.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FiniteF64(f64);

impl FiniteF64 {
    /// Rejects non-finite values and constructs a canonical numeric value.
    pub fn new(value: f64) -> Result<Self, SnapshotError> {
        if !value.is_finite() {
            return Err(SnapshotError::NonFiniteNumber);
        }
        Ok(Self(value))
    }

    /// Returns the finite value.
    #[must_use]
    pub const fn get(self) -> f64 {
        self.0
    }
}

/// A typed semantic value from the canonical snapshot protocol.
#[derive(Clone, Debug, PartialEq)]
#[non_exhaustive]
pub enum TypedValue {
    Null {
        raw_hex: Option<HexString>,
    },
    Boolean {
        value: bool,
        raw_hex: Option<HexString>,
    },
    Byte {
        value: u8,
        raw_hex: Option<HexString>,
    },
    Integer {
        value: i16,
        raw_hex: Option<HexString>,
    },
    Long {
        value: i32,
        raw_hex: Option<HexString>,
    },
    Single {
        value: FiniteF32,
        raw_hex: Option<HexString>,
    },
    Double {
        value: FiniteF64,
        raw_hex: Option<HexString>,
    },
    Decimal {
        value: InvariantDecimal,
        raw_hex: Option<HexString>,
    },
    Currency {
        value: InvariantDecimal,
        raw_hex: Option<HexString>,
    },
    DateTime {
        value: InvariantDateTime,
        raw_hex: Option<HexString>,
    },
    Text {
        value: String,
        raw_hex: Option<HexString>,
        code_page: Option<u32>,
    },
    Binary {
        value: HexString,
        raw_hex: Option<HexString>,
    },
    Guid {
        value: Guid,
        raw_hex: Option<HexString>,
    },
    Memo {
        value: String,
        raw_hex: Option<HexString>,
        code_page: Option<u32>,
    },
    Ole {
        value: HexString,
        raw_hex: Option<HexString>,
    },
}

/// A canonically key-ordered property map.
pub type PropertyMap = BTreeMap<String, TypedValue>;

/// Snapshot producer kind.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProducerKind {
    Dao,
    Rust,
}

/// Identity of the independent snapshot producer.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Producer {
    pub kind: ProducerKind,
    source_revision: String,
}

impl Producer {
    /// Constructs a producer after validating its non-empty bounded revision.
    pub fn new(
        kind: ProducerKind,
        source_revision: impl Into<String>,
    ) -> Result<Self, SnapshotError> {
        let source_revision = source_revision.into();
        if source_revision.is_empty() || source_revision.chars().count() > 200 {
            return Err(SnapshotError::InvalidSourceRevision);
        }
        Ok(Self {
            kind,
            source_revision,
        })
    }

    /// Returns the validated source revision.
    #[must_use]
    pub fn source_revision(&self) -> &str {
        &self.source_revision
    }
}

/// Column schema and properties.
#[derive(Clone, Debug, PartialEq)]
pub struct Column {
    pub name: String,
    pub ordinal: u64,
    pub dao_type: String,
    pub nullable: bool,
    pub required: bool,
    pub auto_increment: bool,
    pub size: Option<u64>,
    pub attributes: i64,
    pub properties: PropertyMap,
}

/// One field in an index, retained in declared order.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IndexField {
    pub name: String,
    pub descending: bool,
}

/// Index schema and properties.
#[derive(Clone, Debug, PartialEq)]
pub struct Index {
    pub name: String,
    pub primary: bool,
    pub unique: bool,
    pub required: bool,
    pub ignore_nulls: bool,
    pub fields: Vec<IndexField>,
    pub properties: PropertyMap,
}

/// A row keyed by its producer-declared canonical sort key.
///
/// Rows sharing a key are ordered by their canonical `values` object. Fully
/// identical rows remain interchangeable and are retained.
#[derive(Clone, Debug, PartialEq)]
pub struct Row {
    pub canonical_key: String,
    pub values: PropertyMap,
}

/// Snapshot table kind.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TableKind {
    User,
    System,
    Linked,
}

/// Table schema, properties, and semantic rows.
#[derive(Clone, Debug, PartialEq)]
pub struct Table {
    pub name: String,
    pub kind: TableKind,
    pub attributes: i64,
    pub columns: Vec<Column>,
    pub indexes: Vec<Index>,
    pub properties: PropertyMap,
    pub rows: Vec<Row>,
}

/// A local/foreign field pair retained in declared order.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RelationshipField {
    pub field: String,
    pub foreign_field: String,
}

/// Relationship schema and properties.
#[derive(Clone, Debug, PartialEq)]
pub struct Relationship {
    pub name: String,
    pub table: String,
    pub foreign_table: String,
    pub attributes: i64,
    pub fields: Vec<RelationshipField>,
    pub properties: PropertyMap,
}

/// Raw bytes intentionally retained for a semantic preservation check.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RawPreservation {
    pub semantic_path: String,
    pub raw_hex: HexString,
    pub purpose: String,
}

/// A protocol 1.0.0 canonical semantic snapshot.
#[derive(Clone, Debug, PartialEq)]
pub struct CanonicalSnapshot {
    pub scenario_id: ScenarioId,
    pub producer: Producer,
    pub database_sha256: Sha256,
    pub database_properties: PropertyMap,
    pub tables: Vec<Table>,
    pub relationships: Vec<Relationship>,
    pub raw_preservation: Vec<RawPreservation>,
}

impl CanonicalSnapshot {
    /// Constructs an empty snapshot with the protocol constants and ordering
    /// declarations fixed by its Rust type.
    #[must_use]
    pub fn new(scenario_id: ScenarioId, producer: Producer, database_sha256: Sha256) -> Self {
        Self {
            scenario_id,
            producer,
            database_sha256,
            database_properties: PropertyMap::new(),
            tables: Vec::new(),
            relationships: Vec::new(),
            raw_preservation: Vec::new(),
        }
    }

    /// Sorts protocol-declared sequences, then validates uniqueness and shape.
    pub fn canonicalize(&mut self) -> Result<(), SnapshotError> {
        self.tables
            .sort_by(|left, right| left.name.cmp(&right.name));
        for table in &mut self.tables {
            table.columns.sort_by_key(|column| column.ordinal);
            table
                .indexes
                .sort_by(|left, right| left.name.cmp(&right.name));
            canonicalize_rows(&mut table.rows)?;
        }
        self.relationships
            .sort_by(|left, right| left.name.cmp(&right.name));
        self.validate()
    }

    /// Emits strict canonical UTF-8 JSON, rejecting unordered input.
    ///
    /// The returned bytes contain compact JSON followed by exactly one LF.
    pub fn to_canonical_json(&self) -> Result<Vec<u8>, SnapshotError> {
        self.validate()?;
        crate::canonical_json::write_snapshot(self)
    }

    /// Clones, sorts, validates, and emits canonical UTF-8 JSON.
    pub fn to_canonicalized_json(&self) -> Result<Vec<u8>, SnapshotError> {
        let mut snapshot = self.clone();
        snapshot.canonicalize()?;
        snapshot.to_canonical_json()
    }

    fn validate(&self) -> Result<(), SnapshotError> {
        ensure_named_order(
            self.tables.iter().map(|table| table.name.as_str()),
            "$.tables",
        )?;
        for (table_index, table) in self.tables.iter().enumerate() {
            let table_path = format!("$.tables[{table_index}]");
            ensure_ordinal_order(
                table.columns.iter().map(|column| column.ordinal),
                &format!("{table_path}.columns"),
            )?;
            ensure_unique_names(
                table.columns.iter().map(|column| column.name.as_str()),
                &format!("{table_path}.columns"),
            )?;
            for (column_index, column) in table.columns.iter().enumerate() {
                if column.dao_type.is_empty() {
                    return Err(SnapshotError::EmptyString {
                        path: format!("{table_path}.columns[{column_index}].dao_type"),
                    });
                }
            }
            ensure_named_order(
                table.indexes.iter().map(|index| index.name.as_str()),
                &format!("{table_path}.indexes"),
            )?;
            for (index_index, index) in table.indexes.iter().enumerate() {
                if index.fields.is_empty() {
                    return Err(SnapshotError::EmptyCollection {
                        path: format!("{table_path}.indexes[{index_index}].fields"),
                    });
                }
            }
            ensure_row_order(&table.rows, &format!("{table_path}.rows"))?;
        }
        ensure_named_order(
            self.relationships
                .iter()
                .map(|relationship| relationship.name.as_str()),
            "$.relationships",
        )?;
        for (index, relationship) in self.relationships.iter().enumerate() {
            if relationship.fields.is_empty() {
                return Err(SnapshotError::EmptyCollection {
                    path: format!("$.relationships[{index}].fields"),
                });
            }
        }
        for (index, raw) in self.raw_preservation.iter().enumerate() {
            if raw.semantic_path.is_empty() {
                return Err(SnapshotError::EmptyString {
                    path: format!("$.raw_preservation[{index}].semantic_path"),
                });
            }
            if raw.purpose.is_empty() {
                return Err(SnapshotError::EmptyString {
                    path: format!("$.raw_preservation[{index}].purpose"),
                });
            }
        }
        Ok(())
    }
}

fn is_lower_hex_digit(byte: u8) -> bool {
    byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
}

fn is_invariant_decimal(value: &str) -> bool {
    let unsigned = value.strip_prefix('-').unwrap_or(value);
    let (integer, fraction) = match unsigned.split_once('.') {
        Some((integer, fraction)) => (integer, Some(fraction)),
        None => (unsigned, None),
    };
    let integer_valid = integer == "0"
        || (!integer.starts_with('0')
            && !integer.is_empty()
            && integer.bytes().all(|byte| byte.is_ascii_digit()));
    integer_valid
        && fraction.is_none_or(|digits| {
            !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit())
        })
}

fn is_invariant_datetime(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() < 19 {
        return false;
    }
    for separator in [(4, b'-'), (7, b'-'), (10, b'T'), (13, b':'), (16, b':')] {
        if bytes.get(separator.0) != Some(&separator.1) {
            return false;
        }
    }
    let fixed_digits = [0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18];
    if !fixed_digits
        .into_iter()
        .all(|index| bytes.get(index).is_some_and(u8::is_ascii_digit))
    {
        return false;
    }
    bytes.len() == 19
        || (bytes.get(19) == Some(&b'.')
            && bytes.len() > 20
            && bytes[20..].iter().all(u8::is_ascii_digit))
}

fn ensure_named_order<'a>(
    values: impl Iterator<Item = &'a str>,
    path: &str,
) -> Result<(), SnapshotError> {
    let mut previous: Option<&str> = None;
    for value in values {
        if let Some(prior) = previous {
            match prior.cmp(value) {
                std::cmp::Ordering::Greater => {
                    return Err(SnapshotError::NonCanonicalOrder {
                        path: path.to_owned(),
                    });
                }
                std::cmp::Ordering::Equal => {
                    return Err(SnapshotError::Duplicate {
                        path: path.to_owned(),
                        value: value.to_owned(),
                    });
                }
                std::cmp::Ordering::Less => {}
            }
        }
        previous = Some(value);
    }
    Ok(())
}

fn ensure_ordinal_order(
    values: impl Iterator<Item = u64>,
    path: &str,
) -> Result<(), SnapshotError> {
    let mut previous = None;
    for value in values {
        if let Some(prior) = previous {
            if prior > value {
                return Err(SnapshotError::NonCanonicalOrder {
                    path: path.to_owned(),
                });
            }
            if prior == value {
                return Err(SnapshotError::Duplicate {
                    path: path.to_owned(),
                    value: value.to_string(),
                });
            }
        }
        previous = Some(value);
    }
    Ok(())
}

fn ensure_unique_names<'a>(
    values: impl Iterator<Item = &'a str>,
    path: &str,
) -> Result<(), SnapshotError> {
    let mut seen = BTreeSet::new();
    for value in values {
        if !seen.insert(value) {
            return Err(SnapshotError::Duplicate {
                path: path.to_owned(),
                value: value.to_owned(),
            });
        }
    }
    Ok(())
}

fn canonicalize_rows(rows: &mut Vec<Row>) -> Result<(), SnapshotError> {
    let mut keyed = rows
        .drain(..)
        .map(|row| {
            let values = crate::canonical_json::write_properties(&row.values)?;
            Ok((row, values))
        })
        .collect::<Result<Vec<_>, SnapshotError>>()?;
    keyed.sort_by(|(left, left_values), (right, right_values)| {
        left.canonical_key
            .cmp(&right.canonical_key)
            .then_with(|| left_values.cmp(right_values))
    });
    rows.extend(keyed.into_iter().map(|(row, _)| row));
    Ok(())
}

fn ensure_row_order(rows: &[Row], path: &str) -> Result<(), SnapshotError> {
    let mut previous: Option<(&str, Vec<u8>)> = None;
    for row in rows {
        let values = crate::canonical_json::write_properties(&row.values)?;
        if let Some((previous_key, previous_values)) = &previous
            && ((*previous_key)
                .cmp(row.canonical_key.as_str())
                .then_with(|| previous_values.cmp(&values)))
                == std::cmp::Ordering::Greater
        {
            return Err(SnapshotError::NonCanonicalOrder {
                path: path.to_owned(),
            });
        }
        previous = Some((&row.canonical_key, values));
    }
    Ok(())
}
