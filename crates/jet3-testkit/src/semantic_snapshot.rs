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
    CoverageReceipt, HexString, Producer, PropertyMap, ScenarioId, SemanticProtocolError,
    SemanticRow, SemanticSnapshot, SemanticTable, Sha256, Sha256Hasher, SnapshotError, TableKind,
    TypedValue, hex_digest,
};
use jet3::{
    AllocationMapError, AllocationTraversalError, CatalogError, DatabasePageError, DatabaseReader,
    ExternalLongValueStorage, IndexDefinitionKind, IndexTreeError, LongValue, LongValueChunkValue,
    LongValueError, LongValueKind, LongValueReference, PageNumber, ReadAt, ResourceBudget,
    RowError, TableDefinition, TableDefinitionError, TextCodePage, UsageMapError, ValueError,
};

#[path = "semantic_snapshot_convert.rs"]
mod convert;
#[path = "semantic_snapshot_coverage.rs"]
mod coverage;
#[path = "semantic_snapshot_retained.rs"]
mod retained;
#[path = "semantic_snapshot_schema.rs"]
mod schema;

use convert::{convert_column, convert_index, convert_value};
use coverage::{collect_allocation_evidence, collect_index_evidence, record_value_branch};
use retained::RetainedLedger;
use schema::{
    CatalogTable, collect_relationship_sides, collect_user_tables, column_names,
    pair_relationships, retain_column_extensions,
};

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
    /// Canonical protocol 1.2 semantic snapshot.
    pub snapshot: SemanticSnapshot,
    /// Database-bound coverage evidence emitted beside the snapshot.
    pub coverage_receipt: CoverageReceipt,
}

impl SemanticSnapshotArtifacts {
    /// Serializes both final artifacts after charging their bounded output
    /// reservations to the live operation budget.
    pub fn to_canonical_json(
        &self,
        budget: &mut ResourceBudget,
    ) -> Result<(Vec<u8>, Vec<u8>), SemanticSnapshotError> {
        let snapshot = crate::semantic_json::write_snapshot_budgeted(&self.snapshot, budget)?;
        let receipt = crate::semantic_json::write_receipt_budgeted(&self.coverage_receipt, budget)?;
        Ok((snapshot, receipt))
    }
}

struct CollectionContext<'a> {
    code_page: TextCodePage,
    budget: &'a mut ResourceBudget,
    ledger: &'a mut RetainedLedger,
    branches: &'a mut BTreeSet<String>,
    producer_extensions: &'a mut PropertyMap,
    table_index: usize,
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
    let mut ledger = RetainedLedger::new();
    let catalog = collect_user_tables(database, budget, &mut ledger)?;
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
        let names = column_names(&definition, entry.root, budget, &mut ledger)?;
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
                producer_extensions: &mut snapshot.producer_extensions,
                table_index: snapshot.tables.len(),
            },
        )?;
        collect_relationship_sides(entry, &definition, &names, &mut sides, budget, &mut ledger)?;
        if definition.relationships().next().is_some() {
            branches.insert("tdef.relationship_reference".to_owned());
        }
        ledger.push(budget, &mut snapshot.tables, table)?;
    }
    snapshot.relationships = pair_relationships(sides, budget, &mut ledger)?;
    let canonicalization_bound = crate::semantic_json::snapshot_allocation_bound(&snapshot)
        .map_err(SemanticSnapshotError::Resource)?;
    budget
        .charge_allocation(canonicalization_bound)
        .map_err(SemanticSnapshotError::Resource)?;
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

fn collect_table<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    entry: &CatalogTable,
    definition: &TableDefinition,
    names: &[String],
    context: &mut CollectionContext<'_>,
) -> Result<SemanticTable, SemanticSnapshotError> {
    let mut columns = Vec::new();
    for (column_index, (column, name)) in definition.columns().iter().zip(names).enumerate() {
        let name = context.ledger.text(context.budget, name)?;
        let column = convert_column(column, name, context.budget, context.ledger)?;
        context.ledger.push(context.budget, &mut columns, column)?;
        retain_column_extensions(
            context.table_index,
            column_index,
            definition,
            context.producer_extensions,
            context.budget,
            context.ledger,
        )?;
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
            context.budget,
            context.ledger,
        )?;
        context.ledger.push(context.budget, &mut indexes, index)?;
    }
    collect_index_evidence(database, definition, context.budget, context.branches)?;
    let rows = collect_rows(database, definition, entry.root, names, context)?;
    context.branches.insert("tdef.column_types".to_owned());
    if !indexes.is_empty() {
        context.branches.insert("tdef.logical_index".to_owned());
        context.branches.insert("tdef.physical_index".to_owned());
    }
    Ok(SemanticTable {
        name: context.ledger.text(context.budget, &entry.name)?,
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
            context
                .ledger
                .insert(cursor.budget_mut(), &mut values, key, value)?;
        }
        context.ledger.push(
            cursor.budget_mut(),
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
        let key = ledger.text(view.budget_mut(), name)?;
        if let jet3::ValueKind::LongValue(LongValue::External(reference)) = decoded.kind() {
            ledger.push(view.budget_mut(), &mut external, (key, *reference))?;
        } else {
            let value = convert_value(&decoded, table, ordinal.get(), view.budget_mut(), ledger)?;
            record_value_branch(&value, code_page, branches);
            ledger.insert(view.budget_mut(), &mut values, key, value)?;
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
    let raw_hex = Some(ledger.hex(rows.budget_mut(), &reference.raw_header())?);
    let retained_capacity = match reference.kind() {
        LongValueKind::Memo => usize::try_from(reference.length())
            .ok()
            .and_then(|length| length.checked_mul(3)),
        LongValueKind::Ole => usize::try_from(reference.length())
            .ok()
            .and_then(|length| length.checked_mul(2)),
    }
    .ok_or(SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
        operation: "size retained external long value",
    }))?;
    ledger.charge(rows.budget_mut(), retained_capacity)?;
    let mut output = String::new();
    output.try_reserve_exact(retained_capacity).map_err(|_| {
        SemanticSnapshotError::Resource(jet3::Error::Io {
            operation: "reserve retained external long value",
            kind: std::io::ErrorKind::OutOfMemory,
        })
    })?;
    let mut stream = rows
        .long_value(reference)
        .map_err(SemanticSnapshotError::LongValue)?;
    match reference.kind() {
        LongValueKind::Memo => {
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
                output.push_str(text.as_str());
            }
            Ok(TypedValue::Memo {
                value: output,
                raw_hex,
                code_page: Some(u32::from(reference.code_page().number())),
            })
        }
        LongValueKind::Ole => {
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
                for byte in *bytes {
                    use std::fmt::Write as _;
                    write!(output, "{byte:02x}").map_err(|_| {
                        SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
                            operation: "format retained external long value",
                        })
                    })?;
                }
            }
            Ok(TypedValue::Ole {
                value: HexString::new(output)?,
                raw_hex,
            })
        }
    }
}
