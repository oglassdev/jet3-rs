use std::error::Error as _;

use jet3::{
    ByteCount, DatabaseReader, Error, JET3_PAGE_SIZE, ReadLimits, ResourceBudget,
    ResourceLimitKind, ResourceLimits, SliceSource, TextCodePage,
};

use crate::{
    Producer, ProducerKind, ScenarioId, SemanticProtocolError, SemanticSnapshotError,
    SemanticSnapshotOptions, SemanticSnapshotOutcome, Sha256, TableKind, TypedValue,
    snapshot_database, snapshot_database_with_receipt,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const CATALOG_ROOT: usize = 1;
const MAP_PAGE: usize = 2;
const CATALOG_DATA: usize = 3;
const TABLE_ROOT: usize = 4;
const INDEX_ROOT: usize = 5;
const TABLE_DATA: usize = 6;

/// Physical kind of the second user column in the synthetic table.
#[derive(Clone, Copy)]
enum SecondColumn {
    Text,
    DateTime,
}

fn catalog_record(id: u32, kind: u16, flags: u32, name: &[u8]) -> Vec<u8> {
    let mut row = vec![0_u8; 31 + name.len() + 6];
    row[0] = 17;
    row[1..5].copy_from_slice(&id.to_le_bytes());
    row[9..11].copy_from_slice(&kind.to_le_bytes());
    row[27..31].copy_from_slice(&flags.to_le_bytes());
    row[31..31 + name.len()].copy_from_slice(name);
    let length = row.len();
    row[length - 6] = u8::try_from(31 + name.len()).unwrap_or_default();
    row[length - 5] = 31;
    row[length - 4] = 11;
    row[length - 3] = 0xff;
    row
}

fn write_rows(page: &mut [u8], owner: u32, rows: &[(Vec<u8>, u16)]) {
    page[0] = 1;
    page[4..8].copy_from_slice(&owner.to_le_bytes());
    page[8..10].copy_from_slice(&u16::try_from(rows.len()).unwrap_or_default().to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, (row, flags)) in rows.iter().enumerate() {
        start -= row.len();
        let raw = u16::try_from(start).unwrap_or_default() | flags;
        page[10 + 2 * index..12 + 2 * index].copy_from_slice(&raw.to_le_bytes());
        page[start..start + row.len()].copy_from_slice(row);
    }
}

fn column_record(
    physical_type: u8,
    ordinal: u16,
    class: u8,
    fixed_offset: u16,
    size: u16,
) -> [u8; 18] {
    let mut record = [0_u8; 18];
    record[0] = physical_type;
    record[1..3].copy_from_slice(&ordinal.to_le_bytes());
    record[5..7].copy_from_slice(&ordinal.to_le_bytes());
    record[7..9].copy_from_slice(&1_u16.to_le_bytes());
    record[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    record[13] = class;
    record[14..16].copy_from_slice(&fixed_offset.to_le_bytes());
    record[16..18].copy_from_slice(&size.to_le_bytes());
    record
}

fn physical_index(fields: &[(u16, bool)]) -> [u8; 39] {
    let mut record = [0_u8; 39];
    for slot in 0..10 {
        let offset = slot * 3;
        record[offset..offset + 2].copy_from_slice(&u16::MAX.to_le_bytes());
        record[offset + 2] = 0xa0_u8.saturating_add(u8::try_from(slot).unwrap_or_default());
    }
    for (slot, (column, ascending)) in fields.iter().enumerate() {
        let offset = slot * 3;
        record[offset..offset + 2].copy_from_slice(&column.to_le_bytes());
        record[offset + 2] = u8::from(*ascending);
    }
    record[31..34].copy_from_slice(&[MAP_PAGE as u8, 0, 0]);
    record[34..38].copy_from_slice(&(INDEX_ROOT as u32).to_le_bytes());
    record[38] = 9;
    record
}

/// A two-column user table `Items(Id Long fixed, Name Text|DateTime)` with
/// one primary index `PK` over the selected fields.
fn user_definition_with_index(second: SecondColumn, fields: &[(u16, bool)]) -> Vec<u8> {
    let mut bytes = vec![0_u8; 43];
    bytes[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    bytes[20] = 0x4e;
    bytes[21..23].copy_from_slice(&2_u16.to_le_bytes());
    bytes[25..27].copy_from_slice(&2_u16.to_le_bytes());
    bytes[27..29].copy_from_slice(&1_u16.to_le_bytes());
    bytes[31..33].copy_from_slice(&1_u16.to_le_bytes());
    bytes[35..39].copy_from_slice(&[2, MAP_PAGE as u8, 0, 0]);
    bytes[39..43].copy_from_slice(&[3, MAP_PAGE as u8, 0, 0]);
    bytes.extend_from_slice(&[1, 2, 3, 4, 5, 6, 7, 8]);
    bytes.extend_from_slice(&column_record(4, 0, 3, 0, 4));
    match second {
        SecondColumn::Text => {
            bytes[23..25].copy_from_slice(&1_u16.to_le_bytes());
            bytes.extend_from_slice(&column_record(10, 1, 2, 0, 255));
        }
        SecondColumn::DateTime => bytes.extend_from_slice(&column_record(8, 1, 3, 4, 8)),
    }
    bytes.extend_from_slice(&[2, b'I', b'd', 4, b'N', b'a', b'm', b'e']);
    bytes.extend_from_slice(&physical_index(fields));
    let mut logical = [0_u8; 20];
    logical[9..13].copy_from_slice(&u32::MAX.to_le_bytes());
    logical[17..19].copy_from_slice(&[4, 4]);
    logical[19] = 1;
    bytes.extend_from_slice(&logical);
    bytes.extend_from_slice(&[2, b'P', b'K']);
    bytes.extend_from_slice(&[0xff, 0xff]);
    let length = u32::try_from(bytes.len()).unwrap_or_default();
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    bytes
}

fn user_definition(second: SecondColumn) -> Vec<u8> {
    user_definition_with_index(second, &[(0, true)])
}

fn text_row(id: u32, name: &[u8]) -> Vec<u8> {
    let mut row = vec![2];
    row.extend_from_slice(&id.to_le_bytes());
    row.extend_from_slice(name);
    let end = u8::try_from(5 + name.len()).unwrap_or_default();
    row.extend_from_slice(&[end, 5, 1, 3]);
    row
}

fn datetime_row(id: u32) -> Vec<u8> {
    let mut row = vec![2];
    row.extend_from_slice(&id.to_le_bytes());
    row.extend_from_slice(&1.5_f64.to_le_bytes());
    row.push(3);
    row
}

fn database_bytes(second: SecondColumn, user_kind: u16) -> Vec<u8> {
    let mut bytes = vec![0_u8; 7 * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    let catalog = &mut bytes[CATALOG_ROOT * PAGE_BYTES..(CATALOG_ROOT + 1) * PAGE_BYTES];
    catalog[0] = 2;
    catalog[35..39].copy_from_slice(&[0, MAP_PAGE as u8, 0, 0]);
    catalog[39..43].copy_from_slice(&[1, MAP_PAGE as u8, 0, 0]);

    let maps = [
        (vec![0, 0, 0, 0, 0, 1 << CATALOG_DATA], 0),
        (vec![0, 0, 0, 0, 0], 0),
        (vec![0, 0, 0, 0, 0, 1 << TABLE_DATA], 0),
        (vec![0, 0, 0, 0, 0], 0),
    ];
    write_rows(
        &mut bytes[MAP_PAGE * PAGE_BYTES..(MAP_PAGE + 1) * PAGE_BYTES],
        0,
        &maps,
    );
    let records = [
        (catalog_record(1, 1, 0x8000_0000, b"MSysObjects"), 0),
        (catalog_record(TABLE_ROOT as u32, user_kind, 0, b"Items"), 0),
    ];
    write_rows(
        &mut bytes[CATALOG_DATA * PAGE_BYTES..(CATALOG_DATA + 1) * PAGE_BYTES],
        CATALOG_ROOT as u32,
        &records,
    );
    let definition = user_definition(second);
    bytes[TABLE_ROOT * PAGE_BYTES..TABLE_ROOT * PAGE_BYTES + definition.len()]
        .copy_from_slice(&definition);
    let index = &mut bytes[INDEX_ROOT * PAGE_BYTES..(INDEX_ROOT + 1) * PAGE_BYTES];
    index[0] = 4;
    index[1] = 1;
    index[2..4].copy_from_slice(
        &u16::try_from(PAGE_BYTES - 248)
            .unwrap_or_default()
            .to_le_bytes(),
    );
    index[4..8].copy_from_slice(&(TABLE_ROOT as u32).to_le_bytes());
    let rows = match second {
        SecondColumn::Text => [(text_row(2, b"b"), 0), (text_row(1, b"a"), 0)],
        SecondColumn::DateTime => [(datetime_row(2), 0), (datetime_row(1), 0)],
    };
    write_rows(
        &mut bytes[TABLE_DATA * PAGE_BYTES..(TABLE_DATA + 1) * PAGE_BYTES],
        TABLE_ROOT as u32,
        &rows,
    );
    bytes
}

fn composite_database_bytes() -> Vec<u8> {
    let mut bytes = database_bytes(SecondColumn::Text, 1);
    let definition = user_definition_with_index(SecondColumn::Text, &[(0, true), (1, true)]);
    let table = &mut bytes[TABLE_ROOT * PAGE_BYTES..(TABLE_ROOT + 1) * PAGE_BYTES];
    table.fill(0);
    table[..definition.len()].copy_from_slice(&definition);
    bytes
}

fn write_index_leaf_entries(bytes: &mut [u8], keys: &[&[u8]]) {
    let page = &mut bytes[INDEX_ROOT * PAGE_BYTES..(INDEX_ROOT + 1) * PAGE_BYTES];
    page.fill(0);
    page[0] = 4;
    page[1] = 1;
    page[4..8].copy_from_slice(&(TABLE_ROOT as u32).to_le_bytes());
    let mut boundary = 0_usize;
    for (slot, key) in keys.iter().enumerate() {
        let start = 248 + boundary;
        page[start..start + key.len()].copy_from_slice(key);
        let trailer = start + key.len();
        page[trailer..trailer + 4].copy_from_slice(&[
            0,
            0,
            TABLE_DATA as u8,
            u8::try_from(slot).unwrap_or_default(),
        ]);
        boundary += key.len() + 4;
        page[22 + boundary / 8] |= 1 << (boundary % 8);
    }
    page[2..4].copy_from_slice(
        &u16::try_from(PAGE_BYTES - 248 - boundary)
            .unwrap_or_default()
            .to_le_bytes(),
    );
}

fn limits(bytes: &[u8]) -> ResourceLimits {
    ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    ))
}

fn options() -> Result<SemanticSnapshotOptions, Box<dyn std::error::Error>> {
    options_for("DAO-READ-ROWS-SINGLE")
}

fn options_for(scenario_id: &str) -> Result<SemanticSnapshotOptions, Box<dyn std::error::Error>> {
    Ok(SemanticSnapshotOptions {
        scenario_id: ScenarioId::new(scenario_id)?,
        producer: Producer::new(ProducerKind::Rust, "test")?,
        database_sha256: Sha256::new("ab".repeat(32))?,
        code_page: TextCodePage::Windows1252,
    })
}

fn snapshot(
    bytes: &[u8],
    limits: ResourceLimits,
) -> Result<crate::SemanticSnapshot, SemanticSnapshotError> {
    let mut budget = ResourceBudget::new(limits);
    let source =
        SliceSource::new(bytes, budget.read_budget()).map_err(SemanticSnapshotError::Resource)?;
    let mut database = DatabaseReader::from_source(source, &mut budget).map_err(|_| {
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "open synthetic snapshot database",
        })
    })?;
    let options = options().map_err(|_| {
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "build snapshot options",
        })
    })?;
    snapshot_database(&mut database, &options, &mut budget)
}

fn artifacts(
    bytes: &[u8],
    limits: ResourceLimits,
) -> Result<crate::SemanticSnapshotArtifacts, SemanticSnapshotError> {
    artifacts_for_scenario(bytes, limits, "DAO-READ-ROWS-SINGLE")
}

fn artifacts_for_scenario(
    bytes: &[u8],
    limits: ResourceLimits,
    scenario_id: &str,
) -> Result<crate::SemanticSnapshotArtifacts, SemanticSnapshotError> {
    let mut budget = ResourceBudget::new(limits);
    let source =
        SliceSource::new(bytes, budget.read_budget()).map_err(SemanticSnapshotError::Resource)?;
    let mut database = DatabaseReader::from_source(source, &mut budget).map_err(|_| {
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "open synthetic snapshot database",
        })
    })?;
    let options = options_for(scenario_id).map_err(|_| {
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "build snapshot options",
        })
    })?;
    snapshot_database_with_receipt(&mut database, &options, &mut budget)
}

#[test]
fn composite_scenario_rejects_empty_or_keyless_index_evidence()
-> Result<(), Box<dyn std::error::Error>> {
    const SCENARIO: &str = "DAO-READ-SCHEMA-INDEX-COMPOSITE-ASCENDING";
    let empty = composite_database_bytes();
    let empty_artifacts = artifacts_for_scenario(&empty, limits(&empty), SCENARIO)?;
    assert!(
        !empty_artifacts
            .coverage_receipt
            .branches
            .contains("index.composite_key_lossless")
    );
    let mut budget = ResourceBudget::new(limits(&empty));
    assert!(matches!(
        empty_artifacts.to_canonical_json(&mut budget),
        Err(SemanticSnapshotError::Protocol(_))
    ));

    let mut keyless = composite_database_bytes();
    write_index_leaf_entries(&mut keyless, &[&[]]);
    assert!(matches!(
        artifacts_for_scenario(&keyless, limits(&keyless), SCENARIO),
        Err(SemanticSnapshotError::IndexTree(_))
    ));
    Ok(())
}

#[test]
fn composite_scenario_records_observed_lossless_key_bytes() -> Result<(), Box<dyn std::error::Error>>
{
    const SCENARIO: &str = "DAO-READ-SCHEMA-INDEX-COMPOSITE-ASCENDING";
    let mut bytes = composite_database_bytes();
    write_index_leaf_entries(
        &mut bytes,
        &[
            &[0x7f, 0x80, 0, 0, 1, 0x7f, 0x60],
            &[0x7f, 0x80, 0, 0, 2, 0x7f, 0x61],
        ],
    );
    let artifacts = artifacts_for_scenario(&bytes, limits(&bytes), SCENARIO)?;
    assert!(
        artifacts
            .coverage_receipt
            .branches
            .contains("index.composite_key_lossless")
    );
    let mut budget = ResourceBudget::new(limits(&bytes));
    artifacts.to_canonical_json(&mut budget)?;
    Ok(())
}

fn snapshot_allocation(
    bytes: &[u8],
    limits: ResourceLimits,
) -> Result<ByteCount, SemanticSnapshotError> {
    let mut budget = ResourceBudget::new(limits);
    let options = options().map_err(|_| {
        SemanticSnapshotError::Resource(Error::Arithmetic {
            operation: "build snapshot options",
        })
    })?;
    {
        let source = SliceSource::new(bytes, budget.read_budget())
            .map_err(SemanticSnapshotError::Resource)?;
        let mut database = DatabaseReader::from_source(source, &mut budget).map_err(|_| {
            SemanticSnapshotError::Resource(Error::Arithmetic {
                operation: "open synthetic snapshot database",
            })
        })?;
        snapshot_database_with_receipt(&mut database, &options, &mut budget)?;
    }
    Ok(budget.allocation_bytes())
}

#[test]
fn user_table_snapshot_is_typed_lossless_and_deterministic()
-> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(SecondColumn::Text, 1);
    let first = snapshot(&bytes, limits(&bytes))?;
    let second = snapshot(&bytes, limits(&bytes))?;
    assert_eq!(first, second);

    assert_eq!(first.tables.len(), 1);
    let table = &first.tables[0];
    assert_eq!(table.name, "Items");
    assert_eq!(table.kind, TableKind::User);
    assert_eq!(
        table
            .columns
            .iter()
            .map(|column| (
                column.name.as_str(),
                column.dao_type.as_str(),
                column.ordinal
            ))
            .collect::<Vec<_>>(),
        vec![("Id", "dbLong", 0), ("Name", "dbText", 1)]
    );
    assert_eq!(table.indexes.len(), 1);
    let index = &table.indexes[0];
    assert_eq!(index.name, "PK");
    assert!(index.primary && index.unique && index.required);
    assert_eq!(index.fields[0].name, "Id");
    assert!(!index.fields[0].descending);

    let mut ids = table
        .rows
        .iter()
        .map(|row| row.values.get("Id").cloned())
        .collect::<Vec<_>>();
    ids.sort_by_key(|value| match value {
        Some(TypedValue::Long { value, .. }) => *value,
        _ => i32::MAX,
    });
    assert_eq!(
        ids,
        vec![
            Some(TypedValue::Long {
                value: 1,
                raw_hex: Some(crate::HexString::new("01000000")?),
            }),
            Some(TypedValue::Long {
                value: 2,
                raw_hex: Some(crate::HexString::new("02000000")?),
            }),
        ]
    );
    assert!(
        table
            .rows
            .iter()
            .all(|row| row.canonical_key.as_str().len() == 64)
    );
    assert!(table.rows.iter().all(|row| row.duplicate_ordinal == 0));
    let expected_name = TypedValue::Text {
        value: "a".to_owned(),
        raw_hex: Some(crate::HexString::new("61")?),
        code_page: Some(1252),
    };
    assert!(
        table
            .rows
            .iter()
            .any(|row| row.values.get("Name") == Some(&expected_name))
    );
    assert!(first.relationships.is_empty());

    let artifacts = artifacts(&bytes, limits(&bytes))?;
    assert_eq!(
        artifacts.snapshot,
        crate::SemanticSnapshotOutcome::Success(first)
    );
    assert_eq!(
        artifacts.coverage_receipt.branches,
        [
            "allocation.inline_map",
            "catalog.record_stream",
            "catalog.root_discovery",
            "open.header_page",
            "open.signature_geometry",
            "rows.direct",
            "tdef.column_types",
            "tdef.logical_index",
            "tdef.physical_index",
            "tdef.single_page",
            "values.fixed_scalar",
            "values.text_cp1252",
            "values.variable_short",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    );
    Ok(())
}

#[test]
fn resource_limits_propagate_as_structured_errors() {
    let bytes = database_bytes(SecondColumn::Text, 1);
    let error = snapshot(
        &bytes,
        limits(&bytes).with_max_allocation_bytes(ByteCount::new(64)),
    )
    .err();
    assert!(
        matches!(
            error,
            Some(SemanticSnapshotError::Resource(
                Error::ResourceLimitExceeded {
                    kind: ResourceLimitKind::AllocationBytes,
                    ..
                }
            ))
        ),
        "{error:?}"
    );

    let error = snapshot(&bytes, limits(&bytes).with_max_item_work(8)).err();
    let mut kinds = Vec::new();
    let mut source: Option<&dyn std::error::Error> =
        error.as_ref().map(|error| error as &dyn std::error::Error);
    while let Some(current) = source {
        if let Some(Error::ResourceLimitExceeded { kind, .. }) = current.downcast_ref::<Error>() {
            kinds.push(*kind);
        }
        source = current.source();
    }
    assert_eq!(kinds, vec![ResourceLimitKind::ItemWork]);
}

#[test]
fn unsupported_catalog_kinds_fail_closed_and_datetime_is_invariant() {
    let bytes = database_bytes(SecondColumn::Text, 7);
    assert_eq!(
        snapshot(&bytes, limits(&bytes)).err(),
        Some(SemanticSnapshotError::UnsupportedCatalogObject {
            id: TABLE_ROOT as u32,
            kind: 7,
        })
    );

    let bytes = database_bytes(SecondColumn::DateTime, 1);
    let snapshot = snapshot(&bytes, limits(&bytes));
    assert!(snapshot.is_ok(), "{snapshot:?}");
    let has_datetime =
        snapshot.ok().is_some_and(|value| {
            value.tables[0].rows.iter().all(|row| matches!(
            row.values.get("Name"),
            Some(TypedValue::DateTime { value, .. }) if value.as_str() == "1899-12-31T12:00:00"
        ))
        });
    assert!(has_datetime);
}

#[test]
fn malformed_row_pages_reject_with_nested_sources() {
    let mut bytes = database_bytes(SecondColumn::Text, 1);
    bytes[TABLE_DATA * PAGE_BYTES + 4] = 0xff;
    let error = snapshot(&bytes, limits(&bytes)).err();
    assert!(
        matches!(error, Some(SemanticSnapshotError::Row(_))),
        "{error:?}"
    );
    let error = error.map(|error| error.source().is_some());
    assert_eq!(error, Some(true));
}

#[test]
fn artifact_pair_rejects_non_rust_producer_and_unsatisfied_index_scenario()
-> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(SecondColumn::Text, 1);
    let original = artifacts(&bytes, limits(&bytes))?;

    let mut wrong_producer = original.clone();
    let SemanticSnapshotOutcome::Success(snapshot) = &mut wrong_producer.snapshot else {
        return Err(std::io::Error::other("synthetic snapshot unexpectedly failed to open").into());
    };
    snapshot.producer = Producer::new(ProducerKind::Dao, "test")?;
    let mut budget = ResourceBudget::new(limits(&bytes));
    assert!(matches!(
        wrong_producer.to_canonical_json(&mut budget),
        Err(SemanticSnapshotError::Protocol(_))
    ));

    let mut wrong_scenario = original;
    let index_scenario = ScenarioId::new("DAO-READ-SCHEMA-INDEX-PRIMARY")?;
    let SemanticSnapshotOutcome::Success(snapshot) = &mut wrong_scenario.snapshot else {
        return Err(std::io::Error::other("synthetic snapshot unexpectedly failed to open").into());
    };
    snapshot.scenario_id = index_scenario.clone();
    wrong_scenario.coverage_receipt.scenario_id = index_scenario;
    let mut budget = ResourceBudget::new(limits(&bytes));
    assert!(matches!(
        wrong_scenario.to_canonical_json(&mut budget),
        Err(SemanticSnapshotError::Protocol(_))
    ));
    Ok(())
}

#[test]
fn artifact_pair_rejects_branch_outside_the_protocol_registry()
-> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(SecondColumn::Text, 1);
    let mut artifacts = artifacts(&bytes, limits(&bytes))?;
    artifacts
        .coverage_receipt
        .branches
        .insert("rows.imaginary".to_owned());

    let mut budget = ResourceBudget::new(limits(&bytes));
    assert_eq!(
        artifacts.to_canonical_json(&mut budget),
        Err(SemanticSnapshotError::Protocol(
            SemanticProtocolError::InvalidModel {
                path: "$.coverage_receipt.branches".to_owned(),
                reason: "coverage receipt branch is not in the closed protocol registry",
            }
        ))
    );
    Ok(())
}

#[test]
fn final_artifact_reservations_are_exactly_budgeted_before_growth()
-> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(SecondColumn::Text, 1);
    let artifacts = artifacts(&bytes, limits(&bytes))?;

    let mut measured = ResourceBudget::new(limits(&bytes));
    let (snapshot, receipt) = artifacts.to_canonical_json(&mut measured)?;
    assert!(!snapshot.is_empty() && !receipt.is_empty());
    assert_eq!(snapshot.capacity(), snapshot.len());
    assert_eq!(receipt.capacity(), receipt.len());
    let exact = measured.allocation_bytes();

    let exact_limits = limits(&bytes).with_max_allocation_bytes(exact);
    let mut exact_budget = ResourceBudget::new(exact_limits);
    artifacts.to_canonical_json(&mut exact_budget)?;
    assert_eq!(exact_budget.allocation_bytes(), exact);

    let one_less = ByteCount::new(exact.get() - 1);
    let mut rejected = ResourceBudget::new(limits(&bytes).with_max_allocation_bytes(one_less));
    let Err(error) = artifacts.to_canonical_json(&mut rejected) else {
        return Err(
            std::io::Error::other("one-less artifact allocation unexpectedly succeeded").into(),
        );
    };
    assert!(matches!(
        error,
        SemanticSnapshotError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            ..
        })
    ));
    assert!(rejected.allocation_bytes().get() < exact.get());
    Ok(())
}

#[test]
fn tiny_artifact_budget_rejects_large_public_model_before_row_walk()
-> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(SecondColumn::Text, 1);
    let mut artifacts = artifacts(&bytes, limits(&bytes))?;
    let SemanticSnapshotOutcome::Success(snapshot) = &mut artifacts.snapshot else {
        return Err(std::io::Error::other("synthetic snapshot unexpectedly failed to open").into());
    };
    let row = snapshot.tables[0]
        .rows
        .first()
        .cloned()
        .ok_or_else(|| std::io::Error::other("synthetic snapshot has no row"))?;
    snapshot.tables[0].rows = vec![row; 20_000];

    let maximum = ByteCount::new(4_096);
    let mut budget = ResourceBudget::new(limits(&bytes).with_max_allocation_bytes(maximum));
    let Err(error) = artifacts.to_canonical_json(&mut budget) else {
        return Err(std::io::Error::other(
            "large public model unexpectedly fit the tiny validation budget",
        )
        .into());
    };
    assert!(matches!(
        error,
        SemanticSnapshotError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            ..
        })
    ));
    assert!(budget.allocation_bytes() <= maximum);
    assert!(budget.total_work_units() <= maximum.get());
    Ok(())
}

#[test]
fn artifact_validation_uses_the_callers_text_decode_budget()
-> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(SecondColumn::Text, 1);
    let artifacts = artifacts(&bytes, limits(&bytes))?;
    let zero = ByteCount::new(0);
    let validation_limits = limits(&bytes)
        .with_max_decoded_value_bytes(zero)
        .with_max_total_decoded_bytes(zero);
    let mut budget = ResourceBudget::new(validation_limits);
    let Err(error) = artifacts.to_canonical_json(&mut budget) else {
        return Err(std::io::Error::other(
            "text validation unexpectedly bypassed the caller's decode budget",
        )
        .into());
    };
    assert!(matches!(
        error,
        SemanticSnapshotError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::DecodedValueBytes,
            ..
        })
    ));
    assert_eq!(budget.decoded_bytes(), zero);
    Ok(())
}

#[test]
fn complete_snapshot_allocation_has_an_exact_boundary() -> Result<(), Box<dyn std::error::Error>> {
    let bytes = database_bytes(SecondColumn::Text, 1);
    let exact = snapshot_allocation(&bytes, limits(&bytes))?;
    assert_eq!(
        snapshot_allocation(&bytes, limits(&bytes).with_max_allocation_bytes(exact))?,
        exact
    );
    let one_less = ByteCount::new(exact.get() - 1);
    let Err(error) =
        snapshot_allocation(&bytes, limits(&bytes).with_max_allocation_bytes(one_less))
    else {
        return Err(
            std::io::Error::other("one-less snapshot allocation unexpectedly succeeded").into(),
        );
    };
    assert!(matches!(
        error,
        SemanticSnapshotError::Resource(Error::ResourceLimitExceeded {
            kind: ResourceLimitKind::AllocationBytes,
            ..
        })
    ));
    Ok(())
}
