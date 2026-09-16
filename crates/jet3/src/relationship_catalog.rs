//! EXP-0073/0114/0279 endpoints, EXP-0059/0062/0268 reciprocals, and EXP-0277 names.
use crate::catalog_name_key::{catalog_names_equal, validate_catalog_name};
use crate::{
    CatalogObjectClass, CatalogObjectKind, ColumnOrdinal, ColumnPhysicalType, ColumnStorageClass,
    DatabaseReader, IndexDirection, PageNumber, ReadAt, Relationship, RelationshipSide,
    ResourceBudget, TableDefinition, TableDefinitionKind, UpdateError, page_edits::reserve,
};

pub(crate) struct Constraint {
    pub parent: TableDefinition,
    pub child: TableDefinition,
    pub parent_column: ColumnOrdinal,
    pub child_column: ColumnOrdinal,
    parent_record: [u8; 20],
    child_record: [u8; 20],
}

struct Record {
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
    for record in &records {
        result.push(resolve(database, record, budget)?);
    }
    check_target(target, &result, budget)?;
    incoming(database, target.root(), &result, budget)?;
    Ok(result)
}

fn resolve<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    record: &Record,
    budget: &mut ResourceBudget,
) -> Result<Constraint, UpdateError> {
    let parent = table(database, &record.parent, budget)?;
    let child = table(database, &record.child, budget)?;
    resolve_tables(record, parent, child, budget)
}

fn resolve_tables(
    record: &Record,
    parent: TableDefinition,
    child: TableDefinition,
    budget: &mut ResourceBudget,
) -> Result<Constraint, UpdateError> {
    budget.charge_work_units(
        (parent.columns().len()
            + child.columns().len()
            + parent.indexes().len()
            + child.indexes().len()) as u64
            * 512,
    )?;
    let parent_column = key_column(&parent, &record.parent_column)?;
    let child_column = key_column(&child, &record.child_column)?;
    let mut foreign = child.relationships().filter(|relation| {
        catalog_names_equal(relation.name().raw_bytes(), &record.name)
            && relation.side() == RelationshipSide::ForeignTable
            && relation.related_table() == parent.root()
    });
    let foreign = unique(&mut foreign)?;
    index(&child, foreign, child_column, false)?;
    let mut primary = parent.relationships().filter(|relation| {
        relation.side() == RelationshipSide::PrimaryTable
            && relation.related_table() == child.root()
            && relation.raw_selector() == foreign.raw_relation_ordinal()
            && relation.raw_relation_ordinal() == foreign.raw_selector()
    });
    let primary = unique(&mut primary)?;
    index(&parent, primary, parent_column, true)?;
    let parent_record = *primary.raw_record();
    let child_record = *foreign.raw_record();
    Ok(Constraint {
        parent,
        child,
        parent_column,
        child_column,
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
    column: ColumnOrdinal,
    parent: bool,
) -> Result<(), UpdateError> {
    if relation.raw_context() != [0, 0] || relation.cascade_updates() || relation.cascade_deletes()
    {
        return Err(UpdateError::Unsupported("cascading relationship mutation"));
    }
    let index = table
        .physical_indexes()
        .get(usize::from(relation.physical_index()))
        .ok_or(UpdateError::Mismatch("relationship physical index"))?;
    if index.fields().len() != 1
        || index.fields()[0].column() != column
        || index.fields()[0].direction() != IndexDirection::Ascending
    {
        return Err(UpdateError::Unsupported(
            "relationship requires one ascending Long key",
        ));
    }
    let flags = index.raw_flags();
    let supported = if parent {
        flags == crate::PhysicalIndexFlagsSpec::Unique.raw()
            || flags == crate::PhysicalIndexFlagsSpec::UniqueRequired.raw()
    } else {
        flags == crate::PhysicalIndexFlagsSpec::Ordinary.raw()
    };
    if !supported {
        return Err(UpdateError::Unsupported(
            "relationship requires a unique parent and ordinary child index",
        ));
    }
    Ok(())
}

fn key_column(table: &TableDefinition, name: &[u8]) -> Result<ColumnOrdinal, UpdateError> {
    let mut columns = table
        .columns()
        .iter()
        .filter(|column| catalog_names_equal(column.name().raw_bytes(), name));
    let column = columns
        .next()
        .ok_or(UpdateError::Mismatch("relationship column absent"))?;
    if columns.next().is_some() {
        return Err(UpdateError::Mismatch("ambiguous relationship column"));
    }
    if column.physical_type() != ColumnPhysicalType::Long
        || column.size() != 4
        || !matches!(column.storage(), ColumnStorageClass::Fixed { .. })
    {
        return Err(UpdateError::Unsupported("relationship Long column schema"));
    }
    Ok(column.ordinal())
}

fn table<S: ReadAt>(
    database: &mut DatabaseReader<S>,
    name: &[u8],
    budget: &mut ResourceBudget,
) -> Result<TableDefinition, UpdateError> {
    let mut root = None;
    {
        let mut catalog = database.catalog(budget)?;
        while let Some(record) = catalog.next_record()? {
            catalog.budget_mut().charge_work_units(512)?;
            if record.class() == CatalogObjectClass::User
                && record.kind() == CatalogObjectKind::Table
                && catalog_names_equal(record.name().raw_bytes(), name)
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
            if validate_catalog_name(target).is_err()
                || [child, parent]
                    .iter()
                    .any(|name| validate_catalog_name(name).is_err())
            {
                return Err(UpdateError::Unsupported(
                    "unresolved relationship endpoint name",
                ));
            }
            if !catalog_names_equal(child, target) && !catalog_names_equal(parent, target) {
                continue;
            }
            if metadata != [0, 1, 0] {
                return Err(UpdateError::Unsupported(
                    "relationship requires one enforced non-cascading key",
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
                .any(|name| validate_catalog_name(name).is_err())
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

#[path = "relationship_validation.rs"]
mod validation;
pub(crate) use validation::validate;
