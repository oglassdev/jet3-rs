//! Finite hosted creation boundaries from EXP-0146/0190/0202.
use jet3::{
    CatalogObjectClass, ColumnSpec, ColumnType, DatabaseReader, IndexColumnSpec, IndexKind,
    IndexSpec, ResourceBudget, ResourceLimits, RowValue, TableSpec, create_database_with_rows,
};
use serde_json::{Value, json};
use std::{fs, path::Path};
type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
pub const CREATION_INDEX_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/creation-index-scenarios.json");
fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}
fn recipe(id: &str) -> Result<Value> {
    let inventory: Value = serde_json::from_str(CREATION_INDEX_SCENARIOS)?;
    inventory["scenarios"]
        .as_array()
        .ok_or("scenarios")?
        .iter()
        .find(|s| s["id"] == id)
        .cloned()
        .ok_or_else(|| "unknown scenario".into())
}
fn generate(id: &str, path: &Path) -> Result<()> {
    let case = recipe(id)?;
    let name = case["recipe"].as_str().ok_or("recipe")?;
    let (columns, rows, keys, kinds, names) = match name {
        "long-depth-three" => (
            vec![ColumnSpec::new(b"Id", ColumnType::Long)],
            (-13900..=13900)
                .map(|v| vec![RowValue::Long(v)])
                .collect::<Vec<_>>(),
            vec![vec![IndexColumnSpec::ascending(0)]],
            vec![IndexKind::Primary],
            vec![b"ById".as_slice()],
        ),
        "nullable-numeric" => {
            let mut rows = (0..120)
                .map(|n| {
                    vec![
                        RowValue::Currency { scaled: n - 60 },
                        RowValue::Double(n as f64),
                        RowValue::Long(n as i32 + 1),
                    ]
                })
                .collect::<Vec<_>>();
            for n in 0..2 {
                rows.extend([
                    vec![RowValue::Null, RowValue::Null, RowValue::Long(121 + n * 3)],
                    vec![
                        RowValue::Null,
                        RowValue::Double(1.0),
                        RowValue::Long(122 + n * 3),
                    ],
                    vec![
                        RowValue::Currency { scaled: 1 },
                        RowValue::Null,
                        RowValue::Long(123 + n * 3),
                    ],
                ]);
            }
            (
                vec![
                    ColumnSpec::new(b"A", ColumnType::Currency),
                    ColumnSpec::new(b"B", ColumnType::Double),
                    ColumnSpec::new(b"Tag", ColumnType::Long),
                ],
                rows,
                vec![vec![
                    IndexColumnSpec::ascending(0),
                    IndexColumnSpec::descending(1),
                ]],
                vec![IndexKind::Unique],
                vec![b"ByKey".as_slice()],
            )
        }
        "multiple-long" => (
            vec![
                ColumnSpec::new(b"Id", ColumnType::Long),
                ColumnSpec::new(b"Group", ColumnType::Long),
                ColumnSpec::new(b"Value", ColumnType::Long),
            ],
            (0..201)
                .map(|n| {
                    vec![
                        RowValue::Long(n + 1),
                        RowValue::Long(n % 3 - 1),
                        RowValue::Long(n - 100),
                    ]
                })
                .collect::<Vec<_>>(),
            vec![
                vec![IndexColumnSpec::ascending(0)],
                vec![IndexColumnSpec::descending(1)],
                vec![
                    IndexColumnSpec::descending(1),
                    IndexColumnSpec::ascending(2),
                ],
            ],
            vec![IndexKind::Primary, IndexKind::Ordinary, IndexKind::Unique],
            vec![
                b"ZPrimary".as_slice(),
                b"AGroup".as_slice(),
                b"MMixed".as_slice(),
            ],
        ),
        _ => return Err("unknown recipe".into()),
    };
    let indexes = keys
        .iter()
        .zip(&kinds)
        .zip(&names)
        .map(|((fields, kind), name)| IndexSpec {
            name,
            kind: *kind,
            fields,
        })
        .collect::<Vec<_>>();
    let slices = rows.iter().map(Vec::as_slice).collect::<Vec<_>>();
    create_database_with_rows(
        path,
        &TableSpec {
            name: b"Rows",
            columns: &columns,
            indexes: &indexes,
        },
        &slices,
        &mut budget(),
    )?;
    Ok(())
}
/// Rereads the tree boundary through public APIs on either platform.
fn inspect(id: &str, path: &Path) -> Result<Value> {
    let case = recipe(id)?;
    let mut b = budget();
    let mut db = DatabaseReader::open(path, &mut b)?;
    let root = {
        let mut catalog = db.catalog(&mut b)?;
        let mut found = None;
        while let Some(r) = catalog.next_record()? {
            if r.class() == CatalogObjectClass::User && r.name().raw_bytes() == b"Rows" {
                if found.is_some() {
                    return Err("duplicate table".into());
                }
                found = r.table_definition();
            }
        }
        found.ok_or("Rows absent")?
    };
    let definition = db.table_definition(root, &mut b)?;
    let expected = case["trees"].as_array().ok_or("trees")?;
    if definition.indexes().len() != expected.len() {
        return Err("index inventory".into());
    }
    let mut results = Vec::new();
    for index in definition.indexes() {
        let name = std::str::from_utf8(index.name().raw_bytes())?;
        let wanted = expected
            .iter()
            .find(|i| i["name"] == name)
            .ok_or("index name")?;
        let tree = db.index_tree(&definition, index.physical_index(), &mut b)?;
        let depth = tree
            .nodes()
            .iter()
            .map(|n| n.depth())
            .max()
            .ok_or("empty tree")?;
        if Some(depth) != wanted["depth"].as_u64()
            || Some(tree.entries().len() as u64) != wanted["entries"].as_u64()
        {
            return Err("declared tree boundary".into());
        }
        let physical = &definition.physical_indexes()[usize::from(index.physical_index())];
        let location = physical.usage_map();
        let mut bytes = [0; jet3::PAGE_BYTES];
        let page = db.read_classified_page(location.page(), &mut bytes, &mut b)?;
        let record = jet3::locate_usage_map(
            page,
            jet3::MapRowLocator::new(location.page(), location.row()),
            &mut b,
        )?;
        let jet3::AllocationMap::Inline(map) = jet3::decode_allocation_map(record.raw(), &mut b)?
        else {
            return Err("expected inline index map".into());
        };
        let mut allocated = map.allocated_pages(db.geometry());
        let mut mapped = Vec::new();
        while let Some(page) = allocated.next_page(&mut b)? {
            mapped.push(page.get());
        }
        mapped.sort_unstable();
        let mut nodes = tree
            .nodes()
            .iter()
            .map(|n| n.page().get())
            .collect::<Vec<_>>();
        nodes.sort_unstable();
        if mapped != nodes {
            return Err("exact index tree map".into());
        }
        results.push(json!({"name":name,"depth":depth,"entries":tree.entries().len(),"maps":mapped,"pages":tree.nodes().iter().map(|n|n.page().get()).collect::<Vec<_>>()}));
    }
    results.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
    Ok(json!({"scenario_id":id,"trees":results}))
}
/// Generates a public creation candidate, or independently checks its tree boundary.
pub fn creation_index_fixture(command: &str, id: &str, directory: &Path) -> Result<()> {
    match command {
        "generate" => {
            generate(id, &directory.join("database.mdb"))?;
            let receipt = inspect(id, &directory.join("database.mdb"))?;
            fs::write(directory.join("layout.json"), serde_json::to_vec(&receipt)?)?;
        }
        "verify" => {
            let actual = inspect(id, &directory.join("database.mdb"))?;
            let expected: Value =
                serde_json::from_slice(&fs::read(directory.join("layout.json"))?)?;
            if actual != expected {
                return Err("tree layout receipt differs".into());
            }
        }
        _ => return Err("expected generate or verify".into()),
    }
    Ok(())
}
#[cfg(all(test, unix))]
mod tests {
    use super::*;
    #[test]
    fn finite_recipes_reach_declared_depths_and_reopen() -> Result<()> {
        let inventory: Value = serde_json::from_str(CREATION_INDEX_SCENARIOS)?;
        for case in inventory["scenarios"].as_array().ok_or("scenarios")? {
            let dir = tempfile::tempdir()?;
            let id = case["id"].as_str().ok_or("id")?;
            creation_index_fixture("generate", id, dir.path())?;
            creation_index_fixture("verify", id, dir.path())?;
            let mut receipt: Value =
                serde_json::from_slice(&fs::read(dir.path().join("layout.json"))?)?;
            receipt["trees"][0]["depth"] = json!(99);
            fs::write(
                dir.path().join("layout.json"),
                serde_json::to_vec(&receipt)?,
            )?;
            assert!(creation_index_fixture("verify", id, dir.path()).is_err());
        }
        Ok(())
    }
}
