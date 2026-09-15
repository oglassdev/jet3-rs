use super::*;
use crate::{ColumnOrdinal, RawField, RowValue, SliceSource, create_database_with_rows};

#[test]
fn fixed_schema_capacity_includes_the_presence_map() -> TestResult {
    for (count, total) in [(8, 2000), (16, 1999)] {
        let directory = TestDirectory::create()?;
        let names: Vec<_> = (0..count).map(|i| format!("F{i:02}")).collect();
        let widths: Vec<_> = (0..count)
            .map(|i| (total - 4) / count + usize::from(i < (total - 4) % count))
            .collect();
        let mut columns = vec![ID];
        columns.extend(names.iter().zip(&widths).map(|(name, &width)| {
            ColumnSpec::new(
                name.as_bytes(),
                ColumnType::FixedText {
                    len: nz(width as u8),
                },
            )
        }));
        let payloads: Vec<_> = widths.iter().map(|&width| vec![b'X'; width]).collect();
        let mut values = vec![RowValue::Long(1)];
        values.extend(payloads.iter().map(|bytes| RowValue::Text(bytes)));
        let spec = TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &[],
        };
        create_database_with_rows(directory.target(), &spec, &[&values], &mut budget())?;
        let original = fs::read(directory.target())?;
        {
            let mut work = budget();
            let mut db = DatabaseReader::open(directory.target(), &mut work)?;
            let definition = db.table_definition(PageNumber::new(20), &mut work)?;
            let mut rows = db.rows(&definition, &mut work)?;
            let row = rows.next_row()?.ok_or("missing fixed row")?;
            assert_eq!(row.raw_bytes().len(), 2003);
            for (i, payload) in payloads.iter().enumerate() {
                assert_eq!(
                    row.field(ColumnOrdinal::new(i as u16 + 1)),
                    Some(RawField::Bytes(payload))
                );
            }
        }
        columns[1] = ColumnSpec::new(
            names[0].as_bytes(),
            ColumnType::FixedText {
                len: nz(widths[0] as u8 + 1),
            },
        );
        let invalid = TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &[],
        };
        assert!(matches!(
            create_database(directory.target(), &[invalid], &mut budget()),
            Err(CreateDatabaseError::Compose(ComposeError::Schema(
                TableSchemaPlanError::Definition(
                    crate::TableDefinitionWriteError::RowLayoutTooLarge {
                        minimum: 2004,
                        maximum: 2003
                    }
                )
            )))
        ));
        assert_eq!(fs::read(directory.target())?, original);
    }
    Ok(())
}

#[test]
fn all_variable_rows_disambiguate_the_final_boundary_and_reject_bad_trailers() -> TestResult {
    // EXP-0258: ff means both an unused threshold and boundary ordinal 255.
    let directory = TestDirectory::create()?;
    let names: Vec<_> = (0..255).map(|i| format!("V{i:03}")).collect();
    let columns: Vec<_> = names
        .iter()
        .map(|name| ColumnSpec::new(name.as_bytes(), ColumnType::Text { max_len: nz(255) }))
        .collect();
    let cases: [(usize, usize, &[u8]); 8] = [
        (5, 295, &[255]),
        (255, 546, &[255, 255]),
        (256, 547, &[255, 255]),
        (257, 548, &[255, 255]),
        (511, 803, &[255, 255, 128]),
        (512, 804, &[255, 255, 128]),
        (513, 805, &[255, 255, 128]),
        (768, 1061, &[255, 255, 201, 128]),
    ];
    let payloads: Vec<_> = cases
        .iter()
        .map(|&(end, _, _)| {
            let mut fields = vec![Vec::new(); 255];
            fields[0] = b"r001".to_vec();
            let mut used = 5;
            for index in [127, 200] {
                if end - used > 255 {
                    fields[index] = vec![b'M'; 255];
                    used += 255;
                }
            }
            fields[254] = vec![b'Z'; end - used];
            fields
        })
        .collect();
    let values: Vec<Vec<_>> = payloads
        .iter()
        .map(|fields| {
            fields
                .iter()
                .map(|p| {
                    if p.is_empty() {
                        RowValue::Null
                    } else {
                        RowValue::Text(p)
                    }
                })
                .collect()
        })
        .collect();
    let rows: Vec<_> = values.iter().map(Vec::as_slice).collect();
    create_database_with_rows(
        directory.target(),
        &TableSpec {
            name: b"Items",
            columns: &columns,
            indexes: &[],
        },
        &rows,
        &mut budget(),
    )?;
    let bytes = fs::read(directory.target())?;
    let mut work = budget();
    let mut db =
        DatabaseReader::from_source(SliceSource::new(&bytes, work.read_budget())?, &mut work)?;
    let definition = db.table_definition(PageNumber::new(20), &mut work)?;
    let mut cursor = db.rows(&definition, &mut work)?;
    let mut damaged_at = None;
    for (fields, &(end, length, jumps)) in payloads.iter().zip(&cases) {
        let row = cursor.next_row()?.ok_or("missing all-variable row")?;
        assert_eq!(row.raw_bytes().len(), length);
        assert_eq!(&row.raw_bytes()[end + 256..end + 256 + jumps.len()], jumps);
        for (ordinal, payload) in fields.iter().enumerate() {
            assert_eq!(
                row.field(ColumnOrdinal::new(ordinal as u16)),
                Some(if payload.is_empty() {
                    RawField::Null
                } else {
                    RawField::Bytes(payload)
                })
            );
        }
        if end == 256 {
            let locator = row.storage_locator();
            let page = locator.page().get() as usize * crate::PAGE_BYTES;
            let slot = page + 10 + usize::from(locator.slot()) * 2;
            let start = usize::from(u16::from_le_bytes([bytes[slot], bytes[slot + 1]]) & 0x07ff);
            damaged_at = Some(page + start + end);
        }
    }
    assert!(cursor.next_row()?.is_none());
    let start = damaged_at.ok_or("missing boundary row")?;
    for offset in [0, 257] {
        let mut damaged = bytes.clone();
        damaged[start + offset] ^= 1;
        let mut work = budget();
        let mut db = DatabaseReader::from_source(
            SliceSource::new(&damaged, work.read_budget())?,
            &mut work,
        )?;
        let mut cursor = db.rows(&definition, &mut work)?;
        let error = loop {
            match cursor.next_row() {
                Ok(Some(_)) => {}
                Ok(None) => break None,
                Err(error) => break Some(error),
            }
        };
        assert!(error.is_some());
    }
    Ok(())
}
