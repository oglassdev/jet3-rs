use super::*;
use crate::creation::schema_plan::plan_table_schema;
use crate::{ColumnOrdinal, ResourceLimits, SliceSource, TableRows, TextCodePage, ValueKind};

type TestResult = Result<(), Box<dyn std::error::Error>>;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

fn columns(names: &[Vec<u8>]) -> Vec<ColumnSpec<'_>> {
    names
        .iter()
        .map(|n| ColumnSpec::new(n, ColumnType::Long))
        .collect()
}

fn names(length: usize) -> Vec<Vec<u8>> {
    let count = ((length - 45) / 24).min(96);
    let mut names = (0..count)
        .map(|n| format!("C{n:04}").into_bytes())
        .collect::<Vec<_>>();
    for n in 0..length - 45 - count * 24 {
        names[n % count].push(b'x');
    }
    names
}

fn bytes(requests: &[TableRows<'_>]) -> Result<Vec<u8>, ComposeError> {
    let plan = compose_database_with_table_rows(requests, &mut budget())?;
    Ok(plan
        .pages()
        .iter()
        .flat_map(|p| p.image().as_bytes().iter().copied())
        .collect())
}

#[test]
fn exact_definition_boundaries_reassemble_without_padding_or_missing_columns() -> TestResult {
    for length in [2048, 2049, 4088, 4089, 6128, 6129] {
        let names = names(length);
        let columns = columns(&names);
        let spec = TableSpec {
            validation: crate::TableValidation::NONE,
            name: b"Wide",
            columns: &columns,
            indexes: &[],
        };
        let row = vec![RowValue::Long(17); columns.len()];
        let data = bytes(&[TableRows {
            table: spec,
            rows: &[&row],
        }])?;
        let plan = plan_table_schema(
            &spec,
            20,
            true,
            &mut crate::ResourceBudget::new(crate::ResourceLimits::default()),
        )?;
        assert_eq!(plan.definition_len(), length);
        let mut logical = data[20 * PAGE_BYTES..21 * PAGE_BYTES].to_vec();
        let mut next = u32::from_le_bytes(logical[4..8].try_into()?);
        let mut chain = Vec::new();
        while next != 0 {
            chain.push(next);
            let page = &data[next as usize * PAGE_BYTES..(next as usize + 1) * PAGE_BYTES];
            assert_eq!(&page[..4], &logical[..4]);
            logical.extend_from_slice(&page[8..]);
            next = u32::from_le_bytes(page[4..8].try_into()?);
        }
        assert_eq!(
            chain,
            (23..23 + crate::creation::schema_plan::continuation_count(length) as u32)
                .collect::<Vec<_>>()
        );
        assert!(logical[length..].iter().all(|b| *b == 0));
        let mut b = budget();
        let source = SliceSource::new(&data, b.read_budget())?;
        let mut db = crate::DatabaseReader::from_source(source, &mut b)?;
        let definition = db.table_definition(PageNumber::new(20), &mut b)?;
        assert_eq!(definition.columns().len(), columns.len());
        assert_eq!(definition.row_count(), 1);
        for (actual, name) in definition.columns().iter().zip(&names) {
            assert_eq!(actual.name().raw_bytes(), name);
        }
        let mut cursor = db.rows(&definition, &mut b)?;
        let mut row = cursor.next_row()?.ok_or("missing wide row")?;
        assert_eq!(row.locator().page().get(), 20 + plan.appended_page_count());
        for column in definition.columns() {
            assert!(matches!(
                row.value(column.ordinal(), TextCodePage::Windows1252)?
                    .ok_or("missing value")?
                    .kind(),
                ValueKind::Long(17)
            ));
        }
        assert!(cursor.next_row()?.is_none());
    }
    Ok(())
}

#[test]
fn later_generated_rows_and_payloads_follow_three_indexed_definition_continuations() -> TestResult {
    let names = names(6129);
    let mut columns = columns(&names);
    columns[0] = ColumnSpec::new(&names[0], ColumnType::AutoIncrement);
    let last = columns.len() - 1;
    columns[last] = ColumnSpec::new(&names[last], ColumnType::Memo);
    let keys = [crate::IndexColumnSpec {
        column: crate::ColumnRef::Ordinal(0),
        direction: IndexDirection::Ascending,
    }];
    let indexes = [b"A", b"B", b"C"].map(|name| crate::IndexSpec {
        name,
        fields: &keys,
        kind: crate::IndexKind::Unique,
    });
    let payload = vec![b'x'; 4096];
    let mut row = vec![RowValue::Long(17); columns.len()];
    row[0] = RowValue::AutoIncrement;
    row[last] = RowValue::Memo(&payload);
    let data = bytes(&[
        TableRows {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"First",
                columns: &[ColumnSpec::new(b"Id", ColumnType::Long)],
                indexes: &[],
            },
            rows: &[],
        },
        TableRows {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name: b"Wide",
                columns: &columns,
                indexes: &indexes,
            },
            rows: &[&row, &row],
        },
    ])?;
    let mut b = budget();
    let source = SliceSource::new(&data, b.read_budget())?;
    let mut db = crate::DatabaseReader::from_source(source, &mut b)?;
    let definition = db.table_definition(PageNumber::new(23), &mut b)?;
    let spec = TableSpec {
        validation: crate::TableValidation::NONE,
        name: b"Wide",
        columns: &columns,
        indexes: &indexes,
    };
    let plan = plan_table_schema(
        &spec,
        23,
        false,
        &mut crate::ResourceBudget::new(crate::ResourceLimits::default()),
    )?;
    assert_eq!(
        crate::creation::schema_plan::continuation_count(plan.definition_len()),
        3
    );
    assert_eq!(definition.row_count(), 2);
    assert_eq!(
        &data[23 * PAGE_BYTES + 16..23 * PAGE_BYTES + 20],
        &2_u32.to_le_bytes()
    );
    for (ordinal, (root, _)) in plan.index_placements().enumerate() {
        assert_eq!(definition.physical_indexes()[ordinal].root(), root);
        assert_eq!(
            db.index_tree(&definition, ordinal as u16, &mut b)?
                .entries()
                .len(),
            2
        );
    }
    let mut cursor = db.rows(&definition, &mut b)?;
    for id in 1..=2 {
        let mut row = cursor.next_row()?.ok_or("missing generated row")?;
        assert!(
            matches!(row.value(ColumnOrdinal::new(0), TextCodePage::Windows1252)?.ok_or("missing id")?.kind(), ValueKind::Long(n) if *n == id)
        );
        let value = row
            .value(ColumnOrdinal::new(last as u16), TextCodePage::Windows1252)?
            .ok_or("missing memo")?;
        let ValueKind::LongValue(crate::LongValue::External(reference)) = value.kind() else {
            return Err("expected external memo".into());
        };
        let reference = *reference;
        let mut stream = cursor.long_value(reference)?;
        let mut actual = Vec::new();
        while let Some(chunk) = stream.next_chunk()? {
            let crate::LongValueChunkValue::Text(text) = chunk.value() else {
                return Err("memo chunk type".into());
            };
            actual.extend_from_slice(text.raw_bytes());
        }
        assert_eq!(actual, payload);
    }
    assert!(cursor.next_row()?.is_none());
    Ok(())
}

#[test]
fn logical_definition_allocation_and_page_encoding_obey_the_callers_budget() -> TestResult {
    let mut b = ResourceBudget::new(
        ResourceLimits::default().with_max_allocation_bytes(ByteCount::new(4088)),
    );
    assert!(matches!(
        definition_pages::DefinitionPages::new(4089, &mut b),
        Err(ComposeError::Encoding(Error::ResourceLimitExceeded { .. }))
    ));
    assert_eq!(b.allocation_bytes().get(), 0);
    let mut depth = ResourceBudget::new(ResourceLimits::default().with_max_chain_depth(2));
    assert!(matches!(
        definition_pages::DefinitionPages::new(4089, &mut depth),
        Err(ComposeError::Encoding(Error::ResourceLimitExceeded { .. }))
    ));
    assert_eq!(depth.allocation_bytes().get(), 0);
    let pages = definition_pages::DefinitionPages::new(4089, &mut budget())?;
    let mut b =
        ResourceBudget::new(ResourceLimits::default().with_max_encoded_bytes(ByteCount::new(0)));
    assert!(pages.root(Some(PageNumber::new(23)), &mut b).is_err());
    let payload = pages.continuations().next().ok_or("continuation payload")?;
    assert!(
        pages
            .continuation(PageNumber::new(23), 0, payload, &mut b)
            .is_err()
    );
    assert_eq!(b.encoded_bytes().get(), 0);
    Ok(())
}

#[test]
fn definition_chains_and_catalog_maps_extend_past_inline_capacity() -> TestResult {
    let names = (0..255)
        .map(|n| format!("C{n:04}{}", "x".repeat(43)).into_bytes())
        .collect::<Vec<_>>();
    let columns = columns(&names);
    let table_names = (0..104).map(|n| format!("T{n:03}")).collect::<Vec<_>>();
    let requests = table_names
        .iter()
        .map(|name| TableRows {
            table: TableSpec {
                validation: crate::TableValidation::NONE,
                name: name.as_bytes(),
                columns: &columns,
                indexes: &[],
            },
            rows: &[],
        })
        .collect::<Vec<_>>();
    let plan = compose_database_with_table_rows(&requests, &mut budget())?;
    assert!(plan.page_count() > 1024);
    assert_eq!(plan.pages()[1].image().as_bytes()[1915], 1);
    Ok(())
}
