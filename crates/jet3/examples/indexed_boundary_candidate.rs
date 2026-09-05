//! Deterministic public insertion matrix for the indexed data-page boundary.
#[path = "support/indexed_boundary.rs"]
mod fixture;
use fixture::*;
use std::{env, fs, path::Path};
fn main() -> Result<()> {
    let directory = env::args()
        .nth(1)
        .ok_or("usage: indexed_boundary_candidate NEW_DIRECTORY")?;
    let directory = Path::new(&directory);
    fs::create_dir(directory)?;
    for (name, count, id, refusal) in [
        ("space", 3, 101, None),
        ("eof", 20, 101, None),
        ("duplicate", 20, 0, Some("duplicate unique key")),
        ("split", 200, 201, Some("full root leaf")),
    ] {
        let original = directory.join(format!("{name}-original.mdb"));
        let candidate = directory.join(format!("{name}-candidate.mdb"));
        create(&original, count)?;
        fs::copy(&original, &candidate)?;
        let result = jet3::insert_row(&candidate, b"Items", &values(id), &mut budget());
        if let Some(message) = refusal {
            if !matches!(result,Err(jet3::UpdateError::Unsupported(m)) if m==message)
                || fs::read(&original)? != fs::read(&candidate)?
            {
                return Err("refusal or preservation".into());
            }
        } else {
            let row = result?;
            let size = fs::metadata(&original)?.len();
            if (name == "eof") != (row.page().get() * 2048 == size) {
                return Err("wrong insertion path".into());
            }
        }
        let _ = definition(&candidate)?;
    }
    Ok(())
}
