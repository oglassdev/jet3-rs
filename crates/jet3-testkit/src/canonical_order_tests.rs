use super::{
    CanonicalSnapshot, Column, HexString, Index, IndexField, Producer, ProducerKind, PropertyMap,
    RawPreservation, Relationship, RelationshipField, Row, ScenarioId, Sha256, SnapshotError,
    Table, TableKind, TypedValue,
};

fn empty_snapshot() -> Result<CanonicalSnapshot, SnapshotError> {
    Ok(CanonicalSnapshot::new(
        ScenarioId::new("DAO-READ-ORDER-001")?,
        Producer::new(ProducerKind::Rust, "order-test")?,
        Sha256::new("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")?,
    ))
}

fn column(name: &str, ordinal: u64) -> Column {
    Column {
        name: name.to_owned(),
        ordinal,
        dao_type: "Text".to_owned(),
        nullable: true,
        required: false,
        auto_increment: false,
        size: Some(255),
        attributes: 0,
        properties: PropertyMap::new(),
    }
}

fn index(name: &str) -> Index {
    Index {
        name: name.to_owned(),
        primary: false,
        unique: false,
        required: false,
        ignore_nulls: false,
        fields: vec![IndexField {
            name: "field".to_owned(),
            descending: false,
        }],
        properties: PropertyMap::new(),
    }
}

fn table(name: &str) -> Table {
    Table {
        name: name.to_owned(),
        kind: TableKind::User,
        attributes: 0,
        columns: Vec::new(),
        indexes: Vec::new(),
        properties: PropertyMap::new(),
        rows: Vec::new(),
    }
}

fn relationship(name: &str) -> Relationship {
    Relationship {
        name: name.to_owned(),
        table: "parent".to_owned(),
        foreign_table: "child".to_owned(),
        attributes: 0,
        fields: vec![RelationshipField {
            field: "id".to_owned(),
            foreign_field: "parent_id".to_owned(),
        }],
        properties: PropertyMap::new(),
    }
}

#[test]
fn strict_emission_rejects_every_unsorted_declared_sequence() -> Result<(), SnapshotError> {
    let mut tables = empty_snapshot()?;
    tables.tables = vec![table("zeta"), table("alpha")];
    assert_eq!(
        tables.to_canonical_json(),
        Err(SnapshotError::NonCanonicalOrder {
            path: "$.tables".to_owned()
        })
    );

    let mut columns = empty_snapshot()?;
    let mut value = table("table");
    value.columns = vec![column("second", 1), column("first", 0)];
    columns.tables.push(value);
    assert_eq!(
        columns.to_canonical_json(),
        Err(SnapshotError::NonCanonicalOrder {
            path: "$.tables[0].columns".to_owned()
        })
    );

    let mut indexes = empty_snapshot()?;
    let mut value = table("table");
    value.indexes = vec![index("zeta"), index("alpha")];
    indexes.tables.push(value);
    assert_eq!(
        indexes.to_canonical_json(),
        Err(SnapshotError::NonCanonicalOrder {
            path: "$.tables[0].indexes".to_owned()
        })
    );

    let mut rows = empty_snapshot()?;
    let mut value = table("table");
    value.rows = vec![
        Row {
            canonical_key: "zeta".to_owned(),
            values: PropertyMap::new(),
        },
        Row {
            canonical_key: "alpha".to_owned(),
            values: PropertyMap::new(),
        },
    ];
    rows.tables.push(value);
    assert_eq!(
        rows.to_canonical_json(),
        Err(SnapshotError::NonCanonicalOrder {
            path: "$.tables[0].rows".to_owned()
        })
    );

    let mut relationships = empty_snapshot()?;
    relationships.relationships = vec![relationship("zeta"), relationship("alpha")];
    assert_eq!(
        relationships.to_canonical_json(),
        Err(SnapshotError::NonCanonicalOrder {
            path: "$.relationships".to_owned()
        })
    );
    Ok(())
}

#[test]
fn explicit_canonicalization_sorts_all_declared_sequences() -> Result<(), SnapshotError> {
    let mut snapshot = empty_snapshot()?;
    let mut zeta = table("zeta");
    zeta.columns = vec![column("second", 1), column("first", 0)];
    zeta.indexes = vec![index("zeta"), index("alpha")];
    zeta.rows = vec![
        Row {
            canonical_key: "zeta".to_owned(),
            values: PropertyMap::new(),
        },
        Row {
            canonical_key: "alpha".to_owned(),
            values: PropertyMap::new(),
        },
    ];
    snapshot.tables = vec![zeta, table("alpha")];
    snapshot.relationships = vec![relationship("zeta"), relationship("alpha")];

    let canonical = snapshot.to_canonicalized_json()?;
    assert!(snapshot.to_canonical_json().is_err());
    snapshot.canonicalize()?;
    assert_eq!(snapshot.to_canonical_json()?, canonical);
    assert_eq!(
        snapshot
            .tables
            .iter()
            .map(|value| value.name.as_str())
            .collect::<Vec<_>>(),
        ["alpha", "zeta"]
    );
    assert_eq!(
        snapshot.tables[1]
            .columns
            .iter()
            .map(|value| value.ordinal)
            .collect::<Vec<_>>(),
        [0, 1]
    );
    assert_eq!(
        snapshot.tables[1]
            .indexes
            .iter()
            .map(|value| value.name.as_str())
            .collect::<Vec<_>>(),
        ["alpha", "zeta"]
    );
    assert_eq!(
        snapshot.tables[1]
            .rows
            .iter()
            .map(|value| value.canonical_key.as_str())
            .collect::<Vec<_>>(),
        ["alpha", "zeta"]
    );
    assert_eq!(
        snapshot
            .relationships
            .iter()
            .map(|value| value.name.as_str())
            .collect::<Vec<_>>(),
        ["alpha", "zeta"]
    );
    Ok(())
}

#[test]
fn canonicalization_rejects_duplicate_identities() -> Result<(), SnapshotError> {
    let mut duplicate_tables = empty_snapshot()?;
    duplicate_tables.tables = vec![table("same"), table("same")];
    assert!(matches!(
        duplicate_tables.canonicalize(),
        Err(SnapshotError::Duplicate { path, value })
            if path == "$.tables" && value == "same"
    ));

    let mut duplicate_ordinals = empty_snapshot()?;
    let mut value = table("table");
    value.columns = vec![column("first", 0), column("second", 0)];
    duplicate_ordinals.tables.push(value);
    assert!(matches!(
        duplicate_ordinals.canonicalize(),
        Err(SnapshotError::Duplicate { path, value })
            if path == "$.tables[0].columns" && value == "0"
    ));

    let mut duplicate_column_names = empty_snapshot()?;
    let mut value = table("table");
    value.columns = vec![column("same", 0), column("same", 1)];
    duplicate_column_names.tables.push(value);
    assert!(matches!(
        duplicate_column_names.canonicalize(),
        Err(SnapshotError::Duplicate { path, value })
            if path == "$.tables[0].columns" && value == "same"
    ));

    let mut duplicate_indexes = empty_snapshot()?;
    let mut value = table("table");
    value.indexes = vec![index("same"), index("same")];
    duplicate_indexes.tables.push(value);
    assert!(matches!(
        duplicate_indexes.canonicalize(),
        Err(SnapshotError::Duplicate { path, value })
            if path == "$.tables[0].indexes" && value == "same"
    ));

    let mut duplicate_relationships = empty_snapshot()?;
    duplicate_relationships.relationships = vec![relationship("same"), relationship("same")];
    assert!(matches!(
        duplicate_relationships.canonicalize(),
        Err(SnapshotError::Duplicate { path, value })
            if path == "$.relationships" && value == "same"
    ));
    Ok(())
}

#[test]
fn duplicate_row_keys_use_values_as_a_tiebreaker() -> Result<(), SnapshotError> {
    let mut snapshot = empty_snapshot()?;
    let mut value = table("table");
    let mut low_values = PropertyMap::new();
    low_values.insert(
        "value".to_owned(),
        TypedValue::Long {
            value: 1,
            raw_hex: None,
        },
    );
    let mut high_values = PropertyMap::new();
    high_values.insert(
        "value".to_owned(),
        TypedValue::Long {
            value: 2,
            raw_hex: None,
        },
    );
    value.rows = vec![
        Row {
            canonical_key: "same".to_owned(),
            values: high_values,
        },
        Row {
            canonical_key: "same".to_owned(),
            values: low_values.clone(),
        },
        Row {
            canonical_key: "same".to_owned(),
            values: low_values,
        },
    ];
    snapshot.tables.push(value);

    assert_eq!(
        snapshot.to_canonical_json(),
        Err(SnapshotError::NonCanonicalOrder {
            path: "$.tables[0].rows".to_owned()
        })
    );
    snapshot.canonicalize()?;
    let rows = &snapshot.tables[0].rows;
    assert_eq!(rows.len(), 3);
    assert_eq!(rows[0], rows[1]);
    assert_ne!(rows[1], rows[2]);
    snapshot.to_canonical_json()?;
    Ok(())
}

#[test]
fn object_keys_use_unicode_codepoint_order() -> Result<(), SnapshotError> {
    let mut snapshot = empty_snapshot()?;
    for key in ["😀", "é", "a"] {
        snapshot.database_properties.insert(
            key.to_owned(),
            TypedValue::Text {
                value: key.to_owned(),
                raw_hex: None,
                code_page: None,
            },
        );
    }
    let json = String::from_utf8_lossy(&snapshot.to_canonical_json()?).into_owned();
    let ascii = json.find("\"a\":");
    let accented = json.find("\"é\":");
    let supplementary = json.find("\"😀\":");
    assert!(ascii < accented);
    assert!(accented < supplementary);
    Ok(())
}

#[test]
fn declared_field_pair_order_and_raw_preservation_order_are_retained() -> Result<(), SnapshotError>
{
    let mut snapshot = empty_snapshot()?;
    let mut value = table("table");
    let mut value_index = index("index");
    value_index.fields = vec![
        IndexField {
            name: "second".to_owned(),
            descending: true,
        },
        IndexField {
            name: "first".to_owned(),
            descending: false,
        },
    ];
    value.indexes.push(value_index);
    snapshot.tables.push(value);
    let mut relation = relationship("relation");
    relation.fields = vec![
        RelationshipField {
            field: "second".to_owned(),
            foreign_field: "foreign_second".to_owned(),
        },
        RelationshipField {
            field: "first".to_owned(),
            foreign_field: "foreign_first".to_owned(),
        },
    ];
    snapshot.relationships.push(relation);
    snapshot.raw_preservation = vec![
        RawPreservation {
            semantic_path: "zeta".to_owned(),
            raw_hex: HexString::new("00")?,
            purpose: "first retained".to_owned(),
        },
        RawPreservation {
            semantic_path: "alpha".to_owned(),
            raw_hex: HexString::new("ff")?,
            purpose: "second retained".to_owned(),
        },
    ];

    let json = String::from_utf8_lossy(&snapshot.to_canonical_json()?).into_owned();
    assert!(json.find("\"name\":\"second\"") < json.find("\"name\":\"first\""));
    assert!(json.find("\"field\":\"second\"") < json.find("\"field\":\"first\""));
    assert!(json.find("\"semantic_path\":\"zeta\"") < json.find("\"semantic_path\":\"alpha\""));
    Ok(())
}

#[test]
fn required_nested_shapes_fail_closed() -> Result<(), SnapshotError> {
    let mut empty_dao_type = empty_snapshot()?;
    let mut value = table("table");
    value.columns.push(column("column", 0));
    value.columns[0].dao_type.clear();
    empty_dao_type.tables.push(value);
    assert_eq!(
        empty_dao_type.to_canonical_json(),
        Err(SnapshotError::EmptyString {
            path: "$.tables[0].columns[0].dao_type".to_owned()
        })
    );

    let mut empty_index_fields = empty_snapshot()?;
    let mut value = table("table");
    let mut value_index = index("index");
    value_index.fields.clear();
    value.indexes.push(value_index);
    empty_index_fields.tables.push(value);
    assert_eq!(
        empty_index_fields.to_canonical_json(),
        Err(SnapshotError::EmptyCollection {
            path: "$.tables[0].indexes[0].fields".to_owned()
        })
    );

    let mut empty_relationship_fields = empty_snapshot()?;
    let mut value = relationship("relationship");
    value.fields.clear();
    empty_relationship_fields.relationships.push(value);
    assert_eq!(
        empty_relationship_fields.to_canonical_json(),
        Err(SnapshotError::EmptyCollection {
            path: "$.relationships[0].fields".to_owned()
        })
    );

    let mut empty_raw_path = empty_snapshot()?;
    empty_raw_path.raw_preservation.push(RawPreservation {
        semantic_path: String::new(),
        raw_hex: HexString::new("")?,
        purpose: "purpose".to_owned(),
    });
    assert_eq!(
        empty_raw_path.to_canonical_json(),
        Err(SnapshotError::EmptyString {
            path: "$.raw_preservation[0].semantic_path".to_owned()
        })
    );

    let mut empty_raw_purpose = empty_snapshot()?;
    empty_raw_purpose.raw_preservation.push(RawPreservation {
        semantic_path: "path".to_owned(),
        raw_hex: HexString::new("")?,
        purpose: String::new(),
    });
    assert_eq!(
        empty_raw_purpose.to_canonical_json(),
        Err(SnapshotError::EmptyString {
            path: "$.raw_preservation[0].purpose".to_owned()
        })
    );
    Ok(())
}
