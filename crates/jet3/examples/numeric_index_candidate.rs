//! Finite public numeric-index candidates for EXP-0183.
use jet3::{
    ColumnSpec, ColumnType, IndexColumnSpec, IndexDirection, IndexKind, IndexNullPolicy, IndexSpec,
    ResourceBudget, ResourceLimits, RowValue, TableSpec, create_database_with_rows,
};
use std::{env, fs, path::Path};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = env::args().skip(1).collect();
    let [directory] = args.as_slice() else {
        return Err("usage: numeric_index_candidate NEW_DIRECTORY".into());
    };
    let directory = Path::new(directory);
    fs::create_dir(directory)?;
    for name in [
        "boolean-asc",
        "byte-desc",
        "integer-asc",
        "currency-desc",
        "single-asc",
        "double-ignore",
        "mixed-include",
        "mixed-required",
    ] {
        let (types, directions, policy, mut rows) = match name {
            "boolean-asc" => (
                vec![ColumnType::Boolean],
                vec![IndexDirection::Ascending],
                IndexNullPolicy::Include,
                vec![
                    vec![RowValue::Boolean(true)],
                    vec![RowValue::Boolean(false)],
                ],
            ),
            "byte-desc" => (
                vec![ColumnType::Byte],
                vec![IndexDirection::Descending],
                IndexNullPolicy::Include,
                [0, 1, 127, 128, 255]
                    .into_iter()
                    .map(|v| vec![RowValue::Byte(v)])
                    .collect(),
            ),
            "integer-asc" => (
                vec![ColumnType::Integer],
                vec![IndexDirection::Ascending],
                IndexNullPolicy::Include,
                [i16::MIN, -256, -1, 0, 1, 255, 256, i16::MAX]
                    .into_iter()
                    .map(|v| vec![RowValue::Integer(v)])
                    .collect(),
            ),
            "currency-desc" => (
                vec![ColumnType::Currency],
                vec![IndexDirection::Descending],
                IndexNullPolicy::Include,
                [i64::MIN, -10000, -1, 0, 1, 10000, i64::MAX]
                    .into_iter()
                    .map(|v| vec![RowValue::Currency { scaled: v }])
                    .collect(),
            ),
            "single-asc" => (
                vec![ColumnType::Single],
                vec![IndexDirection::Ascending],
                IndexNullPolicy::Include,
                [
                    -f32::MAX,
                    -1.0,
                    0.0,
                    f32::from_bits(1),
                    f32::MIN_POSITIVE,
                    1.0,
                    f32::MAX,
                ]
                .into_iter()
                .map(|v| vec![RowValue::Single(v)])
                .collect(),
            ),
            "double-ignore" => {
                let mut rows: Vec<_> = [
                    -f64::MAX,
                    -1.0,
                    0.0,
                    f64::from_bits(1),
                    f64::MIN_POSITIVE,
                    1.0,
                    f64::MAX,
                ]
                .into_iter()
                .map(|v| vec![RowValue::Double(v)])
                .collect();
                rows.extend([vec![RowValue::Null], vec![RowValue::Null]]);
                (
                    vec![ColumnType::Double],
                    vec![IndexDirection::Descending],
                    IndexNullPolicy::IgnoreAllNull,
                    rows,
                )
            }
            "mixed-include" => {
                let mut rows: Vec<_> = (0..120)
                    .map(|v| {
                        vec![
                            RowValue::Currency { scaled: v - 60 },
                            RowValue::Double(v as f64),
                        ]
                    })
                    .collect();
                for _ in 0..2 {
                    rows.extend([
                        vec![RowValue::Null, RowValue::Null],
                        vec![RowValue::Null, RowValue::Double(1.0)],
                        vec![RowValue::Currency { scaled: 1 }, RowValue::Null],
                    ]);
                }
                (
                    vec![ColumnType::Currency, ColumnType::Double],
                    vec![IndexDirection::Ascending, IndexDirection::Descending],
                    IndexNullPolicy::Include,
                    rows,
                )
            }
            "mixed-required" => (
                vec![ColumnType::Integer, ColumnType::Single],
                vec![IndexDirection::Ascending, IndexDirection::Descending],
                IndexNullPolicy::Required,
                vec![
                    vec![RowValue::Integer(i16::MIN), RowValue::Single(-1.0)],
                    vec![RowValue::Integer(0), RowValue::Single(0.0)],
                    vec![RowValue::Integer(i16::MAX), RowValue::Single(1.0)],
                ],
            ),
            _ => return Err("unknown arm".into()),
        };
        let names = [b"A".as_slice(), b"B".as_slice()];
        let mut columns: Vec<_> = types
            .into_iter()
            .enumerate()
            .map(|(n, t)| ColumnSpec::new(names[n], t))
            .collect();
        columns.push(ColumnSpec::new(b"Tag", ColumnType::Long));
        for (n, row) in rows.iter_mut().enumerate() {
            row.push(RowValue::Long(n as i32 + 1));
        }
        let fields: Vec<_> = directions
            .into_iter()
            .enumerate()
            .map(|(n, d)| {
                if d == IndexDirection::Ascending {
                    IndexColumnSpec::ascending(n as u16)
                } else {
                    IndexColumnSpec::descending(n as u16)
                }
            })
            .collect();
        let indexes = [IndexSpec {
            name: b"ByKey",
            kind: IndexKind::Unique.with_null_policy(policy),
            fields: &fields,
        }];
        let rows: Vec<_> = rows.iter().map(|r| r.as_slice()).collect();
        create_database_with_rows(
            directory.join(format!("{name}.mdb")),
            &TableSpec {
                name: b"Rows",
                columns: &columns,
                indexes: &indexes,
            },
            &rows,
            &mut ResourceBudget::new(ResourceLimits::default()),
        )?;
    }
    Ok(())
}
