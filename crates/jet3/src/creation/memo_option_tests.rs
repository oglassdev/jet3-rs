use super::super::*;
use super::TestDirectory;
use crate::{ByteCount, ColumnOrdinal, ColumnSpec, ColumnType, ResourceLimits, RowValue};
use std::fs;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn columns(name: &[u8]) -> [ColumnSpec<'_>; 2] {
    [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(name, ColumnType::Memo).with_allow_zero_length(),
    ]
}

#[test]
fn memo_property_encoder_matches_observed_named_block() -> Result<(), Box<dyn StdError>> {
    let property = crate::memo_property::MemoProperty::new(b"M").ok_or("name")?;
    let mut output = [0; crate::memo_property::MAX_PAYLOAD];
    let n = property.encode(&mut output, &mut budget())?;
    let expected = "4b4b4400210000008000080052657175697265640f00416c6c6f775a65726f4c656e67746817000000010008000000020049640900010100000100001f00000001000700000001004d0900010101000100ff090001010000010000";
    let decoded: Result<Vec<_>, _> = expected
        .as_bytes()
        .chunks_exact(2)
        .map(|pair| {
            std::str::from_utf8(pair)
                .map_err(|_| "hex")
                .and_then(|v| u8::from_str_radix(v, 16).map_err(|_| "hex"))
        })
        .collect();
    assert_eq!(&output[..n], decoded?.as_slice());
    assert!(property.encode(&mut [0; 90], &mut budget()).is_err());
    let limited = ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(0));
    assert!(
        property
            .encode(&mut output, &mut ResourceBudget::new(limited))
            .is_err()
    );
    for invalid in [b"".as_slice(), b"bad name", &[b'x'; 65], &[0xff]] {
        assert!(crate::memo_property::MemoProperty::new(invalid).is_none());
    }
    Ok(())
}

#[test]
fn memo_option_publishes_distinct_empty_null_and_nonempty() -> Result<(), Box<dyn StdError>> {
    let dir = TestDirectory::create()?;
    for name in [b"M".as_slice(), b"Memo42Long"] {
        let path = dir.path.join(std::str::from_utf8(name)?);
        let columns = columns(name);
        let table = TableSpec {
            name: b"Rows",
            columns: &columns,
            indexes: &[],
        };
        let rows: [&[RowValue<'_>]; 3] = [
            &[RowValue::Long(1), RowValue::Null],
            &[RowValue::Long(2), RowValue::Memo(b"")],
            &[RowValue::Long(3), RowValue::Memo(b"A")],
        ];
        create_database_with_rows(&path, &table, &rows, &mut budget())?;
        let mut b = budget();
        let mut db = DatabaseReader::open(&path, &mut b)?;
        {
            let catalog = db.table_definition(PageNumber::new(2), &mut b)?;
            let mut records = db.rows(&catalog, &mut b)?;
            let mut found = false;
            while let Some(record) = records.next_row()? {
                if record
                    .field(ColumnOrdinal::new(0))
                    .and_then(|v| v.raw_bytes())
                    == Some(20_i32.to_le_bytes().as_slice())
                {
                    let expected = [90 + name.len() as u8, 0, 0, 64, 0, 22, 0, 0, 0, 0, 0, 0];
                    assert_eq!(
                        record
                            .field(ColumnOrdinal::new(14))
                            .ok_or("LvProp")?
                            .raw_bytes(),
                        Some(expected.as_slice())
                    );
                    found = true;
                }
            }
            assert!(found);
        }
        let definition = db.table_definition(PageNumber::new(20), &mut b)?;
        let mut cursor = db.rows(&definition, &mut b)?;
        let null = cursor.next_row()?.ok_or("null")?;
        assert_eq!(
            null.field(ColumnOrdinal::new(1))
                .ok_or("field")?
                .raw_bytes(),
            None
        );
        let empty = cursor.next_row()?.ok_or("empty")?;
        assert_eq!(
            empty
                .field(ColumnOrdinal::new(1))
                .ok_or("field")?
                .raw_bytes(),
            Some([0, 0, 0, 128, 0, 0, 0, 0, 0, 0, 0, 0].as_slice())
        );
        let value = cursor.next_row()?.ok_or("value")?;
        assert_eq!(
            value
                .field(ColumnOrdinal::new(1))
                .ok_or("field")?
                .raw_bytes(),
            Some([1, 0, 0, 128, 0, 0, 0, 0, 0, 0, 0, 0, 65].as_slice())
        );
        drop(cursor);
        drop(db);
        let original = fs::read(&path)?;
        assert!(create_database_with_rows(&path, &table, &rows, &mut budget()).is_err());
        assert_eq!(fs::read(&path)?, original);
        let pages =
            compose_database_with_table_rows(&[TableRows { table, rows: &rows }], &mut budget())?
                .into_pages();
        let mut changed = original;
        changed[22 * crate::PAGE_BYTES + 2047] ^= 1;
        fs::write(&path, changed)?;
        assert!(matches!(
            check_memo_written_pages(&path, &[table], &pages, &mut budget()),
            Err(CandidateCheckError::Mismatch { .. })
        ));
    }
    Ok(())
}

#[test]
fn memo_option_refuses_unimplemented_schema_and_default_empty() -> Result<(), Box<dyn StdError>> {
    let dir = TestDirectory::create()?;
    let invalids = [
        [
            ColumnSpec::new(b"Id", ColumnType::Long).with_allow_zero_length(),
            ColumnSpec::new(b"M", ColumnType::Memo),
        ],
        [
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"M", ColumnType::LongBinary).with_allow_zero_length(),
        ],
        [
            ColumnSpec::new(b"Other", ColumnType::Long),
            ColumnSpec::new(b"M", ColumnType::Memo).with_allow_zero_length(),
        ],
        columns(b"bad name"),
    ];
    for (n, columns) in invalids.iter().enumerate() {
        let path = dir.path.join(n.to_string());
        let table = TableSpec {
            name: b"Rows",
            columns,
            indexes: &[],
        };
        assert!(matches!(
            create_database(&path, &[table], &mut budget()),
            Err(CreateDatabaseError::Compose(
                ComposeError::UnsupportedMemoOption
            ))
        ));
        assert!(!path.exists());
    }
    let ordinary = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"M", ColumnType::Memo),
    ];
    let table = TableSpec {
        name: b"Rows",
        columns: &ordinary,
        indexes: &[],
    };
    assert!(
        create_database_with_rows(
            dir.path.join("default"),
            &table,
            &[&[RowValue::Long(1), RowValue::Memo(b"")]],
            &mut budget()
        )
        .is_err()
    );
    let opted = columns(b"M");
    let later = TableSpec {
        name: b"Later",
        columns: &opted,
        indexes: &[],
    };
    assert!(matches!(
        create_database(dir.path.join("later"), &[table, later], &mut budget()),
        Err(CreateDatabaseError::Compose(
            ComposeError::UnsupportedMemoOption
        ))
    ));
    Ok(())
}
