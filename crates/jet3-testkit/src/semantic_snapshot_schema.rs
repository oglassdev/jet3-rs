//! Catalog schema retention and relationship pairing for semantic snapshots.

use jet3::{
    CatalogObjectClass, CatalogObjectKind, DatabaseReader, PageNumber, ReadAt, RelationshipSide,
    ResourceBudget, TableDefinition,
};

use super::SemanticSnapshotError;
use super::retained::RetainedLedger;
use crate::{PropertyMap, Relationship, RelationshipField, SemanticProtocolError, TypedValue};

const DAO_RELATION_UPDATE_CASCADE: i64 = 256;
const DAO_RELATION_DELETE_CASCADE: i64 = 4_096;

pub(super) struct CatalogTable {
    pub(super) name: String,
    pub(super) root: PageNumber,
    pub(super) raw_flags: u32,
}

pub(super) struct RelationshipSideRecord {
    name: String,
    table_name: String,
    table_root: PageNumber,
    side: RelationshipSide,
    related_table: PageNumber,
    fields: Vec<String>,
    cascade_updates: bool,
    cascade_deletes: bool,
}

pub(super) fn collect_user_tables<S: ReadAt>(
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
        let name = ledger.ascii_name(cursor.budget_mut(), record.name().raw_bytes(), Some(root))?;
        ledger.push(
            cursor.budget_mut(),
            &mut tables,
            CatalogTable {
                name,
                root,
                raw_flags: record.raw_flags(),
            },
        )?;
    }
    canonicalize_catalog_tables(&mut tables)?;
    Ok(tables)
}

fn canonicalize_catalog_tables(tables: &mut [CatalogTable]) -> Result<(), SemanticSnapshotError> {
    tables.sort_by(|left, right| left.name.cmp(&right.name));
    if tables.windows(2).any(|pair| pair[0].name == pair[1].name) {
        return Err(SemanticSnapshotError::Protocol(
            SemanticProtocolError::InvalidModel {
                path: "$.tables".to_owned(),
                reason: "names must be non-empty and unique",
            },
        ));
    }
    Ok(())
}

pub(super) fn column_names(
    definition: &TableDefinition,
    table: PageNumber,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
) -> Result<Vec<String>, SemanticSnapshotError> {
    let mut names = Vec::new();
    ledger.reserve_vec(budget, &mut names, definition.columns().len())?;
    for column in definition.columns() {
        let name = ledger.ascii_name(budget, column.name().raw_bytes(), Some(table))?;
        ledger.push(budget, &mut names, name)?;
    }
    Ok(names)
}

pub(super) fn retain_column_extensions(
    table_index: usize,
    column_index: usize,
    definition: &TableDefinition,
    producer_extensions: &mut PropertyMap,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
) -> Result<(), SemanticSnapshotError> {
    use std::fmt::Write as _;

    let column = &definition.columns()[column_index];
    let base_length = "/tables/".len()
        + decimal_digits(table_index)
        + "/columns/".len()
        + decimal_digits(column_index);
    for (suffix, value) in [
        (
            "jet_raw_class",
            TypedValue::Byte {
                value: column.raw_class_flags(),
                raw_hex: Some(ledger.hex(budget, &[column.raw_class_flags()])?),
            },
        ),
        (
            "jet_raw_record",
            TypedValue::Binary {
                value: ledger.hex(budget, column.raw_record())?,
                raw_hex: Some(ledger.hex(budget, column.raw_record())?),
            },
        ),
    ] {
        let key_length = base_length
            .checked_add(1)
            .and_then(|length| length.checked_add(suffix.len()))
            .ok_or(SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
                operation: "size producer-extension key",
            }))?;
        let mut key = String::new();
        ledger.reserve_string(budget, &mut key, key_length)?;
        write!(key, "/tables/{table_index}/columns/{column_index}/{suffix}").map_err(|_| {
            SemanticSnapshotError::Resource(jet3::Error::Arithmetic {
                operation: "format producer-extension key",
            })
        })?;
        debug_assert_eq!(key.len(), key_length);
        ledger.insert(budget, producer_extensions, key, value)?;
    }
    Ok(())
}

fn decimal_digits(mut value: usize) -> usize {
    let mut digits = 1;
    while value >= 10 {
        value /= 10;
        digits += 1;
    }
    digits
}

pub(super) fn collect_relationship_sides(
    entry: &CatalogTable,
    definition: &TableDefinition,
    names: &[String],
    sides: &mut Vec<RelationshipSideRecord>,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
) -> Result<(), SemanticSnapshotError> {
    let relationship_count = definition.relationships().count();
    ledger.reserve_vec(budget, sides, relationship_count)?;
    for relationship in definition.relationships() {
        let missing = SemanticSnapshotError::InvalidIndexReference {
            table: entry.root,
            physical_index: relationship.physical_index(),
        };
        let physical = definition
            .physical_indexes()
            .get(usize::from(relationship.physical_index()))
            .ok_or_else(|| missing.clone())?;
        let mut fields = Vec::new();
        ledger.reserve_vec(budget, &mut fields, physical.fields().len())?;
        for field in physical.fields() {
            let name = names
                .get(usize::from(field.column().get()))
                .ok_or_else(|| missing.clone())?;
            let name = ledger.text(budget, name)?;
            ledger.push(budget, &mut fields, name)?;
        }
        let record = RelationshipSideRecord {
            name: ledger.ascii_name(budget, relationship.name().raw_bytes(), Some(entry.root))?,
            table_name: ledger.text(budget, &entry.name)?,
            table_root: entry.root,
            side: relationship.side(),
            related_table: relationship.related_table(),
            fields,
            cascade_updates: relationship.cascade_updates(),
            cascade_deletes: relationship.cascade_deletes(),
        };
        ledger.push(budget, sides, record)?;
    }
    Ok(())
}

pub(super) fn pair_relationships(
    mut sides: Vec<RelationshipSideRecord>,
    budget: &mut ResourceBudget,
    ledger: &mut RetainedLedger,
) -> Result<Vec<Relationship>, SemanticSnapshotError> {
    sides.sort_by(|left, right| left.name.cmp(&right.name));
    let mut relationships = Vec::new();
    ledger.reserve_vec(budget, &mut relationships, sides.len() / 2)?;
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
        if primary.related_table != foreign.table_root
            || foreign.related_table != primary.table_root
            || primary.fields.len() != foreign.fields.len()
            || primary.cascade_updates != foreign.cascade_updates
            || primary.cascade_deletes != foreign.cascade_deletes
        {
            return Err(SemanticSnapshotError::UnpairedRelationship {
                table: foreign.table_root,
            });
        }
        let mut properties = PropertyMap::new();
        ledger.reserve_properties(budget, &mut properties, 2)?;
        for (key, value) in [
            ("cascade_deletes", foreign.cascade_deletes),
            ("cascade_updates", foreign.cascade_updates),
        ] {
            let key = ledger.text(budget, key)?;
            ledger.insert(
                budget,
                &mut properties,
                key,
                TypedValue::Boolean {
                    value,
                    raw_hex: None,
                },
            )?;
        }
        let mut fields = Vec::new();
        ledger.reserve_vec(budget, &mut fields, primary.fields.len())?;
        for (field, foreign_field) in primary.fields.into_iter().zip(foreign.fields) {
            ledger.push(
                budget,
                &mut fields,
                RelationshipField {
                    field,
                    foreign_field,
                },
            )?;
        }
        ledger.push(
            budget,
            &mut relationships,
            Relationship {
                name: foreign.name,
                table: primary.table_name,
                foreign_table: foreign.table_name,
                attributes: relationship_attributes(
                    foreign.cascade_updates,
                    foreign.cascade_deletes,
                ),
                fields,
                properties,
            },
        )?;
    }
    Ok(relationships)
}

const fn relationship_attributes(cascade_updates: bool, cascade_deletes: bool) -> i64 {
    let updates = if cascade_updates {
        DAO_RELATION_UPDATE_CASCADE
    } else {
        0
    };
    let deletes = if cascade_deletes {
        DAO_RELATION_DELETE_CASCADE
    } else {
        0
    };
    updates | deletes
}

#[cfg(test)]
mod tests {
    use super::{
        CatalogTable, RelationshipSideRecord, canonicalize_catalog_tables, pair_relationships,
    };
    use crate::{
        Producer, ProducerKind, PropertyMap, ScenarioId, SemanticColumn, SemanticSnapshot,
        SemanticTable, Sha256, TableKind, TypedValue,
    };
    use jet3::{PageNumber, ReadLimits, RelationshipSide, ResourceBudget, ResourceLimits};

    use super::super::retained::RetainedLedger;

    fn table(name: &str, root: u64) -> CatalogTable {
        CatalogTable {
            name: name.into(),
            root: PageNumber::new(root),
            raw_flags: 0,
        }
    }

    #[test]
    fn physical_catalog_order_is_canonicalized_before_traversal()
    -> Result<(), Box<dyn std::error::Error>> {
        let mut tables = [table("Zulu", 4), table("Alpha", 5)];
        canonicalize_catalog_tables(&mut tables)?;
        assert_eq!(tables.map(|table| table.name), ["Alpha", "Zulu"]);
        Ok(())
    }

    #[test]
    fn duplicate_catalog_names_reject_before_traversal_begins() {
        let mut tables = [table("Same", 4), table("Same", 5)];
        let mut traversed = 0;
        let result = canonicalize_catalog_tables(&mut tables).map(|()| {
            for _ in &tables {
                traversed += 1;
            }
        });
        assert!(result.is_err());
        assert_eq!(traversed, 0);
    }

    fn relationship_side(
        table_name: &str,
        table_root: u64,
        side: RelationshipSide,
        related_table: u64,
        field: &str,
        cascade_updates: bool,
        cascade_deletes: bool,
    ) -> RelationshipSideRecord {
        RelationshipSideRecord {
            name: "ParentChild".into(),
            table_name: table_name.into(),
            table_root: PageNumber::new(table_root),
            side,
            related_table: PageNumber::new(related_table),
            fields: vec![field.into()],
            cascade_updates,
            cascade_deletes,
        }
    }

    fn semantic_table(name: &str, column: &str) -> SemanticTable {
        SemanticTable {
            name: name.into(),
            kind: TableKind::User,
            attributes: 0,
            columns: vec![SemanticColumn {
                name: column.into(),
                ordinal: 0,
                dao_type: "dbLong".into(),
                auto_increment: false,
                size: Some(4),
                attributes: 1,
                properties: PropertyMap::new(),
            }],
            indexes: Vec::new(),
            properties: PropertyMap::new(),
            rows: Vec::new(),
        }
    }

    #[test]
    fn cascade_options_emit_documented_dao_relationship_attributes()
    -> Result<(), Box<dyn std::error::Error>> {
        for (cascade_updates, cascade_deletes, expected) in [
            (false, false, 0),
            (true, false, 256),
            (false, true, 4_096),
            (true, true, 4_352),
        ] {
            let sides = vec![
                relationship_side(
                    "Parent",
                    4,
                    RelationshipSide::PrimaryTable,
                    5,
                    "Id",
                    cascade_updates,
                    cascade_deletes,
                ),
                relationship_side(
                    "Child",
                    5,
                    RelationshipSide::ForeignTable,
                    4,
                    "ParentId",
                    cascade_updates,
                    cascade_deletes,
                ),
            ];
            let mut budget = ResourceBudget::new(ResourceLimits::new(ReadLimits::default()));
            let relationships = pair_relationships(sides, &mut budget, &mut RetainedLedger::new())?;
            assert_eq!(relationships[0].attributes, expected);
            assert_eq!(
                relationships[0].properties.get("cascade_updates"),
                Some(&TypedValue::Boolean {
                    value: cascade_updates,
                    raw_hex: None,
                })
            );
            assert_eq!(
                relationships[0].properties.get("cascade_deletes"),
                Some(&TypedValue::Boolean {
                    value: cascade_deletes,
                    raw_hex: None,
                })
            );

            let mut snapshot = SemanticSnapshot::new(
                ScenarioId::new("DAO-READ-SCHEMA-RELATIONSHIP-CASCADE")?,
                Producer::new(ProducerKind::Rust, "test")?,
                Sha256::new("ab".repeat(32))?,
            );
            snapshot.tables = vec![
                semantic_table("Child", "ParentId"),
                semantic_table("Parent", "Id"),
            ];
            snapshot.relationships = relationships;
            snapshot.canonicalize()?;
            let json = String::from_utf8(snapshot.to_canonical_json()?)?;
            assert!(
                json.contains(&format!("\"relationships\":[{{\"attributes\":{expected},")),
                "{json}"
            );
        }
        Ok(())
    }
}
