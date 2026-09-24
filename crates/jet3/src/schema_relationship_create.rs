//! Existing relationships compose EXP-0279/0286/0290/0294 indexes and EXP-0297 catalogs.
//! EXP-0307: one-to-one child indexes are unique and include nulls.
use crate::page_edits::{PageEdits, reserve};
use crate::{
    ColumnOrdinal, ColumnRef, IndexColumnSpec, IndexDirection, IndexKind, IndexNullPolicy,
    IndexSpec, LogicalIndexKindSpec, LogicalIndexSpec, PageNumber, RelationshipSide,
    RelationshipSpec, ResourceBudget, TableDefinition, TableRef, UpdateError,
};
use std::fs::File;

pub(crate) fn create(
    file: &mut File,
    journal: &mut PageEdits,
    spec: RelationshipSpec<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    crate::schema_edit::name(spec.name, 63)?;
    let (parent_name, child_name) = match (spec.parent, spec.child) {
        (TableRef::Name(parent), TableRef::Name(child)) => (parent, child),
        _ => {
            return Err(UpdateError::Unsupported(
                "existing relationship table references require names",
            ));
        }
    };
    if !spec.enforce {
        return crate::schema_relationship_catalog::create_unenforced(
            file,
            journal,
            &spec,
            (parent_name, child_name),
            budget,
        );
    }
    let (
        parent,
        child,
        parent_columns,
        child_columns,
        parent_id,
        child_id,
        reused_parent,
        parent_kind,
        object_id,
        folder,
    ) = crate::schema_publish::apply(file, journal, budget, |database, budget| {
        crate::relationship_catalog::validate(database, budget)?;
        let parent = crate::update::indexed_writable_table(database, parent_name, budget)?;
        let child = crate::update::indexed_writable_table(database, child_name, budget)?;
        if !(1..=10).contains(&spec.fields.len()) {
            return Err(UpdateError::Unsupported("relationship field count"));
        }
        let mut parent_columns = Vec::new();
        let mut child_columns = Vec::new();
        reserve(&mut parent_columns, spec.fields.len(), budget)?;
        reserve(&mut child_columns, spec.fields.len(), budget)?;
        for pair in spec.fields {
            let a = column(&parent, pair.parent)?;
            let b = column(&child, pair.child)?;
            let kind = |field: &crate::ColumnDefinition| {
                crate::numeric_index_key::NumericKeyType::from_definition(field)
                    .ok_or(UpdateError::Unsupported("relationship key type"))
            };
            if b.auto_increment() || !crate::relationship_key::compatible(kind(a)?, kind(b)?) {
                return Err(UpdateError::Unsupported("relationship column types"));
            }
            if parent_columns.contains(&a.ordinal()) || child_columns.contains(&b.ordinal()) {
                return Err(UpdateError::Unsupported("repeated relationship field"));
            }
            parent_columns.push(a.ordinal());
            child_columns.push(b.ordinal());
        }
        let same = parent.root() == child.root();
        if same && parent_columns == child_columns {
            return Err(UpdateError::Unsupported(
                "self relationship maps key to itself",
            ));
        }
        if child.indexes().len() + 1 + usize::from(same) > 32 || parent.indexes().len() + 1 > 32 {
            return Err(UpdateError::Unsupported("relationship index capacity"));
        }
        crate::schema_edit::distinct(
            spec.name,
            child.indexes().iter().map(|index| index.name().raw_bytes()),
            budget,
        )?;
        let ascending = select_parent(&parent, &parent_columns, false, budget)?;
        let source = ascending
            .or(select_parent(&parent, &parent_columns, true, budget)?)
            .ok_or(UpdateError::Unsupported(
                "relationship requires ordered unique parent index",
            ))?;
        let flags = parent.physical_indexes()[usize::from(source)].raw_flags();
        let parent_kind = IndexKind::Unique.with_null_policy(
            if flags == crate::PhysicalIndexFlagsSpec::UniqueRequired.raw() {
                IndexNullPolicy::Required
            } else {
                IndexNullPolicy::Include
            },
        );
        let child_id = selector(&child, None, budget)?;
        let parent_id = selector(&parent, same.then_some(child_id), budget)?;
        let (object_id, folder) =
            crate::schema_relationship_catalog::object_identity(database, spec.name, budget)?;
        Ok((
            PageEdits::new(database.geometry().page_count()),
            (
                parent,
                child,
                parent_columns,
                child_columns,
                parent_id,
                child_id,
                ascending,
                parent_kind,
                object_id,
                folder,
            ),
        ))
    })?;
    let hidden = crate::creation::relationship_name::HiddenName::for_selector(parent_id).ok_or(
        UpdateError::Unsupported("relationship hidden-name capacity"),
    )?;
    let child_fields = fields(&child_columns, budget)?;
    let parent_fields = fields(&parent_columns, budget)?;
    add_endpoint(
        file,
        journal,
        Endpoint {
            root: child.root(),
            related: parent.root(),
            name: spec.name,
            fields: &child_fields,
            kind: if spec.unique {
                IndexKind::Unique
            } else {
                IndexKind::Ordinary
            },
            side: RelationshipSide::ForeignTable,
            selector: child_id,
            opposite: parent_id,
            reused: None,
            flags: spec.flags(),
        },
        budget,
    )?;
    add_endpoint(
        file,
        journal,
        Endpoint {
            root: parent.root(),
            related: child.root(),
            name: hidden.bytes(),
            fields: &parent_fields,
            kind: parent_kind,
            side: RelationshipSide::PrimaryTable,
            selector: parent_id,
            opposite: child_id,
            reused: reused_parent,
            flags: spec.flags(),
        },
        budget,
    )?;
    let mut pairs = Vec::new();
    reserve(&mut pairs, parent_columns.len(), budget)?;
    for (a, b) in parent_columns.iter().zip(&child_columns) {
        pairs.push((
            parent.columns()[usize::from(a.get())].name().raw_bytes(),
            child.columns()[usize::from(b.get())].name().raw_bytes(),
        ));
    }
    crate::schema_relationship_catalog::publish(
        file,
        journal,
        &spec,
        (parent_name, child_name),
        &pairs,
        (object_id, folder),
        budget,
    )
}

fn column<'a>(
    table: &'a TableDefinition,
    reference: ColumnRef<'_>,
) -> Result<&'a crate::ColumnDefinition, UpdateError> {
    match reference {
        ColumnRef::Name(name) => table
            .columns()
            .iter()
            .find(|column| column.name().raw_bytes() == name),
        ColumnRef::Ordinal(ordinal) => table.columns().get(usize::from(ordinal)),
    }
    .ok_or(UpdateError::NotFound("relationship column"))
}
fn fields(
    columns: &[ColumnOrdinal],
    budget: &mut ResourceBudget,
) -> Result<Vec<IndexColumnSpec<'static>>, UpdateError> {
    let mut fields = Vec::new();
    reserve(&mut fields, columns.len(), budget)?;
    fields.extend(
        columns
            .iter()
            .map(|column| IndexColumnSpec::ascending(column.get())),
    );
    Ok(fields)
}
fn selector(
    table: &TableDefinition,
    occupied: Option<u32>,
    budget: &mut ResourceBudget,
) -> Result<u32, UpdateError> {
    for selector in 0_u32..32 {
        budget.charge_items(table.indexes().len() as u64)?;
        if occupied != Some(selector)
            && !table
                .indexes()
                .iter()
                .any(|index| index.raw_record()[..4] == selector.to_le_bytes())
        {
            return Ok(selector);
        }
    }
    Err(UpdateError::Unsupported(
        "relationship index identity capacity",
    ))
}
fn select_parent(
    table: &TableDefinition,
    columns: &[ColumnOrdinal],
    descending: bool,
    budget: &mut ResourceBudget,
) -> Result<Option<u16>, UpdateError> {
    let mut selected: Option<(u16, crate::catalog_name_key::NameKey)> = None;
    for index in table.indexes() {
        let physical = &table.physical_indexes()[usize::from(index.physical_index())];
        if ![
            crate::PhysicalIndexFlagsSpec::Unique.raw(),
            crate::PhysicalIndexFlagsSpec::UniqueRequired.raw(),
        ]
        .contains(&physical.raw_flags())
        {
            continue;
        }
        if physical.fields().len() != columns.len()
            || physical
                .fields()
                .iter()
                .zip(columns)
                .any(|(field, &column)| field.column() != column)
        {
            continue;
        }
        if physical
            .fields()
            .iter()
            .any(|field| field.direction() == IndexDirection::Descending)
            != descending
        {
            continue;
        }
        budget.charge_work_units(1024)?;
        let key = crate::catalog_name_key::NameKey::new(index.name().raw_bytes())
            .map_err(|_| UpdateError::Unsupported("relationship index name"))?;
        if selected
            .as_ref()
            .is_none_or(|(_, prior)| key.bytes() < prior.bytes())
        {
            selected = Some((index.physical_index(), key));
        }
    }
    Ok(selected.map(|(physical, _)| physical))
}

struct Endpoint<'a> {
    root: PageNumber,
    related: PageNumber,
    name: &'a [u8],
    fields: &'a [IndexColumnSpec<'a>],
    kind: IndexKind,
    side: RelationshipSide,
    selector: u32,
    opposite: u32,
    reused: Option<u16>,
    flags: crate::relationship_flags::RelationshipFlags,
}
fn add_endpoint(
    file: &mut File,
    journal: &mut PageEdits,
    endpoint: Endpoint<'_>,
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    crate::schema_publish::apply(file, journal, budget, |database, budget| {
        let table = database.table_definition(endpoint.root, budget)?;
        if !table.physical_indexes().is_empty() {
            crate::index_mutation::load(database, &table, budget)?;
        }
        let mut definition = crate::schema_definition::DefinitionEdit::new(&table, budget)?;
        let mut edits = PageEdits::new(database.geometry().page_count());
        let physical = if let Some(reused) = endpoint.reused {
            crate::schema_edit::distinct(
                endpoint.name,
                table.indexes().iter().map(|index| index.name().raw_bytes()),
                budget,
            )?;
            reserve(&mut definition.indexes, 1, budget)?;
            definition
                .indexes
                .push(crate::schema_definition::NamedRecord {
                    record: [0; 20],
                    name: endpoint.name,
                });
            reused
        } else {
            crate::schema_index::create(
                database,
                &table,
                IndexSpec {
                    name: endpoint.name,
                    fields: endpoint.fields,
                    kind: endpoint.kind,
                },
                &mut definition,
                &mut edits,
                budget,
            )?;
            let record = &definition
                .indexes
                .last()
                .ok_or(UpdateError::Mismatch("new relationship index"))?
                .record;
            u16::from_le_bytes([record[4], record[5]])
        };
        let mut record = [0; 20];
        crate::column_definition_writer::write_logical_record(
            &mut crate::BinaryWriter::new(&mut record, budget)?,
            &LogicalIndexSpec {
                name: endpoint.name,
                physical_index: physical,
                kind: LogicalIndexKindSpec::Relationship {
                    side: endpoint.side,
                    related_table: endpoint.related,
                    raw_selector: endpoint.selector,
                    relation_ordinal: endpoint.opposite,
                    cascade_updates: endpoint.flags.updates,
                    cascade_deletes: endpoint.flags.deletes,
                },
            },
        )?;
        definition
            .indexes
            .last_mut()
            .ok_or(UpdateError::Mismatch("new relationship index"))?
            .record = record;
        crate::schema_index::sort_names(&mut definition, budget)?;
        definition.stage(database, &table, &mut edits, budget)?;
        Ok((edits, ()))
    })
}
