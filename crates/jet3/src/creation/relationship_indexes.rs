//! EXP-0279 chooses an eligible relationship index in logical name order.
use crate::catalog_name_key::NameKey;
use crate::{
    ColumnRef, ComposeError, IndexDirection, PhysicalIndexFlagsSpec, RelationshipSide,
    ResourceBudget, TableSpec,
};

pub(crate) fn select_existing(
    table: &TableSpec<'_>,
    column: u16,
    side: RelationshipSide,
    budget: &mut ResourceBudget,
) -> Result<Option<u16>, ComposeError> {
    select_direction(table, column, side, IndexDirection::Ascending, budget)
}

/// EXP-0286: a descending unique parent supplies the flags of a new ascending tree.
pub(crate) fn select_descending_parent(
    table: &TableSpec<'_>,
    column: u16,
    budget: &mut ResourceBudget,
) -> Result<Option<u16>, ComposeError> {
    select_direction(
        table,
        column,
        RelationshipSide::PrimaryTable,
        IndexDirection::Descending,
        budget,
    )
}

fn select_direction(
    table: &TableSpec<'_>,
    column: u16,
    side: RelationshipSide,
    direction: IndexDirection,
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
                index.kind.flags() == PhysicalIndexFlagsSpec::Ordinary
            }
        };
        if !eligible || index.fields.len() != 1 || index.fields[0].direction != direction {
            continue;
        }
        budget.charge_work_units(match index.fields[0].column {
            ColumnRef::Ordinal(_) => 1,
            ColumnRef::Name(_) => (table.columns.len() as u64).saturating_mul(64),
        })?;
        if index.fields[0].column.resolve(table.columns) != Some(column) {
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
