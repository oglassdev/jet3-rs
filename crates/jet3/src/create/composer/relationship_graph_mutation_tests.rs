//! Internal graph fixtures composed from EXP-0059/0073/0268 primitives.
//! These isolate constraint checks; they do not establish DAO compatibility.
use super::*;
use crate::testkit::create;
use crate::testkit::index;
use crate::testkit::table;
use crate::{
    ColumnOrdinal, DatabaseReader, FieldUpdate, FileSource, IndexColumnSpec, IndexFieldSpec,
    IndexKind, IndexSpec, PAGE_BYTES, RowDelete, RowLocator, TableDefinition, TableRows,
    TextCodePage, ValueKind, WriteError,
};
use std::{fs, path::PathBuf};
pub(super) type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;
pub(super) const NAMES: [&[u8]; 3] = [b"Alpha", b"Bravo", b"Charlie"];
const COLUMNS: [ColumnSpec<'static>; 3] = [
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"One", ColumnType::Long),
    ColumnSpec::new(b"Two", ColumnType::Long),
];
const INDEXES: [IndexSpec<'static>; 3] = [
    index(
        b"ById",
        &[IndexColumnSpec {
            column: crate::ColumnRef::Ordinal(0),
            direction: IndexDirection::Ascending,
        }],
        IndexKind::Primary,
    ),
    index(
        b"ByOne",
        &[IndexColumnSpec {
            column: crate::ColumnRef::Ordinal(1),
            direction: IndexDirection::Ascending,
        }],
        IndexKind::Ordinary,
    ),
    index(
        b"ByTwo",
        &[IndexColumnSpec {
            column: crate::ColumnRef::Ordinal(2),
            direction: IndexDirection::Ascending,
        }],
        IndexKind::Ordinary,
    ),
];
#[derive(Clone, Copy)]
pub(super) struct Edge {
    pub(super) name: &'static [u8],
    pub(super) parent: usize,
    pub(super) child: usize,
    pub(super) column: u16,
}
pub(super) const fn edge(name: &'static [u8], parent: usize, child: usize, column: u16) -> Edge {
    Edge {
        name,
        parent,
        child,
        column,
    }
}
pub(super) use crate::testkit::budget;
pub(super) struct Fixture(crate::testkit::TempDir);
impl Fixture {
    pub(super) fn path(&self) -> PathBuf {
        self.0.join("source.mdb")
    }
}
pub(super) fn definition(
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
pub(super) fn fixture(edges: &[Edge]) -> Result<Fixture> {
    let directory = crate::testkit::TempDir::new("relationship-graph")?;
    let fixture = Fixture(directory);
    let requests = NAMES.map(|name| TableRows {
        table: table(name, &COLUMNS, &INDEXES),
        rows: &[
            &[RowValue::Long(1), RowValue::Null, RowValue::Null],
            &[RowValue::Long(2), RowValue::Long(1), RowValue::Long(1)],
            &[RowValue::Long(3), RowValue::Long(2), RowValue::Long(2)],
        ],
    });
    create(fixture.path(), &requests)?;
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let tables = NAMES
        .iter()
        .map(|name| definition(&mut db, name, &mut work))
        .collect::<Result<Vec<_>>>()?;
    let mut edits = crate::write::page_edits::PageEdits::new(db.geometry().page_count());
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
            crate::index::key::text::ENCODING_CONTEXT,
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
            let prefix = crate::catalog::name_key::LONG_COMPONENT_LEN;
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
pub(super) fn locator(fixture: &Fixture, table: usize, id: i32) -> Result<RowLocator> {
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
pub(super) fn field(
    fixture: &Fixture,
    table: usize,
    id: i32,
    column: u16,
    value: RowValue<'_>,
) -> std::result::Result<(), WriteError> {
    let row = locator(fixture, table, id).map_err(|_| WriteError::Mismatch("test row absent"))?;
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
        crate::relationship::catalog::load(&mut db, &table, name, &mut work)?;
    }
    Ok(())
}
#[test]
fn two_foreign_keys_and_both_parent_endpoints_enforce_every_constraint() -> Result {
    for second_parent in [0, 2] {
        let fixture = fixture(&[
            edge(b"LeftRelation", 0, 1, 1),
            edge(b"RightRelation", second_parent, 1, 2),
        ])?;
        check(&fixture)?;
        let before = fs::read(fixture.path())?;
        for column in [1, 2] {
            assert!(matches!(
                field(&fixture, 1, 2, column, RowValue::Long(999)),
                Err(WriteError::RelationshipConstraint { .. })
            ));
        }
        assert!(matches!(
            field(&fixture, second_parent, 1, 0, RowValue::Long(99)),
            Err(WriteError::RelationshipConstraint { .. })
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
        edge(b"FirstRelation", 0, 1, 1),
        edge(b"SecondRelation", 1, 2, 1),
    ])?;
    let before = fs::read(fixture.path())?;
    assert!(matches!(
        field(&fixture, 1, 2, 1, RowValue::Long(999)),
        Err(WriteError::RelationshipConstraint { .. })
    ));
    assert!(matches!(
        field(&fixture, 1, 2, 0, RowValue::Long(99)),
        Err(WriteError::RelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(fixture.path())?, before);
    field(&fixture, 1, 3, 0, RowValue::Long(33))?;
    field(&fixture, 1, 2, 1, RowValue::Long(3))?;
    check(&fixture)
}
#[test]
fn parent_remains_protected_until_both_children_release_its_key() -> Result {
    let fixture = fixture(&[edge(b"FirstChild", 0, 1, 1), edge(b"SecondChild", 0, 2, 1)])?;
    field(&fixture, 1, 2, 1, RowValue::Long(3))?;
    let before = fs::read(fixture.path())?;
    let request = RowDelete {
        table: NAMES[0],
        row: locator(&fixture, 0, 1)?,
    };
    assert!(matches!(
        crate::delete_row(fixture.path(), request, &mut budget()),
        Err(WriteError::RelationshipConstraint { .. })
    ));
    assert_eq!(fs::read(fixture.path())?, before);
    field(&fixture, 2, 2, 1, RowValue::Long(3))?;
    crate::delete_row(fixture.path(), request, &mut budget())?;
    check(&fixture)
}

#[test]
fn duplicate_catalog_bindings_cannot_hide_a_different_target_record() -> Result {
    let edge = edge(b"Repeated", 0, 1, 1);
    let fixture = fixture(&[edge, edge])?;
    let before = fs::read(fixture.path())?;
    assert!(matches!(
        field(&fixture, 0, 3, 2, RowValue::Long(8)),
        Err(WriteError::Mismatch("relationship catalog component count"))
    ));
    assert_eq!(fs::read(fixture.path())?, before);
    Ok(())
}
