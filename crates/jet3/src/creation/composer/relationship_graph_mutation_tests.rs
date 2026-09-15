//! Internal graph fixtures composed from EXP-0059/0073/0268 primitives.
//! These isolate constraint checks; they do not establish DAO compatibility.
use super::*;
use crate::{
    ColumnOrdinal, DatabaseReader, FieldUpdate, FileSource, IndexColumnSpec, IndexFieldSpec,
    IndexKind, IndexSpec, ResourceLimits, RowDelete, RowLocator, TableDefinition, TableRows,
    TextCodePage, UpdateError, ValueKind,
};
use std::{
    fs,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};
type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;
static NEXT: AtomicU64 = AtomicU64::new(0);
const NAMES: [&[u8]; 3] = [b"Alpha", b"Bravo", b"Charlie"];
const COLUMNS: [ColumnSpec<'static>; 3] = [
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"One", ColumnType::Long),
    ColumnSpec::new(b"Two", ColumnType::Long),
];
const INDEXES: [IndexSpec<'static>; 3] = [
    IndexSpec {
        name: b"ById",
        fields: &[IndexColumnSpec {
            column: crate::ColumnRef::Ordinal(0),
            direction: IndexDirection::Ascending,
        }],
        kind: IndexKind::Primary,
    },
    IndexSpec {
        name: b"ByOne",
        fields: &[IndexColumnSpec {
            column: crate::ColumnRef::Ordinal(1),
            direction: IndexDirection::Ascending,
        }],
        kind: IndexKind::Ordinary,
    },
    IndexSpec {
        name: b"ByTwo",
        fields: &[IndexColumnSpec {
            column: crate::ColumnRef::Ordinal(2),
            direction: IndexDirection::Ascending,
        }],
        kind: IndexKind::Ordinary,
    },
];
#[derive(Clone, Copy)]
struct Edge {
    name: &'static [u8],
    parent: usize,
    child: usize,
    column: u16,
}
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
struct Fixture(PathBuf);
impl Fixture {
    fn path(&self) -> PathBuf {
        self.0.join("source.mdb")
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
fn definition(
    db: &mut DatabaseReader<FileSource>,
    name: &[u8],
    work: &mut ResourceBudget,
) -> Result<TableDefinition> {
    let mut root = None;
    let mut catalog = db.catalog(work)?;
    while let Some(record) = catalog.next_record()? {
        if record.name().raw_bytes() == name {
            root = record.table_definition();
        }
    }
    drop(catalog);
    Ok(db.table_definition(root.ok_or("table absent")?, work)?)
}
fn fixture(edges: &[Edge]) -> Result<Fixture> {
    let directory = std::env::temp_dir().join(format!(
        "jet3-relationship-graph-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&directory)?;
    let fixture = Fixture(directory);
    let requests = NAMES.map(|name| TableRows {
        table: TableSpec {
            name,
            columns: &COLUMNS,
            indexes: &INDEXES,
        },
        rows: &[
            &[RowValue::Long(1), RowValue::Null, RowValue::Null],
            &[RowValue::Long(2), RowValue::Long(1), RowValue::Long(1)],
            &[RowValue::Long(3), RowValue::Long(2), RowValue::Long(2)],
        ],
    });
    crate::create_database_with_table_rows(fixture.path(), &requests, &mut budget())?;
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let tables = NAMES
        .iter()
        .map(|name| definition(&mut db, name, &mut work))
        .collect::<Result<Vec<_>>>()?;
    let mut edits = crate::page_edits::PageEdits::new(db.geometry().page_count());
    let hidden = (0..edges.len())
        .map(|i| format!(".r{}", char::from(b'B' + i as u8)).into_bytes())
        .collect::<Vec<_>>();
    let fields = [0, 1, 2].map(|column| IndexFieldSpec {
        column,
        direction: IndexDirection::Ascending,
    });
    for (position, table) in tables.iter().enumerate() {
        let physical = table
            .physical_indexes()
            .iter()
            .enumerate()
            .map(|(i, index)| PhysicalIndexSpec {
                fields: &fields[i..i + 1],
                usage_map_page: index.usage_map().page(),
                usage_map_row: index.usage_map().row(),
                root: index.root(),
                flags: if i == 0 {
                    PhysicalIndexFlagsSpec::UniqueRequired
                } else {
                    PhysicalIndexFlagsSpec::Ordinary
                },
                entry_count: index.distinct_key_count(),
            })
            .collect::<Vec<_>>();
        let mut logical = INDEXES
            .iter()
            .enumerate()
            .map(|(i, index)| LogicalIndexSpec {
                name: index.name,
                physical_index: i as u16,
                kind: index.kind.logical_kind(),
            })
            .collect::<Vec<_>>();
        for (i, edge) in edges.iter().enumerate() {
            if edge.parent == position {
                logical.push(LogicalIndexSpec {
                    name: &hidden[i],
                    physical_index: 0,
                    kind: LogicalIndexKindSpec::Relationship {
                        side: crate::RelationshipSide::PrimaryTable,
                        related_table: tables[edge.child].root(),
                        raw_selector: i as u32 + 1,
                        relation_ordinal: u32::from(edge.column),
                        cascade_updates: false,
                        cascade_deletes: false,
                    },
                });
            }
            if edge.child == position {
                let relation = LogicalIndexSpec {
                    name: edge.name,
                    physical_index: edge.column,
                    kind: LogicalIndexKindSpec::Relationship {
                        side: crate::RelationshipSide::ForeignTable,
                        related_table: tables[edge.parent].root(),
                        raw_selector: u32::from(edge.column),
                        relation_ordinal: i as u32 + 1,
                        cascade_updates: false,
                        cascade_deletes: false,
                    },
                };
                if let Some(index) = logical.iter_mut().find(|index| {
                    index.physical_index == edge.column
                        && (index.kind == LogicalIndexKindSpec::Ordinary || index.name == edge.name)
                }) {
                    *index = relation;
                } else {
                    logical.push(relation);
                }
            }
        }
        logical.sort_unstable_by_key(|index| index.name);
        let mut image = [0; PAGE_BYTES];
        encode_table_definition(
            &TableDefinitionSpec {
                kind: TableDefinitionKind::User,
                columns: &COLUMNS,
                system_column_classes: &[],
                physical_indexes: &physical,
                indexes: &logical,
                owned_map: table.maps().owned(),
                available_map: table.maps().available(),
                row_count: table.row_count(),
                long_value_maps: &[],
            },
            &mut image,
            &mut work,
        )?;
        edits.set_image(
            &mut db,
            table.root(),
            PageImage::from_bytes(image),
            &mut work,
        )?;
    }
    let central = db.table_definition(PageNumber::new(MSYS_RELATIONSHIPS_ROOT), &mut work)?;
    let layout = central
        .columns()
        .iter()
        .map(RowColumnLayout::from)
        .collect::<Vec<_>>();
    let mut rows = Vec::new();
    for edge in edges {
        let values = [
            RowValue::Text(edge.name),
            RowValue::Long(0),
            RowValue::Long(1),
            RowValue::Long(0),
            RowValue::Text(NAMES[edge.child]),
            RowValue::Text(COLUMNS[usize::from(edge.column)].name()),
            RowValue::Text(NAMES[edge.parent]),
            RowValue::Text(b"Id"),
        ];
        let mut row = [0; PAGE_BYTES];
        let length = encode_row(&layout, &values, &mut row, &mut work)?.get() as usize;
        rows.push(row[..length].to_vec());
    }
    let page = edits.append(
        data_page(
            MSYS_RELATIONSHIPS_ROOT,
            &rows.iter().map(Vec::as_slice).collect::<Vec<_>>(),
            &mut work,
        )?,
        &mut work,
    )?;
    for locator in [central.maps().owned(), central.maps().available()] {
        edits.map_bit(&mut db, locator, page, false, true, &mut work)?;
    }
    let mut counts = [0; 3];
    for (i, root) in [
        RELATIONSHIPS_NAME_ROOT,
        RELATIONSHIPS_OBJECT_ROOT,
        RELATIONSHIPS_REFERENCED_ROOT,
    ]
    .into_iter()
    .enumerate()
    {
        let mut entries = Vec::new();
        for (row, edge) in edges.iter().enumerate() {
            let name = match i {
                0 => edge.name,
                1 => NAMES[edge.child],
                _ => NAMES[edge.parent],
            };
            let mut entry = OwnedIndexEntry::name(0, name, row as u8)?;
            let prefix = crate::catalog_name_key::LONG_COMPONENT_LEN;
            entry.key.copy_within(prefix..entry.len, 0);
            entry.len -= prefix;
            entries.push(entry);
        }
        sort_index_entries(&mut entries);
        counts[i] = entries
            .iter()
            .enumerate()
            .filter(|(j, entry)| {
                *j == 0 || entry.key[..entry.len] != entries[j - 1].key[..entries[j - 1].len]
            })
            .count() as u32;
        edits.set_image(
            &mut db,
            PageNumber::new(root),
            index_page(MSYS_RELATIONSHIPS_ROOT, page.get(), &entries, &mut work)?,
            &mut work,
        )?;
    }
    edits.set_image(
        &mut db,
        central.root(),
        msys_relationships_definition(edges.len() as u32, counts, &mut work)?,
        &mut work,
    )?;
    edits.publish(&fixture.path(), db, &mut work, |_| {
        Ok::<(), std::convert::Infallible>(())
    })?;
    Ok(fixture)
}
fn locator(fixture: &Fixture, table: usize, id: i32) -> Result<RowLocator> {
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let definition = definition(&mut db, NAMES[table], &mut work)?;
    let mut rows = db.rows(&definition, &mut work)?;
    while let Some(mut row) = rows.next_row()? {
        if matches!(row.value(ColumnOrdinal::new(0), TextCodePage::Windows1252)?.ok_or("Id absent")?.kind(), ValueKind::Long(value) if *value == id)
        {
            return Ok(row.locator());
        }
    }
    Err("Id absent".into())
}
fn field(
    fixture: &Fixture,
    table: usize,
    id: i32,
    column: u16,
    value: RowValue<'_>,
) -> std::result::Result<(), UpdateError> {
    let row = locator(fixture, table, id).map_err(|_| UpdateError::Mismatch("test row absent"))?;
    crate::update_field(
        fixture.path(),
        FieldUpdate {
            table: NAMES[table],
            row,
            column: ColumnOrdinal::new(column),
            value,
        },
        &mut budget(),
    )
}
fn check(fixture: &Fixture) -> Result {
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    db.validate(TextCodePage::Windows1252, &mut work)?;
    for name in NAMES {
        let table = definition(&mut db, name, &mut work)?;
        crate::relationship_catalog::load(&mut db, &table, name, &mut work)?;
    }
    Ok(())
}
#[test]
fn two_foreign_keys_and_both_parent_endpoints_enforce_every_constraint() -> Result {
    for second_parent in [0, 2] {
        let fixture = fixture(&[
            Edge {
                name: b"LeftRelation",
                parent: 0,
                child: 1,
                column: 1,
            },
            Edge {
                name: b"RightRelation",
                parent: second_parent,
                child: 1,
                column: 2,
            },
        ])?;
        check(&fixture)?;
        let before = fs::read(fixture.path())?;
        for column in [1, 2] {
            assert!(matches!(
                field(&fixture, 1, 2, column, RowValue::Long(999)),
                Err(UpdateError::RelationshipConstraint { .. })
            ));
        }
        assert!(matches!(
            field(&fixture, second_parent, 1, 0, RowValue::Long(99)),
            Err(UpdateError::RelationshipConstraint { .. })
        ));
        assert_eq!(fs::read(fixture.path())?, before);
        field(&fixture, 1, 2, 1, RowValue::Long(3))?;
        crate::update_row(
            fixture.path(),
            crate::RowUpdate {
                table: NAMES[1],
                row: locator(&fixture, 1, 2)?,
                values: &[RowValue::Long(2), RowValue::Long(3), RowValue::Null],
            },
            &mut budget(),
        )?;
        check(&fixture)?;
    }
    Ok(())
}
#[test]
fn chain_middle_table_is_checked_as_both_parent_and_child() -> Result {
    let fixture = fixture(&[
        Edge {
            name: b"FirstRelation",
            parent: 0,
            child: 1,
            column: 1,
        },
        Edge {
            name: b"SecondRelation",
            parent: 1,
            child: 2,
            column: 1,
        },
    ])?;
    let before = fs::read(fixture.path())?;
    assert!(matches!(
        field(&fixture, 1, 2, 1, RowValue::Long(999)),
        Err(UpdateError::RelationshipConstraint { .. })
    ));
    assert!(matches!(
        field(&fixture, 1, 2, 0, RowValue::Long(99)),
        Err(UpdateError::RelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(fixture.path())?, before);
    field(&fixture, 1, 3, 0, RowValue::Long(33))?;
    field(&fixture, 1, 2, 1, RowValue::Long(3))?;
    check(&fixture)
}
#[test]
fn self_reference_checks_the_resulting_row_set_once_per_side() -> Result {
    let fixture = fixture(&[Edge {
        name: b"SelfRelation",
        parent: 0,
        child: 0,
        column: 1,
    }])?;
    crate::insert_row(
        fixture.path(),
        NAMES[0],
        &[RowValue::Long(4), RowValue::Long(4), RowValue::Null],
        &mut budget(),
    )?;
    let before = fs::read(fixture.path())?;
    assert!(matches!(
        field(&fixture, 0, 4, 0, RowValue::Long(44)),
        Err(UpdateError::RelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(fixture.path())?, before);
    crate::update_row(
        fixture.path(),
        crate::RowUpdate {
            table: NAMES[0],
            row: locator(&fixture, 0, 4)?,
            values: &[RowValue::Long(44), RowValue::Long(44), RowValue::Null],
        },
        &mut budget(),
    )?;
    crate::delete_row(
        fixture.path(),
        RowDelete {
            table: NAMES[0],
            row: locator(&fixture, 0, 44)?,
        },
        &mut budget(),
    )?;
    check(&fixture)
}

#[test]
fn parent_remains_protected_until_both_children_release_its_key() -> Result {
    let fixture = fixture(&[
        Edge {
            name: b"FirstChild",
            parent: 0,
            child: 1,
            column: 1,
        },
        Edge {
            name: b"SecondChild",
            parent: 0,
            child: 2,
            column: 1,
        },
    ])?;
    field(&fixture, 1, 2, 1, RowValue::Long(3))?;
    let before = fs::read(fixture.path())?;
    let request = RowDelete {
        table: NAMES[0],
        row: locator(&fixture, 0, 1)?,
    };
    assert!(matches!(
        crate::delete_row(fixture.path(), request, &mut budget()),
        Err(UpdateError::RelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(fixture.path())?, before);
    field(&fixture, 2, 2, 1, RowValue::Long(3))?;
    crate::delete_row(fixture.path(), request, &mut budget())?;
    check(&fixture)
}

#[test]
fn duplicate_catalog_bindings_cannot_hide_a_different_target_record() -> Result {
    let edge = Edge {
        name: b"Repeated",
        parent: 0,
        child: 1,
        column: 1,
    };
    let fixture = fixture(&[edge, edge])?;
    let before = fs::read(fixture.path())?;
    assert!(matches!(
        field(&fixture, 0, 3, 2, RowValue::Long(8)),
        Err(UpdateError::Mismatch(
            "unresolved target relationship record"
        ))
    ));
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(())
}

#[test]
fn shared_foreign_index_still_requires_the_key_in_both_parent_tables() -> Result {
    let fixture = fixture(&[
        Edge {
            name: b"FirstParent",
            parent: 0,
            child: 1,
            column: 1,
        },
        Edge {
            name: b"SecondParent",
            parent: 2,
            child: 1,
            column: 1,
        },
    ])?;
    crate::delete_row(
        fixture.path(),
        RowDelete {
            table: NAMES[2],
            row: locator(&fixture, 2, 3)?,
        },
        &mut budget(),
    )?;
    let before = fs::read(fixture.path())?;
    assert!(matches!(
        field(&fixture, 1, 2, 1, RowValue::Long(3)),
        Err(UpdateError::RelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(fixture.path())?, before);
    field(&fixture, 1, 2, 1, RowValue::Long(2))?;
    check(&fixture)
}
