//! Shared JSON schema descriptions for creation and schema edits.
use crate::names::Name;
use jet3::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexDirection, IndexKind, IndexNullPolicy,
    IndexSpec, PropertyChange, RelationshipField, RelationshipSpec, TableRef, TableValidation,
};
use serde::Deserialize;
use std::num::NonZeroU8;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Column {
    name: Name,
    #[serde(rename = "type")]
    kind: Kind,
    size: Option<NonZeroU8>,
    #[serde(default)]
    allow_zero_length: bool,
    #[serde(default)]
    required: bool,
    default_value: Option<Name>,
    validation_rule: Option<Name>,
    validation_text: Option<Name>,
    description: Option<Name>,
}

/// A text property edit: absent keeps, `null` clears and a string sets.
#[derive(Default)]
pub(crate) enum Change {
    #[default]
    Keep,
    Clear,
    Set(Name),
}

impl<'de> Deserialize<'de> for Change {
    fn deserialize<D: serde::Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Ok(Option::<Name>::deserialize(deserializer)?.map_or(Self::Clear, Self::Set))
    }
}

impl Change {
    pub(crate) fn edit(&self) -> PropertyChange<'_> {
        match self {
            Self::Keep => PropertyChange::Keep,
            Self::Clear => PropertyChange::Clear,
            Self::Set(value) => PropertyChange::Set(value.bytes()),
        }
    }
}

/// Table-level ValidationRule and ValidationText for creation.
pub(crate) fn validation<'a>(
    rule: Option<&'a Name>,
    text: Option<&'a Name>,
) -> TableValidation<'a> {
    TableValidation {
        rule: rule.map(Name::bytes),
        text: text.map(Name::bytes),
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum Kind {
    Boolean,
    Byte,
    Integer,
    Long,
    AutoIncrement,
    Currency,
    Single,
    Double,
    DateTime,
    Guid,
    Text,
    FixedText,
    Binary,
    Memo,
    LongBinary,
}

impl Column {
    pub(crate) fn spec(&self) -> Result<ColumnSpec<'_>, String> {
        let size = || {
            self.size
                .ok_or_else(|| format!("column {} requires size (1..255)", self.name))
        };
        if self.size.is_some() && !matches!(self.kind, Kind::Text | Kind::FixedText | Kind::Binary)
        {
            return Err(format!("column {} does not accept size", self.name));
        }
        if self.allow_zero_length && !matches!(self.kind, Kind::Text | Kind::FixedText | Kind::Memo)
        {
            return Err(format!(
                "column {} requires text or memo for allow_zero_length",
                self.name
            ));
        }
        let kind = match self.kind {
            Kind::Boolean => ColumnType::Boolean,
            Kind::Byte => ColumnType::Byte,
            Kind::Integer => ColumnType::Integer,
            Kind::Long => ColumnType::Long,
            Kind::AutoIncrement => ColumnType::AutoIncrement,
            Kind::Currency => ColumnType::Currency,
            Kind::Single => ColumnType::Single,
            Kind::Double => ColumnType::Double,
            Kind::DateTime => ColumnType::DateTime,
            Kind::Guid => ColumnType::Guid,
            Kind::Text => ColumnType::Text { max_len: size()? },
            Kind::FixedText => ColumnType::FixedText { len: size()? },
            Kind::Binary => ColumnType::Binary { max_len: size()? },
            Kind::Memo => ColumnType::Memo,
            Kind::LongBinary => ColumnType::LongBinary,
        };
        let mut spec = ColumnSpec::new(self.name.bytes(), kind);
        if self.allow_zero_length {
            spec = spec.with_allow_zero_length();
        }
        if self.required {
            spec = spec.with_required();
        }
        if let Some(value) = &self.default_value {
            spec = spec.with_default_value(value.bytes());
        }
        if let Some(value) = &self.validation_rule {
            spec = spec.with_validation_rule(value.bytes());
        }
        if let Some(value) = &self.validation_text {
            spec = spec.with_validation_text(value.bytes());
        }
        if let Some(value) = &self.description {
            spec = spec.with_description(value.bytes());
        }
        Ok(spec)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Index {
    name: Name,
    kind: KeyKind,
    fields: Vec<IndexField>,
    null_policy: Option<NullPolicy>,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum KeyKind {
    Primary,
    Unique,
    Ordinary,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum NullPolicy {
    Include,
    IgnoreAllNull,
    Required,
}

impl Index {
    pub(crate) fn fields(&self) -> Vec<IndexColumnSpec<'_>> {
        self.fields
            .iter()
            .map(|field| IndexColumnSpec {
                column: ColumnRef::Name(field.column.bytes()),
                direction: match field.direction {
                    Direction::Ascending => IndexDirection::Ascending,
                    Direction::Descending => IndexDirection::Descending,
                },
            })
            .collect()
    }

    pub(crate) fn spec<'a>(&'a self, fields: &'a [IndexColumnSpec<'a>]) -> IndexSpec<'a> {
        let kind = match self.kind {
            KeyKind::Primary => IndexKind::Primary,
            KeyKind::Unique => IndexKind::Unique,
            KeyKind::Ordinary => IndexKind::Ordinary,
        };
        IndexSpec {
            name: self.name.bytes(),
            fields,
            kind: self.null_policy.as_ref().map_or(kind, |policy| {
                kind.with_null_policy(match policy {
                    NullPolicy::Include => IndexNullPolicy::Include,
                    NullPolicy::IgnoreAllNull => IndexNullPolicy::IgnoreAllNull,
                    NullPolicy::Required => IndexNullPolicy::Required,
                })
            }),
        }
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct IndexField {
    column: Name,
    #[serde(default)]
    direction: Direction,
}

#[derive(Default, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Direction {
    #[default]
    Ascending,
    Descending,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Relation {
    #[serde(default)]
    cascade_updates: bool,
    #[serde(default)]
    cascade_deletes: bool,
    name: Name,
    parent: Endpoint,
    child: Endpoint,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Endpoint {
    table: Name,
    column: Option<Name>,
    columns: Option<Vec<Name>>,
}

impl Relation {
    pub(crate) fn fields(&self) -> Result<Vec<RelationshipField<'_>>, String> {
        let parent = self.parent.columns()?;
        let child = self.child.columns()?;
        if parent.len() != child.len() {
            return Err("relationship endpoints require the same number of columns".into());
        }
        Ok(parent
            .iter()
            .zip(child)
            .map(|(parent, child)| RelationshipField {
                parent: ColumnRef::Name(parent.bytes()),
                child: ColumnRef::Name(child.bytes()),
            })
            .collect())
    }

    pub(crate) fn spec<'a>(&'a self, fields: &'a [RelationshipField<'a>]) -> RelationshipSpec<'a> {
        RelationshipSpec {
            cascade_updates: self.cascade_updates,
            cascade_deletes: self.cascade_deletes,
            name: self.name.bytes(),
            parent: TableRef::Name(self.parent.table.bytes()),
            child: TableRef::Name(self.child.table.bytes()),
            fields,
        }
    }
}

impl Endpoint {
    fn columns(&self) -> Result<&[Name], String> {
        match (&self.column, &self.columns) {
            (Some(column), None) => Ok(std::slice::from_ref(column)),
            (None, Some(columns)) if (1..=10).contains(&columns.len()) => Ok(columns),
            _ => Err("relationship endpoint requires column or columns (1..10)".into()),
        }
    }
}
