//! Focused synthetic shapes of the native schema edits recorded by EXP-0297.
use super::reader_tests::*;
use crate::{
    ColumnStorageClass, DatabaseReader, Error, PAGE_BYTES, PageNumber, ResourceBudget,
    RowColumnLayout, RowValue, SliceSource, TableDefinition, TableDefinitionError, TextCodePage,
    ValueKind,
};

use crate::testkit::TestResult;

fn schema(records: &[[u8; 18]], storage: u16, variables: u16) -> Vec<u8> {
    let mut bytes = definition()[..43].to_vec();
    bytes[21..23].copy_from_slice(&storage.to_le_bytes());
    bytes[23..25].copy_from_slice(&variables.to_le_bytes());
    bytes[25..27].copy_from_slice(&(records.len() as u16).to_le_bytes());
    for record in records {
        bytes.extend_from_slice(record);
    }
    for ordinal in 0..records.len() {
        bytes.extend_from_slice(&[1, b'A' + ordinal as u8]);
    }
    bytes.extend_from_slice(&[0xff, 0xff]);
    let length = bytes.len() as u32;
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    bytes
}

fn image(schema: &[u8], row: &[u8]) -> Vec<u8> {
    let mut bytes = database_bytes([0, SECOND_DATA as u8, 0, 0], 0x8000, ROOT as u32);
    bytes[ROOT * PAGE_BYTES..(ROOT + 1) * PAGE_BYTES].fill(0);
    bytes[ROOT * PAGE_BYTES..ROOT * PAGE_BYTES + schema.len()].copy_from_slice(schema);
    write_rows(
        &mut bytes[FIRST_DATA * PAGE_BYTES..(FIRST_DATA + 1) * PAGE_BYTES],
        ROOT as u32,
        &[(row, 0)],
    );
    write_rows(
        &mut bytes[SECOND_DATA * PAGE_BYTES..(SECOND_DATA + 1) * PAGE_BYTES],
        ROOT as u32,
        &[],
    );
    bytes
}

fn read_schema(schema: &[u8]) -> Result<TableDefinition, TableDefinitionError> {
    let bytes = image(schema, &[0]);
    let mut budget = ResourceBudget::new(limits(&bytes));
    let source =
        SliceSource::new(&bytes, budget.read_budget()).map_err(TableDefinitionError::Resource)?;
    let mut database = DatabaseReader::from_source(source, &mut budget).map_err(|_| {
        TableDefinitionError::Resource(Error::Arithmetic {
            operation: "open schema-gap test database",
        })
    })?;
    database.table_definition(PageNumber::new(ROOT as u64), &mut budget)
}

/// Stores `row` under `schema` and returns its fields by live column.
fn read_fields(schema: &[u8], row: &[u8]) -> TestResult<Vec<Option<Vec<u8>>>> {
    with_rows(&image(schema, row), |rows, definition| {
        let row = rows.next_row()?.ok_or("missing stored row")?;
        definition
            .columns()
            .iter()
            .map(|column| {
                let field = row.field(column.ordinal()).ok_or("missing field")?;
                Ok(field.raw_bytes().map(<[u8]>::to_vec))
            })
            .collect()
    })
}

/// Encodes `values` with the definition's own live layouts.
fn encode(definition: &TableDefinition, values: &[RowValue<'_>]) -> TestResult<Vec<u8>> {
    let layouts: Vec<_> = definition
        .columns()
        .iter()
        .map(RowColumnLayout::from)
        .collect();
    let mut output = [0xcc; 64];
    let length = crate::encode_row(&layouts, values, &mut output, &mut crate::testkit::budget())?;
    Ok(output[..length.get() as usize].to_vec())
}

fn dropped_variable_records() -> [[u8; 18]; 4] {
    [
        column_record(4, 0, 0, 3, 0, 4),
        column_record(10, 2, 1, 2, 0, 40),
        column_record(4, 3, 2, 3, 4, 4),
        column_record(10, 4, 2, 2, 0, 40),
    ]
}

fn original_row() -> Vec<u8> {
    let mut row = vec![4];
    row.extend_from_slice(&7_i32.to_le_bytes());
    row.extend_from_slice(&99_i32.to_le_bytes());
    row.extend_from_slice(b"CL");
    row.extend_from_slice(&[11, 10, 9, 2, 0x0f]);
    row
}

#[test]
fn preserves_storage_ids_and_reads_unchanged_rows_after_variable_drop_append() -> TestResult {
    let mut records = dropped_variable_records();
    records[3][5..7].copy_from_slice(&2_u16.to_le_bytes());
    let raw = schema(&records, 5, 3);
    let definition = read_schema(&raw)?;
    assert_eq!(definition.storage_column_count(), 5);
    assert_eq!(definition.storage_variable_count(), 3);
    assert_eq!(definition.columns()[1].ordinal().get(), 1);
    assert_eq!(definition.columns()[1].storage_ordinal(), 2);
    assert_eq!(definition.columns()[1].raw_record(), &records[1]);
    assert_eq!(definition.columns()[1].display_position(), 2);
    assert_eq!(definition.columns()[3].display_position(), 2);
    let fields = read_fields(&raw, &original_row())?;
    assert_eq!(fields[1].as_deref(), Some(&b"L"[..]));
    assert_eq!(fields[2].as_deref(), Some(&99_i32.to_le_bytes()[..]));
    assert_eq!(fields[3], None);
    Ok(())
}

#[test]
fn writes_empty_deleted_variable_slots_and_physical_presence_bits() -> TestResult {
    let raw = schema(&dropped_variable_records(), 5, 3);
    let definition = read_schema(&raw)?;
    let encoded = encode(
        &definition,
        &[
            RowValue::Long(8),
            RowValue::Text(b"label"),
            RowValue::Null,
            RowValue::Text(b"new"),
        ],
    )?;
    let length = encoded.len();
    assert_eq!(encoded[0], 5);
    assert_eq!(encoded.last(), Some(&0x15));
    assert_eq!(&encoded[length - 6..length - 1], &[17, 14, 9, 9, 3]);
    let fields = read_fields(&raw, &encoded)?;
    assert_eq!(fields[1].as_deref(), Some(&b"label"[..]));
    assert_eq!(fields[2], None);
    assert_eq!(fields[3].as_deref(), Some(&b"new"[..]));
    Ok(())
}

#[test]
fn fixed_holes_can_be_reused_out_of_order_without_reinterpreting_old_rows() -> TestResult {
    let records = [
        column_record(4, 0, 0, 3, 0, 4),
        column_record(10, 1, 0, 2, 0, 40),
        column_record(10, 2, 1, 2, 0, 40),
        column_record(4, 4, 2, 3, 8, 4),
        column_record(4, 5, 2, 3, 4, 4),
    ];
    let raw = schema(&records, 6, 2);
    let fields = read_fields(&raw, &original_row())?;
    assert_eq!(fields[2].as_deref(), Some(&b"L"[..]));
    assert_eq!(fields[3..], [None, None]);
    let output = encode(
        &read_schema(&raw)?,
        &[
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Null,
            RowValue::Long(44),
            RowValue::Long(55),
        ],
    )?;
    assert_eq!(&output[5..9], &55_i32.to_le_bytes());
    assert_eq!(&output[9..13], &44_i32.to_le_bytes());
    assert_eq!(output.last(), Some(&0x31));
    Ok(())
}

#[test]
fn dropped_last_fixed_field_and_deleted_variable_trailers_remain_opaque() -> TestResult {
    let baseline = [
        column_record(4, 0, 0, 3, 0, 4),
        column_record(10, 1, 0, 2, 0, 40),
        column_record(10, 2, 1, 2, 0, 40),
    ];
    for raw in [schema(&baseline, 4, 2), schema(&baseline[..1], 4, 2)] {
        let fields = read_fields(&raw, &original_row())?;
        assert_eq!(fields[0].as_deref(), Some(&7_i32.to_le_bytes()[..]));
    }
    Ok(())
}

#[test]
fn physical_ids_across_bitmap_bytes_and_appended_boolean_false() -> TestResult {
    let raw = schema(
        &[
            column_record(4, 0, 0, 3, 0, 4),
            column_record(1, 8, 0, 3, 0, 1),
        ],
        9,
        0,
    );
    let definition = read_schema(&raw)?;
    with_rows(&image(&raw, &[1, 7, 0, 0, 0, 1]), |rows, _| {
        let mut row = rows.next_row()?.ok_or("missing old fixed row")?;
        assert_eq!(
            row.value(definition.columns()[1].ordinal(), TextCodePage::Windows1252)?
                .ok_or("boolean")?
                .kind(),
            &ValueKind::Boolean(false)
        );
        Ok(())
    })?;
    assert_eq!(
        encode(&definition, &[RowValue::Long(7), RowValue::Boolean(true)])?,
        [9, 7, 0, 0, 0, 1, 1]
    );
    Ok(())
}

#[test]
fn resolves_physical_index_and_long_value_map_storage_ids_to_live_positions() -> TestResult {
    let raw_schema = schema(&dropped_variable_records(), 5, 3);
    let definition = read_schema(&raw_schema)?;
    let bytes = image(&raw_schema, &[0]);
    let mut budget = ResourceBudget::new(limits(&bytes));
    let database = open(&bytes, &mut budget)?;
    let mut raw = [0_u8; 39];
    for slot in raw[..30].chunks_exact_mut(3) {
        slot[..2].fill(0xff);
    }
    raw[..3].copy_from_slice(&[4, 0, 1]);
    raw[30..34].copy_from_slice(&[0, MAP_PAGE as u8, 0, 0]);
    raw[34..38].copy_from_slice(&(FIRST_DATA as u32).to_le_bytes());
    let mut decode = |raw| {
        crate::definition::physical_index::decode_physical(
            0,
            [0; 8],
            raw,
            definition.columns(),
            &[0],
            database.geometry(),
            &mut budget,
        )
    };
    let physical = decode(raw)?;
    assert_eq!(physical.fields()[0].column().get(), 3);
    assert_eq!(physical.raw_record(), &raw);
    raw[0] = 1;
    assert!(decode(raw).is_err());

    let mut lob_schema = schema(
        &[
            column_record(4, 0, 0, 3, 0, 4),
            column_record(12, 2, 1, 2, 0, 0),
        ],
        3,
        2,
    );
    let group = [2, 0, 0, MAP_PAGE as u8, 0, 0, 1, MAP_PAGE as u8, 0, 0];
    let end = lob_schema.len() - 2;
    lob_schema.splice(end..end, group);
    let length = lob_schema.len() as u32;
    lob_schema[8..12].copy_from_slice(&length.to_le_bytes());
    let definition = read_schema(&lob_schema)?;
    assert_eq!(definition.long_value_maps()[0].column().get(), 1);
    assert_eq!(definition.long_value_maps()[0].raw_group(), &group);
    lob_schema[end] = 1;
    assert!(read_schema(&lob_schema).is_err());
    Ok(())
}

#[test]
fn rejects_gap_definition_corruption_and_overlapping_live_fixed_ranges() {
    let valid = schema(&dropped_variable_records(), 5, 3);
    for (offset, value) in [
        (21, 3),
        (22, 1),
        (23, 6),
        (43 + 18 + 1, 0),
        (43 + 18 + 1, 5),
        (43 + 54 + 3, 1),
    ] {
        let mut raw = valid.clone();
        raw[offset] = value;
        assert!(
            read_schema(&raw).is_err(),
            "accepted corrupted offset {offset}"
        );
    }
    let mut records = dropped_variable_records();
    records[2][14..16].copy_from_slice(&2_u16.to_le_bytes());
    assert!(matches!(
        read_schema(&schema(&records, 5, 3)),
        Err(TableDefinitionError::InvalidFixedOffset { .. })
    ));
}

#[test]
fn sparse_rows_reject_short_fixed_regions_and_out_of_range_variable_trailers() -> TestResult {
    let raw = schema(&dropped_variable_records(), 5, 3);
    // Short fixed region, too few and too many variables, too many columns.
    for (offset, value) in [(13, 8), (14, 1), (14, 4), (0, 6)] {
        let mut row = original_row();
        row[offset] = value;
        with_rows(&image(&raw, &row), |rows, _| {
            assert!(rows.next_row().is_err(), "offset {offset}");
            Ok(())
        })?;
    }
    Ok(())
}

#[test]
fn public_dense_writer_still_rejects_storage_holes() {
    let mut output = [0; 32];
    for column in [
        RowColumnLayout::new(
            crate::ColumnPhysicalType::Long,
            ColumnStorageClass::Fixed { offset: 4 },
            4,
        ),
        RowColumnLayout::new(
            crate::ColumnPhysicalType::Text,
            ColumnStorageClass::Variable { index: 1 },
            40,
        ),
    ] {
        assert!(
            crate::encode_row(
                &[column],
                &[RowValue::Null],
                &mut output,
                &mut crate::testkit::budget()
            )
            .is_err()
        );
    }
}
