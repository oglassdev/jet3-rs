use crate::{
    CoverageBranches, CoverageReceipt, CoverageReceiptOutcome, FiniteF32, HexString, IndexField,
    Producer, ProducerKind, PropertyMap, RawPreservation, Relationship, RelationshipField,
    ScenarioId, SemanticColumn, SemanticIndex, SemanticRow, SemanticSnapshot, SemanticTable,
    Sha256, TableKind, TypedValue,
};

const SOURCE_REVISION_FIXTURES: &str = include_str!(
    "../../../oracle/windows-dao/protocol/v1_2/fixtures/source-revision-length-vectors.tsv"
);
const TEXT_CODE_PAGE_FIXTURES: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/fixtures/text-code-page-vectors.tsv");
const RELATIONSHIP_FIELD_UNIQUENESS_FIXTURES: &str = include_str!(
    "../../../oracle/windows-dao/protocol/v1_2/fixtures/relationship-field-uniqueness-vectors.tsv"
);
const SEMANTIC_NAME_FIXTURES: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/fixtures/semantic-name-vectors.tsv");
const PRODUCER_EXTENSION_NORMALIZATION_FIXTURES: &str = include_str!(
    "../../../oracle/windows-dao/protocol/v1_2/fixtures/producer-extension-normalization-vector.tsv"
);
const PRODUCER_EXTENSION_PATH_FIXTURES: &str = include_str!(
    "../../../oracle/windows-dao/protocol/v1_2/fixtures/producer-extension-path-vectors.tsv"
);

fn long(value: i32) -> Result<TypedValue, Box<dyn std::error::Error>> {
    Ok(TypedValue::Long {
        value,
        raw_hex: Some(HexString::new(format!("{value:08x}"))?),
    })
}

fn test_error(message: &'static str) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidData, message)
}

fn valid_snapshot() -> Result<SemanticSnapshot, Box<dyn std::error::Error>> {
    let mut snapshot = SemanticSnapshot::new(
        ScenarioId::new("DAO-READ-ROWS-SINGLE")?,
        Producer::new(ProducerKind::Rust, "test")?,
        Sha256::new("ab".repeat(32))?,
    );
    snapshot.tables.push(SemanticTable {
        name: "Items".into(),
        kind: TableKind::User,
        attributes: 0,
        columns: vec![SemanticColumn {
            name: "Id".into(),
            ordinal: 0,
            dao_type: "dbLong".into(),
            auto_increment: false,
            size: Some(4),
            attributes: 1,
            properties: PropertyMap::new(),
        }],
        indexes: vec![SemanticIndex {
            name: "PK".into(),
            primary: true,
            unique: true,
            required: true,
            fields: vec![IndexField {
                name: "Id".into(),
                descending: false,
            }],
            properties: PropertyMap::new(),
        }],
        properties: PropertyMap::new(),
        rows: vec![SemanticRow {
            canonical_key: Sha256::new("00".repeat(32))?,
            duplicate_ordinal: 0,
            values: PropertyMap::from([("Id".into(), long(1)?)]),
        }],
    });
    snapshot.relationships.push(Relationship {
        name: "Self".into(),
        table: "Items".into(),
        foreign_table: "Items".into(),
        attributes: 0,
        fields: vec![RelationshipField {
            field: "Id".into(),
            foreign_field: "Id".into(),
        }],
        properties: PropertyMap::new(),
    });
    snapshot.raw_preservation.push(RawPreservation {
        semantic_path: "/tables/0".into(),
        raw_hex: HexString::new("00")?,
        purpose: "test".into(),
    });
    snapshot.producer_extensions.insert(
        "/tables/0/columns/0/required".into(),
        TypedValue::Boolean {
            value: false,
            raw_hex: None,
        },
    );
    snapshot.canonicalize()?;
    Ok(snapshot)
}

fn external_header_snapshot() -> Result<SemanticSnapshot, Box<dyn std::error::Error>> {
    let mut snapshot = valid_snapshot()?;
    let memo = TypedValue::Memo {
        value: "memo".into(),
        raw_hex: Some(HexString::new("6d656d6f")?),
        code_page: Some(1252),
    };
    let ole = TypedValue::Ole {
        value: HexString::new("01")?,
        raw_hex: Some(HexString::new("01")?),
    };
    let zero_key = Sha256::new("00".repeat(32))?;
    let table = &mut snapshot.tables[0];
    table.columns = vec![
        SemanticColumn {
            name: "Memo".into(),
            ordinal: 0,
            dao_type: "dbMemo".into(),
            auto_increment: false,
            size: Some(0),
            attributes: 2,
            properties: PropertyMap::new(),
        },
        SemanticColumn {
            name: "Memo/Part~Name".into(),
            ordinal: 1,
            dao_type: "dbLongBinary".into(),
            auto_increment: false,
            size: Some(0),
            attributes: 2,
            properties: PropertyMap::new(),
        },
    ];
    table.indexes.clear();
    table.rows = [0, 1]
        .map(|duplicate_ordinal| SemanticRow {
            canonical_key: zero_key.clone(),
            duplicate_ordinal,
            values: PropertyMap::from([
                ("Memo".into(), memo.clone()),
                ("Memo/Part~Name".into(), ole.clone()),
            ]),
        })
        .into();
    let mut second_table = table.clone();
    second_table.name = "Other".into();
    snapshot.tables.push(second_table);
    snapshot.relationships.clear();
    snapshot.raw_preservation.clear();
    snapshot.producer_extensions.clear();
    snapshot.canonicalize()?;
    Ok(snapshot)
}

fn apply_semantic_name_mutation(
    snapshot: &mut SemanticSnapshot,
    mutation: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    match mutation {
        "none" => {}
        "table_name" => snapshot.tables[0].name.clear(),
        "column_name" => {
            snapshot.tables[0].columns[0].name.clear();
            snapshot.tables[0].indexes[0].fields[0].name.clear();
            snapshot.relationships[0].fields[0].field.clear();
            snapshot.relationships[0].fields[0].foreign_field.clear();
            let value = snapshot.tables[0].rows[0]
                .values
                .pop_first()
                .ok_or_else(|| test_error("expected row value"))?
                .1;
            snapshot.tables[0].rows[0]
                .values
                .insert(String::new(), value);
        }
        "index_name" => snapshot.tables[0].indexes[0].name.clear(),
        "index_field" => snapshot.tables[0].indexes[0].fields[0].name.clear(),
        "relationship_name" => snapshot.relationships[0].name.clear(),
        "relationship_table" => snapshot.relationships[0].table.clear(),
        "relationship_foreign_table" => snapshot.relationships[0].foreign_table.clear(),
        "relationship_field" => snapshot.relationships[0].fields[0].field.clear(),
        "relationship_foreign_field" => {
            snapshot.relationships[0].fields[0].foreign_field.clear();
        }
        "database_property_name" => {
            snapshot.database_properties.insert(
                String::new(),
                TypedValue::Boolean {
                    value: true,
                    raw_hex: None,
                },
            );
        }
        "raw_semantic_path" => snapshot.raw_preservation[0].semantic_path.clear(),
        "raw_purpose" => snapshot.raw_preservation[0].purpose.clear(),
        _ => return Err(test_error("unknown semantic-name mutation").into()),
    }
    Ok(())
}

#[test]
fn shared_semantic_name_vectors_match_full_rust_validation()
-> Result<(), Box<dyn std::error::Error>> {
    let mut seen = 0;
    for (line_index, line) in SEMANTIC_NAME_FIXTURES
        .lines()
        .enumerate()
        .filter(|(_, line)| !line.starts_with('#'))
    {
        let fields: Vec<_> = line.split('\t').collect();
        let [case, mutation, validation_layers, expected_valid] = fields.as_slice() else {
            return Err(test_error("invalid semantic-name fixture shape").into());
        };
        if !matches!(*validation_layers, "schema_and_semantic" | "semantic_only") {
            return Err(test_error("unknown semantic-name validation layers").into());
        }
        let mut snapshot = valid_snapshot()?;
        apply_semantic_name_mutation(&mut snapshot, mutation)?;
        let actual_valid = snapshot
            .canonicalize()
            .and_then(|()| snapshot.to_canonical_json())
            .is_ok();
        assert_eq!(
            actual_valid,
            expected_valid.parse::<bool>()?,
            "{case} on line {}",
            line_index + 1
        );
        seen += 1;
    }
    assert_eq!(seen, 13);
    Ok(())
}

#[test]
fn shared_producer_extension_vector_normalizes_same_named_semantic_fields()
-> Result<(), Box<dyn std::error::Error>> {
    let mut seen = 0;
    for (line_index, line) in PRODUCER_EXTENSION_NORMALIZATION_FIXTURES
        .lines()
        .enumerate()
        .filter(|(_, line)| !line.starts_with('#'))
    {
        let fields: Vec<_> = line.split('\t').collect();
        let [
            case,
            semantic_key,
            bits_hex,
            input_json,
            canonical_json,
            opaque_json,
        ] = fields.as_slice()
        else {
            return Err(test_error("invalid producer-extension fixture shape").into());
        };
        let bits = u32::from_str_radix(bits_hex, 16)?;
        let value = TypedValue::Single {
            value: FiniteF32::new(f32::from_bits(bits))?,
            raw_hex: Some(HexString::new(*bits_hex)?),
        };
        let mut snapshot = valid_snapshot()?;
        snapshot.tables[0].columns.push(SemanticColumn {
            name: (*semantic_key).into(),
            ordinal: 1,
            dao_type: "dbSingle".into(),
            auto_increment: false,
            size: Some(4),
            attributes: 1,
            properties: PropertyMap::from([((*semantic_key).into(), value.clone())]),
        });
        snapshot.tables[0].rows[0]
            .values
            .insert((*semantic_key).into(), value);
        snapshot.canonicalize()?;

        let rendered = String::from_utf8(snapshot.to_canonical_json()?)?;
        let expected = format!(
            "\"{semantic_key}\":{{\"kind\":\"single\",\"raw_hex\":\"{bits_hex}\",\"value\":{canonical_json}}}"
        );
        assert_eq!(rendered.matches(&expected).count(), 2, "{case}");
        assert_eq!(*input_json, "0.1", "{case}");
        assert!(opaque_json.contains("\"value\":0.1"), "{case}");
        assert!(!opaque_json.contains(canonical_json), "{case}");
        seen += 1;
        assert_eq!(line_index + 1, 2);
    }
    assert_eq!(seen, 1);
    Ok(())
}

#[test]
fn shared_producer_extension_paths_have_canonical_unique_targets()
-> Result<(), Box<dyn std::error::Error>> {
    let header = HexString::new("0102030405060708090a0b0c")?;
    let mut seen = 0;
    for (line_index, line) in PRODUCER_EXTENSION_PATH_FIXTURES
        .lines()
        .enumerate()
        .filter(|(_, line)| !line.starts_with('#'))
    {
        let fields: Vec<_> = line.split('\t').collect();
        let [case, paths, expected_valid] = fields.as_slice() else {
            return Err(test_error("invalid producer-extension path fixture shape").into());
        };
        let mut snapshot = external_header_snapshot()?;
        for path in paths.split('|') {
            snapshot.producer_extensions.insert(
                path.into(),
                TypedValue::Binary {
                    value: header.clone(),
                    raw_hex: Some(header.clone()),
                },
            );
        }
        assert_eq!(
            snapshot.to_canonical_json().is_ok(),
            expected_valid.parse::<bool>()?,
            "{case} on line {}",
            line_index + 1
        );
        seen += 1;
    }
    assert_eq!(seen, 9);
    Ok(())
}

#[test]
fn direct_construction_cannot_bypass_schema_and_reference_validation()
-> Result<(), Box<dyn std::error::Error>> {
    assert!(valid_snapshot()?.to_canonical_json().is_ok());

    let mut duplicate = valid_snapshot()?;
    duplicate.tables.push(duplicate.tables[0].clone());
    assert!(duplicate.to_canonical_json().is_err());

    let mut ordinal = valid_snapshot()?;
    ordinal.tables[0].columns[0].ordinal = 1;
    assert!(ordinal.to_canonical_json().is_err());

    let mut normalization = valid_snapshot()?;
    normalization.tables[0].columns[0].attributes = 3;
    assert!(normalization.to_canonical_json().is_err());

    let mut index_reference = valid_snapshot()?;
    index_reference.tables[0].indexes[0].fields[0].name = "Missing".into();
    assert!(index_reference.to_canonical_json().is_err());

    let mut invalid_primary = valid_snapshot()?;
    invalid_primary.tables[0].indexes[0].unique = false;
    assert!(invalid_primary.to_canonical_json().is_err());

    let mut relationship_reference = valid_snapshot()?;
    relationship_reference.relationships[0].foreign_table = "Missing".into();
    assert!(relationship_reference.to_canonical_json().is_err());
    Ok(())
}

#[test]
fn direct_construction_cannot_bypass_row_and_nested_value_validation()
-> Result<(), Box<dyn std::error::Error>> {
    let mut missing_column = valid_snapshot()?;
    missing_column.tables[0].rows[0].values.clear();
    assert!(missing_column.to_canonical_json().is_err());

    let mut wrong_kind = valid_snapshot()?;
    wrong_kind.tables[0].rows[0].values.insert(
        "Id".into(),
        TypedValue::Text {
            value: "1".into(),
            raw_hex: Some(HexString::new("31")?),
            code_page: Some(1252),
        },
    );
    assert!(wrong_kind.to_canonical_json().is_err());

    let mut missing_raw = valid_snapshot()?;
    missing_raw.database_properties.insert("P".into(), long(1)?);
    let Some(TypedValue::Long { raw_hex, .. }) = missing_raw.database_properties.get_mut("P")
    else {
        return Err(test_error("expected long database property P").into());
    };
    *raw_hex = None;
    assert!(missing_raw.to_canonical_json().is_err());

    let mut wrong_width = valid_snapshot()?;
    let Some(TypedValue::Long { raw_hex, .. }) = wrong_width.tables[0].rows[0].values.get_mut("Id")
    else {
        return Err(test_error("expected long row value Id").into());
    };
    *raw_hex = Some(HexString::new("01")?);
    assert!(wrong_width.to_canonical_json().is_err());

    let mut wrong_key = valid_snapshot()?;
    wrong_key.tables[0].rows[0].canonical_key = Sha256::new("ff".repeat(32))?;
    assert!(wrong_key.to_canonical_json().is_err());
    Ok(())
}

#[test]
fn shared_source_revision_vectors_match_producer_and_receipt_validation()
-> Result<(), Box<dyn std::error::Error>> {
    let mut seen = 0;
    for (line_index, line) in SOURCE_REVISION_FIXTURES
        .lines()
        .enumerate()
        .filter(|(_, line)| !line.starts_with('#'))
    {
        let fields: Vec<_> = line.split('\t').collect();
        let [case, scalar, repetitions, expected_valid] = fields.as_slice() else {
            return Err(test_error("invalid source-revision fixture shape").into());
        };
        if scalar.chars().count() != 1 || scalar.len() == 1 {
            return Err(test_error("source-revision fixture scalar must be multibyte").into());
        }
        let source_revision = scalar.repeat(repetitions.parse()?);
        let expected_valid = expected_valid.parse::<bool>()?;
        let producer_valid = Producer::new(ProducerKind::Rust, source_revision.clone()).is_ok();
        let receipt_valid = CoverageReceipt {
            scenario_id: ScenarioId::new("DAO-READ-ROWS-SINGLE")?,
            source_revision,
            database_sha256: Sha256::new("ab".repeat(32))?,
            outcome: CoverageReceiptOutcome::Success {
                allocated_set_sha256: Sha256::new("cd".repeat(32))?,
            },
            branches: CoverageBranches::new(),
        }
        .to_canonical_json()
        .is_ok();
        assert_eq!(
            producer_valid,
            expected_valid,
            "producer {case} on line {}",
            line_index + 1
        );
        assert_eq!(
            receipt_valid,
            expected_valid,
            "receipt {case} on line {}",
            line_index + 1
        );
        seen += 1;
    }
    assert_eq!(seen, 2);
    Ok(())
}

#[test]
fn shared_text_code_page_vectors_match_full_rust_validation()
-> Result<(), Box<dyn std::error::Error>> {
    let mut seen = 0;
    for (line_index, line) in TEXT_CODE_PAGE_FIXTURES
        .lines()
        .enumerate()
        .filter(|(_, line)| !line.starts_with('#'))
    {
        let fields: Vec<_> = line.split('\t').collect();
        if fields.len() != 6 {
            return Err(test_error("invalid text code-page fixture shape").into());
        }
        let [case, kind, code_page, raw_hex, value, expected_valid] = fields.as_slice() else {
            return Err(test_error("invalid text code-page fixture shape").into());
        };
        let expected_valid = expected_valid.parse::<bool>()?;
        let actual_valid = match (code_page.parse::<u32>(), HexString::new(*raw_hex)) {
            (Ok(code_page), Ok(raw_hex)) => {
                let typed = match *kind {
                    "text" => TypedValue::Text {
                        value: (*value).into(),
                        raw_hex: Some(raw_hex),
                        code_page: Some(code_page),
                    },
                    "memo" => TypedValue::Memo {
                        value: (*value).into(),
                        raw_hex: Some(raw_hex),
                        code_page: Some(code_page),
                    },
                    _ => return Err(test_error("unknown text code-page fixture kind").into()),
                };
                let mut snapshot = valid_snapshot()?;
                snapshot
                    .database_properties
                    .insert("TextVector".into(), typed);
                snapshot.to_canonical_json().is_ok()
            }
            (Err(_), _) | (_, Err(_)) => false,
        };
        assert_eq!(
            actual_valid,
            expected_valid,
            "{case} on line {}",
            line_index + 1
        );
        seen += 1;
    }
    assert_eq!(seen, 8);
    Ok(())
}

#[test]
fn shared_relationship_field_uniqueness_vectors_match_full_rust_validation()
-> Result<(), Box<dyn std::error::Error>> {
    let mut seen = 0;
    for (line_index, line) in RELATIONSHIP_FIELD_UNIQUENESS_FIXTURES
        .lines()
        .enumerate()
        .filter(|(_, line)| !line.starts_with('#'))
    {
        let fields: Vec<_> = line.split('\t').collect();
        let [case, field, foreign_field, expected_valid] = fields.as_slice() else {
            return Err(test_error("invalid relationship-field fixture shape").into());
        };
        let mut snapshot = valid_snapshot()?;
        snapshot.tables[0].rows.clear();
        snapshot.tables[0].columns.push(SemanticColumn {
            name: "Flag".into(),
            ordinal: 1,
            dao_type: "dbBoolean".into(),
            auto_increment: false,
            size: Some(1),
            attributes: 1,
            properties: PropertyMap::new(),
        });
        snapshot.relationships[0].fields.push(RelationshipField {
            field: (*field).into(),
            foreign_field: (*foreign_field).into(),
        });
        assert_eq!(
            snapshot.to_canonical_json().is_ok(),
            expected_valid.parse::<bool>()?,
            "{case} on line {}",
            line_index + 1
        );
        seen += 1;
    }
    assert_eq!(seen, 2);
    Ok(())
}

#[test]
fn direct_construction_cannot_bypass_extension_and_raw_preservation_validation()
-> Result<(), Box<dyn std::error::Error>> {
    let mut pointer = valid_snapshot()?;
    let value = pointer
        .producer_extensions
        .pop_first()
        .ok_or_else(|| test_error("expected producer extension"))?
        .1;
    pointer
        .producer_extensions
        .insert("not/a/pointer".into(), value);
    assert!(pointer.to_canonical_json().is_err());

    let mut raw_path = valid_snapshot()?;
    raw_path.raw_preservation[0].semantic_path.clear();
    assert!(raw_path.to_canonical_json().is_err());

    let mut raw_purpose = valid_snapshot()?;
    raw_purpose.raw_preservation[0].purpose.clear();
    assert!(raw_purpose.to_canonical_json().is_err());
    Ok(())
}
