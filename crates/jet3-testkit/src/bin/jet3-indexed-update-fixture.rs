fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let [command, id, directory] = args.as_slice() else {
        return Err("usage: jet3-indexed-update-fixture generate|verify SCENARIO DIRECTORY".into());
    };
    jet3_testkit::indexed_update_fixture(command, id, std::path::Path::new(directory))
}
