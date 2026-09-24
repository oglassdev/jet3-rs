use super::update_tests::*;
use crate::{
    ColumnOrdinal, ColumnSpec, ColumnStorageClass, ColumnType, DatabaseReader, PAGE_BYTES,
    PublishStage, RowValue, TextCodePage, write::update::*,
};
use std::fs;
use std::num::NonZeroU8;

#[test]
fn single_field_rewrites_preserve_unassigned_bytes_and_values() -> TestResult {
    let width = NonZeroU8::new(255).ok_or("width")?;
    let fixture = Fixture::new(
        &[
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(b"Padding", ColumnType::Long),
            ColumnSpec::new(b"Flag", ColumnType::Boolean),
            ColumnSpec::new(b"Text", ColumnType::Text { max_len: width }).with_allow_zero_length(),
            ColumnSpec::new(b"Binary", ColumnType::Binary { max_len: width }),
        ],
        &[&[
            RowValue::Long(1),
            RowValue::Null,
            RowValue::Boolean(false),
            RowValue::Text(b"before"),
            RowValue::Binary(b"binary"),
        ]],
    )?;
    let locator = fixture.locator(0)?;
    let mut work = budget();
    let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
    let table = guarded_table(&mut db, b"Items", true, &mut work)?;
    let ColumnStorageClass::Fixed { offset } = table.columns()[1].storage() else {
        return Err("fixed padding".into());
    };
    let padding = 1 + usize::from(offset)..5 + usize::from(offset);
    let mut original = fs::read(fixture.path())?;
    let page = locator.page().get() as usize * PAGE_BYTES;
    let slot = page + 10 + 2 * usize::from(locator.slot());
    let start =
        page + usize::from(u16::from_le_bytes(original[slot..slot + 2].try_into()?) & 0x1fff);
    original[start + padding.start..start + padding.end].copy_from_slice(&[0xa5; 4]);
    drop(db);
    fs::write(fixture.path(), &original)?;
    let mut expected = [
        RowValue::Long(1),
        RowValue::Null,
        RowValue::Boolean(false),
        RowValue::Text(b"before"),
        RowValue::Binary(b"binary"),
    ];
    for (column, value) in [
        (0, RowValue::Null),
        (0, RowValue::Long(2)),
        (2, RowValue::Boolean(true)),
        (2, RowValue::Null),
        (3, RowValue::Text(&[b'x'; 255])),
        (4, RowValue::Binary(&[0x81; 255])),
        (3, RowValue::Text(b"")),
        (4, RowValue::Null),
    ] {
        update_field(
            fixture.path(),
            FieldUpdate {
                column: ColumnOrdinal::new(column),
                ..request(locator, value)
            },
            &mut budget(),
        )?;
        expected[usize::from(column)] = if column == 2 && matches!(value, RowValue::Null) {
            RowValue::Boolean(false)
        } else {
            value
        };
        let mut work = budget();
        let mut db = DatabaseReader::open(fixture.path(), &mut work)?;
        let table = guarded_table(&mut db, b"Items", true, &mut work)?;
        {
            let mut rows = db.rows(&table, &mut work)?;
            let mut row = rows.next_row()?.ok_or("row")?;
            assert_eq!(row.locator(), locator);
            assert_eq!(&row.raw_bytes()[padding.clone()], &[0xa5; 4]);
            for (ordinal, value) in expected.iter().enumerate() {
                let actual = crate::row::scalar_values::read_column(
                    &mut row,
                    ColumnOrdinal::new(ordinal as u16),
                )?;
                assert_eq!(actual, *value);
            }
        }
        db.validate(TextCodePage::Windows1252, &mut work)?;
    }
    fixture.assert_only_original()
}

#[test]
fn field_rewrites_enforce_column_options_and_preserve_failed_publications() -> TestResult {
    let fixture = Fixture::new(
        &[ColumnSpec::new(
            b"Value",
            ColumnType::Text {
                max_len: NonZeroU8::new(32).ok_or("width")?,
            },
        )
        .with_required()],
        &[&[RowValue::Text(b"before")]],
    )?;
    let row = fixture.locator(0)?;
    let original = fs::read(fixture.path())?;
    for value in [
        RowValue::Null,
        RowValue::Text(b""),
        RowValue::Long(1),
        RowValue::LongValue(&[0; 12]),
    ] {
        assert!(update_field(fixture.path(), request(row, value), &mut budget()).is_err());
        assert_eq!(fs::read(fixture.path())?, original);
    }
    let result = update_with_hook(
        &fixture.path(),
        request(row, RowValue::Text(b"after")),
        &mut budget(),
        |stage| {
            if stage == PublishStage::PrePublish {
                Err(std::io::Error::other("injected"))
            } else {
                Ok(())
            }
        },
    );
    assert!(
        matches!(result, Err(UpdateError::Publish(error)) if error.stage() == PublishStage::PrePublish)
    );
    assert_eq!(fs::read(fixture.path())?, original);
    fixture.assert_only_original()
}
