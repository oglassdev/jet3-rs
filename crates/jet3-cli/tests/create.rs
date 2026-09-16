#![forbid(unsafe_code)]
use serde_json::Value;
#[cfg(any(unix, windows))]
use serde_json::json;
#[cfg(any(unix, windows))]
use std::fs;
use std::{
    io::Write,
    process::{Command, Output, Stdio},
};

type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;

fn run(output: &std::path::Path, request: &str) -> Result<Output> {
    let mut child = Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .arg("create")
        .arg(output)
        .args(["--input", "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    child
        .stdin
        .take()
        .ok_or("missing stdin")?
        .write_all(request.as_bytes())?;
    Ok(child.wait_with_output()?)
}

#[test]
fn create_rejects_unknown_fields_types_and_arguments() -> Result {
    let directory = tempfile::tempdir()?;
    let output = directory.path().join("invalid.mdb");
    for request in [
        r#"{"tables":[],"overwrite":true}"#,
        r#"{"tables":[{"name":"T","columns":[],"unknown":0}]}"#,
        r#"{"tables":[{"name":"T","columns":[{"name":"Id","type":"long","unknown":0}]}]}"#,
        r#"{"tables":[{"name":"T","columns":[{"name":"Id","type":"long"}],"rows":[[{"long":2147483648}]]}]}"#,
        r#"{"tables":[{"name":"T","columns":[{"name":"Id","type":"long","size":4}]}]}"#,
        r#"{"tables":[{"name":"T","columns":[{"name":"Text","type":"text","size":0}]}]}"#,
        r#"{"tables":[{"name":"T","columns":[{"name":"Id","type":"long","allow_zero_length":true}]}]}"#,
        r#"{"tables":[{"name":"T","columns":[{"name":"Text","type":"text","size":10}],"rows":[[{"text":"é"}]]}]}"#,
        r#"{"tables":[{"name":"T","columns":[{"name":"B","type":"binary","size":2}],"rows":[[{"binary":[256]}]]}]}"#,
        r#"{"tables":[{"name":"T","columns":[{"name":"F","type":"single"}],"rows":[[{"single":1e300}]]}]}"#,
        r#"{"tables":[],"relationship":{"name":"R","parent":{"table":"A","column":"Id","cascade":true},"child":{"table":"B","column":"Id"}}}"#,
    ] {
        let result = run(&output, request)?;
        assert_eq!(result.status.code(), Some(1), "{request}");
        assert!(result.stdout.is_empty());
        let error: Value = serde_json::from_slice(&result.stderr)?;
        assert_eq!(error["error"], "create_failed");
        assert!(!output.exists());
    }
    let missing = Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .arg("create")
        .arg(&output)
        .output()?;
    assert_eq!(missing.status.code(), Some(2));
    let duplicate = Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .arg("create")
        .arg(&output)
        .args(["--input", "-", "--input", "-"])
        .output()?;
    assert_eq!(duplicate.status.code(), Some(2));
    Ok(())
}

#[test]
#[cfg(any(unix, windows))]
fn create_file_input_preserves_typed_rows_and_refuses_overwrite() -> Result {
    let directory = tempfile::tempdir()?;
    let output = directory.path().join("created.mdb");
    let input = directory.path().join("request.json");
    let request = json!({"tables":[{
        "name":"Items", "columns":[{"name":"Id","type":"auto_increment"},{"name":"Label","type":"text","size":20,"required":true}],
        "indexes":[{"name":"ById","kind":"primary","fields":[{"column":"Id"}]}],
        "rows":[["auto_increment",{"text":"Hello"}],["auto_increment",{"text":[233]}]]
    }]});
    fs::write(&input, request.to_string())?;
    let result = Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .arg("create")
        .arg(&output)
        .arg("--input")
        .arg(&input)
        .output()?;
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert_eq!(serde_json::from_slice::<Value>(&result.stdout)?["ok"], true);
    let bytes = fs::read(&output)?;
    let mut invalid = request.clone();
    invalid["tables"][0]["rows"][0][1] = Value::Null;
    let refused = directory.path().join("required-null.mdb");
    let result = run(&refused, &invalid.to_string())?;
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("RequiredValueMissing"));
    assert!(!refused.exists());
    let inspection = Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .arg("inspect")
        .arg(&output)
        .arg("--rows")
        .output()?;
    assert!(inspection.status.success());
    let document: Value = serde_json::from_slice(&inspection.stdout)?;
    let table = document["tables"]
        .as_array()
        .ok_or("missing tables")?
        .iter()
        .find(|t| t["kind"] == "User" && t["columns"][0]["name"] == "Id")
        .ok_or("Items not found")?;
    assert_eq!(
        table["rows"],
        json!([{"Id":1,"Label":"Hello"},{"Id":2,"Label":"é"}])
    );
    assert_eq!(table["indexes"][0]["name"], "ById");
    assert!(!run(&output, r#"{"tables":[]}"#)?.status.success());
    assert_eq!(fs::read(&output)?, bytes);
    Ok(())
}

#[test]
#[cfg(any(unix, windows))]
fn create_stdin_relationship_and_empty_database_use_public_api() -> Result {
    let directory = tempfile::tempdir()?;
    let empty = directory.path().join("empty.mdb");
    let result = run(&empty, r#"{"tables":[]}"#)?;
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let output = directory.path().join("related.mdb");
    let request = json!({"tables":[
        {"name":"Parent","columns":[{"name":"Id","type":"long"}],"indexes":[{"name":"ById","kind":"primary","fields":[{"column":"Id"}]}],"rows":[[{"long":1}]]},
        {"name":"Child","columns":[{"name":"ParentId","type":"long"}],"rows":[[{"long":1}],[{"long":1}]]}
    ],"relationship":{"name":"ParentChild","parent":{"table":"Parent","column":"Id"},"child":{"table":"Child","column":"ParentId"}}});
    let result = run(&output, &request.to_string())?;
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let snapshot = Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .arg("inspect")
        .arg(&output)
        .arg("--rows")
        .output()?;
    assert!(snapshot.status.success());
    let document: Value = serde_json::from_slice(&snapshot.stdout)?;
    let child = document["tables"]
        .as_array()
        .ok_or("missing tables")?
        .iter()
        .find(|t| t["columns"][0]["name"] == "ParentId")
        .ok_or("Child not found")?;
    assert_eq!(child["rows"], json!([{"ParentId":1},{"ParentId":1}]));
    assert_eq!(child["indexes"][0]["name"], "ParentChild");
    let mut empty_request = request;
    empty_request["tables"][0]["rows"] = json!([]);
    empty_request["tables"][1]["rows"] = json!([]);
    empty_request["tables"][0]["columns"]
        .as_array_mut()
        .ok_or("missing columns")?
        .push(json!({"name":"Alternate","type":"long"}));
    empty_request["tables"][0]["indexes"]
        .as_array_mut()
        .ok_or("missing indexes")?
        .push(json!({"name":"ByAlternate","kind":"unique","fields":[{"column":"Alternate"}]}));
    let empty_related = directory.path().join("empty-related.mdb");
    let result = run(&empty_related, &empty_request.to_string())?;
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    Ok(())
}

#[test]
#[cfg(any(unix, windows))]
fn create_relationship_array_resolves_generated_parents_and_column_options() -> Result {
    let directory = tempfile::tempdir()?;
    let mut request = json!({"tables":[
        {"name":"Parent","columns":[{"name":"Id","type":"auto_increment"},{"name":"Label","type":"text","size":8,"allow_zero_length":true}],"indexes":[{"name":"ById","kind":"primary","fields":[{"column":"Id"}]}],"rows":[["auto_increment",{"text":""}]]},
        {"name":"ChildA","columns":[{"name":"Id","type":"long"},{"name":"ParentId","type":"long"}],"rows":[[{"long":10},{"long":1}]]},
        {"name":"ChildB","columns":[{"name":"Id","type":"long"},{"name":"ParentId","type":"long"}],"rows":[[{"long":20},null]]}
    ],"relationships":[
        {"name":"ParentA","parent":{"table":"Parent","column":"Id"},"child":{"table":"ChildA","column":"ParentId"}},
        {"name":"ParentB","parent":{"table":"Parent","column":"Id"},"child":{"table":"ChildB","column":"ParentId"}}
    ]});
    for empty in [false, true] {
        let output = directory.path().join(format!("graph-{empty}.mdb"));
        if empty {
            for table in request["tables"].as_array_mut().ok_or("tables absent")? {
                table["rows"] = json!([]);
            }
        }
        let result = run(&output, &request.to_string())?;
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let snapshot = Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
            .arg("inspect")
            .arg(&output)
            .arg("--rows")
            .output()?;
        assert!(snapshot.status.success());
        let document: Value = serde_json::from_slice(&snapshot.stdout)?;
        let tables = document["tables"].as_array().ok_or("tables absent")?;
        let parent = tables
            .iter()
            .find(|table| {
                table["kind"] == "User"
                    && table["columns"].as_array().is_some_and(|columns| {
                        columns.iter().any(|column| column["name"] == "Label")
                    })
            })
            .ok_or("parent absent")?;
        assert_eq!(
            parent["rows"],
            if empty {
                json!([])
            } else {
                json!([{"Id":1,"Label":""}])
            }
        );
        for name in ["ParentA", "ParentB"] {
            assert!(tables.iter().any(|table| {
                table["indexes"]
                    .as_array()
                    .is_some_and(|indexes| indexes.iter().any(|index| index["name"] == name))
            }));
        }
    }
    request["relationship"] = request["relationships"][0].clone();
    let invalid = directory.path().join("ambiguous.mdb");
    let result = run(&invalid, &request.to_string())?;
    assert!(!result.status.success());
    assert!(
        String::from_utf8_lossy(&result.stderr)
            .contains("specify either relationship or relationships")
    );
    assert!(!invalid.exists());
    Ok(())
}

#[test]
#[cfg(any(unix, windows))]
fn composite_endpoint_columns_preserve_order_and_reject_ambiguous_shapes() -> Result {
    let directory = tempfile::tempdir()?;
    let request = json!({"tables":[
        {"name":"Parent","columns":[{"name":"A","type":"long"},{"name":"B","type":"long"}],"indexes":[{"name":"Pair","kind":"unique","fields":[{"column":"A"},{"column":"B"}]}],"rows":[[{"long":11},{"long":22}]]},
        {"name":"Child","columns":[{"name":"X","type":"long"},{"name":"Y","type":"long"}],"rows":[[{"long":11},{"long":22}]]}
    ],"relationships":[{"name":"Pair","parent":{"table":"Parent","columns":["A","B"]},"child":{"table":"Child","columns":["X","Y"]}}]});
    let output = directory.path().join("composite.mdb");
    let result = run(&output, &request.to_string())?;
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    let validation = Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
        .arg("validate")
        .arg(&output)
        .output()?;
    assert!(validation.status.success());
    let document: Value = serde_json::from_slice(&validation.stdout)?;
    assert_eq!(document["checked"]["relationship_catalog_rows"], 2);
    assert_eq!(document["checked"]["relationships_with_verified_keys"], 1);
    for endpoint in [
        json!({"table":"Parent","column":"A","columns":["A","B"]}),
        json!({"table":"Parent","columns":["A"]}),
        json!({"table":"Parent","columns":["B","A"]}),
        json!({"table":"Parent","columns":[]}),
    ] {
        let mut invalid = request.clone();
        invalid["relationships"][0]["parent"] = endpoint;
        let absent = directory.path().join("refused.mdb");
        assert!(!run(&absent, &invalid.to_string())?.status.success());
        assert!(!absent.exists());
    }
    Ok(())
}
