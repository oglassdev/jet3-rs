#![cfg(unix)]
#[path = "../examples/support/indexed_boundary.rs"]
mod fixture;
use fixture::*;
use jet3::{DatabaseReader, MapRowLocator, PAGE_BYTES, PageNumber, UpdateError};
use std::fs;
struct Temp(std::path::PathBuf);
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}
#[test]
fn indexed_boundary_grouped_matrix() -> Result<()> {
    let dir = Temp(std::env::temp_dir().join(format!("jet3-boundary-test-{}", std::process::id())));
    fs::create_dir(&dir.0)?;
    for (name, count, id, error) in [
        ("space", 3, 101, None),
        ("eof", 20, 101, None),
        ("duplicate", 20, 0, Some("duplicate unique key")),
        ("split", 200, 201, Some("full root leaf")),
    ] {
        let path = dir.0.join(name);
        create(&path, count)?;
        let before = fs::read(&path)?;
        let table = definition(&path)?;
        let mut b = budget();
        let mut db = DatabaseReader::open(&path, &mut b)?;
        let mut cursor = db.rows(&table, &mut b)?;
        let mut prior = Vec::new();
        while let Some(r) = cursor.next_row()? {
            prior.push(r.locator());
        }
        drop(cursor);
        if name != "space" {
            for page in prior
                .iter()
                .map(|r| r.page())
                .collect::<std::collections::BTreeSet<_>>()
            {
                let start = page.get() as usize * PAGE_BYTES;
                assert!(u16::from_le_bytes(before[start + 2..start + 4].try_into()?) < 99);
            }
        }
        let result = jet3::insert_row(&path, b"Items", &values(id), &mut budget());
        if let Some(message) = error {
            assert!(matches!(result,Err(UpdateError::Unsupported(m)) if m==message));
            assert_eq!(fs::read(&path)?, before);
            continue;
        }
        let added = result?;
        let after = fs::read(&path)?;
        let eof = name == "eof";
        assert_eq!(after.len(), before.len() + if eof { PAGE_BYTES } else { 0 });
        assert_eq!(
            added.page().get() as usize * PAGE_BYTES == before.len(),
            eof
        );
        let mut expected = before.clone();
        let root = table.root().get() as usize * PAGE_BYTES;
        for offset in [12, 47] {
            expected[root + offset..root + offset + 4]
                .copy_from_slice(&((count + 1) as u32).to_le_bytes());
        }
        let leaf = table.physical_indexes()[0].root().get() as usize * PAGE_BYTES;
        expected[leaf + 2..leaf + 4]
            .copy_from_slice(&(1800 - ((count + 1) * 9) as u16).to_le_bytes());
        expected[leaf + 22..leaf + 248].fill(0);
        let mut records = Vec::new();
        for (key, row) in (0..count)
            .zip(prior.iter())
            .chain(std::iter::once((id, &added)))
        {
            let mut record = vec![0x7f];
            record.extend_from_slice(&((key as u32) ^ 0x80000000).to_be_bytes());
            record.extend_from_slice(&(row.page().get() as u32).to_be_bytes()[1..]);
            record.push(row.slot());
            records.push(record);
        }
        records.sort();
        for (n, record) in records.iter().enumerate() {
            expected[leaf + 248 + n * 9..leaf + 248 + (n + 1) * 9].copy_from_slice(record);
            let end = (n + 1) * 9;
            expected[leaf + 22 + end / 8] |= 1 << (end % 8);
        }
        if eof {
            let page = added.page().get() as usize;
            for (role, loc) in [
                MapRowLocator::new(PageNumber::new(1), 0),
                table.maps().owned(),
                table.maps().available(),
            ]
            .iter()
            .enumerate()
            {
                let mut bytes = [0; PAGE_BYTES];
                let classified = db.read_classified_page(loc.page(), &mut bytes, &mut b)?;
                let record = jet3::locate_usage_map(classified, *loc, &mut b)?;
                let raw = record.raw();
                let base = u32::from_le_bytes(raw[1..5].try_into()?) as usize;
                let offset = loc.page().get() as usize * PAGE_BYTES
                    + record.range().start
                    + 5
                    + (page - base) / 8;
                let mask = 1 << ((page - base) % 8);
                if role == 0 {
                    expected[offset] &= !mask;
                } else {
                    expected[offset] |= mask;
                }
            }
            expected.extend_from_slice(&after[before.len()..]);
        } else {
            let start = added.page().get() as usize * PAGE_BYTES;
            let old = &before[start..start + PAGE_BYTES];
            let new = &after[start..start + PAGE_BYTES];
            expected[start + 2..start + 4].copy_from_slice(&new[2..4]);
            expected[start + 8..start + 10].copy_from_slice(&new[8..10]);
            let slot = 10 + usize::from(added.slot()) * 2;
            expected[start + slot..start + slot + 2].copy_from_slice(&new[slot..slot + 2]);
            let low = u16::from_le_bytes(new[slot..slot + 2].try_into()?) as usize;
            let high = u16::from_le_bytes(old[slot - 2..slot].try_into()?) as usize;
            expected[start + low..start + high].copy_from_slice(&new[low..high]);
        }
        assert_eq!(
            after, expected,
            "all unrelated metadata, Notes payloads and slack"
        );
        let mut db = DatabaseReader::open(&path, &mut b)?;
        let table = definition(&path)?;
        let tree = db.index_tree(&table, 0, &mut b)?;
        assert_eq!(tree.nodes().len(), 1);
        assert_eq!(tree.entries().len(), (count + 1) as usize);
        assert_eq!(
            tree.entries()
                .iter()
                .map(|e| e.key().raw_bytes())
                .collect::<Vec<_>>(),
            records.iter().map(|r| &r[..5]).collect::<Vec<_>>()
        );
        let mut cursor = db.rows(&table, &mut b)?;
        let mut seen = 0;
        while let Some(mut r) = cursor.next_row()? {
            let raw = r
                .field(table.columns()[0].ordinal())
                .and_then(|f| f.raw_bytes())
                .ok_or("Id")?;
            let key = i32::from_le_bytes(raw.try_into()?);
            assert_eq!(
                r.field(table.columns()[1].ordinal())
                    .and_then(|f| f.raw_bytes()),
                Some(NAME.as_slice())
            );
            let currency = (-123456_i64).to_le_bytes();
            assert_eq!(
                r.field(table.columns()[2].ordinal())
                    .and_then(|f| f.raw_bytes()),
                if key % 2 == 0 {
                    None
                } else {
                    Some(currency.as_slice())
                }
            );
            assert!(
                matches!(r.value(table.columns()[3].ordinal(), jet3::TextCodePage::Windows1252)?.ok_or("Active")?.kind(), jet3::ValueKind::Boolean(v) if *v == (key%2!=0))
            );
            let entry = tree
                .entries()
                .iter()
                .find(|e| e.row() == r.locator())
                .ok_or("locator")?;
            assert_eq!(
                &entry.key().raw_bytes()[1..],
                &((key as u32) ^ 0x80000000).to_be_bytes()
            );
            seen += 1;
        }
        assert_eq!(seen, count + 1);
    }
    Ok(())
}
