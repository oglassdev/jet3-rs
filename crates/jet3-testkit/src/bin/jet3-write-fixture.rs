//! Creates a declared protocol write fixture through the public API.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.len() != 2 {
        return Err("usage: jet3-write-fixture SCENARIO OUTPUT.mdb".into());
    }
    jet3_testkit::write_fixture(&args[0], std::path::Path::new(&args[1]))
}
