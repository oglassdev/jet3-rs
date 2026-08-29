//! A hand-assembled minimal Jet 3 database for adapter tests.
//!
//! The database holds `MSysObjects` and one user table `Items(Id Long,
//! Name Text)` with primary index `PK` on `Id` and three rows, one of them a
//! byte-identical duplicate. Page layout follows the `EXP-0057`–`EXP-0062`
//! observations the reader is built on; it is a test input, not evidence.

use jet3::JET3_PAGE_SIZE;

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const CATALOG_ROOT: usize = 1;
const MAP_PAGE: usize = 2;
const CATALOG_DATA: usize = 3;
const TABLE_ROOT: usize = 4;
const INDEX_ROOT: usize = 5;
const TABLE_DATA: usize = 6;

/// Rows stored in the synthetic `Items` table as `(Id, Name)`.
pub const ITEMS_ROWS: [(u32, &str); 3] = [(2, "b"), (1, "a"), (1, "a")];

fn catalog_record(id: u32, kind: u16, flags: u32, name: &[u8]) -> Vec<u8> {
    let mut row = vec![0_u8; 31 + name.len() + 6];
    row[0] = 17;
    row[1..5].copy_from_slice(&id.to_le_bytes());
    row[9..11].copy_from_slice(&kind.to_le_bytes());
    row[27..31].copy_from_slice(&flags.to_le_bytes());
    row[31..31 + name.len()].copy_from_slice(name);
    let length = row.len();
    row[length - 6] = (31 + name.len()) as u8;
    row[length - 5] = 31;
    row[length - 4] = 11;
    row[length - 3] = 0xff;
    row
}

fn write_rows(page: &mut [u8], owner: u32, rows: &[Vec<u8>]) {
    page[0] = 1;
    page[4..8].copy_from_slice(&owner.to_le_bytes());
    page[8..10].copy_from_slice(&(rows.len() as u16).to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, row) in rows.iter().enumerate() {
        start -= row.len();
        page[10 + 2 * index..12 + 2 * index].copy_from_slice(&(start as u16).to_le_bytes());
        page[start..start + row.len()].copy_from_slice(row);
    }
}

fn column_record(
    physical_type: u8,
    ordinal: u16,
    class: u8,
    fixed_offset: u16,
    size: u16,
) -> [u8; 18] {
    let mut record = [0_u8; 18];
    record[0] = physical_type;
    record[1..3].copy_from_slice(&ordinal.to_le_bytes());
    record[5..7].copy_from_slice(&ordinal.to_le_bytes());
    record[7..9].copy_from_slice(&1_u16.to_le_bytes());
    record[9..13].copy_from_slice(&[0x09, 0x04, 0xe4, 0x04]);
    record[13] = class;
    record[14..16].copy_from_slice(&fixed_offset.to_le_bytes());
    record[16..18].copy_from_slice(&size.to_le_bytes());
    record
}

fn physical_index() -> [u8; 39] {
    let mut record = [0_u8; 39];
    for slot in 0..10 {
        record[slot * 3..slot * 3 + 2].copy_from_slice(&u16::MAX.to_le_bytes());
    }
    record[..2].copy_from_slice(&0_u16.to_le_bytes());
    record[2] = 1;
    record[31..34].copy_from_slice(&[MAP_PAGE as u8, 0, 0]);
    record[34..38].copy_from_slice(&(INDEX_ROOT as u32).to_le_bytes());
    record[38] = 9;
    record
}

fn user_definition() -> Vec<u8> {
    let mut bytes = vec![0_u8; 43];
    bytes[..4].copy_from_slice(&[0x02, 0x01, 0x56, 0x43]);
    bytes[20] = 0x4e;
    bytes[21..23].copy_from_slice(&2_u16.to_le_bytes());
    bytes[23..25].copy_from_slice(&1_u16.to_le_bytes());
    bytes[25..27].copy_from_slice(&2_u16.to_le_bytes());
    bytes[27..29].copy_from_slice(&1_u16.to_le_bytes());
    bytes[31..33].copy_from_slice(&1_u16.to_le_bytes());
    bytes[35..39].copy_from_slice(&[2, MAP_PAGE as u8, 0, 0]);
    bytes[39..43].copy_from_slice(&[3, MAP_PAGE as u8, 0, 0]);
    bytes.extend_from_slice(&[1, 2, 3, 4, 5, 6, 7, 8]);
    bytes.extend_from_slice(&column_record(4, 0, 3, 0, 4));
    bytes.extend_from_slice(&column_record(10, 1, 2, 0, 255));
    bytes.extend_from_slice(&[2, b'I', b'd', 4, b'N', b'a', b'm', b'e']);
    bytes.extend_from_slice(&physical_index());
    let mut logical = [0_u8; 20];
    logical[9..13].copy_from_slice(&u32::MAX.to_le_bytes());
    logical[17..19].copy_from_slice(&[4, 4]);
    logical[19] = 1;
    bytes.extend_from_slice(&logical);
    bytes.extend_from_slice(&[2, b'P', b'K']);
    bytes.extend_from_slice(&[0xff, 0xff]);
    let length = bytes.len() as u32;
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    bytes
}

fn text_row(id: u32, name: &[u8]) -> Vec<u8> {
    let mut row = vec![2];
    row.extend_from_slice(&id.to_le_bytes());
    row.extend_from_slice(name);
    row.extend_from_slice(&[(5 + name.len()) as u8, 5, 1, 3]);
    row
}

/// Returns the complete synthetic database bytes.
#[must_use]
pub fn synthetic_database() -> Vec<u8> {
    synthetic_database_with_rows(&ITEMS_ROWS)
}

fn synthetic_database_with_rows(rows: &[(u32, &str)]) -> Vec<u8> {
    let mut bytes = vec![0_u8; 7 * PAGE_BYTES];
    bytes[4..19].copy_from_slice(b"Standard Jet DB");
    bytes[0x41] = 0x4e;
    bytes[0x42..0x50].copy_from_slice(&[
        0x86, 0xfb, 0xec, 0x37, 0x5d, 0x44, 0x9c, 0xfa, 0xc6, 0x5e, 0x28, 0xe6, 0x13, 0xb6,
    ]);
    let catalog = &mut bytes[CATALOG_ROOT * PAGE_BYTES..(CATALOG_ROOT + 1) * PAGE_BYTES];
    catalog[0] = 2;
    catalog[35..39].copy_from_slice(&[0, MAP_PAGE as u8, 0, 0]);
    catalog[39..43].copy_from_slice(&[1, MAP_PAGE as u8, 0, 0]);
    let maps = [
        vec![0, 0, 0, 0, 0, 1 << CATALOG_DATA],
        vec![0, 0, 0, 0, 0],
        vec![0, 0, 0, 0, 0, 1 << TABLE_DATA],
        vec![0, 0, 0, 0, 0],
    ];
    write_rows(
        &mut bytes[MAP_PAGE * PAGE_BYTES..(MAP_PAGE + 1) * PAGE_BYTES],
        0,
        &maps,
    );
    let records = [
        catalog_record(CATALOG_ROOT as u32, 1, 0x8000_0000, b"MSysObjects"),
        catalog_record(TABLE_ROOT as u32, 1, 0, b"Items"),
    ];
    write_rows(
        &mut bytes[CATALOG_DATA * PAGE_BYTES..(CATALOG_DATA + 1) * PAGE_BYTES],
        CATALOG_ROOT as u32,
        &records,
    );
    let definition = user_definition();
    bytes[TABLE_ROOT * PAGE_BYTES..TABLE_ROOT * PAGE_BYTES + definition.len()]
        .copy_from_slice(&definition);
    let index = &mut bytes[INDEX_ROOT * PAGE_BYTES..(INDEX_ROOT + 1) * PAGE_BYTES];
    index[0] = 4;
    index[1] = 1;
    index[2..4].copy_from_slice(&((PAGE_BYTES - 248) as u16).to_le_bytes());
    index[4..8].copy_from_slice(&(TABLE_ROOT as u32).to_le_bytes());
    let rows: Vec<Vec<u8>> = rows
        .iter()
        .map(|(id, name)| text_row(*id, name.as_bytes()))
        .collect();
    write_rows(
        &mut bytes[TABLE_DATA * PAGE_BYTES..(TABLE_DATA + 1) * PAGE_BYTES],
        TABLE_ROOT as u32,
        &rows,
    );
    bytes
}

#[cfg(test)]
mod tests {
    use super::{synthetic_database, synthetic_database_with_rows};
    use crate::{
        PROTOCOL_SCENARIOS, Producer, Scalar, SnapshotOptions, SnapshotOutcome, coverage,
        parse_scenarios, snapshot_bytes,
    };
    use jet3::TextCodePage;

    #[test]
    fn synthetic_database_snapshots_through_the_real_reader()
    -> Result<(), Box<dyn std::error::Error>> {
        let bytes = synthetic_database();
        let options = SnapshotOptions {
            scenario_id: "DAO-READ-ROWS-DUPLICATES".to_owned(),
            source_revision: "test".to_owned(),
            code_page: TextCodePage::Windows1252,
        };
        let SnapshotOutcome::Snapshot { snapshot, branches } = snapshot_bytes(&bytes, &options)?
        else {
            return Err("expected a snapshot".into());
        };
        assert_eq!(snapshot.tables.len(), 1);
        let table = &snapshot.tables[0];
        assert_eq!(table.name, "Items");
        assert_eq!(
            table
                .columns
                .iter()
                .map(|column| column.dao_type.as_str())
                .collect::<Vec<_>>(),
            ["dbLong", "dbText"]
        );
        assert_eq!(table.indexes[0].name, "PK");
        assert!(table.indexes[0].primary);
        assert_eq!(table.rows.len(), 3);
        let duplicates: Vec<_> = table
            .rows
            .iter()
            .map(|row| (row.values["Id"].value.clone(), row.duplicate_ordinal))
            .collect();
        assert!(duplicates.contains(&(Scalar::Integer(1), 1)));
        assert_eq!(table.rows[0].values["Name"].code_page, Some(1252));
        for branch in [
            "open.header_page",
            "catalog.record_stream",
            "allocation.inline_map",
            "tdef.single_page",
            "tdef.logical_index",
            "rows.direct",
            "values.fixed_scalar",
            "values.variable_short",
            "values.text_cp1252",
        ] {
            assert!(branches.contains(branch), "{branch}");
        }
        assert!(!branches.contains("rows.deleted_skip"));
        let first = snapshot.to_canonical_json()?;
        let SnapshotOutcome::Snapshot { snapshot, .. } = snapshot_bytes(&bytes, &options)? else {
            return Err("expected a snapshot".into());
        };
        assert_eq!(first, snapshot.to_canonical_json()?);
        Ok(())
    }

    #[test]
    fn jet4_header_is_an_opening_failure() -> Result<(), Box<dyn std::error::Error>> {
        let mut bytes = synthetic_database();
        bytes[0x14] = 0x01;
        let options = SnapshotOptions {
            scenario_id: "DAO-READ-OPEN-REJECT-JET4".to_owned(),
            source_revision: "test".to_owned(),
            code_page: TextCodePage::Windows1252,
        };
        let SnapshotOutcome::OpeningFailure { error_class, .. } = snapshot_bytes(&bytes, &options)?
        else {
            return Err("expected an opening failure".into());
        };
        assert_eq!(error_class, "unsupported_version");
        Ok(())
    }

    #[test]
    fn empty_user_table_satisfies_its_coverage_scenario() -> Result<(), Box<dyn std::error::Error>>
    {
        let bytes = synthetic_database_with_rows(&[]);
        let options = SnapshotOptions {
            scenario_id: "DAO-READ-ROWS-EMPTY-TABLE".to_owned(),
            source_revision: "test".to_owned(),
            code_page: TextCodePage::Windows1252,
        };
        let outcome = snapshot_bytes(&bytes, &options)?;
        let receipt = coverage(
            &options.scenario_id,
            Producer {
                kind: "rust",
                source_revision: options.source_revision,
            },
            crate::sha256_hex(&bytes),
            &outcome,
            &parse_scenarios(PROTOCOL_SCENARIOS)?,
        );
        let scenario = receipt
            .scenarios
            .iter()
            .find(|scenario| scenario.id == "DAO-READ-ROWS-EMPTY-TABLE")
            .ok_or("missing coverage scenario")?;
        assert!(scenario.satisfied, "{:?}", scenario.missing_branches);
        Ok(())
    }
}
