//! Public field-update fixture generation and independent retained-byte check.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.len() != 3 {
        return Err("usage: jet3-update-fixture generate|verify SCENARIO DIRECTORY".into());
    }
    jet3_testkit::update_fixture(&args[0], &args[1], std::path::Path::new(&args[2]))
}
