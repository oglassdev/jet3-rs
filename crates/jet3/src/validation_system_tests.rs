use super::*;

#[test]
fn system_row_counts_layouts_and_index_values_are_checked() -> TestResult {
    let original = fixture()?;
    let table = definition(&original, b"MSysACEs")?;
    let mut work = budget();
    let mut database = open(&original, &mut work)?;
    let mut cursor = database.rows(&table, &mut work)?;
    let row = cursor.next_row()?.ok_or("missing ACE")?;
    let start = page_start(row.storage_locator().page()) + PAGE_BYTES - row.raw_bytes().len();
    assert_eq!(row.storage_locator().slot(), 0);
    for defect in 0..3 {
        let mut changed = original.clone();
        match defect {
            0 => {
                let offset = page_start(table.root()) + 12;
                changed[offset..offset + 4].copy_from_slice(&(table.row_count() + 1).to_le_bytes());
            }
            1 => changed[start] = 0,
            _ => {
                // EXP-0062/0073: the first system Long key begins at byte 248.
                let index = page_start(table.physical_indexes()[0].root());
                changed[index + 252] ^= 1;
            }
        }
        let error = validate(&changed)?
            .err()
            .ok_or("accepted corrupt system table")?;
        let ValidationError::Table { table, source } = error else {
            return Err("missing system table context".into());
        };
        assert_eq!(table.name().raw_bytes(), b"MSysACEs");
        assert_eq!(table.class(), CatalogObjectClass::System);
        assert!(match defect {
            0 => matches!(source, TableValidationError::RowCount { .. }),
            1 => matches!(source, TableValidationError::Rows { .. }),
            _ => matches!(source, TableValidationError::IndexContents { index: 0, .. }),
        });
    }
    Ok(())
}

#[test]
fn catalog_property_payloads_are_traversed_and_owned() -> TestResult {
    let columns = [ColumnSpec::new(b"Body", ColumnType::Memo).with_allow_zero_length()];
    let plan = compose_database_with_table_rows(
        &[TableRows {
            table: TableSpec {
                name: b"Items",
                columns: &columns,
                indexes: &[],
            },
            rows: &[],
        }],
        &mut budget(),
    )?;
    let original: Vec<_> = plan
        .pages()
        .iter()
        .flat_map(|p| p.image().as_bytes())
        .copied()
        .collect();
    let report = validate(&original)??;
    assert_eq!(report.system_tables, 4);
    assert_eq!(report.long_values, 1);
    assert!(report.long_value_bytes > 0);
    let table = definition(&original, b"MSysObjects")?;
    let column = table
        .columns()
        .iter()
        .find(|c| c.name().raw_bytes() == b"LvProp")
        .ok_or("missing LvProp")?
        .ordinal();
    let mut work = budget();
    let mut database = open(&original, &mut work)?;
    let mut cursor = database.rows(&table, &mut work)?;
    let mut target = None;
    while let Some(mut row) = cursor.next_row()? {
        let value = row
            .value(column, TextCodePage::Windows1252)?
            .ok_or("missing property value")?;
        if let ValueKind::LongValue(LongValue::External(reference)) = value.kind() {
            target = Some(reference.target());
        }
    }
    let target = target.ok_or("missing property payload")?;
    let mut changed = original;
    // EXP-0061: every external catalog property uses the same LVAL owner framing.
    changed[page_start(target.page()) + 4] ^= 1;
    assert!(
        matches!(validate(&changed)?, Err(ValidationError::Table { table, source:
        TableValidationError::LongValue { column: actual, .. }
    }) if table.name().raw_bytes() == b"MSysObjects" && actual == column)
    );
    Ok(())
}
