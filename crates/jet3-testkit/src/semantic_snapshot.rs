//! Rust producer of protocol canonical semantic snapshots.
//!
//! This adapter translates the currently supported semantic-reader output of
//! one opened Jet 3 database into the typed [`CanonicalSnapshot`] model. It is
//! bound to the public `jet3` API only, charges every retained object to the
//! caller's operation budget before allocating it, and fails closed on any
//! catalog object, value, or name form the reader does not yet interpret.
//! Schema facts the reader cannot establish are reported as unavailable, never
//! guessed. A snapshot is a Rust self-read; it never establishes DAO
//! compatibility on its own.

use std::collections::BTreeSet;
use std::error::Error;
use std::fmt;

use crate::{
    CoverageReceipt, HexString, Producer, PropertyMap, Relationship, RelationshipField, ScenarioId,
    SemanticProtocolError, SemanticRow, SemanticSnapshot, SemanticTable, Sha256, Sha256Hasher,
    SnapshotError, TableKind, TypedValue, hex_digest,
};
use jet3::{
    AllocationMapError, AllocationTraversalError, CatalogError, CatalogObjectClass,
    CatalogObjectKind, DatabasePageError, DatabaseReader, ExternalLongValueStorage,
    IndexDefinitionKind, IndexTreeError, LongValue, LongValueChunkValue, LongValueError,
    LongValueKind, LongValueReference, PageNumber, ReadAt, RelationshipSide, ResourceBudget,
    RowError, TableDefinition, TableDefinitionError, TextCodePage, UsageMapError, ValueError,
};

#[path = "semantic_snapshot_convert.rs"]
mod convert;
#[path = "semantic_snapshot_coverage.rs"]
mod coverage;
#[path = "semantic_snapshot_retained.rs"]
mod retained;

use convert::{convert_column, convert_index, convert_value};
use coverage::{collect_allocation_evidence, collect_index_evidence, record_value_branch};
use retained::RetainedLedger;

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
    /// Streaming one external Memo/OLE value failed.
    LongValue(LongValueError),
    /// Traversing one owned-page set failed.
    Allocation(AllocationTraversalError),
    /// Reading the allocation-map record page failed.
    DatabasePage(DatabasePageError),
    /// Locating the allocation-map record failed.
    UsageMap(UsageMapError),
    /// Decoding the allocation-map record failed.
    AllocationMap(AllocationMapError),
    /// Traversing one decoded index tree failed.
    IndexTree(IndexTreeError),
    /// Charging retained snapshot state to the operation budget failed.
    Resource(jet3::Error),
    /// The collected model violated the protocol's canonical constraints.
    Snapshot(SnapshotError),
    /// The versioned protocol 1.2 model was invalid.
    Protocol(SemanticProtocolError),
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
            Self::LongValue(source) => write!(formatter, "snapshot long value failed: {source}"),
            Self::Allocation(source) => write!(formatter, "snapshot allocation failed: {source}"),
            Self::DatabasePage(source) => write!(formatter, "snapshot map page failed: {source}"),
            Self::UsageMap(source) => write!(formatter, "snapshot usage map failed: {source}"),
            Self::AllocationMap(source) => {
                write!(formatter, "snapshot map decoding failed: {source}")
            }
            Self::IndexTree(source) => {
                write!(formatter, "snapshot index traversal failed: {source}")
            }
            Self::Resource(source) => write!(formatter, "snapshot resource policy: {source}"),
            Self::Snapshot(source) => write!(formatter, "snapshot canonical model: {source}"),
            Self::Protocol(source) => write!(formatter, "snapshot protocol model: {source}"),
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
            Self::LongValue(source) => Some(source),
            Self::Allocation(source) => Some(source),
            Self::DatabasePage(source) => Some(source),
            Self::UsageMap(source) => Some(source),
            Self::AllocationMap(source) => Some(source),
            Self::IndexTree(source) => Some(source),
            Self::Resource(source) => Some(source),
            Self::Snapshot(source) => Some(source),
            Self::Protocol(source) => Some(source),
            _ => None,
        }
    }
}

impl From<SnapshotError> for SemanticSnapshotError {
    fn from(source: SnapshotError) -> Self {
        Self::Snapshot(source)
    }
}

impl From<SemanticProtocolError> for SemanticSnapshotError {
    fn from(source: SemanticProtocolError) -> Self {
        Self::Protocol(source)
    }
}

/// The paired protocol documents emitted for one successful Rust read.
#[derive(Clone, Debug, PartialEq)]
pub struct SemanticSnapshotArtifacts {
    pub snapshot: SemanticSnapshot,
    pub coverage_receipt: CoverageReceipt,
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

struct CollectionContext<'a> {
    code_page: TextCodePage,
    budget: &'a mut ResourceBudget,
    ledger: &'a mut RetainedLedger,
    branches: &'a mut BTreeSet<String>,
}

/// Produces one deterministic canonical snapshot of every user table.
///
/// The catalog is streamed once; every user table definition is decoded, its
/// rows are streamed and decoded with the selected code page, and the result
/// is canonicalized under the protocol's ordering rules. System-class catalog
/// objects are not snapshotted in this slice. Retained state is charged to
/// `budget` through a ledger that is reconciled whenever no reader cursor
/// holds the budget.
pub fn snapshot_database<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    options: &SemanticSnapshotOptions,
    budget: &mut ResourceBudget,
) -> Result<SemanticSnapshot, SemanticSnapshotError> {
    snapshot_database_with_receipt(database, options, budget).map(|artifacts| artifacts.snapshot)
}

/// Produces the canonical snapshot and its database-bound coverage receipt.
pub fn snapshot_database_with_receipt<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    options: &SemanticSnapshotOptions,
    budget: &mut ResourceBudget,
) -> Result<SemanticSnapshotArtifacts, SemanticSnapshotError> {
    let mut ledger = RetainedLedger::new(budget);
    let catalog = collect_user_tables(database, budget, &mut ledger)?;
    ledger.sync(budget)?;
    let mut snapshot = SemanticSnapshot::new(
        options.scenario_id.clone(),
        options.producer.clone(),
        options.database_sha256.clone(),
    );
    let mut branches = BTreeSet::from([
        "open.signature_geometry".to_owned(),
        "open.header_page".to_owned(),
        "catalog.root_discovery".to_owned(),
        "catalog.record_stream".to_owned(),
    ]);
    let mut allocated_hasher = Sha256Hasher::new();
    let mut sides = Vec::new();
    for entry in &catalog {
        let definition = database
            .table_definition(entry.root, budget)
            .map_err(SemanticSnapshotError::TableDefinition)?;
        collect_allocation_evidence(
            database,
            entry,
            &definition,
            budget,
            &mut branches,
            &mut allocated_hasher,
        )?;
        ledger.sync(budget)?;
        let names = column_names(&definition, entry.root, &mut ledger)?;
        let table = collect_table(
            database,
            entry,
            &definition,
            &names,
            &mut CollectionContext {
                code_page: options.code_page,
                budget,
                ledger: &mut ledger,
                branches: &mut branches,
            },
        )?;
        ledger.sync(budget)?;
        collect_relationship_sides(entry, &definition, &names, &mut sides, &mut ledger)?;
        if definition.relationships().next().is_some() {
            branches.insert("tdef.relationship_reference".to_owned());
        }
        ledger.push(&mut snapshot.tables, table)?;
        ledger.sync(budget)?;
    }
    snapshot.relationships = pair_relationships(sides, &mut ledger)?;
    ledger.sync(budget)?;
    snapshot.canonicalize()?;
    let receipt = CoverageReceipt {
        scenario_id: options.scenario_id.clone(),
        source_revision: options.producer.source_revision().to_owned(),
        database_sha256: options.database_sha256.clone(),
        allocated_set_sha256: Sha256::new(hex_digest(allocated_hasher.finalize().map_err(
            |_| SemanticProtocolError::InvalidModel {
                path: "$.allocated_set_sha256".to_owned(),
                reason: "SHA-256 length is not representable",
            },
        )?))?,
        branches,
    };
    Ok(SemanticSnapshotArtifacts {
        snapshot,
        coverage_receipt: receipt,
    })
}

fn collect_user_tables<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
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
            _ => None,
        };
        let Some(root) = root else {
            return Err(SemanticSnapshotError::UnsupportedCatalogObject {
                id: record.id().get(),
                kind: record.kind().raw(),
            });
        };
        let name = ledger.ascii_name(record.name().raw_bytes(), Some(root))?;
        ledger.push(
            &mut tables,
            CatalogTable {
                name,
                root,
                raw_flags: record.raw_flags(),
            },
        )?;
    }
    Ok(tables)
}

fn column_names(
    definition: &TableDefinition,
    table: PageNumber,
    ledger: &mut RetainedLedger,
) -> Result<Vec<String>, SemanticSnapshotError> {
    let mut names = Vec::new();
    for column in definition.columns() {
        let name = ledger.ascii_name(column.name().raw_bytes(), Some(table))?;
        ledger.push(&mut names, name)?;
    }
    Ok(names)
}

fn collect_table<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    entry: &CatalogTable,
    definition: &TableDefinition,
    names: &[String],
    context: &mut CollectionContext<'_>,
) -> Result<SemanticTable, SemanticSnapshotError> {
    let mut columns = Vec::new();
    for (column, name) in definition.columns().iter().zip(names) {
        let name = context.ledger.text(name)?;
        let column = convert_column(column, name, context.ledger)?;
        context.ledger.push(&mut columns, column)?;
    }
    let mut indexes = Vec::new();
    for logical in definition.indexes() {
        let primary = match logical.kind() {
            IndexDefinitionKind::Primary => true,
            IndexDefinitionKind::Ordinary => false,
            IndexDefinitionKind::Relationship(_) => continue,
        };
        let index = convert_index(
            definition,
            logical,
            primary,
            names,
            entry.root,
            context.ledger,
        )?;
        context.ledger.push(&mut indexes, index)?;
    }
    context.ledger.sync(context.budget)?;
    collect_index_evidence(database, definition, context.budget, context.branches)?;
    let rows = collect_rows(database, definition, entry.root, names, context)?;
    context.branches.insert("tdef.column_types".to_owned());
    if !indexes.is_empty() {
        context.branches.insert("tdef.logical_index".to_owned());
        context.branches.insert("tdef.physical_index".to_owned());
    }
    Ok(SemanticTable {
        name: context.ledger.text(&entry.name)?,
        kind: TableKind::User,
        attributes: i64::from(entry.raw_flags),
        columns,
        indexes,
        properties: PropertyMap::new(),
        rows,
    })
}

/// Streams and retains every row with an empty producer key.
///
/// The binding P8 row-key contract (protocol v1.2) is not created yet. Until
/// it is, rows carry no declared key, so the shared canonicalizer orders them
/// purely by the canonical tuple over all lossless typed values; this adapter
/// deliberately implements no other row-identity convention.
fn collect_rows<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    definition: &TableDefinition,
    table: PageNumber,
    names: &[String],
    context: &mut CollectionContext<'_>,
) -> Result<Vec<SemanticRow>, SemanticSnapshotError> {
    let mut rows = Vec::new();
    let mut cursor = database
        .rows(definition, context.budget)
        .map_err(SemanticSnapshotError::Row)?;
    while let Some(view) = cursor.next_row().map_err(SemanticSnapshotError::Row)? {
        let (mut values, external) = collect_row(
            view,
            definition,
            table,
            names,
            context.code_page,
            context.ledger,
            context.branches,
        )?;
        for (key, reference) in external {
            let value =
                stream_external_value(&mut cursor, reference, context.ledger, context.branches)?;
            context.ledger.insert(&mut values, key, value)?;
        }
        context.ledger.push(
            &mut rows,
            SemanticRow {
                canonical_key: Sha256::new("00".repeat(32))?,
                duplicate_ordinal: 0,
                values,
            },
        )?;
    }
    let coverage = cursor.coverage();
    for (covered, branch) in [
        (coverage.deleted_skip(), "rows.deleted_skip"),
        (coverage.direct(), "rows.direct"),
        (coverage.overflow_pointer(), "rows.overflow_pointer"),
        (coverage.wide_variable_layout(), "rows.wide_variable_layout"),
    ] {
        if covered {
            context.branches.insert(branch.to_owned());
        }
    }
    Ok(rows)
}

fn collect_row(
    mut view: jet3::RowView<'_, '_>,
    definition: &TableDefinition,
    table: PageNumber,
    names: &[String],
    code_page: TextCodePage,
    ledger: &mut RetainedLedger,
    branches: &mut BTreeSet<String>,
) -> Result<(PropertyMap, Vec<(String, LongValueReference)>), SemanticSnapshotError> {
    let mut values = PropertyMap::new();
    let mut external = Vec::new();
    for (column, name) in definition.columns().iter().zip(names) {
        let ordinal = column.ordinal();
        let decoded = view
            .value(ordinal, code_page)
            .map_err(SemanticSnapshotError::Value)?
            .ok_or(SemanticSnapshotError::InvalidIndexReference {
                table,
                physical_index: ordinal.get(),
            })?;
        let key = ledger.text(name)?;
        if let jet3::ValueKind::LongValue(LongValue::External(reference)) = decoded.kind() {
            ledger.push(&mut external, (key, *reference))?;
        } else {
            let value = convert_value(&decoded, table, ordinal.get(), ledger)?;
            record_value_branch(&value, code_page, branches);
            ledger.insert(&mut values, key, value)?;
        }
    }
    Ok((values, external))
}

fn stream_external_value<S: ReadAt>(
    rows: &mut jet3::RowCursor<'_, '_, S>,
    reference: LongValueReference,
    ledger: &mut RetainedLedger,
    branches: &mut BTreeSet<String>,
) -> Result<TypedValue, SemanticSnapshotError> {
    branches.insert(
        match reference.storage() {
            ExternalLongValueStorage::SinglePage => "long_value.single_page",
            ExternalLongValueStorage::Chained => "long_value.chained",
        }
        .to_owned(),
    );
    let raw_hex = Some(ledger.hex(&reference.raw_header())?);
    let mut stream = rows
        .long_value(reference)
        .map_err(SemanticSnapshotError::LongValue)?;
    match reference.kind() {
        LongValueKind::Memo => {
            let mut output = String::new();
            while let Some(chunk) = stream
                .next_chunk()
                .map_err(SemanticSnapshotError::LongValue)?
            {
                let LongValueChunkValue::Text(text) = chunk.value() else {
                    return Err(SemanticSnapshotError::UnsupportedValue {
                        table: reference.source().page(),
                        column: u16::MAX,
                        form: UnsupportedValueForm::ExternalLongValue,
                    });
                };
                ledger.append_text(&mut output, text.as_str())?;
            }
            Ok(TypedValue::Memo {
                value: output,
                raw_hex,
                code_page: Some(u32::from(reference.code_page().number())),
            })
        }
        LongValueKind::Ole => {
            let mut output = String::new();
            while let Some(chunk) = stream
                .next_chunk()
                .map_err(SemanticSnapshotError::LongValue)?
            {
                let LongValueChunkValue::Binary(bytes) = chunk.value() else {
                    return Err(SemanticSnapshotError::UnsupportedValue {
                        table: reference.source().page(),
                        column: u16::MAX,
                        form: UnsupportedValueForm::ExternalLongValue,
                    });
                };
                ledger.append_hex(&mut output, bytes)?;
            }
            Ok(TypedValue::Ole {
                value: HexString::new(output)?,
                raw_hex,
            })
        }
    }
}

fn collect_relationship_sides(
    entry: &CatalogTable,
    definition: &TableDefinition,
    names: &[String],
    sides: &mut Vec<RelationshipSideRecord>,
    ledger: &mut RetainedLedger,
) -> Result<(), SemanticSnapshotError> {
    for relationship in definition.relationships() {
        // A relationship record is itself the coverage event; pairing below
        // still fails closed if its counterpart is absent or inconsistent.
        let missing = SemanticSnapshotError::InvalidIndexReference {
            table: entry.root,
            physical_index: relationship.physical_index(),
        };
        let physical = definition
            .physical_indexes()
            .get(usize::from(relationship.physical_index()))
            .ok_or_else(|| missing.clone())?;
        let mut fields = Vec::new();
        for field in physical.fields() {
            let name = names
                .get(usize::from(field.column().get()))
                .ok_or_else(|| missing.clone())?;
            let name = ledger.text(name)?;
            ledger.push(&mut fields, name)?;
        }
        let record = RelationshipSideRecord {
            name: ledger.ascii_name(relationship.name().raw_bytes(), Some(entry.root))?,
            table_name: ledger.text(&entry.name)?,
            table_root: entry.root,
            side: relationship.side(),
            related_table: relationship.related_table(),
            fields,
            cascade_updates: relationship.cascade_updates(),
            cascade_deletes: relationship.cascade_deletes(),
        };
        ledger.push(sides, record)?;
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
    ledger: &mut RetainedLedger,
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
        for (key, value) in [
            ("cascade_deletes", foreign.cascade_deletes),
            ("cascade_updates", foreign.cascade_updates),
        ] {
            let key = ledger.text(key)?;
            ledger.insert(
                &mut properties,
                key,
                TypedValue::Boolean {
                    value,
                    raw_hex: None,
                },
            )?;
        }
        let mut fields = Vec::new();
        for (field, foreign_field) in primary.fields.into_iter().zip(foreign.fields) {
            ledger.push(
                &mut fields,
                RelationshipField {
                    field,
                    foreign_field,
                },
            )?;
        }
        ledger.push(
            &mut relationships,
            Relationship {
                name: foreign.name,
                table: primary.table_name,
                foreign_table: foreign.table_name,
                attributes: 0,
                fields,
                properties,
            },
        )?;
    }
    Ok(relationships)
}
