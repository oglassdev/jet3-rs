//! Compare every declared and implicit graph index with the creation request.
use crate::{
    ColumnRef, IndexDefinitionKind, IndexDirection, PageNumber, RelationshipSide, RelationshipSpec,
    ResourceBudget, TableDefinition, TableRef,
    create::{
        api::*,
        relationship_indexes::{select_descending_parent, select_existing},
        relationship_name::HiddenName,
    },
};

pub(super) fn check(
    definition: &TableDefinition,
    requests: &[TableRows<'_>],
    relationships: &[RelationshipSpec<'_>],
    tables: &[(PageNumber, u64)],
    position: usize,
    budget: &mut ResourceBudget,
) -> Result<(), ImageCheckError> {
    let mismatch = |detail| ImageCheckError::Mismatch { detail };
    let request = &requests[position].table;
    let resolve = |reference| match reference {
        TableRef::Ordinal(ordinal) => (ordinal < requests.len()).then_some(ordinal),
        TableRef::Name(name) => requests.iter().position(|r| r.table.name == name),
    };
    budget
        .charge_work_units((definition.indexes().len() as u64).saturating_mul(
            (request.indexes.len() as u64 + relationships.len() as u64 * 2 + 1) * 255,
        ))
        .map_err(ImageCheckError::Read)?;
    let named_fields = request
        .indexes
        .iter()
        .flat_map(|index| index.fields)
        .filter(|field| matches!(field.column, ColumnRef::Name(_)))
        .count();
    budget
        .charge_work_units((named_fields as u64) * (request.columns.len() as u64) * 64)
        .map_err(ImageCheckError::Read)?;
    for (ordinal, expected) in request.indexes.iter().enumerate() {
        let actual = definition
            .physical_indexes()
            .get(ordinal)
            .ok_or(mismatch("graph declared physical index"))?;
        if actual.raw_flags() != expected.kind.flags().raw()
            || actual.fields().len() != expected.fields.len()
            || actual
                .fields()
                .iter()
                .zip(expected.fields)
                .any(|(field, wanted)| {
                    Some(field.column().get()) != wanted.column.resolve(request.columns)
                        || field.direction() != wanted.direction
                })
        {
            return Err(mismatch("graph declared physical index schema"));
        }
        let mut matching = definition
            .indexes()
            .iter()
            .filter(|index| index.name().raw_bytes() == expected.name);
        let logical = matching
            .next()
            .ok_or(mismatch("graph declared logical index"))?;
        let kind = if expected.kind.is_primary() {
            IndexDefinitionKind::Primary
        } else {
            IndexDefinitionKind::Ordinary
        };
        if matching.next().is_some()
            || usize::from(logical.physical_index()) != ordinal
            || logical.kind() != kind
        {
            return Err(mismatch("graph declared logical index schema"));
        }
    }
    let mut generated = [None; crate::create::schema_plan::MAX_OBSERVED_INDEXES];
    let mut generated_count = 0;
    let mut logical_ordinal = request.indexes.len();
    for spec in relationships {
        if !(1..=crate::index::entry::MAX_FIELDS).contains(&spec.fields.len()) {
            return Err(mismatch("graph relationship field count"));
        }
        budget
            .charge_work_units((requests.len() as u64) * 128)
            .map_err(ImageCheckError::Read)?;
        let parent = resolve(spec.parent).ok_or(mismatch("graph schema parent reference"))?;
        let child = resolve(spec.child).ok_or(mismatch("graph schema child reference"))?;
        if child == position || parent == position {
            budget
                .charge_work_units((request.columns.len() as u64) * 128 * spec.fields.len() as u64)
                .map_err(ImageCheckError::Read)?;
        }
        if child == position {
            let mut resolved = [u16::MAX; crate::index::entry::MAX_FIELDS];
            for (field, column) in spec.fields.iter().zip(&mut resolved) {
                *column = field
                    .child
                    .resolve(request.columns)
                    .ok_or(mismatch("graph schema child column"))?;
            }
            let columns = &resolved[..spec.fields.len()];
            let existing = select_existing(
                request,
                columns,
                RelationshipSide::ForeignTable,
                spec.unique,
                budget,
            )
            .map_err(ImageCheckError::RowEncoding)?;
            let physical = if let Some(physical) = existing {
                usize::from(physical)
            } else if let Some(slot) = generated[..generated_count]
                .iter()
                .position(|&c| c == Some((RelationshipSide::ForeignTable, resolved, spec.unique)))
            {
                request.indexes.len() + slot
            } else {
                let target = generated
                    .get_mut(generated_count)
                    .ok_or(mismatch("graph schema relationship bound"))?;
                *target = Some((RelationshipSide::ForeignTable, resolved, spec.unique));
                generated_count += 1;
                request.indexes.len() + generated_count - 1
            };
            let index = definition
                .physical_indexes()
                .get(physical)
                .ok_or(mismatch("graph foreign physical index"))?;
            let expected_flags = if spec.unique {
                crate::PhysicalIndexFlagsSpec::Unique
            } else {
                crate::PhysicalIndexFlagsSpec::Ordinary
            }
            .raw();
            if index.raw_flags() != expected_flags
                || index.fields().len() != columns.len()
                || index.fields().iter().zip(columns).any(|(field, &column)| {
                    field.column().get() != column || field.direction() != IndexDirection::Ascending
                })
            {
                return Err(mismatch("graph foreign physical index schema"));
            }
            check_relation(
                definition,
                spec.name,
                physical,
                tables[parent].0,
                RelationshipSide::ForeignTable,
                logical_ordinal,
                spec.flags(),
            )?;
            logical_ordinal += 1;
        }
        if parent == position {
            let mut resolved = [u16::MAX; crate::index::entry::MAX_FIELDS];
            for (field, column) in spec.fields.iter().zip(&mut resolved) {
                *column = field
                    .parent
                    .resolve(request.columns)
                    .ok_or(mismatch("graph schema parent column"))?;
            }
            let columns = &resolved[..spec.fields.len()];
            let existing = select_existing(
                request,
                columns,
                RelationshipSide::PrimaryTable,
                false,
                budget,
            )
            .map_err(ImageCheckError::RowEncoding)?;
            let physical = if let Some(physical) = existing {
                usize::from(physical)
            } else {
                let source = select_descending_parent(request, columns, budget)
                    .map_err(ImageCheckError::RowEncoding)?
                    .ok_or(mismatch("graph descending parent source"))?;
                let slot = if let Some(slot) =
                    generated[..generated_count].iter().position(|&entry| {
                        entry == Some((RelationshipSide::PrimaryTable, resolved, false))
                    }) {
                    slot
                } else {
                    let slot = generated_count;
                    *generated
                        .get_mut(slot)
                        .ok_or(mismatch("graph generated index bound"))? =
                        Some((RelationshipSide::PrimaryTable, resolved, false));
                    generated_count += 1;
                    slot
                };
                let physical = request.indexes.len() + slot;
                let index = definition
                    .physical_indexes()
                    .get(physical)
                    .ok_or(mismatch("graph generated parent index"))?;
                if index.raw_flags() != request.indexes[usize::from(source)].kind.flags().raw()
                    || index.fields().len() != columns.len()
                    || index.fields().iter().zip(columns).any(|(field, &column)| {
                        field.column().get() != column
                            || field.direction() != IndexDirection::Ascending
                    })
                {
                    return Err(mismatch("graph generated parent index schema"));
                }
                physical
            };
            let name = HiddenName::for_selector(logical_ordinal as u32)
                .ok_or(mismatch("graph hidden name ordinal"))?;
            check_relation(
                definition,
                name.bytes(),
                physical,
                tables[child].0,
                RelationshipSide::PrimaryTable,
                logical_ordinal,
                spec.flags(),
            )?;
            logical_ordinal += 1;
        }
    }
    if definition.physical_indexes().len() != request.indexes.len() + generated_count
        || definition.indexes().len() != logical_ordinal
    {
        return Err(mismatch("graph exact index inventory"));
    }
    Ok(())
}

fn check_relation(
    definition: &TableDefinition,
    name: &[u8],
    physical: usize,
    target: PageNumber,
    side: RelationshipSide,
    selector: usize,
    flags: crate::relationship::flags::RelationshipFlags,
) -> Result<(), ImageCheckError> {
    let mismatch = |detail| ImageCheckError::Mismatch { detail };
    let mut matching = definition
        .relationships()
        .filter(|r| r.name().raw_bytes() == name);
    let relation = matching
        .next()
        .ok_or(mismatch("graph expected logical relationship"))?;
    if matching.next().is_some()
        || usize::from(relation.physical_index()) != physical
        || relation.related_table() != target
        || relation.side() != side
        || relation.raw_selector() as usize != selector
        || relation.raw_context() != flags.context()
    {
        return Err(mismatch("graph logical relationship schema"));
    }
    Ok(())
}
