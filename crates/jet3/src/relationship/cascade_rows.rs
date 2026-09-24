//! Budgeted scalar snapshots used to plan related-row changes before publication.
use crate::{
    ColumnOrdinal, DatabaseReader, FileSource, PageNumber, ResourceBudget, RowLocator, RowValue,
    WriteError,
    index::key::scalar::ScalarKeyType,
    relationship::{catalog::Constraint, key::Key},
    write::page_edits::reserve,
};

pub(super) enum Value {
    Scalar(RowValue<'static>),
    Text(Vec<u8>),
    Binary(Vec<u8>),
}

impl Value {
    pub fn copy(value: RowValue<'_>, budget: &mut ResourceBudget) -> Result<Self, WriteError> {
        let scalar = match value {
            RowValue::Null => RowValue::Null,
            RowValue::Boolean(v) => RowValue::Boolean(v),
            RowValue::Byte(v) => RowValue::Byte(v),
            RowValue::Integer(v) => RowValue::Integer(v),
            RowValue::Long(v) => RowValue::Long(v),
            RowValue::Currency { scaled } => RowValue::Currency { scaled },
            RowValue::Single(v) => RowValue::Single(v),
            RowValue::Double(v) => RowValue::Double(v),
            RowValue::DateTime { days } => RowValue::DateTime { days },
            RowValue::Guid(v) => RowValue::Guid(v),
            RowValue::Text(bytes) | RowValue::Binary(bytes) => {
                let mut saved = Vec::new();
                reserve(&mut saved, bytes.len(), budget)?;
                budget.charge_work_units(bytes.len() as u64)?;
                saved.extend_from_slice(bytes);
                return Ok(if matches!(value, RowValue::Text(_)) {
                    Self::Text(saved)
                } else {
                    Self::Binary(saved)
                });
            }
            _ => return Err(WriteError::Unsupported("non-scalar cascade key")),
        };
        Ok(Self::Scalar(scalar))
    }

    pub fn value(&self) -> RowValue<'_> {
        match self {
            Self::Scalar(value) => *value,
            Self::Text(bytes) => RowValue::Text(bytes),
            Self::Binary(bytes) => RowValue::Binary(bytes),
        }
    }

    fn equal(&self, other: RowValue<'_>, budget: &mut ResourceBudget) -> Result<bool, WriteError> {
        budget.charge_work_units(match self {
            Self::Text(bytes) | Self::Binary(bytes) => bytes.len() as u64,
            Self::Scalar(_) => 1,
        })?;
        Ok(match (self.value(), other) {
            (RowValue::Single(a), RowValue::Single(b)) => a.to_bits() == b.to_bits(),
            (RowValue::Double(a), RowValue::Double(b)) => a.to_bits() == b.to_bits(),
            (RowValue::DateTime { days: a }, RowValue::DateTime { days: b }) => {
                a.to_bits() == b.to_bits()
            }
            (a, b) => a == b,
        })
    }
}

pub(super) struct Field {
    pub column: ColumnOrdinal,
    pub before: Value,
    pub after: Option<Value>,
    pub explicit: bool,
}

pub(super) struct Row {
    pub table: PageNumber,
    pub locator: RowLocator,
    pub fields: Vec<Field>,
    pub deleted: bool,
}

impl Row {
    pub fn values(
        &self,
        columns: &[ColumnOrdinal],
        after: bool,
    ) -> Result<[RowValue<'_>; crate::index::entry::MAX_FIELDS], WriteError> {
        let mut values = [RowValue::Null; crate::index::entry::MAX_FIELDS];
        if columns.len() > values.len() {
            return Err(WriteError::Mismatch("cascade key width"));
        }
        for (&column, value) in columns.iter().zip(&mut values) {
            let field = self
                .fields
                .iter()
                .find(|field| field.column == column)
                .ok_or(WriteError::Mismatch("cascade key column absent"))?;
            *value = if after {
                field.after.as_ref().unwrap_or(&field.before)
            } else {
                &field.before
            }
            .value();
        }
        Ok(values)
    }

    pub fn key(
        &self,
        columns: &[ColumnOrdinal],
        kinds: &[ScalarKeyType],
        after: bool,
        budget: &mut ResourceBudget,
    ) -> Result<Option<Key>, WriteError> {
        budget.charge_work_units((columns.len() * self.fields.len()) as u64)?;
        let values = self.values(columns, after)?;
        Key::encode(kinds, &values[..columns.len()], budget)
    }

    pub fn assigned(&self, columns: &[ColumnOrdinal]) -> bool {
        self.deleted
            || self
                .fields
                .iter()
                .any(|field| field.after.is_some() && columns.contains(&field.column))
    }

    pub fn assign(
        &mut self,
        column: ColumnOrdinal,
        value: RowValue<'_>,
        explicit: bool,
        budget: &mut ResourceBudget,
    ) -> Result<bool, WriteError> {
        budget.charge_work_units(self.fields.len() as u64)?;
        let field = self
            .fields
            .iter_mut()
            .find(|field| field.column == column)
            .ok_or(WriteError::Mismatch("cascade assignment column absent"))?;
        if field.explicit && !explicit {
            return Ok(false);
        }
        field.explicit |= explicit;
        if let Some(after) = &field.after
            && after.equal(value, budget)?
        {
            return Ok(false);
        }
        field.after = Some(Value::copy(value, budget)?);
        Ok(true)
    }
}

pub(super) fn load(
    database: &mut DatabaseReader<FileSource>,
    constraints: &[Constraint],
    budget: &mut ResourceBudget,
) -> Result<Vec<Row>, WriteError> {
    let mut roots = Vec::new();
    let mut result = Vec::new();
    for constraint in constraints {
        for table in [&constraint.parent, &constraint.child] {
            budget.charge_work_units(roots.len() as u64)?;
            if roots.contains(&table.root()) {
                continue;
            }
            reserve(&mut roots, 1, budget)?;
            roots.push(table.root());
            crate::index::mutation::load(database, table, budget)?;
            let mut selected = [false; u8::MAX as usize];
            for constraint in constraints {
                for (root, columns) in [
                    (constraint.parent.root(), &constraint.parent_columns),
                    (constraint.child.root(), &constraint.child_columns),
                ] {
                    budget.charge_work_units(columns.len() as u64)?;
                    if root == table.root() {
                        for column in columns {
                            *selected
                                .get_mut(usize::from(column.get()))
                                .ok_or(WriteError::Unsupported("cascade column ordinal"))? = true;
                        }
                    }
                }
            }
            let mut cursor = database.rows(table, budget)?;
            let mut count = 0_u32;
            while let Some(mut row) = cursor.next_row()? {
                count = count
                    .checked_add(1)
                    .ok_or(WriteError::Mismatch("cascade row count"))?;
                let mut fields = Vec::new();
                for (ordinal, &selected) in selected.iter().enumerate() {
                    row.budget_mut().charge_work_units(1)?;
                    if selected {
                        let column = ColumnOrdinal::new(ordinal as u16);
                        let value = crate::row::scalar_values::read_column(&mut row, column)?;
                        reserve(&mut fields, 1, row.budget_mut())?;
                        fields.push(Field {
                            column,
                            before: Value::copy(value, row.budget_mut())?,
                            after: None,
                            explicit: false,
                        });
                    }
                }
                let locator = row.locator();
                reserve(&mut result, 1, cursor.owned.budget_mut())?;
                result.push(Row {
                    table: table.root(),
                    locator,
                    fields,
                    deleted: false,
                });
            }
            if count != table.row_count() {
                return Err(WriteError::Mismatch("cascade table row count"));
            }
        }
    }
    Ok(result)
}

pub(super) fn equal(
    left: &Option<Key>,
    right: &Option<Key>,
    budget: &mut ResourceBudget,
) -> Result<bool, WriteError> {
    budget.charge_work_units(left.as_ref().map_or(1, |key| key.bytes().len()) as u64)?;
    Ok(left.as_ref().map(Key::bytes) == right.as_ref().map(Key::bytes))
}
