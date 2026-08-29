//! Snapshot production through the public `jet3` reader.
//!
//! The catalog is streamed once, every user table definition is decoded, its
//! index trees are traversed, and its rows are streamed and decoded with the
//! selected code page. Every reader branch exercised on the way is recorded
//! by its `branch-registry.json` id.

use std::collections::BTreeSet;

use jet3::{
    AllocationMap, ByteCount, CatalogObjectClass, CatalogObjectKind, CatalogRecord,
    DatabaseFormatError, DatabaseOpenError, DatabaseReader, DefinitionName,
    ExternalLongValueStorage, IndexDefinitionKind, IndexDirection, IndexKeyEncoding, IndexNodeKind,
    JET3_PAGE_SIZE, LongValueChunkValue, LongValueKind, LongValueReference, PageNumber, ReadLimits,
    RelationshipSide, ResourceBudget, ResourceLimits, RowCursor, RowView, SliceSource,
    TableDefinition, TextCodePage, decode_allocation_map, locate_table_maps, locate_usage_map,
};

use crate::semantic_values::{Converted, convert_column, convert_value, memo_value, ole_value};
use crate::{
    Index, IndexField, PropertyMap, Relationship, RelationshipField, SemanticSnapshot,
    SnapshotError, Table, TableKind, reader_error, row_from_values, sha256_hex,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
// DAO RelationAttributeEnum: dbRelationUpdateCascade and dbRelationDeleteCascade.
const RELATION_UPDATE_CASCADE: i64 = 256;
const RELATION_DELETE_CASCADE: i64 = 4096;

/// Caller-selected snapshot identity and decoding choices.
#[derive(Clone, Debug)]
pub struct SnapshotOptions {
    /// Protocol scenario the database was generated for.
    pub scenario_id: String,
    /// Producer revision recorded in the snapshot.
    pub source_revision: String,
    /// Code page used to decode every text and memo value.
    pub code_page: TextCodePage,
}

/// Reader branch ids observed while producing a snapshot.
pub type Branches = BTreeSet<String>;

/// Result of running the reader over one database.
#[derive(Debug)]
pub enum SnapshotOutcome {
    /// The database opened and was snapshotted completely.
    Snapshot {
        /// The canonical snapshot.
        snapshot: Box<SemanticSnapshot>,
        /// Reader branches exercised.
        branches: Branches,
    },
    /// The reader rejected the header with a protocol error class.
    OpeningFailure {
        /// `unsupported_version`, `encrypted_database`, or `password_protected`.
        error_class: &'static str,
        /// Reader branches exercised.
        branches: Branches,
    },
}

/// Snapshots the complete database bytes.
pub fn snapshot_bytes(
    bytes: &[u8],
    options: &SnapshotOptions,
) -> Result<SnapshotOutcome, SnapshotError> {
    let limits = ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    ));
    let mut budget = ResourceBudget::new(limits);
    let source = SliceSource::new(bytes, budget.read_budget()).map_err(reader_error)?;
    let mut branches = Branches::new();
    let mut database = match DatabaseReader::from_source(source, &mut budget) {
        Ok(database) => database,
        Err(DatabaseOpenError::Format(error)) => {
            branches.insert("open.signature_geometry".to_owned());
            branches.insert("open.header_page".to_owned());
            branches.insert("open.rejected_format".to_owned());
            let error_class = match error {
                DatabaseFormatError::UnsupportedVersion { .. } => "unsupported_version",
                DatabaseFormatError::EncryptedOrUnsupported { .. } => "encrypted_database",
                DatabaseFormatError::PasswordedOrUnsupported => "password_protected",
                _ => return Err(reader_error(error)),
            };
            return Ok(SnapshotOutcome::OpeningFailure {
                error_class,
                branches,
            });
        }
        Err(error) => return Err(reader_error(error)),
    };
    branches.insert("open.signature_geometry".to_owned());
    branches.insert("open.header_page".to_owned());
    let mut snapshot = SemanticSnapshot::new(
        &options.scenario_id,
        &options.source_revision,
        sha256_hex(bytes),
    )?;
    let mut reader = Reader {
        database: &mut database,
        budget: &mut budget,
        code_page: options.code_page,
        branches,
        sides: Vec::new(),
    };
    for record in reader.catalog()? {
        if let Some(table) = reader.table(&record)? {
            snapshot.tables.push(table);
        }
    }
    snapshot.relationships = reader.pair_relationships()?;
    snapshot.canonicalize()?;
    Ok(SnapshotOutcome::Snapshot {
        snapshot: Box::new(snapshot),
        branches: reader.branches,
    })
}

struct Reader<'a, S> {
    database: &'a mut DatabaseReader<S>,
    budget: &'a mut ResourceBudget,
    code_page: TextCodePage,
    branches: Branches,
    sides: Vec<RelationshipSideRecord>,
}

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

impl<S: jet3::ReadAt> Reader<'_, S> {
    fn branch(&mut self, id: &str) {
        self.branches.insert(id.to_owned());
    }

    fn catalog(&mut self) -> Result<Vec<CatalogRecord>, SnapshotError> {
        let mut cursor = self.database.catalog(self.budget).map_err(reader_error)?;
        let mut records = Vec::new();
        while let Some(record) = cursor.next_record().map_err(reader_error)? {
            records.push(record);
        }
        drop(cursor);
        self.branch("catalog.root_discovery");
        if !records.is_empty() {
            self.branch("catalog.record_stream");
        }
        Ok(records)
    }

    fn table(&mut self, record: &CatalogRecord) -> Result<Option<Table>, SnapshotError> {
        let (Some(root), CatalogObjectKind::Table, CatalogObjectClass::User) =
            (record.table_definition(), record.kind(), record.class())
        else {
            return Ok(None);
        };
        let name = record
            .name()
            .decoded_ascii()
            .ok_or_else(|| SnapshotError::NonAsciiName(record.name().raw_bytes().to_vec()))?
            .to_owned();
        let definition = self
            .database
            .table_definition(root, self.budget)
            .map_err(reader_error)?;
        self.branch(
            if u64::from(definition.logical_length()) <= PAGE_BYTES as u64 {
                "tdef.single_page"
            } else {
                "tdef.continuation_chain"
            },
        );
        self.branch("tdef.column_types");
        self.allocation_branches(root)?;
        let column_names = definition
            .columns()
            .iter()
            .map(|column| ascii_name(column.name()))
            .collect::<Result<Vec<_>, _>>()?;
        let columns = definition
            .columns()
            .iter()
            .zip(&column_names)
            .map(|(column, name)| convert_column(column, name.clone()))
            .collect();
        let indexes = self.indexes(&definition, &column_names)?;
        self.relationship_sides(&definition, &column_names, &name, root)?;
        for ordinal in 0..definition.physical_indexes().len() {
            self.index_tree(&definition, ordinal)?;
        }
        let rows = self.rows(&definition)?;
        Ok(Some(Table {
            attributes: i64::from(record.raw_flags()),
            columns,
            indexes,
            kind: TableKind::User,
            name,
            properties: PropertyMap::new(),
            rows,
        }))
    }

    fn allocation_branches(&mut self, root: PageNumber) -> Result<(), SnapshotError> {
        let geometry = self.database.geometry();
        let mut page = [0_u8; PAGE_BYTES];
        let classified = self
            .database
            .read_classified_page(root, &mut page, self.budget)
            .map_err(reader_error)?;
        let locator = locate_table_maps(classified, geometry, self.budget)
            .map_err(reader_error)?
            .owned();
        let mut map_page = [0_u8; PAGE_BYTES];
        let classified = self
            .database
            .read_classified_page(locator.page(), &mut map_page, self.budget)
            .map_err(reader_error)?;
        let record = locate_usage_map(classified, locator, self.budget).map_err(reader_error)?;
        match decode_allocation_map(record.raw(), self.budget).map_err(reader_error)? {
            AllocationMap::Inline(_) => self.branch("allocation.inline_map"),
            AllocationMap::Indirect(map) => {
                self.branch("allocation.indirect_map");
                let mut references = map.map_page_references();
                let mut slot = 0_u64;
                while let Some(reference) = references
                    .next_reference(self.budget)
                    .map_err(reader_error)?
                {
                    if slot > 0 && reference != 0 {
                        self.branch("allocation.extended_slot");
                    }
                    slot += 1;
                }
            }
            _ => {}
        }
        Ok(())
    }

    fn indexes(
        &mut self,
        definition: &TableDefinition,
        column_names: &[String],
    ) -> Result<Vec<Index>, SnapshotError> {
        let mut indexes = Vec::new();
        for logical in definition.indexes() {
            let primary = match logical.kind() {
                IndexDefinitionKind::Primary => true,
                IndexDefinitionKind::Ordinary => false,
                IndexDefinitionKind::Relationship(_) => continue,
            };
            let physical = definition
                .physical_indexes()
                .get(usize::from(logical.physical_index()))
                .ok_or(SnapshotError::UnsupportedValue("physical index reference"))?;
            let fields = physical
                .fields()
                .iter()
                .map(|field| {
                    Ok(IndexField {
                        descending: field.direction() == IndexDirection::Descending,
                        name: column_names
                            .get(usize::from(field.column().get()))
                            .cloned()
                            .ok_or(SnapshotError::UnsupportedValue("index column reference"))?,
                    })
                })
                .collect::<Result<Vec<_>, SnapshotError>>()?;
            indexes.push(Index {
                fields,
                name: ascii_name(logical.name())?,
                primary,
                properties: PropertyMap::new(),
                required: physical.required(),
                unique: physical.unique(),
            });
            self.branch("tdef.logical_index");
            self.branch("tdef.physical_index");
        }
        Ok(indexes)
    }

    fn relationship_sides(
        &mut self,
        definition: &TableDefinition,
        column_names: &[String],
        table_name: &str,
        root: PageNumber,
    ) -> Result<(), SnapshotError> {
        for relationship in definition.relationships() {
            self.branch("tdef.relationship_reference");
            let physical = definition
                .physical_indexes()
                .get(usize::from(relationship.physical_index()))
                .ok_or(SnapshotError::UnsupportedValue("physical index reference"))?;
            let fields = physical
                .fields()
                .iter()
                .map(|field| {
                    column_names
                        .get(usize::from(field.column().get()))
                        .cloned()
                        .ok_or(SnapshotError::UnsupportedValue("index column reference"))
                })
                .collect::<Result<Vec<_>, _>>()?;
            self.sides.push(RelationshipSideRecord {
                name: ascii_name(relationship.name())?,
                table_name: table_name.to_owned(),
                table_root: root,
                side: relationship.side(),
                related_table: relationship.related_table(),
                fields,
                cascade_updates: relationship.cascade_updates(),
                cascade_deletes: relationship.cascade_deletes(),
            });
        }
        Ok(())
    }

    /// Pairs the primary-side and foreign-side records of each relationship
    /// by name and mutual table references.
    fn pair_relationships(&mut self) -> Result<Vec<Relationship>, SnapshotError> {
        let mut sides = std::mem::take(&mut self.sides);
        sides.sort_by(|left, right| left.name.cmp(&right.name));
        let mut relationships = Vec::new();
        let mut remaining = sides.into_iter().peekable();
        while let Some(first) = remaining.next() {
            let unpaired = || SnapshotError::UnpairedRelationship(first.name.clone());
            let second = remaining
                .next_if(|candidate| candidate.name == first.name)
                .ok_or_else(unpaired)?;
            let (primary, foreign) = match (first.side, second.side) {
                (RelationshipSide::PrimaryTable, RelationshipSide::ForeignTable) => (first, second),
                (RelationshipSide::ForeignTable, RelationshipSide::PrimaryTable) => (second, first),
                _ => return Err(unpaired()),
            };
            if primary.related_table != foreign.table_root
                || foreign.related_table != primary.table_root
                || primary.fields.len() != foreign.fields.len()
                || primary.cascade_updates != foreign.cascade_updates
                || primary.cascade_deletes != foreign.cascade_deletes
            {
                return Err(SnapshotError::UnpairedRelationship(primary.name));
            }
            let mut attributes = 0;
            if foreign.cascade_updates {
                attributes |= RELATION_UPDATE_CASCADE;
            }
            if foreign.cascade_deletes {
                attributes |= RELATION_DELETE_CASCADE;
            }
            relationships.push(Relationship {
                attributes,
                fields: primary
                    .fields
                    .into_iter()
                    .zip(foreign.fields)
                    .map(|(field, foreign_field)| RelationshipField {
                        field,
                        foreign_field,
                    })
                    .collect(),
                foreign_table: foreign.table_name,
                name: foreign.name,
                properties: PropertyMap::new(),
                table: primary.table_name,
            });
        }
        Ok(relationships)
    }

    fn index_tree(
        &mut self,
        definition: &TableDefinition,
        ordinal: usize,
    ) -> Result<(), SnapshotError> {
        let physical = &definition.physical_indexes()[ordinal];
        let ordinal = u16::try_from(ordinal)
            .map_err(|_| SnapshotError::UnsupportedValue("physical index ordinal"))?;
        let tree = self
            .database
            .index_tree(definition, ordinal, self.budget)
            .map_err(reader_error)?;
        if tree
            .nodes()
            .iter()
            .any(|node| node.kind() == IndexNodeKind::Intermediate)
        {
            self.branch("index.branch_leaf_traversal");
        }
        let composite = physical.fields().len() > 1
            || physical
                .fields()
                .iter()
                .any(|field| field.direction() == IndexDirection::Descending);
        for entry in tree.entries() {
            match entry.key().encoding() {
                IndexKeyEncoding::Null => {}
                IndexKeyEncoding::Unsupported if composite => {
                    self.branch("index.composite_key_lossless");
                }
                IndexKeyEncoding::Unsupported => {}
                _ => self.branch("index.single_field_key"),
            }
        }
        Ok(())
    }

    fn rows(&mut self, definition: &TableDefinition) -> Result<Vec<crate::Row>, SnapshotError> {
        let has_variable = definition
            .columns()
            .iter()
            .any(|column| matches!(column.storage(), jet3::ColumnStorageClass::Variable { .. }));
        let code_page = self.code_page;
        let mut cursor = self
            .database
            .rows(definition, self.budget)
            .map_err(reader_error)?;
        let mut rows = Vec::new();
        let mut branches = Branches::new();
        let mut expected_slot: Option<(PageNumber, u8)> = None;
        while let Some(mut row) = cursor.next_row().map_err(reader_error)? {
            branches.insert("rows.direct".to_owned());
            let locator = row.locator();
            if locator != row.storage_locator() {
                branches.insert("rows.overflow_pointer".to_owned());
            }
            if has_variable && row.raw_bytes().len() > usize::from(u8::MAX) {
                branches.insert("rows.wide_variable_layout".to_owned());
            }
            match expected_slot {
                Some((page, slot)) if page == locator.page() && slot != locator.slot() => {
                    branches.insert("rows.deleted_skip".to_owned());
                }
                Some((page, _)) if page == locator.page() => {}
                _ if locator.slot() != 0 => {
                    branches.insert("rows.deleted_skip".to_owned());
                }
                _ => {}
            }
            expected_slot = Some((locator.page(), locator.slot().wrapping_add(1)));
            let (values, pending) = decode_row(&mut row, definition, code_page, &mut branches)?;
            let values = resolve_external(&mut cursor, values, pending, &mut branches)?;
            rows.push(row_from_values(values)?);
        }
        drop(cursor);
        self.branches.extend(branches);
        Ok(rows)
    }
}

type Pending = Vec<(String, LongValueReference)>;

fn decode_row(
    row: &mut RowView<'_, '_>,
    definition: &TableDefinition,
    code_page: TextCodePage,
    branches: &mut Branches,
) -> Result<(PropertyMap, Pending), SnapshotError> {
    let mut values = PropertyMap::new();
    let mut pending = Pending::new();
    for column in definition.columns() {
        let name = ascii_name(column.name())?;
        let decoded = row
            .value(column.ordinal(), code_page)
            .map_err(reader_error)?
            .ok_or(SnapshotError::UnsupportedValue("column ordinal"))?;
        match decoded.kind() {
            jet3::ValueKind::Null => branches.insert("values.null_field".to_owned()),
            jet3::ValueKind::Text(text) => {
                branches.insert("values.variable_short".to_owned());
                branches.insert(text_branch(text.code_page()).to_owned())
            }
            jet3::ValueKind::Binary(_) => branches.insert("values.variable_short".to_owned()),
            jet3::ValueKind::LongValue(jet3::LongValue::Inline { .. }) => {
                branches.insert("long_value.inline".to_owned())
            }
            jet3::ValueKind::LongValue(_) | jet3::ValueKind::Boolean(_) => false,
            _ => branches.insert("values.fixed_scalar".to_owned()),
        };
        match convert_value(&decoded)? {
            Converted::Value(value) => {
                values.insert(name, value);
            }
            Converted::External(reference) => pending.push((name, reference)),
        }
    }
    Ok((values, pending))
}

fn resolve_external<S: jet3::ReadAt>(
    cursor: &mut RowCursor<'_, '_, S>,
    mut values: PropertyMap,
    pending: Pending,
    branches: &mut Branches,
) -> Result<PropertyMap, SnapshotError> {
    for (name, reference) in pending {
        branches.insert(
            match reference.storage() {
                ExternalLongValueStorage::SinglePage => "long_value.single_page",
                _ => "long_value.chained",
            }
            .to_owned(),
        );
        let mut stream = cursor.long_value(reference).map_err(reader_error)?;
        let mut raw = Vec::new();
        let mut text = String::new();
        while let Some(chunk) = stream.next_chunk().map_err(reader_error)? {
            match chunk.value() {
                LongValueChunkValue::Text(decoded) => {
                    raw.extend_from_slice(decoded.raw_bytes());
                    text.push_str(decoded.as_str());
                    branches.insert(text_branch(decoded.code_page()).to_owned());
                }
                LongValueChunkValue::Binary(bytes) => raw.extend_from_slice(bytes),
                _ => return Err(SnapshotError::UnsupportedValue("long value chunk")),
            }
        }
        let value = match reference.kind() {
            LongValueKind::Memo => memo_value(text, &raw, reference.code_page().number()),
            _ => ole_value(&raw),
        };
        values.insert(name, value);
    }
    Ok(values)
}

const fn text_branch(code_page: TextCodePage) -> &'static str {
    match code_page {
        TextCodePage::Windows1251 => "values.text_cp1251",
        TextCodePage::Windows1252 => "values.text_cp1252",
    }
}

fn ascii_name(name: &DefinitionName) -> Result<String, SnapshotError> {
    name.decoded_ascii()
        .map(str::to_owned)
        .ok_or_else(|| SnapshotError::NonAsciiName(name.raw_bytes().to_vec()))
}
