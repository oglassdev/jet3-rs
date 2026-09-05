//! Deterministic bounded Memo property candidates; no DAO acceptance claim.
use jet3::{
    ColumnSpec, ColumnType, ResourceBudget, ResourceLimits, RowValue, TableSpec,
    create_database_with_rows,
};
use std::{env, fs, path::Path};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args().skip(1).collect();
    let [directory] = args.as_slice() else {
        return Err("usage: memo_allow_empty_candidate NEW_DIRECTORY".into());
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    for (file, table, memo) in [
        ("short.mdb", b"Rows".as_slice(), b"M".as_slice()),
        (
            "renamed.mdb",
            b"Ledger7".as_slice(),
            b"Memo42Long".as_slice(),
        ),
    ] {
        let columns = [
            ColumnSpec::new(b"Id", ColumnType::Long),
            ColumnSpec::new(memo, ColumnType::Memo).with_allow_zero_length(),
        ];
        let rows: [&[RowValue<'_>]; 3] = [
            &[RowValue::Long(1), RowValue::Null],
            &[RowValue::Long(2), RowValue::Memo(b"")],
            &[RowValue::Long(3), RowValue::Memo(b"A")],
        ];
        create_database_with_rows(
            directory.join(file),
            &TableSpec {
                name: table,
                columns: &columns,
                indexes: &[],
            },
            &rows,
            &mut ResourceBudget::new(ResourceLimits::default()),
        )?;
    }
    Ok(())
}
