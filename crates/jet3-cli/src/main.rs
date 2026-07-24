#![forbid(unsafe_code)]

use std::env;
use std::process::ExitCode;

const HELP: &str = "\
jet3-cli — diagnostics for Access 97 / Jet 3 databases

Usage:
  jet3-cli --help
  jet3-cli --version

Database commands are not implemented in the bootstrap scaffold.
";

fn main() -> ExitCode {
    match env::args().nth(1).as_deref() {
        None | Some("--help" | "-h") => {
            println!("{HELP}");
            ExitCode::SUCCESS
        }
        Some("--version" | "-V") => {
            println!("jet3-cli {}", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
        Some(argument) => {
            eprintln!("unknown argument: {argument}\n\n{HELP}");
            ExitCode::from(2)
        }
    }
}
