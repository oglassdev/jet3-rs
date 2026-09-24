//! EXP-0073/0114/0279/0290 endpoints, EXP-0059/0062/0268 reciprocals, and EXP-0277 names.
use crate::ColumnPhysicalType;
use crate::catalog_name_key::{catalog_names_equal_for, validate_catalog_name_for};
use crate::numeric_index_key::NumericKeyType;
use crate::{
    CatalogObjectClass, CatalogObjectKind, ColumnOrdinal, ColumnStorageClass, DatabaseReader,
    IndexDirection, PageNumber, ReadAt, Relationship, RelationshipSide, ResourceBudget,
    TableDefinition, TableDefinitionKind, UpdateError, page_edits::reserve,
};

pub(crate) struct Constraint {
    pub flags: crate::relationship_flags::RelationshipFlags,
    pub parent: TableDefinition,
    pub child: TableDefinition,
    pub parent_columns: Vec<ColumnOrdinal>,
    pub child_columns: Vec<ColumnOrdinal>,
    pub parent_kinds: Vec<NumericKeyType>,
    pub child_kinds: Vec<NumericKeyType>,
    pub self_reference_requires_existing_parent: bool,
    parent_record: [u8; 20],
    child_record: [u8; 20],
}

struct Record {
    order: crate::SortOrder,
    name: Vec<u8>,
    parent: Vec<u8>,
    child: Vec<u8>,
    parent_column: Vec<u8>,
    child_column: Vec<u8>,
    metadata: [i32; 3],
}

pub(crate) fn load<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    target: &TableDefinition,
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<Vec<Constraint>, UpdateError> {
    let records = records(database, name, budget)?;
    let mut result = Vec::new();
    reserve(&mut result, records.len(), budget)?;
    for group in groups(&records, budget)? {
        let ordered = ordered(&group, budget)?;
        // EXP-0301: unenforced relationships have no indexes and no integrity rules.
        if enforced(&ordered) {
            result.push(resolve(database, &ordered, budget)?);
        }
    }
    check_target(target, &result, budget)?;
    incoming(database, target.root(), &result, budget)?;
    Ok(result)
}

/// EXP-0301: unenforced relationships may have more key fields than an index.
fn interpreted(metadata: &[i32; 3]) -> Option<crate::relationship_flags::RelationshipFlags> {
    let flags = crate::relationship_flags::RelationshipFlags::decode(metadata[0])?;
    let limit = if flags.enforced {
        crate::numeric_index_entry::MAX_FIELDS as i32
    } else {
        i32::MAX
    };
    (1..=limit).contains(&metadata[1]).then_some(flags)
}

fn enforced(records: &[&Record]) -> bool {
    records.first().is_some_and(|record| {
        crate::relationship_flags::RelationshipFlags::decode(record.metadata[0])
            .is_some_and(|flags| flags.enforced)
    })
}

fn resolve<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    records: &[&Record],
    budget: &mut ResourceBudget,
) -> Result<Constraint, UpdateError> {
    let record = records
        .first()
        .ok_or(UpdateError::Mismatch("empty relationship"))?;
    let parent = table(database, &record.parent, budget)?;
    let child = table(database, &record.child, budget)?;
    resolve_tables(records, parent, child, budget)
}

fn resolve_tables(
    records: &[&Record],
    parent: TableDefinition,
    child: TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<Constraint, UpdateError> {
    budget.charge_work_units(
        ((parent.columns().len() + child.columns().len()) * records.len()
            + parent.indexes().len()
            + child.indexes().len()) as u64
            * 512,
    )?;
    let record = records
        .first()
        .ok_or(UpdateError::Mismatch("empty relationship"))?;
    let mut parent_columns = Vec::new();
    let mut child_columns = Vec::new();
    let mut parent_kinds = Vec::new();
    let mut child_kinds = Vec::new();
    for record in records {
        let (parent_column, parent_kind) =
            key_column(record.order, &parent, &record.parent_column)?;
        let (child_column, child_kind) = key_column(record.order, &child, &record.child_column)?;
        if !crate::relationship_key::compatible(parent_kind, child_kind) {
            return Err(UpdateError::Mismatch("relationship endpoint types differ"));
        }
        if parent_columns.contains(&parent_column) || child_columns.contains(&child_column) {
            return Err(UpdateError::Mismatch(
                "relationship repeats an endpoint column",
            ));
        }
        reserve(&mut parent_columns, 1, budget)?;
        reserve(&mut child_columns, 1, budget)?;
        reserve(&mut parent_kinds, 1, budget)?;
        reserve(&mut child_kinds, 1, budget)?;
        parent_columns.push(parent_column);
        child_columns.push(child_column);
        parent_kinds.push(parent_kind);
        child_kinds.push(child_kind);
    }
    let mut foreign = child.relationships().filter(|relation| {
        catalog_names_equal_for(record.order, relation.name().raw_bytes(), &record.name)
            && relation.side() == RelationshipSide::ForeignTable
            && relation.related_table() == parent.root()
    });
    let foreign = unique(&mut foreign)?;
    let flags = crate::relationship_flags::RelationshipFlags::decode(record.metadata[0])
        .ok_or(UpdateError::Unsupported("relationship catalog flags"))?;
    index(&child, foreign, &child_columns, false, flags)?;
    let mut primary = parent.relationships().filter(|relation| {
        relation.side() == RelationshipSide::PrimaryTable
            && relation.related_table() == child.root()
            && relation.raw_selector() == foreign.raw_relation_ordinal()
            && relation.raw_relation_ordinal() == foreign.raw_selector()
    });
    let primary = unique(&mut primary)?;
    index(&parent, primary, &parent_columns, true, flags)?;
    // EXP-0286: self-key checks see the parent tree in physical update order.
    let self_reference_requires_existing_parent =
        parent.root() == child.root() && primary.physical_index() >= foreign.physical_index();
    let parent_record = *primary.raw_record();
    let child_record = *foreign.raw_record();
    Ok(Constraint {
        flags,
        parent,
        child,
        parent_columns,
        child_columns,
        parent_kinds,
        child_kinds,
        self_reference_requires_existing_parent,
        parent_record,
        child_record,
    })
}

fn record_matches(root: PageNumber, record: &[u8; 20], constraint: &Constraint) -> bool {
    (root == constraint.parent.root() && *record == constraint.parent_record)
        || (root == constraint.child.root() && *record == constraint.child_record)
}

fn endpoint_count(target: PageNumber, constraints: &[Constraint]) -> usize {
    constraints.iter().fold(0, |count, constraint| {
        count
            + usize::from(constraint.parent.root() == target)
            + usize::from(constraint.child.root() == target)
    })
}

fn check_target(
    target: &TableDefinition,
    constraints: &[Constraint],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    budget.charge_work_units(
        (target.indexes().len() as u64).saturating_mul(constraints.len() as u64 * 40 + 1),
    )?;
    let mut count = 0;
    for relation in target.relationships() {
        if constraints
            .iter()
            .filter(|constraint| record_matches(target.root(), relation.raw_record(), constraint))
            .count()
            != 1
        {
            return Err(UpdateError::Mismatch(
                "unresolved target relationship record",
            ));
        }
        count += 1;
    }
    if count != endpoint_count(target.root(), constraints) {
        return Err(UpdateError::Mismatch(
            "relationship catalog/index inventory",
        ));
    }
    Ok(())
}

fn incoming<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    target: PageNumber,
    constraints: &[Constraint],
    budget: &mut ResourceBudget,
) -> Result<(), UpdateError> {
    let mut roots = Vec::new();
    {
        let mut catalog = database.catalog(budget)?;
        while let Some(record) = catalog.next_record()? {
            let root = if record.class() == CatalogObjectClass::User
                && record.kind() == CatalogObjectKind::Table
            {
                record.table_definition()
            } else {
                None
            };
            if let Some(root) = root {
                reserve(&mut roots, 1, catalog.budget_mut())?;
                roots.push(root);
            }
        }
    }
    let mut count = 0_usize;
    for root in roots {
        let table = database.table_definition(root, budget)?;
        budget.charge_work_units(table.indexes().len() as u64)?;
        for relation in table
            .relationships()
            .filter(|relation| relation.related_table() == target)
        {
            budget.charge_work_units(constraints.len() as u64 * 40)?;
            let matches = constraints
                .iter()
                .filter(|constraint| record_matches(root, relation.raw_record(), constraint))
                .count();
            if matches != 1 {
                return Err(UpdateError::Mismatch(
                    "unresolved incoming relationship record",
                ));
            }
            count = count.checked_add(1).ok_or(UpdateError::Mismatch(
                "incoming relationship count overflow",
            ))?;
        }
    }
    if count != endpoint_count(target, constraints) {
        return Err(UpdateError::Mismatch("incoming relationship inventory"));
    }
    Ok(())
}

fn unique<'a>(
    relations: &mut impl Iterator<Item = Relationship<'a>>,
) -> Result<Relationship<'a>, UpdateError> {
    let relation = relations.next().ok_or(UpdateError::Mismatch(
        "missing reciprocal relationship index",
    ))?;
    if relations.next().is_some() {
        return Err(UpdateError::Mismatch(
            "ambiguous reciprocal relationship index",
        ));
    }
    Ok(relation)
}

fn index(
    table: &TableDefinition,
    relation: Relationship<'_>,
    columns: &[ColumnOrdinal],
    parent: bool,
    flags: crate::relationship_flags::RelationshipFlags,
) -> Result<(), UpdateError> {
    if relation.raw_context() != flags.context() {
        return Err(UpdateError::Mismatch("relationship cascade flags differ"));
    }
    let index = table
        .physical_indexes()
        .get(usize::from(relation.physical_index()))
        .ok_or(UpdateError::Mismatch("relationship physical index"))?;
    if index.fields().len() != columns.len()
        || index.fields().iter().zip(columns).any(|(field, &column)| {
            field.column() != column || field.direction() != IndexDirection::Ascending
        })
    {
        return Err(UpdateError::Unsupported(
            "relationship requires ascending fields in catalog order",
        ));
    }
    let unique_child = flags.unique;
    let flags = index.raw_flags();
    let supported = if parent {
        flags == crate::PhysicalIndexFlagsSpec::Unique.raw()
            || flags == crate::PhysicalIndexFlagsSpec::UniqueRequired.raw()
    } else if unique_child {
        flags == crate::PhysicalIndexFlagsSpec::Unique.raw()
    } else {
        flags == crate::PhysicalIndexFlagsSpec::Ordinary.raw()
    };
    if !supported {
        return Err(UpdateError::Unsupported(
            "relationship index uniqueness differs from catalog attributes",
        ));
    }
    Ok(())
}

fn key_column(
    order: crate::SortOrder,
    table: &TableDefinition,
    name: &[u8],
) -> Result<(ColumnOrdinal, NumericKeyType), UpdateError> {
    let mut columns = table
        .columns()
        .iter()
        .filter(|column| catalog_names_equal_for(order, column.name().raw_bytes(), name));
    let column = columns
        .next()
        .ok_or(UpdateError::Mismatch("relationship column absent"))?;
    if columns.next().is_some() {
        return Err(UpdateError::Mismatch("ambiguous relationship column"));
    }
    let kind = NumericKeyType::from_definition(column).ok_or(UpdateError::Unsupported(
        "relationship scalar column schema",
    ))?;
    if !matches!(
        kind,
        NumericKeyType::Text { .. } | NumericKeyType::Binary { .. }
    ) && !matches!(column.storage(), ColumnStorageClass::Fixed { .. })
    {
        return Err(UpdateError::Unsupported(
            "relationship scalar column schema",
        ));
    }
    Ok((column.ordinal(), kind))
}

fn table<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<TableDefinition, UpdateError> {
    let order = database.header().sort_order();
    let mut root = None;
    {
        let mut catalog = database.catalog(budget)?;
        while let Some(record) = catalog.next_record()? {
            catalog.budget_mut().charge_work_units(512)?;
            if record.class() == CatalogObjectClass::User
                && record.kind() == CatalogObjectKind::Table
                && catalog_names_equal_for(order, record.name().raw_bytes(), name)
            {
                if root.is_some() {
                    return Err(UpdateError::Mismatch("ambiguous relationship table"));
                }
                root = record.table_definition();
            }
        }
    }
    let table = database.table_definition(
        root.ok_or(UpdateError::Mismatch("relationship table absent"))?,
        budget,
    )?;
    if table.kind() != TableDefinitionKind::User {
        return Err(UpdateError::Unsupported("relationship non-user endpoint"));
    }
    Ok(table)
}

fn catalog_root<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    budget: &mut ResourceBudget,
) -> Result<PageNumber, UpdateError> {
    let mut catalog = database.catalog(budget)?;
    let mut root = None;
    while let Some(record) = catalog.next_record()? {
        if record.class() == CatalogObjectClass::System
            && record.name().raw_bytes() == b"MSysRelationships"
        {
            if root.is_some() {
                return Err(UpdateError::Mismatch("ambiguous relationship catalog"));
            }
            root = record.table_definition();
        }
    }
    root.ok_or(UpdateError::Unsupported("missing relationship catalog"))
}

fn copy_name(name: &[u8], budget: &mut ResourceBudget) -> Result<Vec<u8>, UpdateError> {
    let mut result = Vec::new();
    reserve(&mut result, name.len(), budget)?;
    result.extend_from_slice(name);
    Ok(result)
}

fn records<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    target: &[u8],
    budget: &mut ResourceBudget,
) -> Result<Vec<Record>, UpdateError> {
    read_records(database, Some(target), budget)
}

fn read_records<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    target: Option<&[u8]>,
    budget: &mut ResourceBudget,
) -> Result<Vec<Record>, UpdateError> {
    let order = database.header().sort_order();
    let root = catalog_root(database, budget)?;
    let definition = database.table_definition(root, budget)?;
    if definition.kind() != TableDefinitionKind::System {
        return Err(UpdateError::Unsupported("relationship catalog kind"));
    }
    let names = [
        b"szRelationship".as_slice(),
        b"grbit",
        b"ccolumn",
        b"icolumn",
        b"szObject",
        b"szColumn",
        b"szReferencedObject",
        b"szReferencedColumn",
    ];
    let mut columns = [ColumnOrdinal::new(0); 8];
    for (position, name) in names.iter().enumerate() {
        budget.charge_work_units(definition.columns().len() as u64 * 255)?;
        let mut matches = definition
            .columns()
            .iter()
            .filter(|column| column.name().raw_bytes() == *name);
        let column = matches.next().ok_or(UpdateError::Unsupported(
            "relationship catalog column absent",
        ))?;
        let kind = if (1..=3).contains(&position) {
            ColumnPhysicalType::Long
        } else {
            ColumnPhysicalType::Text
        };
        if matches.next().is_some() || column.physical_type() != kind {
            return Err(UpdateError::Unsupported("relationship catalog column type"));
        }
        columns[position] = column.ordinal();
    }
    let mut result = Vec::new();
    let mut rows = database.rows(&definition, budget)?;
    let mut count = 0_u32;
    loop {
        rows.owned.budget_mut().charge_work_units(1024)?;
        let Some(mut row) = rows.next_row()? else {
            break;
        };
        count = count
            .checked_add(1)
            .ok_or(UpdateError::Mismatch("relationship row count overflow"))?;
        let mut metadata = [0; 3];
        for (value, column) in metadata.iter_mut().zip(&columns[1..4]) {
            *value = match row
                .value(*column, crate::TextCodePage::Windows1252)?
                .ok_or(UpdateError::Mismatch("relationship metadata absent"))?
                .kind()
            {
                crate::ValueKind::Long(value) => *value,
                _ => return Err(UpdateError::Mismatch("relationship metadata type")),
            };
        }
        let field = |position: usize| {
            row.field(columns[position])
                .and_then(|field| field.raw_bytes())
                .ok_or(UpdateError::Unsupported("null relationship field"))
        };
        let child = field(4)?;
        let parent = field(6)?;
        if let Some(target) = target {
            if validate_catalog_name_for(target, order).is_err()
                || [child, parent]
                    .iter()
                    .any(|name| validate_catalog_name_for(name, order).is_err())
            {
                return Err(UpdateError::Unsupported(
                    "unresolved relationship endpoint name",
                ));
            }
            if !catalog_names_equal_for(order, child, target)
                && !catalog_names_equal_for(order, parent, target)
            {
                continue;
            }
            if interpreted(&metadata).is_none() {
                return Err(UpdateError::Unsupported(
                    "relationship requires enforced scalar keys",
                ));
            }
        }
        let sources = [field(0)?, parent, child, field(7)?, field(5)?];
        if target.is_some() && sources[0].len() > 63 {
            return Err(UpdateError::Unsupported(
                "relationship name exceeds 63 bytes",
            ));
        }
        if target.is_some()
            && sources
                .iter()
                .any(|name| validate_catalog_name_for(name, order).is_err())
        {
            return Err(UpdateError::Unsupported("unresolved relationship name"));
        }
        // Detach the row before using its cursor's budget.
        let mut saved = [[0; 255]; 5];
        let mut lengths = [0; 5];
        for ((destination, length), source) in saved.iter_mut().zip(&mut lengths).zip(sources) {
            *length = source.len();
            destination
                .get_mut(..source.len())
                .ok_or(UpdateError::Mismatch("relationship name capacity"))?
                .copy_from_slice(source);
        }
        let budget = rows.owned.budget_mut();
        reserve(&mut result, 1, budget)?;
        let mut name = |i: usize| copy_name(&saved[i][..lengths[i]], budget);
        result.push(Record {
            order,
            name: name(0)?,
            parent: name(1)?,
            child: name(2)?,
            parent_column: name(3)?,
            child_column: name(4)?,
            metadata,
        });
    }
    if count != definition.row_count() {
        return Err(UpdateError::Mismatch("relationship catalog row count"));
    }
    Ok(result)
}

pub(crate) fn catalog<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    budget: &mut ResourceBudget,
) -> Result<Vec<crate::CatalogRelationship>, UpdateError> {
    let records = read_records(database, None, budget)?;
    let groups = groups(&records, budget)?;
    let mut result = Vec::new();
    reserve(&mut result, groups.len(), budget)?;
    for group in groups {
        let ordered = ordered(&group, budget)?;
        let first = ordered
            .first()
            .ok_or(UpdateError::Mismatch("empty relationship"))?;
        let mut fields = Vec::new();
        reserve(&mut fields, ordered.len(), budget)?;
        for record in &ordered {
            fields.push(crate::CatalogRelationshipField {
                parent: copy_name(&record.parent_column, budget)?,
                child: copy_name(&record.child_column, budget)?,
            });
        }
        result.push(crate::CatalogRelationship {
            name: copy_name(&first.name, budget)?,
            parent: copy_name(&first.parent, budget)?,
            child: copy_name(&first.child, budget)?,
            fields,
            raw_attributes: first.metadata[0],
        });
    }
    Ok(result)
}

#[path = "relationship_groups.rs"]
mod grouping;
use grouping::{groups, ordered};

#[path = "relationship_component.rs"]
mod connected;
pub(crate) use connected::component;

#[path = "relationship_validation.rs"]
mod validation;
pub(crate) use validation::validate;
