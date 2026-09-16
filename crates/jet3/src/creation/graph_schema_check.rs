//! Compare every declared and implicit graph index with the creation request.
use super::*;
use crate::{IndexDefinitionKind, IndexDirection, TableDefinition};

pub(super) fn check(
    definition: &TableDefinition,
    requests: &[TableRows<'_>],
    relationships: &[RelationshipSpec<'_>],
    tables: &[(PageNumber, u64)],
    position: usize,
    budget: &mut ResourceBudget,
) -> Result<(), CandidateCheckError> {
    let mismatch = |detail| CandidateCheckError::Mismatch { detail };
    let request = &requests[position].table;
    let resolve = |reference| match reference {
        TableRef::Ordinal(ordinal) => (ordinal < requests.len()).then_some(ordinal),
        TableRef::Name(name) => requests.iter().position(|r| r.table.name == name),
    };
    budget
        .charge_work_units((definition.indexes().len() as u64).saturating_mul(
            (request.indexes.len() as u64 + relationships.len() as u64 * 2 + 1) * 255,
        ))
        .map_err(CandidateCheckError::Read)?;
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
    let mut foreign_columns = [None; 2];
    let mut foreign_count = 0;
    let mut logical_ordinal = request.indexes.len();
    for spec in relationships {
        let parent = resolve(spec.parent.table).ok_or(mismatch("graph schema parent reference"))?;
        let child = resolve(spec.child.table).ok_or(mismatch("graph schema child reference"))?;
        if child == position {
            let column = spec
                .child
                .column
                .resolve(request.columns)
                .ok_or(mismatch("graph schema child column"))?;
            let slot = if let Some(slot) = foreign_columns[..foreign_count]
                .iter()
                .position(|&c| c == Some(column))
            {
                slot
            } else {
                let target = foreign_columns
                    .get_mut(foreign_count)
                    .ok_or(mismatch("graph schema relationship bound"))?;
                *target = Some(column);
                foreign_count += 1;
                foreign_count - 1
            };
            let physical = request.indexes.len() + slot;
            let index = definition
                .physical_indexes()
                .get(physical)
                .ok_or(mismatch("graph foreign physical index"))?;
            if index.raw_flags() != 0
                || index.fields().len() != 1
                || index.fields()[0].column().get() != column
                || index.fields()[0].direction() != IndexDirection::Ascending
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
            )?;
            logical_ordinal += 1;
        }
        if parent == position {
            let suffix = u8::try_from(logical_ordinal)
                .ok()
                .filter(|&n| n <= 25)
                .ok_or(mismatch("graph hidden name ordinal"))?;
            let name = [b'.', b'r', b'A' + suffix];
            check_relation(
                definition,
                &name,
                0,
                tables[child].0,
                RelationshipSide::PrimaryTable,
                logical_ordinal,
            )?;
            logical_ordinal += 1;
        }
    }
    if definition.physical_indexes().len() != request.indexes.len() + foreign_count
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
) -> Result<(), CandidateCheckError> {
    let mismatch = |detail| CandidateCheckError::Mismatch { detail };
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
        || relation.raw_context() != [0, 0]
    {
        return Err(mismatch("graph logical relationship schema"));
    }
    Ok(())
}
