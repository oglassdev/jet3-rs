use super::memo_option_tests::*;
use crate::{
    ColumnOrdinal, ColumnSpec, ColumnType, DatabaseReader, IndexSpec, PageNumber, ResourceBudget,
    ResourceLimits, RowValue, TableSpec,
    create::{api::*, api_tests::TestDirectory, composer::compose_database_with_table_rows},
};
use std::error::Error as StdError;
use std::fs;

#[test]
fn empty_options_are_per_column_on_later_indexed_tables() -> Result<(), Box<dyn StdError>> {
    let directory = TestDirectory::create()?;
    let path = directory.path.join("options.mdb");
    let width = std::num::NonZeroU8::new(8).ok_or("width")?;
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Text Value", ColumnType::Text { max_len: width })
            .with_allow_zero_length(),
        ColumnSpec::new(b"Memo_Value", ColumnType::Memo).with_allow_zero_length(),
        ColumnSpec::new(b"DefaultMemo", ColumnType::Memo),
        ColumnSpec::new(b"Ole", ColumnType::LongBinary),
    ];
    let indexes = [
        IndexSpec {
            name: b"ById",
            fields: &[crate::IndexColumnSpec::ascending(0)],
            kind: crate::IndexKind::Primary,
        },
        IndexSpec {
            name: b"ByText",
            fields: &[crate::IndexColumnSpec::descending(1)],
            kind: crate::IndexKind::Ordinary,
        },
    ];
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Items",
        columns: &columns,
        indexes: &indexes,
    };
    create_database_with_table_rows(
        &path,
        &[
            TableRows {
                table: TableSpec {
                    validation: crate::TableValidation::NONE,
                    name: b"Anchor",
                    columns: &[ColumnSpec::new(b"Id", ColumnType::Long)],
                    indexes: &[],
                },
                rows: &[&[RowValue::Long(9)]],
            },
            TableRows {
                table,
                rows: &[
                    &[
                        RowValue::Long(1),
                        RowValue::Null,
                        RowValue::Null,
                        RowValue::Null,
                        RowValue::Null,
                    ],
                    &[
                        RowValue::Long(2),
                        RowValue::Text(b""),
                        RowValue::Memo(b""),
                        RowValue::Null,
                        RowValue::LongBinary(b""),
                    ],
                ],
            },
        ],
        &mut budget(),
    )?;
    let locator = {
        let mut work = budget();
        let mut db = DatabaseReader::open(&path, &mut work)?;
        let table = crate::write::update::indexed_writable_table(&mut db, b"Items", &mut work)?;
        let mut rows = db.rows(&table, &mut work)?;
        rows.next_row()?.ok_or("first row")?.locator()
    };
    let original = fs::read(&path)?;
    assert!(
        crate::update_row(
            &path,
            crate::RowUpdate {
                table: b"Items",
                row: locator,
                values: &[
                    RowValue::Long(1),
                    RowValue::Text(b""),
                    RowValue::Memo(b""),
                    RowValue::Memo(b""),
                    RowValue::Null,
                ],
            },
            &mut budget()
        )
        .is_err()
    );
    assert_eq!(fs::read(&path)?, original);
    crate::update_row(
        &path,
        crate::RowUpdate {
            table: b"Items",
            row: locator,
            values: &[
                RowValue::Long(1),
                RowValue::Text(b""),
                RowValue::Memo(b""),
                RowValue::Memo(b"kept"),
                RowValue::LongBinary(b""),
            ],
        },
        &mut budget(),
    )?;
    crate::insert_row(
        &path,
        b"Items",
        &[
            RowValue::Long(3),
            RowValue::Text(b""),
            RowValue::Memo(b""),
            RowValue::Null,
            RowValue::LongBinary(b""),
        ],
        &mut budget(),
    )?;
    let mut work = budget();
    let mut db = DatabaseReader::open(&path, &mut work)?;
    let report = db.validate(crate::TextCodePage::Windows1252, &mut work)?;
    assert_eq!(report.indexes_with_verified_keys, 9);
    assert_eq!(report.index_entries, 46);
    let table = crate::write::update::indexed_writable_table(&mut db, b"Items", &mut work)?;
    let mut rows = db.rows(&table, &mut work)?;
    let mut count = 0;
    while let Some(mut row) = rows.next_row()? {
        assert_eq!(
            row.field(ColumnOrdinal::new(1)).ok_or("Text")?.raw_bytes(),
            Some(b"".as_slice())
        );
        assert!(
            matches!(row.value(ColumnOrdinal::new(2), crate::TextCodePage::Windows1252)?.ok_or("Memo")?.kind(),
            crate::ValueKind::LongValue(crate::LongValue::Inline { value: crate::InlineLongValue::Text(text), .. }) if text.raw_bytes().is_empty())
        );
        assert_eq!(
            row.field(ColumnOrdinal::new(4)).ok_or("Ole")?.raw_bytes(),
            None
        );
        count += 1;
    }
    assert_eq!(count, 3);
    Ok(())
}

#[test]
fn malformed_properties_and_disabled_empty_values_preserve_input() -> Result<(), Box<dyn StdError>>
{
    let directory = TestDirectory::create()?;
    let path = directory.path.join("properties.mdb");
    let fields = columns(b"M");
    create_database_with_rows(
        &path,
        &TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Rows",
            columns: &fields,
            indexes: &[],
        },
        &[&[RowValue::Long(1), RowValue::Memo(b"kept")]],
        &mut budget(),
    )?;
    let original = fs::read(&path)?;
    // EXP-0208: the first property page contains the named 91-byte payload.
    let page = 22 * crate::PAGE_BYTES;
    let start = page
        + usize::from(u16::from_le_bytes([
            original[page + 10],
            original[page + 11],
        ]));
    for (offset, value) in [(0, b'X'), (8, 0), (60, 11), (66, 6), (81, 1), (81, 0)] {
        let mut changed = original.clone();
        changed[start + offset] = value;
        fs::write(&path, &changed)?;
        let error = crate::insert_row(
            &path,
            b"Rows",
            &[RowValue::Long(2), RowValue::Memo(b"")],
            &mut budget(),
        )
        .err()
        .ok_or("invalid property accepted")?;
        if offset == 81 && value == 0 {
            assert!(matches!(
                error,
                crate::UpdateError::Encoding(crate::RowWriteError::ZeroLengthNotAllowed {
                    ordinal: 1,
                    ..
                })
            ));
        } else {
            assert!(matches!(
                error,
                crate::UpdateError::ColumnProperties(crate::ColumnPropertyError::Invalid(_))
            ));
        }
        assert_eq!(fs::read(&path)?, changed);
    }
    fs::write(&path, &original)?;
    let mut work = budget();
    let mut db = DatabaseReader::open(&path, &mut work)?;
    let catalog = db.table_definition(PageNumber::new(2), &mut work)?;
    let maps = catalog
        .long_value_maps()
        .iter()
        .find(|map| map.column() == ColumnOrdinal::new(14))
        .ok_or("LvProp maps")?;
    let mut bit_offsets = Vec::new();
    for locator in [
        maps.owned(),
        maps.available(),
        crate::MapRowLocator::new(PageNumber::new(1), 0),
    ] {
        let bits = crate::alloc::mutation_map::MapBits::load(&mut db, locator, &mut work)?;
        let span = bits.spans.first().ok_or("inline span")?;
        let bit = 22 - span.first;
        bit_offsets.push((
            span.page.get() as usize * crate::PAGE_BYTES + span.offset + bit as usize / 8,
            1_u8 << (bit % 8),
        ));
    }
    drop(db);
    for case in 0..3 {
        let mut changed = original.clone();
        if case < 2 {
            let (offset, mask) = bit_offsets[0];
            changed[offset] &= !mask;
            if case == 1 {
                let (offset, mask) = bit_offsets[1];
                changed[offset] &= !mask;
            }
        } else {
            let (offset, mask) = bit_offsets[2];
            changed[offset] |= mask;
        }
        fs::write(&path, &changed)?;
        assert!(matches!(
            crate::insert_row(
                &path,
                b"Rows",
                &[RowValue::Long(2), RowValue::Memo(b"")],
                &mut budget()
            ),
            Err(crate::UpdateError::ColumnProperties(
                crate::ColumnPropertyError::Invalid(_)
            ))
        ));
        assert_eq!(fs::read(&path)?, changed);
    }
    let mut work = budget();
    let mut db = DatabaseReader::open(&path, &mut work)?;
    let definition = db.table_definition(PageNumber::new(20), &mut work)?;
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_total_work_units(0));
    assert!(matches!(
        crate::properties::reader::decode(
            &original[start..start + 91],
            definition.columns(),
            &mut limited
        ),
        Err(crate::ColumnPropertyError::Resource(
            crate::Error::ResourceLimitExceeded { .. }
        ))
    ));
    Ok(())
}

#[test]
fn chained_properties_keep_options_independent() -> Result<(), Box<dyn StdError>> {
    let directory = TestDirectory::create()?;
    let path = directory.path.join("chained.mdb");
    let names = (0..26)
        .map(|i| format!("Field{i:02}_{}", "x".repeat(56)))
        .collect::<Vec<_>>();
    let mut fields = vec![ColumnSpec::new(b"Id", ColumnType::Long)];
    let width = std::num::NonZeroU8::new(8).ok_or("width")?;
    for (i, name) in names.iter().enumerate() {
        let field = ColumnSpec::new(name.as_bytes(), ColumnType::Text { max_len: width });
        let field = if i % 3 == 0 {
            field.with_required()
        } else {
            field
        };
        fields.push(if i % 2 == 0 {
            field.with_allow_zero_length()
        } else {
            field
        });
    }
    let mut initial = vec![RowValue::Long(1)];
    initial.extend((0..26).map(|i| RowValue::Text(if i % 2 == 0 { b"" } else { b"value" })));
    create_database_with_rows(
        &path,
        &TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Rows",
            columns: &fields,
            indexes: &[],
        },
        &[&initial],
        &mut budget(),
    )?;
    let mut work = budget();
    let mut db = DatabaseReader::open(&path, &mut work)?;
    let catalog = db.table_definition(PageNumber::new(2), &mut work)?;
    let mut records = db.rows(&catalog, &mut work)?;
    let mut chained = false;
    while let Some(mut row) = records.next_row()? {
        if row
            .field(ColumnOrdinal::new(0))
            .and_then(|field| field.raw_bytes())
            == Some(20_i32.to_le_bytes().as_slice())
        {
            chained = matches!(row.value(ColumnOrdinal::new(14), crate::TextCodePage::Windows1252)?.ok_or("LvProp")?.kind(),
                crate::ValueKind::LongValue(crate::LongValue::External(reference)) if reference.storage() == crate::ExternalLongValueStorage::Chained);
        }
    }
    assert!(chained);
    drop(records);
    drop(db);
    initial[0] = RowValue::Long(2);
    crate::insert_row(&path, b"Rows", &initial, &mut budget())?;
    let original = fs::read(&path)?;
    initial[0] = RowValue::Long(3);
    initial[2] = RowValue::Text(b"");
    assert!(matches!(
        crate::insert_row(&path, b"Rows", &initial, &mut budget()),
        Err(crate::UpdateError::Encoding(
            crate::RowWriteError::ZeroLengthNotAllowed { ordinal: 2, .. }
        ))
    ));
    assert_eq!(fs::read(&path)?, original);
    initial[1] = RowValue::Null;
    initial[2] = RowValue::Text(b"value");
    assert!(matches!(
        crate::insert_row(&path, b"Rows", &initial, &mut budget()),
        Err(crate::UpdateError::Encoding(
            crate::RowWriteError::RequiredValueMissing { ordinal: 1, .. }
        ))
    ));
    assert_eq!(fs::read(&path)?, original);
    Ok(())
}

#[test]
fn text_only_properties_are_checked_before_publication() -> Result<(), Box<dyn StdError>> {
    let directory = TestDirectory::create()?;
    let path = directory.path.join("text.mdb");
    let columns = [ColumnSpec::new(
        b"Text",
        ColumnType::Text {
            max_len: std::num::NonZeroU8::new(8).ok_or("width")?,
        },
    )
    .with_allow_zero_length()];
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Rows",
        columns: &columns,
        indexes: &[],
    };
    let rows: &[&[RowValue<'_>]] = &[&[RowValue::Text(b"")]];
    create_database_with_rows(&path, &table, rows, &mut budget())?;
    let pages =
        compose_database_with_table_rows(&[TableRows { table, rows }], &mut budget())?.into_pages();
    let mut changed = fs::read(&path)?;
    changed[23 * crate::PAGE_BYTES - 1] ^= 1;
    fs::write(&path, &changed)?;
    assert!(matches!(
        check_long_value_written_pages(&path, &[table], &pages, &mut budget()),
        Err(ImageCheckError::Mismatch { .. })
    ));

    // A Text-only target must reject a property fragment also claimed as table data.
    create_database_with_table_rows(
        directory.path.join("alias.mdb"),
        &[
            TableRows { table, rows },
            TableRows {
                table: TableSpec {
                    validation: crate::TableValidation::NONE,
                    name: b"Other",
                    columns: &[ColumnSpec::new(b"Id", ColumnType::Long)],
                    indexes: &[],
                },
                rows: &[&[RowValue::Long(1)]],
            },
        ],
        &mut budget(),
    )?;
    let alias = directory.path.join("alias.mdb");
    let mut work = budget();
    let mut db = DatabaseReader::open(&alias, &mut work)?;
    let definition = crate::write::update::indexed_writable_table(&mut db, b"Other", &mut work)?;
    let map =
        crate::alloc::mutation_map::MapBits::load(&mut db, definition.maps().owned(), &mut work)?;
    let span = map.spans.first().ok_or("table map")?;
    let bit = 22 - span.first;
    let offset = span.page.get() as usize * crate::PAGE_BYTES + span.offset + bit as usize / 8;
    drop(db);
    let mut changed = fs::read(&alias)?;
    changed[offset] |= 1 << (bit % 8);
    fs::write(&alias, &changed)?;
    assert!(matches!(
        crate::insert_row(&alias, b"Rows", &[RowValue::Text(b"")], &mut budget()),
        Err(crate::UpdateError::ColumnProperties(
            crate::ColumnPropertyError::Invalid("property page belongs to another object")
        ))
    ));
    assert_eq!(fs::read(&alias)?, changed);

    let names = (0..22)
        .map(|i| format!("Field{i:02}_{}", "x".repeat(56)))
        .collect::<Vec<_>>();
    let columns = names
        .iter()
        .map(|name| {
            ColumnSpec::new(
                name.as_bytes(),
                ColumnType::Text {
                    max_len: std::num::NonZeroU8::MIN,
                },
            )
            .with_allow_zero_length()
        })
        .collect::<Vec<_>>();
    let table = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Chained",
        columns: &columns,
        indexes: &[],
    };
    let plan = crate::create::schema_plan::plan_table_schema(
        &table,
        20,
        true,
        &mut crate::ResourceBudget::new(crate::ResourceLimits::default()),
    )?;
    assert_eq!(plan.continuation_page(), None);
    assert_eq!(plan.property_page_count(), 2);
    let mut limited = ResourceBudget::new(ResourceLimits::default().with_max_chain_depth(1));
    let destination = directory.path.join("limited.mdb");
    assert!(create_database(&destination, &[table], &mut limited).is_err());
    assert!(!destination.exists());
    Ok(())
}
