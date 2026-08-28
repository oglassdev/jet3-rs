//! Canonical JSON emission for the typed snapshot model.

use std::fmt::{self, Write as _};

use crate::{
    CanonicalSnapshot, ClassifierSnapshot, Column, FiniteF32, FiniteF64, Index, IndexField,
    Producer, ProducerKind, PropertyMap, RawPreservation, Relationship, RelationshipField, Row,
    SnapshotError, Table, TableKind, TypedValue,
};

const PROTOCOL_VERSION: &str = "1.0.0";
const DOCUMENT_TYPE: &str = "canonical_snapshot";

pub(crate) fn write_snapshot(snapshot: &CanonicalSnapshot) -> Result<Vec<u8>, SnapshotError> {
    let mut writer = JsonWriter::new();
    writer.snapshot(snapshot)?;
    writer.bytes.push(b'\n');
    writer.into_bytes().ok_or(SnapshotError::NumberFormatting)
}

pub(crate) fn write_properties(properties: &PropertyMap) -> Result<Vec<u8>, SnapshotError> {
    let mut writer = JsonWriter::new();
    writer.properties(properties)?;
    writer.into_bytes().ok_or(SnapshotError::NumberFormatting)
}

pub(crate) fn write_classifier_snapshot(snapshot: &ClassifierSnapshot) -> Vec<u8> {
    let mut writer = JsonWriter::new();
    writer.bytes.push(b'{');
    let mut first = true;
    writer.key(&mut first, "document_type");
    writer.string("page_classifier_snapshot");
    writer.key(&mut first, "fixtures");
    writer.bytes.push(b'{');
    for (index, fixture) in snapshot.fixtures().iter().enumerate() {
        writer.comma(index);
        let key = format!(
            "{}@{}",
            fixture.sha256().as_str(),
            snapshot.source_commit().as_str()
        );
        writer.string(&key);
        writer.bytes.push(b':');
        writer.bytes.push(b'{');
        let mut fixture_first = true;
        writer.key(&mut fixture_first, "fixture_sha256");
        writer.string(fixture.sha256().as_str());
        writer.key(&mut fixture_first, "page_count");
        writer.unsigned(fixture.page_count());
        writer.key(&mut fixture_first, "page_kinds");
        writer.bytes.push(b'{');
        let mut kind_first = true;
        for (name, count) in fixture.histogram().named_counts() {
            writer.key(&mut kind_first, name);
            writer.unsigned(count);
        }
        writer.key(&mut kind_first, "unknown");
        writer.bytes.push(b'{');
        for (index, (tag, count)) in fixture.histogram().unknown_counts().enumerate() {
            writer.comma(index);
            writer.string(&format!("{tag:02x}"));
            writer.bytes.push(b':');
            writer.unsigned(count);
        }
        writer.bytes.push(b'}');
        writer.bytes.push(b'}');
        writer.bytes.push(b'}');
    }
    writer.bytes.push(b'}');
    writer.key(&mut first, "ordering");
    writer.bytes.extend_from_slice(
        b"{\"fixture_keys\":\"sha256_then_commit_codepoint_ascending\",\
\"object_keys\":\"unicode_codepoint_ascending\",\
\"unknown_tags\":\"numeric_ascending\"}",
    );
    writer.key(&mut first, "schema_version");
    writer.unsigned(1);
    writer.key(&mut first, "source_commit");
    writer.string(snapshot.source_commit().as_str());
    writer.bytes.push(b'}');
    writer.bytes.push(b'\n');
    writer.into_bytes().unwrap_or_default()
}

pub(crate) struct JsonWriter {
    pub(crate) bytes: JsonBytes,
}

impl JsonWriter {
    pub(crate) fn new() -> Self {
        Self {
            bytes: JsonBytes::Output(Vec::new()),
        }
    }

    pub(crate) fn counting() -> Self {
        Self {
            bytes: JsonBytes::Count(0),
        }
    }

    pub(crate) fn with_output(bytes: Vec<u8>) -> Self {
        Self {
            bytes: JsonBytes::Output(bytes),
        }
    }

    pub(crate) fn counted_len(&self) -> Option<usize> {
        match self.bytes {
            JsonBytes::Count(length) => Some(length),
            JsonBytes::Output(_) => None,
        }
    }

    pub(crate) fn into_bytes(self) -> Option<Vec<u8>> {
        match self.bytes {
            JsonBytes::Output(bytes) => Some(bytes),
            JsonBytes::Count(_) => None,
        }
    }

    fn snapshot(&mut self, snapshot: &CanonicalSnapshot) -> Result<(), SnapshotError> {
        self.bytes.push(b'{');
        let mut first = true;
        self.key(&mut first, "database_properties");
        self.properties(&snapshot.database_properties)?;
        self.key(&mut first, "database_sha256");
        self.string(snapshot.database_sha256.as_str());
        self.key(&mut first, "document_type");
        self.string(DOCUMENT_TYPE);
        self.key(&mut first, "ordering");
        self.ordering();
        self.key(&mut first, "producer");
        self.producer(&snapshot.producer);
        self.key(&mut first, "protocol_version");
        self.string(PROTOCOL_VERSION);
        self.key(&mut first, "raw_preservation");
        self.raw_preservation(&snapshot.raw_preservation);
        self.key(&mut first, "relationships");
        self.relationships(&snapshot.relationships)?;
        self.key(&mut first, "scenario_id");
        self.string(snapshot.scenario_id.as_str());
        self.key(&mut first, "tables");
        self.tables(&snapshot.tables)?;
        self.bytes.push(b'}');
        Ok(())
    }

    fn ordering(&mut self) {
        self.bytes.push(b'{');
        let mut first = true;
        for (key, value) in [
            ("columns", "ordinal_ascending"),
            ("indexes", "name_codepoint_ascending"),
            ("object_keys", "unicode_codepoint_ascending"),
            ("objects", "name_codepoint_ascending"),
            ("relationships", "name_codepoint_ascending"),
            ("rows", "declared_key_then_canonical_value"),
        ] {
            self.key(&mut first, key);
            self.string(value);
        }
        self.bytes.push(b'}');
    }

    pub(crate) fn producer(&mut self, producer: &Producer) {
        self.bytes.push(b'{');
        let mut first = true;
        self.key(&mut first, "kind");
        self.string(match producer.kind {
            ProducerKind::Dao => "dao",
            ProducerKind::Rust => "rust",
        });
        self.key(&mut first, "source_revision");
        self.string(producer.source_revision());
        self.bytes.push(b'}');
    }

    pub(crate) fn properties(&mut self, properties: &PropertyMap) -> Result<(), SnapshotError> {
        self.bytes.push(b'{');
        let mut first = true;
        for (key, value) in properties {
            self.key(&mut first, key);
            self.typed_value(value)?;
        }
        self.bytes.push(b'}');
        Ok(())
    }

    fn typed_value(&mut self, value: &TypedValue) -> Result<(), SnapshotError> {
        self.bytes.push(b'{');
        let mut first = true;
        if let Some(code_page) = value.code_page() {
            self.key(&mut first, "code_page");
            self.unsigned(u64::from(code_page));
        }
        self.key(&mut first, "kind");
        self.string(value.kind());
        if let Some(raw_hex) = value.raw_hex() {
            self.key(&mut first, "raw_hex");
            self.string(raw_hex.as_str());
        }
        self.key(&mut first, "value");
        match value {
            TypedValue::Null { .. } => self.bytes.extend_from_slice(b"null"),
            TypedValue::Boolean { value, .. } => self.boolean(*value),
            TypedValue::Byte { value, .. } => self.unsigned(u64::from(*value)),
            TypedValue::Integer { value, .. } => self.integer(i64::from(*value)),
            TypedValue::Long { value, .. } => self.integer(i64::from(*value)),
            TypedValue::Single { value, .. } => {
                let number = canonical_f32(*value)?;
                self.bytes.extend_from_slice(number.as_bytes());
            }
            TypedValue::Double { value, .. } => {
                let number = canonical_f64(*value)?;
                self.bytes.extend_from_slice(number.as_bytes());
            }
            TypedValue::Decimal { value, .. } | TypedValue::Currency { value, .. } => {
                self.string(value.as_str());
            }
            TypedValue::DateTime { value, .. } => self.string(value.as_str()),
            TypedValue::Text { value, .. } | TypedValue::Memo { value, .. } => self.string(value),
            TypedValue::Binary { value, .. } | TypedValue::Ole { value, .. } => {
                self.string(value.as_str());
            }
            TypedValue::Guid { value, .. } => self.string(value.as_str()),
        }
        self.bytes.push(b'}');
        Ok(())
    }

    fn tables(&mut self, tables: &[Table]) -> Result<(), SnapshotError> {
        self.bytes.push(b'[');
        for (index, table) in tables.iter().enumerate() {
            self.comma(index);
            self.table(table)?;
        }
        self.bytes.push(b']');
        Ok(())
    }

    fn table(&mut self, table: &Table) -> Result<(), SnapshotError> {
        self.bytes.push(b'{');
        let mut first = true;
        self.key(&mut first, "attributes");
        self.integer(table.attributes);
        self.key(&mut first, "columns");
        self.columns(&table.columns)?;
        self.key(&mut first, "indexes");
        self.indexes(&table.indexes)?;
        self.key(&mut first, "kind");
        self.string(match table.kind {
            TableKind::User => "user",
            TableKind::System => "system",
            TableKind::Linked => "linked",
        });
        self.key(&mut first, "name");
        self.string(&table.name);
        self.key(&mut first, "properties");
        self.properties(&table.properties)?;
        self.key(&mut first, "rows");
        self.rows(&table.rows)?;
        self.bytes.push(b'}');
        Ok(())
    }

    fn columns(&mut self, columns: &[Column]) -> Result<(), SnapshotError> {
        self.bytes.push(b'[');
        for (index, column) in columns.iter().enumerate() {
            self.comma(index);
            self.bytes.push(b'{');
            let mut first = true;
            self.key(&mut first, "attributes");
            self.integer(column.attributes);
            self.key(&mut first, "auto_increment");
            self.boolean(column.auto_increment);
            self.key(&mut first, "dao_type");
            self.string(&column.dao_type);
            self.key(&mut first, "name");
            self.string(&column.name);
            self.key(&mut first, "nullable");
            self.boolean(column.nullable);
            self.key(&mut first, "ordinal");
            self.unsigned(column.ordinal);
            self.key(&mut first, "properties");
            self.properties(&column.properties)?;
            self.key(&mut first, "required");
            self.boolean(column.required);
            self.key(&mut first, "size");
            if let Some(size) = column.size {
                self.unsigned(size);
            } else {
                self.bytes.extend_from_slice(b"null");
            }
            self.bytes.push(b'}');
        }
        self.bytes.push(b']');
        Ok(())
    }

    fn indexes(&mut self, indexes: &[Index]) -> Result<(), SnapshotError> {
        self.bytes.push(b'[');
        for (index, value) in indexes.iter().enumerate() {
            self.comma(index);
            self.bytes.push(b'{');
            let mut first = true;
            self.key(&mut first, "fields");
            self.index_fields(&value.fields);
            self.key(&mut first, "ignore_nulls");
            self.boolean(value.ignore_nulls);
            self.key(&mut first, "name");
            self.string(&value.name);
            self.key(&mut first, "primary");
            self.boolean(value.primary);
            self.key(&mut first, "properties");
            self.properties(&value.properties)?;
            self.key(&mut first, "required");
            self.boolean(value.required);
            self.key(&mut first, "unique");
            self.boolean(value.unique);
            self.bytes.push(b'}');
        }
        self.bytes.push(b']');
        Ok(())
    }

    fn index_fields(&mut self, fields: &[IndexField]) {
        self.bytes.push(b'[');
        for (index, field) in fields.iter().enumerate() {
            self.comma(index);
            self.bytes.push(b'{');
            let mut first = true;
            self.key(&mut first, "descending");
            self.boolean(field.descending);
            self.key(&mut first, "name");
            self.string(&field.name);
            self.bytes.push(b'}');
        }
        self.bytes.push(b']');
    }

    fn rows(&mut self, rows: &[Row]) -> Result<(), SnapshotError> {
        self.bytes.push(b'[');
        for (index, row) in rows.iter().enumerate() {
            self.comma(index);
            self.bytes.push(b'{');
            let mut first = true;
            self.key(&mut first, "canonical_key");
            self.string(&row.canonical_key);
            self.key(&mut first, "values");
            self.properties(&row.values)?;
            self.bytes.push(b'}');
        }
        self.bytes.push(b']');
        Ok(())
    }

    pub(crate) fn relationships(
        &mut self,
        relationships: &[Relationship],
    ) -> Result<(), SnapshotError> {
        self.bytes.push(b'[');
        for (index, relationship) in relationships.iter().enumerate() {
            self.comma(index);
            self.bytes.push(b'{');
            let mut first = true;
            self.key(&mut first, "attributes");
            self.integer(relationship.attributes);
            self.key(&mut first, "fields");
            self.relationship_fields(&relationship.fields);
            self.key(&mut first, "foreign_table");
            self.string(&relationship.foreign_table);
            self.key(&mut first, "name");
            self.string(&relationship.name);
            self.key(&mut first, "properties");
            self.properties(&relationship.properties)?;
            self.key(&mut first, "table");
            self.string(&relationship.table);
            self.bytes.push(b'}');
        }
        self.bytes.push(b']');
        Ok(())
    }

    fn relationship_fields(&mut self, fields: &[RelationshipField]) {
        self.bytes.push(b'[');
        for (index, field) in fields.iter().enumerate() {
            self.comma(index);
            self.bytes.push(b'{');
            let mut first = true;
            self.key(&mut first, "field");
            self.string(&field.field);
            self.key(&mut first, "foreign_field");
            self.string(&field.foreign_field);
            self.bytes.push(b'}');
        }
        self.bytes.push(b']');
    }

    pub(crate) fn raw_preservation(&mut self, values: &[RawPreservation]) {
        self.bytes.push(b'[');
        for (index, value) in values.iter().enumerate() {
            self.comma(index);
            self.bytes.push(b'{');
            let mut first = true;
            self.key(&mut first, "purpose");
            self.string(&value.purpose);
            self.key(&mut first, "raw_hex");
            self.string(value.raw_hex.as_str());
            self.key(&mut first, "semantic_path");
            self.string(&value.semantic_path);
            self.bytes.push(b'}');
        }
        self.bytes.push(b']');
    }

    pub(crate) fn key(&mut self, first: &mut bool, key: &str) {
        if !*first {
            self.bytes.push(b',');
        }
        *first = false;
        self.string(key);
        self.bytes.push(b':');
    }

    pub(crate) fn string(&mut self, value: &str) {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        self.bytes.push(b'"');
        for character in value.chars() {
            match character {
                '"' => self.bytes.extend_from_slice(br#"\""#),
                '\\' => self.bytes.extend_from_slice(br#"\\"#),
                '\u{0008}' => self.bytes.extend_from_slice(br#"\b"#),
                '\u{000c}' => self.bytes.extend_from_slice(br#"\f"#),
                '\n' => self.bytes.extend_from_slice(br#"\n"#),
                '\r' => self.bytes.extend_from_slice(br#"\r"#),
                '\t' => self.bytes.extend_from_slice(br#"\t"#),
                '\u{0000}'..='\u{001f}' => {
                    let value = character as u8;
                    self.bytes.extend_from_slice(br#"\u00"#);
                    self.bytes.push(HEX[usize::from(value >> 4)]);
                    self.bytes.push(HEX[usize::from(value & 0x0f)]);
                }
                _ => {
                    let mut encoded = [0_u8; 4];
                    self.bytes
                        .extend_from_slice(character.encode_utf8(&mut encoded).as_bytes());
                }
            }
        }
        self.bytes.push(b'"');
    }

    pub(crate) fn boolean(&mut self, value: bool) {
        self.bytes
            .extend_from_slice(if value { b"true" } else { b"false" });
    }

    pub(crate) fn integer(&mut self, value: i64) {
        if value.is_negative() {
            self.bytes.push(b'-');
        }
        self.unsigned(value.unsigned_abs());
    }

    pub(crate) fn unsigned(&mut self, value: u64) {
        let mut digits = [0_u8; 20];
        let mut start = digits.len();
        let mut remaining = value;
        loop {
            start -= 1;
            digits[start] = b'0' + (remaining % 10) as u8;
            remaining /= 10;
            if remaining == 0 {
                break;
            }
        }
        self.bytes.extend_from_slice(&digits[start..]);
    }

    pub(crate) fn comma(&mut self, index: usize) {
        if index != 0 {
            self.bytes.push(b',');
        }
    }
}

pub(crate) enum JsonBytes {
    Count(usize),
    Output(Vec<u8>),
}

impl JsonBytes {
    pub(crate) fn push(&mut self, byte: u8) {
        match self {
            Self::Count(length) => *length = length.saturating_add(1),
            Self::Output(bytes) => bytes.push(byte),
        }
    }

    pub(crate) fn extend_from_slice(&mut self, value: &[u8]) {
        match self {
            Self::Count(length) => *length = length.saturating_add(value.len()),
            Self::Output(bytes) => bytes.extend_from_slice(value),
        }
    }
}

impl TypedValue {
    fn kind(&self) -> &'static str {
        match self {
            Self::Null { .. } => "null",
            Self::Boolean { .. } => "boolean",
            Self::Byte { .. } => "byte",
            Self::Integer { .. } => "integer",
            Self::Long { .. } => "long",
            Self::Single { .. } => "single",
            Self::Double { .. } => "double",
            Self::Decimal { .. } => "decimal",
            Self::Currency { .. } => "currency",
            Self::DateTime { .. } => "datetime",
            Self::Text { .. } => "text",
            Self::Binary { .. } => "binary",
            Self::Guid { .. } => "guid",
            Self::Memo { .. } => "memo",
            Self::Ole { .. } => "ole",
        }
    }

    fn raw_hex(&self) -> Option<&crate::HexString> {
        match self {
            Self::Null { raw_hex }
            | Self::Boolean { raw_hex, .. }
            | Self::Byte { raw_hex, .. }
            | Self::Integer { raw_hex, .. }
            | Self::Long { raw_hex, .. }
            | Self::Single { raw_hex, .. }
            | Self::Double { raw_hex, .. }
            | Self::Decimal { raw_hex, .. }
            | Self::Currency { raw_hex, .. }
            | Self::DateTime { raw_hex, .. }
            | Self::Text { raw_hex, .. }
            | Self::Binary { raw_hex, .. }
            | Self::Guid { raw_hex, .. }
            | Self::Memo { raw_hex, .. }
            | Self::Ole { raw_hex, .. } => raw_hex.as_ref(),
        }
    }

    fn code_page(&self) -> Option<u32> {
        match self {
            Self::Text { code_page, .. } | Self::Memo { code_page, .. } => *code_page,
            _ => None,
        }
    }
}

fn canonical_f32(value: FiniteF32) -> Result<NumberText, SnapshotError> {
    let value = value.get();
    let mut scientific = NumberText::new();
    write!(scientific, "{value:e}").map_err(|_| SnapshotError::NumberFormatting)?;
    canonical_float(
        scientific.as_str(),
        value == 0.0 && value.is_sign_negative(),
    )
}

fn canonical_f64(value: FiniteF64) -> Result<NumberText, SnapshotError> {
    let value = value.get();
    let mut scientific = NumberText::new();
    write!(scientific, "{value:e}").map_err(|_| SnapshotError::NumberFormatting)?;
    canonical_float(
        scientific.as_str(),
        value == 0.0 && value.is_sign_negative(),
    )
}

fn canonical_float(scientific: &str, negative_zero: bool) -> Result<NumberText, SnapshotError> {
    let (mantissa, exponent) = scientific
        .split_once('e')
        .ok_or(SnapshotError::NumberFormatting)?;
    let exponent = exponent
        .parse::<i32>()
        .map_err(|_| SnapshotError::NumberFormatting)?;
    let (sign, unsigned_mantissa) = mantissa
        .strip_prefix('-')
        .map_or(("", mantissa), |unsigned| ("-", unsigned));
    let mut digits = NumberText::new();
    for byte in unsigned_mantissa.bytes().filter(|byte| *byte != b'.') {
        digits.push_byte(byte)?;
    }
    if digits.as_str().is_empty() || !digits.as_str().bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(SnapshotError::NumberFormatting);
    }
    if digits.as_str().bytes().all(|byte| byte == b'0') {
        return NumberText::from_str(if negative_zero { "-0.0" } else { "0.0" });
    }
    if !(-4..16).contains(&exponent) {
        let exponent_sign = if exponent.is_negative() { '-' } else { '+' };
        let magnitude = exponent.unsigned_abs();
        let mut output = NumberText::new();
        write!(
            output,
            "{sign}{unsigned_mantissa}e{exponent_sign}{magnitude:02}"
        )
        .map_err(|_| SnapshotError::NumberFormatting)?;
        return Ok(output);
    }
    let decimal_index = i64::from(exponent) + 1;
    let digit_count =
        i64::try_from(digits.as_str().len()).map_err(|_| SnapshotError::NumberFormatting)?;
    let mut output = NumberText::new();
    output.push_text(sign)?;
    if decimal_index <= 0 {
        output.push_text("0.")?;
        for _ in 0..decimal_index.unsigned_abs() {
            output.push_byte(b'0')?;
        }
        output.push_text(digits.as_str())?;
    } else if decimal_index >= digit_count {
        output.push_text(digits.as_str())?;
        for _ in 0..(decimal_index - digit_count) {
            output.push_byte(b'0')?;
        }
    } else {
        let split = usize::try_from(decimal_index).map_err(|_| SnapshotError::NumberFormatting)?;
        let (integer, fraction) = digits.as_str().split_at(split);
        output.push_text(integer)?;
        output.push_byte(b'.')?;
        output.push_text(fraction)?;
    }
    if !output.as_str().contains('.') {
        output.push_text(".0")?;
    }
    Ok(output)
}

struct NumberText {
    bytes: [u8; 64],
    length: usize,
}

impl NumberText {
    const fn new() -> Self {
        Self {
            bytes: [0; 64],
            length: 0,
        }
    }

    fn from_str(value: &str) -> Result<Self, SnapshotError> {
        let mut output = Self::new();
        output.push_text(value)?;
        Ok(output)
    }

    fn as_str(&self) -> &str {
        std::str::from_utf8(&self.bytes[..self.length]).unwrap_or_default()
    }

    fn as_bytes(&self) -> &[u8] {
        &self.bytes[..self.length]
    }

    fn push_byte(&mut self, byte: u8) -> Result<(), SnapshotError> {
        let destination = self
            .bytes
            .get_mut(self.length)
            .ok_or(SnapshotError::NumberFormatting)?;
        *destination = byte;
        self.length += 1;
        Ok(())
    }

    fn push_text(&mut self, value: &str) -> Result<(), SnapshotError> {
        let end = self
            .length
            .checked_add(value.len())
            .ok_or(SnapshotError::NumberFormatting)?;
        let destination = self
            .bytes
            .get_mut(self.length..end)
            .ok_or(SnapshotError::NumberFormatting)?;
        destination.copy_from_slice(value.as_bytes());
        self.length = end;
        Ok(())
    }
}

impl fmt::Write for NumberText {
    fn write_str(&mut self, value: &str) -> fmt::Result {
        self.push_text(value).map_err(|_| fmt::Error)
    }
}
