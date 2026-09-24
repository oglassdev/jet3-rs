use super::mutation_tests::*;
use crate::*;
use std::fs;

#[test]
fn empty_ole_insert_and_replacement_match_null_storage_and_preserve_neighbors() -> TestResult {
    let empty = Fixture::new(true)?;
    let null = Fixture::new(true)?;
    let original = empty.snapshot()?;
    for (fixture, value) in [(&empty, RowValue::LongBinary(b"")), (&null, RowValue::Null)] {
        insert_row(
            fixture.path(),
            b"Rows",
            &[
                RowValue::Long(3),
                RowValue::Long(2),
                RowValue::Memo(b"inserted"),
                value,
            ],
            &mut budget(),
        )?;
        // Replace the chained value; the neighboring OLE and Memo remain live.
        update_row(
            fixture.path(),
            RowUpdate {
                table: b"Rows",
                row: original[&2].locator,
                values: &[
                    RowValue::Long(2),
                    RowValue::Long(3),
                    RowValue::Memo(&[b'b'; 33]),
                    value,
                ],
            },
            &mut budget(),
        )?;
        fixture.validate()?;
    }
    assert_eq!(fs::read(empty.path())?, fs::read(null.path())?);
    let saved = empty.snapshot()?;
    assert_eq!(saved[&1], original[&1]);
    assert_eq!(saved[&2].payloads, [Some(vec![b'b'; 33]), None]);
    assert_eq!(saved[&3].payloads, [Some(b"inserted".to_vec()), None]);
    Ok(())
}
