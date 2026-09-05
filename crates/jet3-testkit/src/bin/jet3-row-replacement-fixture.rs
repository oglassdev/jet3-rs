fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    let [command, scenario, directory] = args.as_slice() else {
        return Err(
            "usage: jet3-row-replacement-fixture generate|verify SCENARIO DIRECTORY".into(),
        );
    };
    jet3_testkit::row_replacement_fixture(command, scenario, std::path::Path::new(directory))
}
