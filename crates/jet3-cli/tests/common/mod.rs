use serde_json::Value;
use std::{
    io::Write,
    path::Path,
    process::{Command, Output, Stdio},
};

pub type Result<T = ()> = std::result::Result<T, Box<dyn std::error::Error>>;

pub fn cli() -> Command {
    Command::new(env!("CARGO_BIN_EXE_jet3-cli"))
}

/// Runs `jet3-cli <command> <path> --input -` with `request` on stdin.
pub fn request(command: &str, path: &Path, request: &Value) -> Result<Output> {
    let mut process = cli()
        .arg(command)
        .arg(path)
        .args(["--input", "-"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    process
        .stdin
        .take()
        .ok_or("missing stdin")?
        .write_all(request.to_string().as_bytes())?;
    Ok(process.wait_with_output()?)
}
