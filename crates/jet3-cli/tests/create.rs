#![forbid(unsafe_code)]
use serde_json::Value;
#[cfg(unix)]
use serde_json::json;
#[cfg(unix)]
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
#[cfg(unix)]
fn create_file_input_preserves_typed_rows_and_refuses_overwrite() -> Result {
    let directory = tempfile::tempdir()?;
    let output = directory.path().join("created.mdb");
    let input = directory.path().join("request.json");
    let request = json!({"tables":[{
        "name":"Items", "columns":[{"name":"Id","type":"auto_increment"},{"name":"Label","type":"text","size":20}],
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
#[cfg(unix)]
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
