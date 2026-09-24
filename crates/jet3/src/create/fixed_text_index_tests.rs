use super::initial_index_tests::*;
use crate::{
    ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexKind, IndexSpec, PageNumber,
    RowValue, TableSpec, create::api_tests::*, create_database_with_rows,
    definition::column_writer::nz,
};
use std::fs;

#[test]
fn fixed_text_indexes_enforce_collisions_and_follow_public_mutations() -> TestResult {
    for width in [1, 8, 255] {
        let directory = TestDirectory::create()?;
        let padded = |first: u8| {
            let mut bytes = vec![b' '; usize::from(width)];
            bytes[0] = first;
            bytes
        };
        let (a, b, lower_b, c, d, e) = (
            padded(b'A'),
            padded(b'B'),
            padded(b'b'),
            padded(b'C'),
            padded(b'D'),
            padded(b'E'),
        );
        let payload = [b'm'; 80];
        let columns = [
            ID,
            ColumnSpec::new(b"Code", ColumnType::FixedText { len: nz(width) }),
            ColumnSpec::new(b"Body", ColumnType::Memo),
        ];
        let indexes = [
            IndexSpec {
                name: b"ById",
                fields: &ID_FIELD,
                kind: IndexKind::Primary,
            },
            IndexSpec {
                name: b"ByCode",
                fields: &[IndexColumnSpec::descending(1)],
                kind: IndexKind::Unique,
            },
            IndexSpec {
                name: b"ByPair",
                fields: &[
                    IndexColumnSpec::ascending(1),
                    IndexColumnSpec::descending(0),
                ],
                kind: IndexKind::Ordinary,
            },
        ];
        let table = TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Items",
            columns: &columns,
            indexes: &indexes,
        };
        create_database_with_rows(
            directory.target(),
            &table,
            &[
                &[
                    RowValue::Long(1),
                    RowValue::Text(&a),
                    RowValue::Memo(&payload),
                ],
                &[
                    RowValue::Long(2),
                    RowValue::Text(&b),
                    RowValue::Memo(&payload),
                ],
                &[RowValue::Long(3), RowValue::Null, RowValue::Memo(&payload)],
            ],
            &mut budget(),
        )?;
        let locators = {
            let mut work = budget();
            let mut db = DatabaseReader::open(directory.target(), &mut work)?;
            let definition = db.table_definition(PageNumber::new(20), &mut work)?;
            let mut cursor = db.rows(&definition, &mut work)?;
            let mut locators = Vec::new();
            while let Some(row) = cursor.next_row()? {
                locators.push(row.locator());
            }
            locators
        };
        let original = fs::read(directory.target())?;
        // EXP-0264: case folding and fixed padding produce the same unique key.
        for action in 0..3 {
            let result = match action {
                0 => crate::update_field(
                    directory.target(),
                    crate::FieldUpdate {
                        table: b"Items",
                        row: locators[0],
                        column: crate::ColumnOrdinal::new(1),
                        value: RowValue::Text(&lower_b),
                    },
                    &mut budget(),
                ),
                1 => crate::update_row(
                    directory.target(),
                    crate::RowUpdate {
                        table: b"Items",
                        row: locators[2],
                        values: &[
                            RowValue::Long(3),
                            RowValue::Text(&lower_b),
                            RowValue::Memo(&payload),
                        ],
                    },
                    &mut budget(),
                ),
                _ => crate::insert_row(
                    directory.target(),
                    b"Items",
                    &[
                        RowValue::Long(4),
                        RowValue::Text(&lower_b),
                        RowValue::Memo(&payload),
                    ],
                    &mut budget(),
                )
                .map(|_| ()),
            };
            assert!(matches!(
                result,
                Err(crate::UpdateError::Unsupported("duplicate unique key"))
            ));
            assert_eq!(fs::read(directory.target())?, original);
        }
        crate::update_field(
            directory.target(),
            crate::FieldUpdate {
                table: b"Items",
                row: locators[0],
                column: crate::ColumnOrdinal::new(1),
                value: RowValue::Text(&c),
            },
            &mut budget(),
        )?;
        crate::update_row(
            directory.target(),
            crate::RowUpdate {
                table: b"Items",
                row: locators[2],
                values: &[
                    RowValue::Long(3),
                    RowValue::Text(&d),
                    RowValue::Memo(&payload),
                ],
            },
            &mut budget(),
        )?;
        crate::insert_row(
            directory.target(),
            b"Items",
            &[
                RowValue::Long(4),
                RowValue::Text(&e),
                RowValue::Memo(&payload),
            ],
            &mut budget(),
        )?;
        crate::delete_row(
            directory.target(),
            crate::RowDelete {
                table: b"Items",
                row: locators[1],
            },
            &mut budget(),
        )?;
        let mut work = budget();
        let mut db = DatabaseReader::open(directory.target(), &mut work)?;
        let report = db.validate(crate::TextCodePage::Windows1252, &mut work)?;
        assert_eq!(report.indexes_with_verified_keys, 10);
        assert_eq!(report.uninterpreted_indexes, 0);
        assert_eq!(report.index_entries, 45);
        let definition = db.table_definition(PageNumber::new(20), &mut work)?;
        let root = definition.physical_indexes()[1].root().get() as usize * crate::PAGE_BYTES;
        let mut cursor = db.rows(&definition, &mut work)?;
        let mut observed = Vec::new();
        while let Some(row) = cursor.next_row()? {
            observed.push(
                row.field(crate::ColumnOrdinal::new(1))
                    .and_then(|v| v.raw_bytes())
                    .ok_or("fixed Code")?
                    .to_vec(),
            );
        }
        assert_eq!(observed, [c, d, e]);
        drop(cursor);
        drop(db);
        let mut broken = fs::read(directory.target())?;
        // EXP-0062: corrupt the first fixed-Text key while retaining leaf framing.
        broken[root + 248] = 0x12;
        fs::write(directory.target(), broken)?;
        let mut work = budget();
        let mut db = DatabaseReader::open(directory.target(), &mut work)?;
        assert!(matches!(
            db.validate(crate::TextCodePage::Windows1252, &mut work),
            Err(crate::ValidationError::Table {
                source: crate::TableValidationError::IndexContents { index: 1, .. },
                ..
            })
        ));
    }
    Ok(())
}
