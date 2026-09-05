//! Deterministic EXP-0126-shaped candidates for subsequent DAO validation.
use jet3::{
    ColumnRef, ColumnSpec, ColumnType, IndexColumnSpec, IndexDirection, IndexKind, IndexSpec,
    ResourceBudget, ResourceLimits, RowValue, TableSpec, create_database_with_rows,
};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let path = args
        .next()
        .ok_or("usage: composite_index_candidate OUTPUT.mdb ARM")?;
    let arm = args.next().ok_or("ARM required")?;
    let (directions, kind, input) = match arm.to_str() {
        Some("descending-unique") => (
            &[IndexDirection::Descending][..],
            IndexKind::Unique,
            [i32::MAX, i32::MIN, 0, -1, 1, -256, 255, 256, -65536, 65536]
                .into_iter()
                .enumerate()
                .map(|(tag, a)| [a, 0, tag as i32])
                .collect::<Vec<_>>(),
        ),
        Some("ascending-descending-unique") => (
            &[IndexDirection::Ascending, IndexDirection::Descending][..],
            IndexKind::Unique,
            PAIRS[..12].to_vec(),
        ),
        Some("descending-ascending-ordinary") => (
            &[IndexDirection::Descending, IndexDirection::Ascending][..],
            IndexKind::Ordinary,
            PAIRS.to_vec(),
        ),
        _ => return Err("unknown EXP-0126 arm".into()),
    };
    let columns = [
        ColumnSpec::new(b"A", ColumnType::Long),
        ColumnSpec::new(b"B", ColumnType::Long),
        ColumnSpec::new(b"Tag", ColumnType::Long),
    ];
    let fields = directions
        .iter()
        .enumerate()
        .map(|(ordinal, direction)| IndexColumnSpec {
            column: ColumnRef::Ordinal(ordinal as u16),
            direction: *direction,
        })
        .collect::<Vec<_>>();
    let indexes = [IndexSpec {
        name: b"ByKey",
        fields: &fields,
        kind,
    }];
    let table = TableSpec {
        name: b"Rows",
        columns: &columns,
        indexes: &indexes,
    };
    let values = input
        .iter()
        .map(|row| row.map(RowValue::Long))
        .collect::<Vec<_>>();
    let rows = values.iter().map(|row| row.as_slice()).collect::<Vec<_>>();
    create_database_with_rows(
        path,
        &table,
        &rows,
        &mut ResourceBudget::new(ResourceLimits::default()),
    )?;
    Ok(())
}

const PAIRS: [[i32; 3]; 14] = [
    [0, 0, 0],
    [i32::MIN, i32::MAX, 1],
    [i32::MAX, i32::MIN, 2],
    [-1, -1, 3],
    [-1, 0, 4],
    [-1, 1, 5],
    [0, i32::MIN, 6],
    [0, i32::MAX, 7],
    [1, -256, 8],
    [1, 255, 9],
    [256, -65536, 10],
    [-256, 65536, 11],
    [0, 0, 12],
    [-1, 0, 13],
];
