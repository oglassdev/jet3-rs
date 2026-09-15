//! Shared scalar models and finite mutation recipes.
use super::*;

const ID_FIELDS: [IndexColumnSpec<'static>; 1] = [IndexColumnSpec {
    column: ColumnRef::Ordinal(0),
    direction: IndexDirection::Ascending,
}];
const PAIR_FIELDS: [IndexColumnSpec<'static>; 2] = [
    IndexColumnSpec {
        column: ColumnRef::Ordinal(1),
        direction: IndexDirection::Ascending,
    },
    IndexColumnSpec {
        column: ColumnRef::Ordinal(2),
        direction: IndexDirection::Descending,
    },
];
const LAST_B: [IndexColumnSpec<'static>; 1] = [PAIR_FIELDS[1]];
const LAST_C: [IndexColumnSpec<'static>; 1] = [IndexColumnSpec {
    column: ColumnRef::Ordinal(3),
    direction: IndexDirection::Descending,
}];

#[derive(Clone, Debug, PartialEq)]
pub(super) enum Scalar {
    Null,
    Boolean(bool),
    Byte(u8),
    Integer(i16),
    Long(i32),
    Currency(i64),
    Single(f32),
    Double(f64),
    DateTime(f64),
    Binary(Vec<u8>),
}
impl Scalar {
    pub(super) fn value(&self) -> RowValue<'_> {
        match self {
            Self::Null => RowValue::Null,
            Self::Boolean(n) => RowValue::Boolean(*n),
            Self::Byte(n) => RowValue::Byte(*n),
            Self::Integer(n) => RowValue::Integer(*n),
            Self::Long(n) => RowValue::Long(*n),
            Self::Currency(scaled) => RowValue::Currency { scaled: *scaled },
            Self::Single(n) => RowValue::Single(*n),
            Self::Double(n) => RowValue::Double(*n),
            Self::DateTime(days) => RowValue::DateTime { days: *days },
            Self::Binary(bytes) => RowValue::Binary(bytes),
        }
    }
    pub(super) fn json(&self) -> String {
        match self {
            Self::Null => "null".into(),
            Self::Boolean(n) => n.to_string(),
            Self::Byte(n) => n.to_string(),
            Self::Integer(n) => n.to_string(),
            Self::Long(n) => n.to_string(),
            Self::Currency(n) => n.to_string(),
            Self::Single(n) => n.to_string(),
            Self::Double(n) | Self::DateTime(n) => n.to_string(),
            Self::Binary(bytes) => quote(&hex(bytes)),
        }
    }
    pub(super) fn read(kind: &ValueKind<'_>) -> Result<Self> {
        Ok(match kind {
            ValueKind::Null => Self::Null,
            ValueKind::Boolean(n) => Self::Boolean(*n),
            ValueKind::Byte(n) => Self::Byte(*n),
            ValueKind::Integer(n) => Self::Integer(*n),
            ValueKind::Long(n) => Self::Long(*n),
            ValueKind::Currency(n) => Self::Currency(n.scaled()),
            ValueKind::Single(n) => Self::Single(*n),
            ValueKind::Double(n) => Self::Double(*n),
            ValueKind::DateTime(n) => Self::DateTime(n.days()),
            ValueKind::Binary(bytes) => Self::Binary(bytes.to_vec()),
            _ => return Err("unexpected scalar type".into()),
        })
    }
}
pub(super) type Row = Vec<Scalar>;
pub(super) fn row_json(row: &[Scalar]) -> String {
    format!(
        "[{}]",
        row.iter().map(|v| v.json()).collect::<Vec<_>>().join(",")
    )
}
pub(super) fn values(row: &[Scalar]) -> Vec<RowValue<'_>> {
    row.iter().map(|v| v.value()).collect()
}
pub(super) fn id(row: &[Scalar]) -> Result<i32> {
    if let Some(Scalar::Long(n)) = row.first() {
        Ok(*n)
    } else {
        Err("Id type".into())
    }
}
pub(super) fn quote(text: &str) -> String {
    let mut out = String::from("\"");
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            c if c.is_control() => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}
pub(super) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
pub(super) fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::default())
}

#[derive(Clone, Copy, PartialEq)]
pub(super) enum Case {
    Integral,
    Wide,
    Deep,
}
impl Case {
    pub(super) fn name(self) -> &'static str {
        match self {
            Self::Integral => "integral",
            Self::Wide => "wide",
            Self::Deep => "deep",
        }
    }
    pub(super) fn count(self) -> i32 {
        match self {
            Self::Integral => 195,
            Self::Wide => 80,
            Self::Deep => 5673,
        }
    }
    pub(super) fn columns(self) -> Vec<ColumnSpec<'static>> {
        let rest = match self {
            Self::Integral => vec![ColumnType::Byte, ColumnType::Integer, ColumnType::Boolean],
            Self::Wide => vec![ColumnType::Currency, ColumnType::Double, ColumnType::Single],
            Self::Deep => vec![ColumnType::Currency, ColumnType::Double],
        };
        [ColumnType::Long]
            .into_iter()
            .chain(rest)
            .zip([b"Id".as_slice(), b"A", b"B", b"C"])
            .map(|(kind, name)| ColumnSpec::new(name, kind))
            .collect()
    }
    pub(super) fn indexes(self) -> [IndexSpec<'static>; 3] {
        [
            IndexSpec {
                name: b"ById",
                kind: IndexKind::Primary,
                fields: &ID_FIELDS,
            },
            IndexSpec {
                name: b"ByPair",
                kind: if self == Self::Integral {
                    IndexKind::Ordinary.with_null_policy(IndexNullPolicy::IgnoreAllNull)
                } else {
                    IndexKind::Unique
                },
                fields: &PAIR_FIELDS,
            },
            IndexSpec {
                name: b"ByLast",
                kind: if self == Self::Wide {
                    IndexKind::Unique.with_null_policy(IndexNullPolicy::IgnoreAllNull)
                } else {
                    IndexKind::Ordinary
                },
                fields: if self == Self::Deep { &LAST_B } else { &LAST_C },
            },
        ]
    }
    pub(super) fn row(self, id: i32) -> Row {
        use Scalar::*;
        match self {
            Self::Integral => vec![
                Long(id),
                if id % 11 == 0 {
                    Null
                } else {
                    Byte((id % 7) as u8)
                },
                if id % 13 == 0 {
                    Null
                } else {
                    Integer((id % 5 - 2) as i16)
                },
                Boolean(id % 2 == 0),
            ],
            Self::Wide => vec![
                Long(id),
                if id % 17 == 0 {
                    Null
                } else {
                    Currency(i64::from(id) * 10001)
                },
                if id % 19 == 0 {
                    Null
                } else {
                    Double(f64::from(id) + 0.5)
                },
                Null,
            ],
            Self::Deep => match id {
                0..=2 => vec![Long(id), Null, Null],
                3 => vec![Long(id), Null, Double(1.5)],
                4 => vec![Long(id), Currency(-100000), Null],
                _ => vec![
                    Long(id),
                    Currency(i64::from(id)),
                    Double(f64::from(id) + 0.5),
                ],
            },
        }
    }
    pub(super) fn stages(self) -> Vec<(&'static str, Vec<Operation>)> {
        use Operation::*;
        use Scalar::*;
        let (inserted, edits, removed, regrown) = match self {
            Self::Integral => (
                (195..325).collect::<Vec<_>>(),
                vec![
                    Field(0, 1, Byte(250)),
                    Field(1, 2, Null),
                    Replace(2, vec![Long(2), Null, Integer(i16::MIN), Boolean(false)]),
                    Replace(3, vec![Long(3), Null, Null, Boolean(true)]),
                    Field(4, 3, Boolean(false)),
                    Field(324, 0, Long(999)),
                ],
                (0..195).collect::<Vec<_>>(),
                (1000..1195).collect::<Vec<_>>(),
            ),
            Self::Wide => (
                (80..92).collect(),
                vec![
                    Field(2, 3, Single(-1.5)),
                    Replace(3, vec![Long(3), Currency(30003), Double(3.5), Single(2.25)]),
                    Field(4, 1, Null),
                    Replace(5, vec![Long(5), Null, Null, Null]),
                    Field(6, 2, Null),
                    Field(7, 0, Long(99)),
                ],
                vec![0, 17, 80],
                vec![120, 121, 122],
            ),
            Self::Deep => (
                vec![5673],
                vec![
                    Field(5673, 1, Currency(-50000)),
                    Replace(6, vec![Long(6), Null, Null]),
                    Field(5, 2, Null),
                    Field(7, 2, Double(-1.25)),
                ],
                vec![5673],
                vec![6000],
            ),
        };
        vec![
            ("original", vec![]),
            (
                "grown",
                inserted.into_iter().map(|n| Insert(self.row(n))).collect(),
            ),
            ("edited", edits),
            ("collapsed", removed.into_iter().map(Delete).collect()),
            (
                "regrown",
                regrown.into_iter().map(|n| Insert(self.row(n))).collect(),
            ),
        ]
    }
}
pub(super) enum Operation {
    Insert(Row),
    Field(i32, u16, Scalar),
    Replace(i32, Row),
    Delete(i32),
}
