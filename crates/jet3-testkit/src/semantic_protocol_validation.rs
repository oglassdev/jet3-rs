//! Complete validation for protocol 1.2 semantic snapshot models.

use std::collections::{BTreeMap, BTreeSet};

use super::{SemanticProtocolError, SemanticSnapshot, SemanticTable, invalid};
use crate::{HexString, PropertyMap, TypedValue};

pub(super) fn validate_snapshot(snapshot: &SemanticSnapshot) -> Result<(), SemanticProtocolError> {
    validate_properties(&snapshot.database_properties, "$.database_properties")?;
    validate_strict_names(
        snapshot.tables.iter().map(|table| table.name.as_str()),
        "$.tables",
    )?;
    let mut tables = BTreeMap::new();
    for (table_index, table) in snapshot.tables.iter().enumerate() {
        let path = format!("$.tables[{table_index}]");
        validate_table(table, &path)?;
        tables.insert(
            table.name.as_str(),
            table
                .columns
                .iter()
                .map(|column| column.name.as_str())
                .collect::<BTreeSet<_>>(),
        );
    }

    validate_strict_names(
        snapshot
            .relationships
            .iter()
            .map(|relationship| relationship.name.as_str()),
        "$.relationships",
    )?;
    for (index, relationship) in snapshot.relationships.iter().enumerate() {
        let path = format!("$.relationships[{index}]");
        let local = tables
            .get(relationship.table.as_str())
            .ok_or_else(|| invalid(format!("{path}.table"), "unknown table"))?;
        let foreign = tables
            .get(relationship.foreign_table.as_str())
            .ok_or_else(|| invalid(format!("{path}.foreign_table"), "unknown table"))?;
        if relationship.fields.is_empty() {
            return Err(invalid(
                format!("{path}.fields"),
                "field list must not be empty",
            ));
        }
        let mut pairs = BTreeSet::new();
        for pair in &relationship.fields {
            if pair.field.is_empty()
                || pair.foreign_field.is_empty()
                || !local.contains(pair.field.as_str())
                || !foreign.contains(pair.foreign_field.as_str())
                || !pairs.insert((pair.field.as_str(), pair.foreign_field.as_str()))
            {
                return Err(invalid(
                    format!("{path}.fields"),
                    "fields must be non-empty, unique, and reference declared columns",
                ));
            }
        }
        validate_properties(&relationship.properties, &format!("{path}.properties"))?;
    }

    let mut previous_raw = None;
    for (index, raw) in snapshot.raw_preservation.iter().enumerate() {
        let path = format!("$.raw_preservation[{index}]");
        if raw.semantic_path.is_empty() || raw.purpose.is_empty() {
            return Err(invalid(path, "semantic_path and purpose must not be empty"));
        }
        if previous_raw.is_some_and(|previous| previous >= raw.semantic_path.as_str()) {
            return Err(invalid(
                "$.raw_preservation",
                "semantic paths must be unique and canonically ordered",
            ));
        }
        previous_raw = Some(raw.semantic_path.as_str());
    }

    for key in snapshot.producer_extensions.keys() {
        if !valid_json_pointer(key) {
            return Err(invalid(
                format!("$.producer_extensions[{key:?}]"),
                "key must be a non-empty JSON pointer",
            ));
        }
    }
    validate_properties(&snapshot.producer_extensions, "$.producer_extensions")
}

fn validate_table(table: &SemanticTable, path: &str) -> Result<(), SemanticProtocolError> {
    validate_properties(&table.properties, &format!("{path}.properties"))?;
    validate_unique_names(
        table.columns.iter().map(|column| column.name.as_str()),
        &format!("{path}.columns"),
    )?;
    for (index, column) in table.columns.iter().enumerate() {
        let column_path = format!("{path}.columns[{index}]");
        if column.ordinal != index as u64 {
            return Err(invalid(
                format!("{column_path}.ordinal"),
                "ordinals must be contiguous from zero",
            ));
        }
        validate_column(column, &column_path)?;
    }

    validate_strict_names(
        table.indexes.iter().map(|index| index.name.as_str()),
        &format!("{path}.indexes"),
    )?;
    let columns = table
        .columns
        .iter()
        .map(|column| column.name.as_str())
        .collect::<BTreeSet<_>>();
    let mut primary_count = 0;
    for (index_ordinal, index) in table.indexes.iter().enumerate() {
        let index_path = format!("{path}.indexes[{index_ordinal}]");
        primary_count += usize::from(index.primary);
        if index.primary && !(index.unique && index.required) {
            return Err(invalid(
                index_path,
                "a primary index must be unique and required",
            ));
        }
        if index.fields.is_empty() {
            return Err(invalid(
                format!("{index_path}.fields"),
                "field list must not be empty",
            ));
        }
        let mut names = BTreeSet::new();
        if index.fields.iter().any(|field| {
            field.name.is_empty()
                || !columns.contains(field.name.as_str())
                || !names.insert(field.name.as_str())
        }) {
            return Err(invalid(
                format!("{index_path}.fields"),
                "fields must be non-empty, unique, and reference declared columns",
            ));
        }
        validate_properties(&index.properties, &format!("{index_path}.properties"))?;
    }
    if primary_count > 1 {
        return Err(invalid(
            format!("{path}.indexes"),
            "at most one index may be primary",
        ));
    }

    let column_by_name = table
        .columns
        .iter()
        .map(|column| (column.name.as_str(), column.dao_type.as_str()))
        .collect::<BTreeMap<_, _>>();
    let mut previous: Option<(&str, u64, Vec<u8>)> = None;
    for (row_index, row) in table.rows.iter().enumerate() {
        let row_path = format!("{path}.rows[{row_index}]");
        if row
            .values
            .keys()
            .map(String::as_str)
            .collect::<BTreeSet<_>>()
            != columns
        {
            return Err(invalid(
                format!("{row_path}.values"),
                "keys must equal declared columns",
            ));
        }
        for (name, value) in &row.values {
            let dao_type = column_by_name[name.as_str()];
            validate_row_value(value, dao_type, &format!("{row_path}.values/{name}"))?;
        }
        let bytes = super::canonical_row_bytes(&row.values)?;
        let digest = super::sha256(&bytes)?;
        if row.canonical_key != digest {
            return Err(invalid(
                format!("{row_path}.canonical_key"),
                "must equal the SHA-256 of canonical values bytes",
            ));
        }
        let expected_ordinal = previous
            .as_ref()
            .filter(|(key, _, prior_bytes)| *key == digest.as_str() && prior_bytes == &bytes)
            .map_or(0, |(_, ordinal, _)| ordinal + 1);
        if previous
            .as_ref()
            .is_some_and(|(key, ordinal, prior_bytes)| {
                (*key, *ordinal) >= (digest.as_str(), row.duplicate_ordinal)
                    || (*key == digest.as_str() && prior_bytes != &bytes)
            })
            || row.duplicate_ordinal != expected_ordinal
        {
            return Err(invalid(
                row_path,
                "rows must be canonical with contiguous duplicate ordinals",
            ));
        }
        previous = Some((row.canonical_key.as_str(), row.duplicate_ordinal, bytes));
    }
    Ok(())
}

fn validate_column(
    column: &crate::SemanticColumn,
    path: &str,
) -> Result<(), SemanticProtocolError> {
    let size = column
        .size
        .ok_or_else(|| invalid(format!("{path}.size"), "normalized size is required"))?;
    let valid_size = match column.dao_type.as_str() {
        "dbBoolean" | "dbByte" => size == 1,
        "dbInteger" => size == 2,
        "dbLong" | "dbSingle" => size == 4,
        "dbCurrency" | "dbDouble" | "dbDate" => size == 8,
        "dbGUID" => size == 16,
        "dbBinary" | "dbText" => (1..=255).contains(&size),
        "dbLongBinary" | "dbMemo" => size == 0,
        _ => false,
    };
    if !valid_size {
        return Err(invalid(
            format!("{path}.size"),
            "DAO type and normalized size are inconsistent",
        ));
    }
    if !matches!(column.attributes, 1 | 2 | 17)
        || column.auto_increment != (column.attributes == 17)
        || (column.auto_increment && column.dao_type != "dbLong")
    {
        return Err(invalid(
            format!("{path}.attributes"),
            "normalized attributes must be fixed, variable, or fixed auto-increment",
        ));
    }
    validate_properties(&column.properties, &format!("{path}.properties"))
}

fn validate_row_value(
    value: &TypedValue,
    dao_type: &str,
    path: &str,
) -> Result<(), SemanticProtocolError> {
    super::validate_typed_value(value, path)?;
    if matches!(value, TypedValue::Null { .. }) {
        return Ok(());
    }
    let admitted = matches!(
        (dao_type, value),
        ("dbBoolean", TypedValue::Boolean { .. })
            | ("dbByte", TypedValue::Byte { .. })
            | ("dbInteger", TypedValue::Integer { .. })
            | ("dbLong", TypedValue::Long { .. })
            | ("dbCurrency", TypedValue::Currency { .. })
            | ("dbSingle", TypedValue::Single { .. })
            | ("dbDouble", TypedValue::Double { .. })
            | ("dbDate", TypedValue::DateTime { .. })
            | ("dbBinary", TypedValue::Binary { .. })
            | ("dbText", TypedValue::Text { .. })
            | ("dbLongBinary", TypedValue::Ole { .. })
            | ("dbMemo", TypedValue::Memo { .. })
            | ("dbGUID", TypedValue::Guid { .. })
    );
    if !admitted {
        return Err(invalid(
            path,
            "typed value kind is not admitted for DAO type",
        ));
    }
    let expected_width = match dao_type {
        "dbByte" => Some(1),
        "dbInteger" => Some(2),
        "dbLong" | "dbSingle" => Some(4),
        "dbCurrency" | "dbDouble" | "dbDate" => Some(8),
        "dbGUID" => Some(16),
        _ => None,
    };
    if let Some(expected) = expected_width {
        let actual = raw_hex(value).map(|raw| raw.as_str().len() / 2);
        if actual != Some(expected) {
            return Err(invalid(path, "raw_hex has the wrong fixed width"));
        }
    }
    Ok(())
}

fn validate_properties(values: &PropertyMap, path: &str) -> Result<(), SemanticProtocolError> {
    for (name, value) in values {
        if name.is_empty() {
            return Err(invalid(path, "property names must not be empty"));
        }
        super::validate_typed_value(value, &format!("{path}/{name}"))?;
    }
    Ok(())
}

fn validate_strict_names<'a>(
    values: impl Iterator<Item = &'a str>,
    path: &str,
) -> Result<(), SemanticProtocolError> {
    let mut previous = None;
    for value in values {
        if value.is_empty() || previous.is_some_and(|prior| prior >= value) {
            return Err(invalid(
                path,
                "names must be non-empty, unique, and canonically ordered",
            ));
        }
        previous = Some(value);
    }
    Ok(())
}

fn validate_unique_names<'a>(
    values: impl Iterator<Item = &'a str>,
    path: &str,
) -> Result<(), SemanticProtocolError> {
    let mut seen = BTreeSet::new();
    if values
        .into_iter()
        .any(|value| value.is_empty() || !seen.insert(value))
    {
        return Err(invalid(path, "names must be non-empty and unique"));
    }
    Ok(())
}

fn valid_json_pointer(value: &str) -> bool {
    !value.is_empty()
        && value.starts_with('/')
        && value.split('/').skip(1).all(|token| {
            let mut bytes = token.bytes();
            while let Some(byte) = bytes.next() {
                if byte == b'~' && !matches!(bytes.next(), Some(b'0' | b'1')) {
                    return false;
                }
            }
            true
        })
}

fn raw_hex(value: &TypedValue) -> Option<&HexString> {
    match value {
        TypedValue::Null { raw_hex }
        | TypedValue::Boolean { raw_hex, .. }
        | TypedValue::Byte { raw_hex, .. }
        | TypedValue::Integer { raw_hex, .. }
        | TypedValue::Long { raw_hex, .. }
        | TypedValue::Single { raw_hex, .. }
        | TypedValue::Double { raw_hex, .. }
        | TypedValue::Decimal { raw_hex, .. }
        | TypedValue::Currency { raw_hex, .. }
        | TypedValue::DateTime { raw_hex, .. }
        | TypedValue::Text { raw_hex, .. }
        | TypedValue::Binary { raw_hex, .. }
        | TypedValue::Guid { raw_hex, .. }
        | TypedValue::Memo { raw_hex, .. }
        | TypedValue::Ole { raw_hex, .. } => raw_hex.as_ref(),
    }
}
