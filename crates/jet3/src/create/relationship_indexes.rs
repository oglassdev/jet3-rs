//! EXP-0279/0290 choose an eligible ordered relationship index in logical name order.
//! EXP-0307: one-to-one child indexes are unique and include nulls.
use crate::{
    ColumnRef, ComposeError, IndexDirection, PhysicalIndexFlagsSpec, RelationshipSide,
    ResourceBudget, TableSpec, catalog::name_key::NameKey,
};

pub(crate) fn select_existing(
    table: &TableSpec<'_>,
    columns: &[u16],
    side: RelationshipSide,
    unique_child: bool,
    budget: &mut ResourceBudget,
) -> Result<Option<u16>, ComposeError> {
    select_direction(table, columns, side, unique_child, false, budget)
}

/// EXP-0286/0290: a parent with descending fields supplies a new ascending tree.
pub(crate) fn select_descending_parent(
    table: &TableSpec<'_>,
    columns: &[u16],
    budget: &mut ResourceBudget,
) -> Result<Option<u16>, ComposeError> {
    select_direction(
        table,
        columns,
        RelationshipSide::PrimaryTable,
        false,
        true,
        budget,
    )
}

fn select_direction(
    table: &TableSpec<'_>,
    columns: &[u16],
    side: RelationshipSide,
    unique_child: bool,
    descending: bool,
    budget: &mut ResourceBudget,
) -> Result<Option<u16>, ComposeError> {
    budget.charge_work_units(table.indexes.len() as u64)?;
    let mut selected: Option<(u16, NameKey)> = None;
    for (ordinal, index) in table.indexes.iter().enumerate() {
        let eligible = match side {
            RelationshipSide::PrimaryTable => matches!(
                index.kind.flags(),
                PhysicalIndexFlagsSpec::Unique | PhysicalIndexFlagsSpec::UniqueRequired
            ),
            RelationshipSide::ForeignTable => {
                index.kind.flags()
                    == if unique_child {
                        PhysicalIndexFlagsSpec::Unique
                    } else {
                        PhysicalIndexFlagsSpec::Ordinary
                    }
            }
        };
        if !eligible || index.fields.len() != columns.len() {
            continue;
        }
        let has_descending = index
            .fields
            .iter()
            .any(|field| field.direction == IndexDirection::Descending);
        if has_descending != descending {
            continue;
        }
        let mut matches = true;
        for (field, &column) in index.fields.iter().zip(columns) {
            budget.charge_work_units(match field.column {
                ColumnRef::Ordinal(_) => 1,
                ColumnRef::Name(_) => (table.columns.len() as u64).saturating_mul(64),
            })?;
            if field.column.resolve(table.columns) != Some(column) {
                matches = false;
                break;
            }
        }
        if !matches {
            continue;
        }
        budget.charge_work_units(512 + 194)?;
        let key = NameKey::new(index.name)?;
        if selected
            .as_ref()
            .is_none_or(|(_, previous)| key.bytes() < previous.bytes())
        {
            let physical =
                u16::try_from(ordinal).map_err(|_| ComposeError::UnsupportedRelationship {
                    detail: "relationship physical index ordinal",
                })?;
            selected = Some((physical, key));
        }
    }
    Ok(selected.map(|(ordinal, _)| ordinal))
}
