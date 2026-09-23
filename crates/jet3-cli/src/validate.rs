use std::{ffi::OsString, path::PathBuf};

use jet3::{ByteCount, DatabaseReader, ReadLimits, ResourceBudget, ResourceLimits, TextCodePage};
use serde_json::json;

pub const HELP: &str = "\
validate: check reachable table data without modifying the file
  jet3-cli validate <file> [--code-page 1252|1251] \
    [--max-input-bytes <bytes>] [--max-work-units <units>]

Checks catalog records, user/system definitions, row counts, values, Memo/OLE chains,
index row membership, supported scalar keys and branch bounds, unique row/payload
storage, catalogued allocation ownership, and supported Long relationship constraints.
Reports unsupported relationship and key schemas separately. Skips non-table
object contents. Success is not whole-file validity or a compatibility claim.
";

#[derive(Debug)]
pub struct ValidateCommand {
    path: PathBuf,
    code_page: TextCodePage,
    max_input_bytes: u64,
    max_work_units: u64,
}

pub fn parse_args(
    mut args: impl Iterator<Item = OsString>,
) -> Result<ValidateCommand, &'static str> {
    let path = args.next().ok_or("missing_file")?;
    if path.to_string_lossy().starts_with('-') {
        return Err("missing_file");
    }
    let mut command = ValidateCommand {
        path: path.into(),
        code_page: TextCodePage::Windows1252,
        max_input_bytes: super::DEFAULT_MAX_INPUT_BYTES,
        max_work_units: ResourceLimits::default().max_total_work_units(),
    };
    let mut seen = [false; 3];
    while let Some(option) = args.next() {
        let index = match option.to_str() {
            Some("--code-page") => 0,
            Some("--max-input-bytes") => 1,
            Some("--max-work-units") => 2,
            _ => return Err("unknown_option"),
        };
        if std::mem::replace(&mut seen[index], true) {
            return Err("duplicate_option");
        }
        if index == 0 {
            let value = args.next().ok_or("missing_option_value")?;
            command.code_page = match value.to_str() {
                Some("1252") => TextCodePage::Windows1252,
                Some("1251") => TextCodePage::Windows1251,
                _ => return Err("invalid_code_page"),
            };
        } else {
            let value = super::parse_u64(args.next(), "missing_option_value", "invalid_limit")?;
            if index == 1 {
                command.max_input_bytes = value;
            } else {
                command.max_work_units = value;
            }
        }
    }
    Ok(command)
}

pub fn run(command: &ValidateCommand) -> Result<String, String> {
    let defaults = ReadLimits::default();
    let read = ReadLimits::new(
        ByteCount::new(command.max_input_bytes),
        defaults.max_single_read_bytes(),
        defaults.max_total_read_bytes(),
    );
    let mut budget = ResourceBudget::new(
        ResourceLimits::new(read).with_max_total_work_units(command.max_work_units),
    );
    let mut database =
        DatabaseReader::open(&command.path, &mut budget).map_err(|e| e.to_string())?;
    let report = database
        .validate(command.code_page, &mut budget)
        .map_err(|e| e.to_string())?;
    Ok(json!({
        "schema_version": 1,
        "ok": true,
        "scope": "catalogued_allocations_and_tables",
        "file": command.path,
        "checked": {
            "catalog_objects": report.catalog_objects,
            "user_tables": report.user_tables,
            "system_tables": report.system_tables,
            "rows": report.rows,
            "values": report.values,
            "indexes": report.indexes,
            "index_entries": report.index_entries,
            "indexes_with_verified_keys": report.indexes_with_verified_keys,
            "relationship_catalog_rows": report.relationship_catalog_rows,
            "relationships_with_verified_keys": report.relationships_with_verified_keys,
            "unenforced_relationships": report.unenforced_relationships,
            "relationship_inventory_checked": report.relationship_inventory_checked,
            "long_values": report.long_values,
            "long_value_bytes": report.long_value_bytes,
        },
        "coverage_limits": {
            "skipped_system_objects": report.skipped_system_objects,
            "skipped_other_objects": report.skipped_other_objects,
            "uninterpreted_relationship_rows": report.uninterpreted_relationship_rows,
            "uninterpreted_indexes": report.uninterpreted_indexes,
            "uninterpreted_index_entries": report.uninterpreted_index_entries,
            "not_checked": ["non_table_object_contents",
                "unreferenced_pages_and_allocation_slack", "unsupported_relationship_forms",
                "unsupported_index_key_schemas", "application_compatibility"],
        },
        "resources": {
            "bytes_read": budget.read_budget().total_read().get(),
            "allocation_bytes": budget.allocation_bytes().get(),
            "decoded_bytes": budget.decoded_bytes().get(),
            "page_visits": budget.page_visits(),
            "total_work_units": budget.total_work_units(),
        },
    })
    .to_string()
        + "\n")
}
