use super::mutation_tests::*;
use crate::*;
use std::fs;

fn descriptor(fixture: &Fixture, column: u16) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let table = fixture.definition()?;
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let mut rows = db.rows(&table, &mut work)?;
    let row = rows.next_row()?.ok_or("row")?;
    Ok(row
        .field(ColumnOrdinal::new(column))
        .and_then(|value| value.raw_bytes())
        .ok_or("descriptor")?
        .to_vec())
}

#[test]
fn field_updates_retain_unassigned_long_value_descriptors_and_payloads() -> TestResult {
    let fixture = Fixture::with_auto(true, true)?;
    let initial = fixture.snapshot()?;
    let row = initial[&1].locator;
    let ole = descriptor(&fixture, 3)?;
    for (column, value, payload) in [
        (1, RowValue::Null, None),
        (1, RowValue::Long(7), None),
        (2, RowValue::Memo(&[b'x'; 8192]), Some(vec![b'x'; 8192])),
        (2, RowValue::Memo(b"short"), Some(b"short".to_vec())),
        (2, RowValue::Null, None),
    ] {
        update_field(
            fixture.path(),
            FieldUpdate {
                table: b"Rows",
                row,
                column: ColumnOrdinal::new(column),
                value,
            },
            &mut budget(),
        )?;
        let actual = fixture.snapshot()?;
        assert_eq!(actual[&2], initial[&2]);
        assert_eq!(actual[&1].locator, row);
        assert_eq!(actual[&1].payloads[1], initial[&1].payloads[1]);
        assert_eq!(descriptor(&fixture, 3)?, ole);
        if column == 2 {
            assert_eq!(actual[&1].payloads[0], payload);
        }
        fixture.validate()?;
    }
    update_field(
        fixture.path(),
        FieldUpdate {
            table: b"Rows",
            row,
            column: ColumnOrdinal::new(3),
            value: RowValue::LongBinary(&[0x55; 4096]),
        },
        &mut budget(),
    )?;
    let actual = fixture.snapshot()?;
    assert_eq!(actual[&1].payloads, [None, Some(vec![0x55; 4096])]);
    assert_eq!(actual[&2], initial[&2]);
    let before = fs::read(fixture.path())?;
    for column in [2, 3] {
        assert!(
            update_field(
                fixture.path(),
                FieldUpdate {
                    table: b"Rows",
                    row,
                    column: ColumnOrdinal::new(column),
                    value: RowValue::LongValue(&[0; 12]),
                },
                &mut budget()
            )
            .is_err()
        );
        assert_eq!(fs::read(fixture.path())?, before);
    }
    fixture.validate()
}
