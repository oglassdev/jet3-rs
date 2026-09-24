#![cfg(any(unix, windows))]
#![forbid(unsafe_code)]

mod common;

use common::{Result, cli, request};
use serde_json::{Value, json};
use std::{fs, path::Path, process::Output};

fn success(output: &Output) -> Result<Value> {
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    Ok(serde_json::from_slice(&output.stdout)?)
}

fn create(path: &Path) -> Result {
    success(&request(
        "create",
        path,
        &json!({"tables":[{
            "name":"Items", "columns":[{"name":"Id","type":"long"},{"name":"Clé","type":"long"}],
            "indexes":[{"name":"ById","kind":"primary","fields":[{"column":"Id"}]}],
            "rows":[[{"long":3},{"long":7}],[{"long":1},null],[{"long":2},{"long":7}]]
        }]}),
    )?)?;
    Ok(())
}

fn inspect(path: &Path) -> Result<Value> {
    inspect_table(path, "Items")
}

fn inspect_table(path: &Path, name: &str) -> Result<Value> {
    let output = cli()
        .arg("inspect")
        .arg(path)
        .args(["--table", name, "--rows"])
        .output()?;
    Ok(success(&output)?["tables"][0].clone())
}

fn entries(path: &Path, table: &Value, index: u16) -> Result<usize> {
    let mut budget = jet3::ResourceBudget::new(jet3::ResourceLimits::default());
    let mut database = jet3::DatabaseReader::open(path, &mut budget)?;
    let root = jet3::PageNumber::new(table["root"].as_u64().ok_or("table root")?);
    let definition = database.table_definition(root, &mut budget)?;
    Ok(database
        .index_tree(&definition, index, &mut budget)?
        .entries()
        .len())
}

fn first_row(path: &Path, table: &Value) -> Result<Value> {
    let mut budget = jet3::ResourceBudget::new(jet3::ResourceLimits::default());
    let mut database = jet3::DatabaseReader::open(path, &mut budget)?;
    let root = jet3::PageNumber::new(table["root"].as_u64().ok_or("table root")?);
    let definition = database.table_definition(root, &mut budget)?;
    let mut rows = database.rows(&definition, &mut budget)?;
    let locator = rows.next_row()?.ok_or("first row")?.locator();
    Ok(json!({"page":locator.page().get(),"slot":locator.slot()}))
}

#[test]
fn index_lifecycle_accepts_stdin_file_input_and_cp1252_names() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("schema.mdb");
    create(&path)?;
    let original = inspect(&path)?;
    let result = success(&request(
        "schema",
        &path,
        &json!({
            "operation":"create_index", "table":"Items",
            "index":{"name":"Café", "kind":"ordinary", "null_policy":"ignore_all_null",
                "fields":[{"column":"Clé", "direction":"descending"}]}
        }),
    )?)?;
    assert_eq!(
        result,
        json!({"ok":true,"operation":"create_index","file":path})
    );
    let table = inspect(&path)?;
    assert_eq!(table["rows"], original["rows"]);
    assert_eq!(table["indexes"][1]["name"], "Café");
    assert_eq!(
        table["physical_indexes"][1]["fields"][0]["direction"],
        "Descending"
    );
    assert_eq!(entries(&path, &table, 1)?, 2);

    let input = directory.path().join("rename.json");
    fs::write(
        &input,
        json!({"operation":"rename_index","table":"Items","index":"Café","name":"Renamed"})
            .to_string(),
    )?;
    let output = cli()
        .arg("schema")
        .arg(&path)
        .arg("--input")
        .arg(input)
        .output()?;
    assert_eq!(success(&output)?["operation"], "rename_index");
    let renamed = inspect(&path)?;
    assert_eq!(renamed["indexes"][1]["name"], "Renamed");
    assert_eq!(renamed["physical_indexes"], table["physical_indexes"]);

    let output = request(
        "schema",
        &path,
        &json!({"operation":"drop_index","table":"Items","index":"Renamed"}),
    )?;
    assert_eq!(success(&output)?["operation"], "drop_index");
    let dropped = inspect(&path)?;
    assert_eq!(dropped["rows"], original["rows"]);
    assert_eq!(dropped["indexes"], original["indexes"]);
    Ok(())
}

#[test]
fn append_fixed_and_memo_columns_preserves_old_rows_and_accepts_updates() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("schema.mdb");
    create(&path)?;
    let original = inspect(&path)?;
    let row = first_row(&path, &original)?;
    for column in [
        json!({"name":"Extra","type":"long"}),
        json!({"name":"Content","type":"memo","allow_zero_length":true}),
    ] {
        let output = request(
            "schema",
            &path,
            &json!({"operation":"create_column","table":"Items","column":column}),
        )?;
        assert_eq!(success(&output)?["operation"], "create_column");
    }
    let appended = inspect(&path)?;
    let mut expected = original["rows"].clone();
    for row in expected.as_array_mut().ok_or("rows")? {
        row["Extra"] = Value::Null;
        row["Content"] = Value::Null;
    }
    assert_eq!(appended["rows"], expected);
    assert_eq!(appended["indexes"], original["indexes"]);
    assert_eq!(first_row(&path, &appended)?, row);
    for (column, value) in [(2, json!({"long":99})), (3, json!({"memo":""}))] {
        success(&request(
            "mutate",
            &path,
            &json!({"operation":"update","table":"Items","row":row,"column":column,"value":value}),
        )?)?;
    }
    let changed = inspect(&path)?;
    let actual = changed["rows"].as_array().ok_or("rows")?;
    let original_rows = expected.as_array().ok_or("rows")?;
    assert_eq!(actual.len(), original_rows.len());
    assert_eq!(actual[0]["Id"], original_rows[0]["Id"]);
    assert_eq!(actual[0]["Clé"], original_rows[0]["Clé"]);
    assert_eq!(actual[0]["Extra"], 99);
    assert!(!actual[0]["Content"].is_null());
    assert_eq!(&actual[1..], &original_rows[1..]);
    assert_eq!(first_row(&path, &changed)?, row);
    Ok(())
}

#[test]
fn drop_append_insert_preserves_sparse_columns_and_autoincrement_sequence() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("schema.mdb");
    create(&path)?;
    for edit in [
        json!({"operation":"create_column","table":"Items","column":{"name":"Sequence","type":"auto_increment"}}),
        json!({"operation":"drop_column","table":"Items","column":"Clé"}),
        json!({"operation":"create_column","table":"Items","column":{"name":"Label","type":"text","size":20}}),
    ] {
        assert_eq!(
            success(&request("schema", &path, &edit)?)?["operation"],
            edit["operation"]
        );
    }
    assert_eq!(
        inspect(&path)?["rows"],
        json!([
            {"Id":3,"Sequence":1,"Label":null},
            {"Id":1,"Sequence":2,"Label":null},
            {"Id":2,"Sequence":3,"Label":null}
        ])
    );
    success(&request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Items","values":[{"long":4},"auto_increment",{"text":"New"}]}),
    )?)?;
    let inserted = inspect(&path)?;
    assert_eq!(
        inserted["rows"][3],
        json!({"Id":4,"Sequence":4,"Label":"New"})
    );
    assert_eq!(entries(&path, &inserted, 0)?, 4);

    success(&request(
        "schema",
        &path,
        &json!({"operation":"create_table","table":{"name":"Keep","columns":[{"name":"Value","type":"long"}]}}),
    )?)?;
    let keep = inspect_table(&path, "Keep")?;
    let dropped = request(
        "schema",
        &path,
        &json!({"operation":"drop_table","table":"Items"}),
    )?;
    assert_eq!(success(&dropped)?["operation"], "drop_table");
    assert_eq!(inspect_table(&path, "Keep")?, keep);
    let output = cli()
        .arg("inspect")
        .arg(&path)
        .args(["--table", "Items"])
        .output()?;
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stderr)?["error"],
        "inspect_failed"
    );
    Ok(())
}

#[test]
fn column_option_changes_retain_old_values_and_constrain_later_writes() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("schema.mdb");
    success(&request(
        "create",
        &path,
        &json!({"tables":[{
            "name":"Items", "columns":[{"name":"Label","type":"text","size":20,"allow_zero_length":true}],
            "rows":[[null],[{"text":""}],[{"text":"Present"}]]
        }]}),
    )?)?;
    let original = inspect(&path)?["rows"].clone();
    let edited = request(
        "schema",
        &path,
        &json!({"operation":"set_column_options","table":"Items","column":"Label","required":true,"allow_zero_length":false}),
    )?;
    assert_eq!(success(&edited)?["operation"], "set_column_options");
    assert_eq!(inspect(&path)?["rows"], original);
    let before = fs::read(&path)?;
    for value in [Value::Null, json!({"text":""})] {
        let refused = request(
            "mutate",
            &path,
            &json!({"operation":"insert","table":"Items","values":[value]}),
        )?;
        assert_eq!(refused.status.code(), Some(1));
        assert_eq!(fs::read(&path)?, before);
    }
    success(&request(
        "schema",
        &path,
        &json!({"operation":"set_column_options","table":"Items","column":"Label","allow_zero_length":true}),
    )?)?;
    let before = fs::read(&path)?;
    let refused = request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Items","values":[null]}),
    )?;
    assert_eq!(refused.status.code(), Some(1));
    assert_eq!(fs::read(&path)?, before);
    success(&request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Items","values":[{"text":""}]}),
    )?)?;
    success(&request(
        "schema",
        &path,
        &json!({"operation":"set_column_options","table":"Items","column":"Label","required":false}),
    )?)?;
    success(&request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Items","values":[null]}),
    )?)?;
    assert_eq!(
        inspect(&path)?["rows"],
        json!([
            {"Label":null},{"Label":""},{"Label":"Present"},{"Label":""},{"Label":null}
        ])
    );
    Ok(())
}

#[test]
fn relationship_lifecycle_checks_rows_and_retains_shared_ordinary_indexes() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("schema.mdb");
    success(&request(
        "create",
        &path,
        &json!({"tables":[
            {"name":"Parent","columns":[{"name":"Id","type":"long"},{"name":"Alternate","type":"long"}],
             "indexes":[{"name":"ById","kind":"primary","fields":[{"column":"Id"}]},{"name":"ByAlternate","kind":"unique","fields":[{"column":"Alternate"}]}],
             "rows":[[{"long":1},{"long":10}]]},
            {"name":"Child","columns":[{"name":"ParentId","type":"long"},{"name":"Alternate","type":"long"}],
             "indexes":[{"name":"Existing","kind":"ordinary","fields":[{"column":"ParentId"}]}],
             "rows":[[{"long":1},{"long":10}]]}
        ]}),
    )?)?;
    let child = inspect_table(&path, "Child")?;
    let orphan = json!({"name":"Invalid","parent":{"table":"Parent","column":"Alternate"},"child":{"table":"Child","column":"ParentId"}});
    let before = fs::read(&path)?;
    let refused = request(
        "schema",
        &path,
        &json!({"operation":"create_relationship","relationship":orphan}),
    )?;
    assert_eq!(refused.status.code(), Some(1));
    assert_eq!(
        serde_json::from_slice::<Value>(&refused.stderr)?["error"],
        "schema_failed"
    );
    assert_eq!(fs::read(&path)?, before);

    let relation = json!({"name":"ParentChild","parent":{"table":"Parent","column":"Id"},"child":{"table":"Child","column":"ParentId"}});
    let created = request(
        "schema",
        &path,
        &json!({"operation":"create_relationship","relationship":relation}),
    )?;
    assert_eq!(success(&created)?["operation"], "create_relationship");
    let related = inspect_table(&path, "Child")?;
    assert_eq!(related["physical_indexes"], child["physical_indexes"]);
    assert_eq!(related["indexes"].as_array().ok_or("indexes")?.len(), 2);
    let before = fs::read(&path)?;
    let refused = request(
        "schema",
        &path,
        &json!({"operation":"replace_relationship","name":"ParentChild","relationship":orphan}),
    )?;
    assert_eq!(refused.status.code(), Some(1));
    assert_eq!(fs::read(&path)?, before);

    let replacement = json!({"name":"AlternateRelation","cascade_updates":true,"cascade_deletes":true,
        "parent":{"table":"Parent","columns":["Alternate"]},"child":{"table":"Child","columns":["Alternate"]}});
    let replaced = request(
        "schema",
        &path,
        &json!({"operation":"replace_relationship","name":"ParentChild","relationship":replacement}),
    )?;
    assert_eq!(success(&replaced)?["operation"], "replace_relationship");
    let parent = inspect_table(&path, "Parent")?;
    let row = first_row(&path, &parent)?;
    success(&request(
        "mutate",
        &path,
        &json!({"operation":"update","table":"Parent","row":row,"column":1,"value":{"long":20}}),
    )?)?;
    assert_eq!(
        inspect_table(&path, "Child")?["rows"],
        json!([{"ParentId":1,"Alternate":20}])
    );
    let dropped = request(
        "schema",
        &path,
        &json!({"operation":"drop_relationship","name":"AlternateRelation"}),
    )?;
    assert_eq!(success(&dropped)?["operation"], "drop_relationship");
    let retained = inspect_table(&path, "Child")?;
    assert_eq!(retained["indexes"], child["indexes"]);
    assert_eq!(retained["physical_indexes"], child["physical_indexes"]);
    success(&request(
        "mutate",
        &path,
        &json!({"operation":"delete","table":"Parent","row":row}),
    )?)?;
    assert_eq!(inspect_table(&path, "Child")?["rows"], retained["rows"]);

    // An unenforced relationship over an orphan key; inspect reports it in request spelling.
    let loose = json!({"name":"Loose","enforce":false,"join":"left_and_right",
        "parent":{"table":"Parent","column":"Alternate"},"child":{"table":"Child","column":"ParentId"}});
    success(&request(
        "schema",
        &path,
        &json!({"operation":"create_relationship","relationship":loose}),
    )?)?;
    let relationships = &success(&cli().arg("inspect").arg(&path).output()?)?["relationships"];
    assert_eq!(relationships[0]["join"], "left_and_right");
    assert_eq!(relationships[0]["enforced"], false);
    assert_eq!(relationships[0]["raw_attributes"], 0x0300_0002);
    assert_eq!(inspect_table(&path, "Child")?["indexes"], child["indexes"]);
    Ok(())
}

#[test]
fn create_table_and_rename_columns_retain_values_and_column_properties() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("schema.mdb");
    create(&path)?;
    let original = inspect(&path)?;
    let output = request(
        "schema",
        &path,
        &json!({"operation":"create_table","table":{
            "name":"Notes", "columns":[
                {"name":"Id","type":"auto_increment"},
                {"name":"Title","type":"text","size":40,"required":true},
                {"name":"Content","type":"memo","allow_zero_length":true}
            ], "indexes":[{"name":"ById","kind":"primary","fields":[{"column":"Id"}]}]
        }}),
    )?;
    assert_eq!(success(&output)?["operation"], "create_table");
    let added = inspect_table(&path, "Notes")?;
    assert_eq!(added["rows"], json!([]));
    assert_eq!(added["indexes"][0]["name"], "ById");
    assert_eq!(inspect(&path)?, original);

    let before = fs::read(&path)?;
    let refused = request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Notes",
            "values":["auto_increment",null,{"memo":""}]
        }),
    )?;
    assert_eq!(refused.status.code(), Some(1));
    assert_eq!(fs::read(&path)?, before);
    success(&request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Notes",
            "values":["auto_increment",{"text":"Present"},{"memo":""}]
        }),
    )?)?;
    let inserted = inspect_table(&path, "Notes")?;
    let rows = inserted["rows"].as_array().ok_or("rows")?;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0]["Id"], 1);
    assert_eq!(rows[0]["Title"], "Present");
    assert!(!rows[0]["Content"].is_null());
    assert_eq!(inspect(&path)?, original);

    for (column, name) in [("Title", "Résumé"), ("Content", "Body")] {
        let output = request(
            "schema",
            &path,
            &json!({"operation":"rename_column","table":"Notes","column":column,"name":name}),
        )?;
        assert_eq!(success(&output)?["operation"], "rename_column");
    }
    let renamed = inspect_table(&path, "Notes")?;
    assert_eq!(renamed["rows"][0]["Résumé"], rows[0]["Title"]);
    assert_eq!(renamed["rows"][0]["Body"], rows[0]["Content"]);
    assert_eq!(renamed["indexes"], inserted["indexes"]);
    let before = fs::read(&path)?;
    let refused = request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Notes",
            "values":["auto_increment",null,{"memo":""}]
        }),
    )?;
    assert_eq!(refused.status.code(), Some(1));
    assert_eq!(fs::read(&path)?, before);
    success(&request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Notes",
            "values":["auto_increment",{"text":"Second"},{"memo":""}]
        }),
    )?)?;
    let after = inspect_table(&path, "Notes")?;
    assert_eq!(after["rows"].as_array().ok_or("rows")?.len(), 2);
    assert_eq!(after["rows"][1]["Résumé"], "Second");
    assert!(!after["rows"][1]["Body"].is_null());
    assert_eq!(inspect(&path)?, original);
    Ok(())
}

#[test]
fn table_rename_preserves_contents_and_accepts_cp1252_names() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("schema.mdb");
    create(&path)?;
    let original = inspect(&path)?;
    let output = request(
        "schema",
        &path,
        &json!({"operation":"rename_table","table":"Items","name":"Éléments"}),
    )?;
    assert_eq!(success(&output)?["operation"], "rename_table");
    let renamed = inspect_table(&path, "Éléments")?;
    assert_eq!(renamed["name"], "Éléments");
    for field in ["root", "columns", "indexes", "physical_indexes", "rows"] {
        assert_eq!(renamed[field], original[field], "{field}");
    }
    let before = fs::read(&path)?;
    let refused = request(
        "schema",
        &path,
        &json!({"operation":"rename_table","table":"Items","name":"Other"}),
    )?;
    assert_eq!(refused.status.code(), Some(1));
    assert_eq!(
        serde_json::from_slice::<Value>(&refused.stderr)?["error"],
        "schema_failed"
    );
    assert_eq!(fs::read(&path)?, before);
    Ok(())
}

#[test]
fn create_and_schema_share_null_policy_and_direction_defaults() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("schema.mdb");
    success(&request(
        "create",
        &path,
        &json!({"tables":[{
            "name":"Items", "columns":[{"name":"Id","type":"long"}],
            "indexes":[{"name":"Ignored","kind":"ordinary","null_policy":"ignore_all_null","fields":[{"column":"Id"}]}],
            "rows":[[{"long":1}], [null]]
        }]}),
    )?)?;
    let table = inspect(&path)?;
    assert_eq!(entries(&path, &table, 0)?, 1);
    success(&request(
        "schema",
        &path,
        &json!({"operation":"create_index","table":"Items",
            "index":{"name":"Included","kind":"ordinary","fields":[{"column":"Id"}]}
        }),
    )?)?;
    let table = inspect(&path)?;
    assert_eq!(entries(&path, &table, 1)?, 2);
    assert_eq!(
        table["physical_indexes"][1]["fields"][0]["direction"],
        "Ascending"
    );

    let required = directory.path().join("required.mdb");
    create(&required)?;
    success(&request(
        "schema",
        &required,
        &json!({"operation":"create_index","table":"Items",
            "index":{"name":"Required","kind":"ordinary","null_policy":"required","fields":[{"column":"Id"}]}
        }),
    )?)?;
    assert_eq!(inspect(&required)?["physical_indexes"][1]["required"], true);
    Ok(())
}

#[test]
fn refused_schema_requests_report_json_errors_and_preserve_the_file() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("schema.mdb");
    create(&path)?;
    let original = fs::read(&path)?;
    for input in [
        json!({"operation":"unknown","table":"Items"}),
        json!({"operation":"set_column_options","table":"Items","column":"Clé","required":"yes"}),
        json!({"operation":"set_column_options","table":"Items","column":"Clé","size":20}),
        json!({"operation":"drop_table","table":"Missing"}),
        json!({"operation":"drop_column","table":"Items","column":"Id"}),
        json!({"operation":"drop_column","table":"Items","column":"Missing"}),
        json!({"operation":"create_column","table":"Items","column":{"name":"Extra","type":"text"}}),
        json!({"operation":"create_column","table":"Items","column":{"name":"Extra","type":"long","size":4}}),
        json!({"operation":"create_column","table":"Items","column":{"name":"Id","type":"long"}}),
        json!({"operation":"rename_column","table":"Items","column":"Clé","name":"Id"}),
        json!({"operation":"rename_column","table":"Items","column":"Missing","name":"Other"}),
        json!({"operation":"create_table","table":{"name":"New","columns":[{"name":"Id","type":"long"}],"rows":[]}}),
        json!({"operation":"create_table","table":{"name":"Items","columns":[{"name":"Id","type":"long"}]}}),
        json!({"operation":"drop_index","table":"Items","index":"ById","overwrite":true}),
        json!({"operation":"drop_index","table":"Missing","index":"ById"}),
        json!({"operation":"drop_index","table":"Items","index":"Missing"}),
        json!({"operation":"rename_index","table":"Items","index":"ById","name":"漢"}),
        json!({"operation":"create_index","table":"Items","index":{"name":"ById","kind":"ordinary","fields":[{"column":"Clé"}]}}),
        json!({"operation":"create_index","table":"Items","index":{"name":"New","kind":"ordinary","unknown":true,"fields":[{"column":"Clé"}]}}),
        json!({"operation":"create_index","table":"Items","index":{"name":"New","kind":"ordinary","null_policy":"invalid","fields":[{"column":"Clé"}]}}),
        json!({"operation":"create_index","table":"Items","index":{"name":"New","kind":"ordinary","fields":[{"column":"Clé","direction":"invalid"}]}}),
        json!({"operation":"create_index","table":"Items","index":{"name":"New","kind":"unique","fields":[{"column":"Clé"}]}}),
        json!({"operation":"create_index","table":"Items","index":{"name":"New","kind":"ordinary","null_policy":"required","fields":[{"column":"Clé"}]}}),
    ] {
        let result = request("schema", &path, &input)?;
        assert_eq!(result.status.code(), Some(1), "{input}");
        assert!(result.stdout.is_empty());
        let error: Value = serde_json::from_slice(&result.stderr)?;
        assert_eq!(error["error"], "schema_failed");
        assert!(matches!(
            error.get("publication_stage"),
            Some(Value::Null | Value::String(_))
        ));
        assert_eq!(fs::read(&path)?, original);
    }
    Ok(())
}

#[test]
fn schema_requires_one_input_option_and_is_listed_in_help() -> Result {
    for args in [
        vec!["schema"],
        vec!["schema", "file.mdb"],
        vec!["schema", "file.mdb", "--input"],
        vec!["schema", "file.mdb", "--input", "-", "--input", "-"],
    ] {
        let output = cli().args(args).output()?;
        assert_eq!(output.status.code(), Some(2));
        assert!(output.stdout.is_empty());
        assert_eq!(
            serde_json::from_slice::<Value>(&output.stderr)?["ok"],
            false
        );
    }
    let help = cli().arg("--help").output()?;
    assert!(help.status.success());
    assert!(
        String::from_utf8_lossy(&help.stdout)
            .contains("schema <file.mdb> --input <request.json|->")
    );
    Ok(())
}

#[test]
fn replacing_index_is_atomic_when_new_keys_violate_constraints() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("schema.mdb");
    create(&path)?;
    let before = fs::read(&path)?;
    let output = request(
        "schema",
        &path,
        &json!({
            "operation":"replace_index", "table":"Items", "index":"ById",
            "replacement":{"name":"UniqueOther", "kind":"unique", "fields":[{"column":"Clé"}]}
        }),
    )?;
    assert!(!output.status.success());
    assert_eq!(fs::read(&path)?, before);
    success(&request(
        "schema",
        &path,
        &json!({
            "operation":"replace_index", "table":"Items", "index":"ById",
            "replacement":{"name":"DescendingId", "kind":"unique", "fields":[{"column":"Id","direction":"descending"}]}
        }),
    )?)?;
    let table = inspect(&path)?;
    assert_eq!(table["indexes"][0]["name"], "DescendingId");
    assert_eq!(
        table["physical_indexes"][0]["fields"][0]["direction"],
        "Descending"
    );
    assert_eq!(entries(&path, &table, 0)?, 3);
    Ok(())
}

#[test]
fn text_properties_are_created_edited_inspected_and_enforced() -> Result {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("properties.mdb");
    success(&request(
        "create",
        &path,
        &json!({"tables":[{
            "name":"Items",
            "columns":[
                {"name":"Id","type":"long","description":"Clé"},
                {"name":"Qty","type":"long","default_value":"0","validation_text":"no"}
            ],
            "validation_text":"table message"
        }]}),
    )?)?;
    let properties = &inspect(&path)?["properties"];
    assert_eq!(properties["validation_rule"], Value::Null);
    assert_eq!(properties["validation_text"], "table message");
    assert_eq!(properties["columns"][0]["description"], "Clé");
    assert_eq!(properties["columns"][1]["default_value"], "0");
    success(&request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Items","values":[{"long":1},null]}),
    )?)?;
    success(&request(
        "schema",
        &path,
        &json!({"operation":"set_column_properties","table":"Items","column":"Qty",
                "validation_rule":">=0","default_value":null}),
    )?)?;
    success(&request(
        "schema",
        &path,
        &json!({"operation":"set_table_properties","table":"Items","validation_rule":"[Qty]<9"}),
    )?)?;
    let properties = &inspect(&path)?["properties"];
    assert_eq!(properties["validation_rule"], "[Qty]<9");
    assert_eq!(properties["columns"][1]["validation_rule"], ">=0\u{0}");
    assert_eq!(properties["columns"][1]["default_value"], Value::Null);
    assert_eq!(properties["columns"][1]["validation_text"], "no");
    let before = fs::read(&path)?;
    let refused = request(
        "mutate",
        &path,
        &json!({"operation":"insert","table":"Items","values":[{"long":2},{"long":1}]}),
    )?;
    assert!(!refused.status.success());
    assert!(String::from_utf8_lossy(&refused.stderr).contains("ValidationRule"));
    assert_eq!(fs::read(&path)?, before);
    Ok(())
}
