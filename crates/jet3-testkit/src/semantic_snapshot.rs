//! Rust producer of protocol canonical semantic snapshots.
//!
//! This adapter translates the currently supported semantic-reader output of
//! one opened Jet 3 database into the typed [`CanonicalSnapshot`] model. It is
//! bound to the public `jet3` API only, charges every collected object to the
//! caller's operation budget, and fails closed on any catalog object, value,
//! or name form the reader does not yet interpret. A snapshot is a Rust
//! self-read; it never establishes DAO compatibility on its own.

use std::error::Error;
use std::fmt;

use jet3::{
    CatalogError, CatalogObjectClass, CatalogObjectKind, DatabaseReader, IndexDefinitionKind,
    PageNumber, ReadAt, RelationshipSide, ResourceBudget, RowError, TableDefinition,
    TableDefinitionError, TextCodePage, ValueError,
};

use crate::{
    CanonicalSnapshot, Producer, PropertyMap, Relationship, RelationshipField, Row, ScenarioId,
    Sha256, SnapshotError, Table, TableKind, TypedValue,
};

#[path = "semantic_snapshot_convert.rs"]
mod convert;

use convert::{convert_column, convert_index, convert_value, decode_ascii_name};

/// Caller-supplied identity and decoding policy for one Rust snapshot.
#[derive(Clone, Debug, PartialEq)]
pub struct SemanticSnapshotOptions {
    /// Protocol scenario that produced the database under snapshot.
    pub scenario_id: ScenarioId,
    /// Identity of this Rust producer, including its source revision.
    pub producer: Producer,
    /// Independently computed digest of the exact database bytes.
    pub database_sha256: Sha256,
    /// Explicitly selected code page for text values; none is inferred.
    pub code_page: TextCodePage,
}

/// A structured failure while producing a Rust canonical snapshot.
#[derive(Clone, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub enum SemanticSnapshotError {
    /// Catalog discovery or streaming failed.
    Catalog(CatalogError),
    /// Decoding one table definition failed.
    TableDefinition(TableDefinitionError),
    /// Streaming one table's rows failed.
    Row(RowError),
    /// Decoding one field failed.
    Value(ValueError),
    /// Charging collected snapshot state to the operation budget failed.
    Resource(jet3::Error),
    /// The collected model violated the protocol's canonical constraints.
    Snapshot(SnapshotError),
    /// A user-class catalog object has a kind this adapter does not interpret.
    UnsupportedCatalogObject {
        /// Catalog object identifier.
        id: u32,
        /// Lossless raw object kind.
        kind: u16,
    },
    /// A table, column, or index name contains non-ASCII bytes, whose code
    /// page interpretation is not established by this adapter.
    NonAsciiName {
        /// Table-definition root of the object, when known.
        table: Option<PageNumber>,
    },
    /// A value form the snapshot adapter does not yet represent.
    UnsupportedValue {
        /// Table-definition root of the row's table.
        table: PageNumber,
        /// Ordinal of the column holding the value.
        column: u16,
        /// Which value form was encountered.
        form: UnsupportedValueForm,
    },
    /// A logical index references a physical index outside its definition.
    InvalidIndexReference {
        /// Table-definition root of the definition.
        table: PageNumber,
        /// Referenced physical-index ordinal.
        physical_index: u16,
    },
    /// A relationship's two table sides could not be paired consistently.
    UnpairedRelationship {
        /// Table-definition root that supplied one side of the relationship.
        table: PageNumber,
    },
}

/// A value form that is decoded by `jet3` but not yet represented here.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub enum UnsupportedValueForm {
    /// An externally stored Memo/OLE value; streaming it is a later slice.
    ExternalLongValue,
    /// An OLE Automation date whose calendar conversion is not yet recorded.
    DateTime,
    /// A non-finite single or double value with no canonical JSON form.
    NonFiniteNumber,
}

impl fmt::Display for SemanticSnapshotError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Catalog(source) => write!(formatter, "snapshot catalog failed: {source}"),
            Self::TableDefinition(source) => {
                write!(formatter, "snapshot table definition failed: {source}")
            }
            Self::Row(source) => write!(formatter, "snapshot row stream failed: {source}"),
            Self::Value(source) => write!(formatter, "snapshot value decoding failed: {source}"),
            Self::Resource(source) => write!(formatter, "snapshot resource policy: {source}"),
            Self::Snapshot(source) => write!(formatter, "snapshot canonical model: {source}"),
            Self::UnsupportedCatalogObject { id, kind } => write!(
                formatter,
                "catalog object {id} has unsupported kind {kind:#06x}"
            ),
            Self::NonAsciiName { table } => {
                write!(
                    formatter,
                    "non-ASCII definition name (table root {table:?})"
                )
            }
            Self::UnsupportedValue {
                table,
                column,
                form,
            } => write!(
                formatter,
                "unsupported value form {form:?} in table root {table:?} column {column}"
            ),
            Self::InvalidIndexReference {
                table,
                physical_index,
            } => write!(
                formatter,
                "table root {table:?} references missing physical index {physical_index}"
            ),
            Self::UnpairedRelationship { table } => {
                write!(
                    formatter,
                    "relationship on table root {table:?} is unpaired"
                )
            }
        }
    }
}

impl Error for SemanticSnapshotError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            Self::Catalog(source) => Some(source),
            Self::TableDefinition(source) => Some(source),
            Self::Row(source) => Some(source),
            Self::Value(source) => Some(source),
            Self::Resource(source) => Some(source),
            Self::Snapshot(source) => Some(source),
            _ => None,
        }
    }
}

impl From<SnapshotError> for SemanticSnapshotError {
    fn from(source: SnapshotError) -> Self {
        Self::Snapshot(source)
    }
}

/// One user table discovered in the catalog before its definition is read.
struct CatalogTable {
    name: String,
    root: PageNumber,
    raw_flags: u32,
}

/// One relationship record observed from one table side.
struct RelationshipSideRecord {
    name: String,
    table_name: String,
    table_root: PageNumber,
    side: RelationshipSide,
    related_table: PageNumber,
    fields: Vec<String>,
    cascade_updates: bool,
    cascade_deletes: bool,
}

/// Produces one deterministic canonical snapshot of every user table.
///
/// The catalog is streamed once; every user table definition is decoded, its
/// rows are streamed and decoded with the selected code page, and the result
/// is canonicalized under the protocol's ordering rules. System-class catalog
/// objects are not snapshotted in this slice.
pub fn snapshot_database<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    options: &SemanticSnapshotOptions,
    budget: &mut ResourceBudget,
) -> Result<CanonicalSnapshot, SemanticSnapshotError> {
    let catalog = collect_user_tables(database, budget)?;
    let mut snapshot = CanonicalSnapshot::new(
        options.scenario_id.clone(),
        options.producer.clone(),
        options.database_sha256.clone(),
    );
    let mut sides = Vec::new();
    for entry in &catalog {
        let definition = database
            .table_definition(entry.root, budget)
            .map_err(SemanticSnapshotError::TableDefinition)?;
        let table = collect_table(database, entry, &definition, options.code_page, budget)?;
        collect_relationship_sides(entry, &definition, &mut sides, budget)?;
        snapshot.tables.push(table);
    }
    snapshot.relationships = pair_relationships(sides)?;
    snapshot.canonicalize()?;
    Ok(snapshot)
}

fn charge_collected(
    budget: &mut ResourceBudget,
    bytes: usize,
) -> Result<(), SemanticSnapshotError> {
    let count = jet3::ByteCount::from_usize(bytes).map_err(SemanticSnapshotError::Resource)?;
    budget
        .charge_allocation(count)
        .map_err(SemanticSnapshotError::Resource)
}

fn collect_user_tables<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    budget: &mut ResourceBudget,
) -> Result<Vec<CatalogTable>, SemanticSnapshotError> {
    let mut tables = Vec::new();
    let mut cursor = database
        .catalog(budget)
        .map_err(SemanticSnapshotError::Catalog)?;
    while let Some(record) = cursor
        .next_record()
        .map_err(SemanticSnapshotError::Catalog)?
    {
        if record.class() == CatalogObjectClass::System {
            continue;
        }
        let root = match record.kind() {
            CatalogObjectKind::Table => record.table_definition(),
            _ => {
                return Err(SemanticSnapshotError::UnsupportedCatalogObject {
                    id: record.id().get(),
                    kind: record.kind().raw(),
                });
            }
        };
        let Some(root) = root else {
            return Err(SemanticSnapshotError::UnsupportedCatalogObject {
                id: record.id().get(),
                kind: record.kind().raw(),
            });
        };
        let name = decode_ascii_name(record.name().raw_bytes(), Some(root))?;
        tables.push(CatalogTable {
            name,
            root,
            raw_flags: record.raw_flags(),
        });
    }
    let collected = tables
        .iter()
        .map(|table| table.name.len() + std::mem::size_of::<CatalogTable>())
        .sum();
    charge_collected(budget, collected)?;
    Ok(tables)
}

fn column_names(
    definition: &TableDefinition,
    table: PageNumber,
) -> Result<Vec<String>, SemanticSnapshotError> {
    definition
        .columns()
        .iter()
        .map(|column| decode_ascii_name(column.name().raw_bytes(), Some(table)))
        .collect()
}

fn collect_table<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    entry: &CatalogTable,
    definition: &TableDefinition,
    code_page: TextCodePage,
    budget: &mut ResourceBudget,
) -> Result<Table, SemanticSnapshotError> {
    let names = column_names(definition, entry.root)?;
    let columns = definition
        .columns()
        .iter()
        .zip(&names)
        .map(|(column, name)| convert_column(column, name.clone()))
        .collect::<Vec<_>>();
    let mut indexes = Vec::new();
    let mut primary_columns = Vec::new();
    for logical in definition.indexes() {
        let primary = match logical.kind() {
            IndexDefinitionKind::Primary => true,
            IndexDefinitionKind::Ordinary => false,
            IndexDefinitionKind::Relationship(_) => continue,
        };
        let index = convert_index(definition, logical, primary, &names, entry.root)?;
        if primary {
            primary_columns = index
                .fields
                .iter()
                .map(|field| field.name.clone())
                .collect();
        }
        indexes.push(index);
    }
    charge_collected(
        budget,
        columns
            .iter()
            .map(|column| column.name.len())
            .sum::<usize>()
            + indexes.len() * std::mem::size_of::<crate::Index>(),
    )?;
    let rows = collect_rows(
        database,
        definition,
        entry.root,
        &names,
        &primary_columns,
        code_page,
        budget,
    )?;
    Ok(Table {
        name: entry.name.clone(),
        kind: TableKind::User,
        attributes: i64::from(entry.raw_flags),
        columns,
        indexes,
        properties: PropertyMap::new(),
        rows,
    })
}

fn collect_rows<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    definition: &TableDefinition,
    table: PageNumber,
    names: &[String],
    primary_columns: &[String],
    code_page: TextCodePage,
    budget: &mut ResourceBudget,
) -> Result<Vec<Row>, SemanticSnapshotError> {
    let mut rows = Vec::new();
    let mut cursor = database
        .rows(definition, budget)
        .map_err(SemanticSnapshotError::Row)?;
    while let Some(mut view) = cursor.next_row().map_err(SemanticSnapshotError::Row)? {
        let mut values = PropertyMap::new();
        for (column, name) in definition.columns().iter().zip(names) {
            let ordinal = column.ordinal();
            let decoded = view
                .value(ordinal, code_page)
                .map_err(SemanticSnapshotError::Value)?
                .ok_or(SemanticSnapshotError::InvalidIndexReference {
                    table,
                    physical_index: ordinal.get(),
                })?;
            let value = convert_value(&decoded, table, ordinal.get())?;
            values.insert(name.clone(), value);
        }
        let canonical_key = canonical_row_key(&values, primary_columns)?;
        rows.push(Row {
            canonical_key,
            values,
        });
    }
    let collected = rows
        .iter()
        .map(|row| row.canonical_key.len() + std::mem::size_of::<Row>())
        .sum();
    charge_collected(budget, collected)?;
    Ok(rows)
}

/// Derives the producer-declared row key from the primary-key values.
///
/// Tables without a primary index use an empty key so rows order purely by
/// their canonical values. The key is canonical JSON of the primary-key
/// columns, which both producers can derive from schema plus values alone.
fn canonical_row_key(
    values: &PropertyMap,
    primary_columns: &[String],
) -> Result<String, SemanticSnapshotError> {
    if primary_columns.is_empty() {
        return Ok(String::new());
    }
    let mut key_values = PropertyMap::new();
    for name in primary_columns {
        if let Some(value) = values.get(name) {
            key_values.insert(name.clone(), value.clone());
        }
    }
    let bytes = crate::canonical_json::write_properties(&key_values)?;
    String::from_utf8(bytes)
        .map_err(|_| SemanticSnapshotError::Snapshot(SnapshotError::NumberFormatting))
}

fn collect_relationship_sides(
    entry: &CatalogTable,
    definition: &TableDefinition,
    sides: &mut Vec<RelationshipSideRecord>,
    budget: &mut ResourceBudget,
) -> Result<(), SemanticSnapshotError> {
    let names = column_names(definition, entry.root)?;
    for relationship in definition.relationships() {
        let physical = definition
            .physical_indexes()
            .get(usize::from(relationship.physical_index()))
            .ok_or(SemanticSnapshotError::InvalidIndexReference {
                table: entry.root,
                physical_index: relationship.physical_index(),
            })?;
        let fields = physical
            .fields()
            .iter()
            .map(|field| {
                names.get(usize::from(field.column().get())).cloned().ok_or(
                    SemanticSnapshotError::InvalidIndexReference {
                        table: entry.root,
                        physical_index: relationship.physical_index(),
                    },
                )
            })
            .collect::<Result<Vec<_>, _>>()?;
        let name = decode_ascii_name(relationship.name().raw_bytes(), Some(entry.root))?;
        charge_collected(
            budget,
            name.len() + entry.name.len() + std::mem::size_of::<RelationshipSideRecord>(),
        )?;
        sides.push(RelationshipSideRecord {
            name,
            table_name: entry.name.clone(),
            table_root: entry.root,
            side: relationship.side(),
            related_table: relationship.related_table(),
            fields,
            cascade_updates: relationship.cascade_updates(),
            cascade_deletes: relationship.cascade_deletes(),
        });
    }
    Ok(())
}

/// Pairs each relationship's primary-table and foreign-table records.
///
/// Every record must find exactly one counterpart with the same name whose
/// root references cross-match, the same field count, and the same cascade
/// options; anything else fails closed.
fn pair_relationships(
    mut sides: Vec<RelationshipSideRecord>,
) -> Result<Vec<Relationship>, SemanticSnapshotError> {
    sides.sort_by(|left, right| left.name.cmp(&right.name));
    let mut relationships = Vec::new();
    let mut remaining = sides.into_iter().peekable();
    while let Some(first) = remaining.next() {
        let second = remaining
            .next_if(|candidate| candidate.name == first.name)
            .ok_or(SemanticSnapshotError::UnpairedRelationship {
                table: first.table_root,
            })?;
        if remaining
            .peek()
            .is_some_and(|candidate| candidate.name == first.name)
        {
            return Err(SemanticSnapshotError::UnpairedRelationship {
                table: first.table_root,
            });
        }
        let (primary, foreign) = match (first.side, second.side) {
            (RelationshipSide::PrimaryTable, RelationshipSide::ForeignTable) => (first, second),
            (RelationshipSide::ForeignTable, RelationshipSide::PrimaryTable) => (second, first),
            _ => {
                return Err(SemanticSnapshotError::UnpairedRelationship {
                    table: first.table_root,
                });
            }
        };
        let consistent = primary.related_table == foreign.table_root
            && foreign.related_table == primary.table_root
            && primary.fields.len() == foreign.fields.len()
            && primary.cascade_updates == foreign.cascade_updates
            && primary.cascade_deletes == foreign.cascade_deletes;
        if !consistent {
            return Err(SemanticSnapshotError::UnpairedRelationship {
                table: foreign.table_root,
            });
        }
        let mut properties = PropertyMap::new();
        properties.insert(
            "cascade_deletes".to_owned(),
            TypedValue::Boolean {
                value: foreign.cascade_deletes,
                raw_hex: None,
            },
        );
        properties.insert(
            "cascade_updates".to_owned(),
            TypedValue::Boolean {
                value: foreign.cascade_updates,
                raw_hex: None,
            },
        );
        relationships.push(Relationship {
            name: foreign.name,
            table: primary.table_name,
            foreign_table: foreign.table_name,
            attributes: 0,
            fields: primary
                .fields
                .into_iter()
                .zip(foreign.fields)
                .map(|(field, foreign_field)| RelationshipField {
                    field,
                    foreign_field,
                })
                .collect(),
            properties,
        });
    }
    Ok(relationships)
}
