use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use jet3::{
    ByteCount, DatabaseReader, JET3_PAGE_SIZE, ReadLimits, ResourceBudget, ResourceLimits,
    SliceSource, TextCodePage,
};
use jet3_testkit::{
    Producer, ProducerKind, ScenarioId, SemanticSnapshotOptions, Sha256,
    snapshot_database_with_receipt,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const CATALOG_ROOT: usize = 1;
const MAP_PAGE: usize = 2;
const CATALOG_DATA: usize = 3;
const TABLE_ROOT: usize = 4;
const INDEX_ROOT: usize = 5;
const TABLE_DATA: usize = 6;

struct TestDirectory(PathBuf);

impl TestDirectory {
    fn create() -> std::io::Result<Self> {
        let path = std::env::temp_dir().join(format!(
            "jet3-semantic-artifacts-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        if path.exists() {
            fs::remove_dir_all(&path)?;
        }
        fs::create_dir(&path)?;
        Ok(Self(path))
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TestDirectory {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn catalog_record(id: u32, kind: u16, flags: u32, name: &[u8]) -> Vec<u8> {
    let mut row = vec![0_u8; 31 + name.len() + 6];
    row[0] = 17;
    row[1..5].copy_from_slice(&id.to_le_bytes());
    row[9..11].copy_from_slice(&kind.to_le_bytes());
    row[27..31].copy_from_slice(&flags.to_le_bytes());
    row[31..31 + name.len()].copy_from_slice(name);
    let length = row.len();
    row[length - 6] = u8::try_from(31 + name.len()).unwrap_or_default();
    row[length - 5] = 31;
    row[length - 4] = 11;
    row[length - 3] = 0xff;
    row
}

fn write_rows(page: &mut [u8], owner: u32, rows: &[(Vec<u8>, u16)]) {
    page[0] = 1;
    page[4..8].copy_from_slice(&owner.to_le_bytes());
    page[8..10].copy_from_slice(&u16::try_from(rows.len()).unwrap_or_default().to_le_bytes());
    let mut start = PAGE_BYTES;
    for (index, (row, flags)) in rows.iter().enumerate() {
        start -= row.len();
        let raw = u16::try_from(start).unwrap_or_default() | flags;
        page[10 + 2 * index..12 + 2 * index].copy_from_slice(&raw.to_le_bytes());
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
        let offset = slot * 3;
        record[offset..offset + 2].copy_from_slice(&u16::MAX.to_le_bytes());
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
    let length = u32::try_from(bytes.len()).unwrap_or_default();
    bytes[8..12].copy_from_slice(&length.to_le_bytes());
    bytes
}

fn text_row(id: u32, name: &[u8]) -> Vec<u8> {
    let mut row = vec![2];
    row.extend_from_slice(&id.to_le_bytes());
    row.extend_from_slice(name);
    let end = u8::try_from(5 + name.len()).unwrap_or_default();
    row.extend_from_slice(&[end, 5, 1, 3]);
    row
}

fn database_bytes() -> Vec<u8> {
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
        (vec![0, 0, 0, 0, 0, 1 << CATALOG_DATA], 0),
        (vec![0, 0, 0, 0, 0], 0),
        (vec![0, 0, 0, 0, 0, 1 << TABLE_DATA], 0),
        (vec![0, 0, 0, 0, 0], 0),
    ];
    write_rows(
        &mut bytes[MAP_PAGE * PAGE_BYTES..(MAP_PAGE + 1) * PAGE_BYTES],
        0,
        &maps,
    );
    let records = [
        (catalog_record(1, 1, 0x8000_0000, b"MSysObjects"), 0),
        (catalog_record(TABLE_ROOT as u32, 1, 0, b"Items"), 0),
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
    index[2..4].copy_from_slice(
        &u16::try_from(PAGE_BYTES - 248)
            .unwrap_or_default()
            .to_le_bytes(),
    );
    index[4..8].copy_from_slice(&(TABLE_ROOT as u32).to_le_bytes());
    write_rows(
        &mut bytes[TABLE_DATA * PAGE_BYTES..(TABLE_DATA + 1) * PAGE_BYTES],
        TABLE_ROOT as u32,
        &[(text_row(2, b"b"), 0), (text_row(1, b"a"), 0)],
    );
    bytes
}

fn artifact_bytes() -> Result<(Vec<u8>, Vec<u8>), Box<dyn std::error::Error>> {
    let bytes = database_bytes();
    let limits = ResourceLimits::new(ReadLimits::new(
        ByteCount::new(bytes.len() as u64),
        JET3_PAGE_SIZE,
        ByteCount::new(u64::MAX),
    ));
    let mut budget = ResourceBudget::new(limits);
    let options = SemanticSnapshotOptions {
        scenario_id: ScenarioId::new("DAO-READ-ROWS-SINGLE")?,
        producer: Producer::new(ProducerKind::Rust, "integration-test")?,
        database_sha256: Sha256::new("ab".repeat(32))?,
        code_page: TextCodePage::Windows1252,
    };
    let artifacts = {
        let source = SliceSource::new(&bytes, budget.read_budget())?;
        let mut database = DatabaseReader::from_source(source, &mut budget)?;
        snapshot_database_with_receipt(&mut database, &options, &mut budget)?
    };
    Ok(artifacts.to_canonical_json(&mut budget)?)
}

#[test]
fn semantic_snapshot_artifacts_pass_the_shared_protocol_validator()
-> Result<(), Box<dyn std::error::Error>> {
    let repository = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let directory = TestDirectory::create()?;
    let (snapshot_bytes, receipt_bytes) = artifact_bytes()?;
    let documents = [
        (directory.path().join("snapshot.json"), snapshot_bytes),
        (directory.path().join("receipt.json"), receipt_bytes),
    ];
    for (path, bytes) in &documents {
        fs::write(path, bytes)?;
        let output = Command::new("python3")
            .arg("-B")
            .arg("oracle/windows-dao/scripts/validate_protocol_v1_2.py")
            .arg("document")
            .arg(path)
            .current_dir(&repository)
            .output()?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            return Err(format!("shared validator rejected {}: {stderr}", path.display()).into());
        }
    }
    Ok(())
}
