//! EXP-0299 native payloads: Rust creation and edits reproduce DAO bytes exactly.
use super::api_tests::*;
use crate::{
    ColumnSpec, ColumnType, PropertyChange, TableValidation,
    definition::column_writer::nz,
    properties::{blob::PropertyBlob, column::CreationProperties},
    schema::column_options::{PropertyEdit, apply},
};

const KEEP: PropertyChange<'static> = PropertyChange::Keep;

fn hex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .filter_map(|at| u8::from_str_radix(&text[at..at + 2], 16).ok())
        .collect()
}

fn created(
    columns: &[ColumnSpec<'_>],
    validation: TableValidation<'_>,
) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let properties = CreationProperties::new(
        columns,
        validation,
        crate::SortOrder::General,
        &mut budget(),
    )?
    .ok_or("properties")?;
    let mut bytes = vec![0; properties.len()];
    properties.encode(&mut bytes, &mut budget())?;
    Ok(bytes)
}

fn edited(bytes: &[u8], edits: &[PropertyEdit<'_>]) -> Result<Vec<u8>, Box<dyn std::error::Error>> {
    let mut blob = PropertyBlob::parse(bytes, &mut budget())?;
    for edit in edits {
        apply(&mut blob, *edit, &mut budget())?;
    }
    Ok(blob.encode(&mut budget())?)
}

fn field<'a>(column: &'a [u8], text: [PropertyChange<'a>; 4]) -> PropertyEdit<'a> {
    PropertyEdit {
        column: Some(column),
        required: None,
        allow_zero_length: None,
        text,
    }
}

fn table_edit<'a>(rule: PropertyChange<'a>, text: PropertyChange<'a>) -> PropertyEdit<'a> {
    PropertyEdit {
        column: None,
        required: None,
        allow_zero_length: None,
        text: [rule, text, KEEP, KEEP],
    }
}

const TEXT20: ColumnType = ColumnType::Text { max_len: nz(20) };

fn seed_columns() -> [ColumnSpec<'static>; 3] {
    [
        ColumnSpec::new(b"A", ColumnType::Long),
        ColumnSpec::new(b"B", TEXT20),
        ColumnSpec::new(b"Auto", ColumnType::AutoIncrement),
    ]
}

const SEED_RULE: TableValidation<'static> = TableValidation {
    rule: Some(b"[A]>0"),
    text: Some(b"positive"),
};

#[test]
fn creation_reproduces_native_pre_append_payloads() -> TestResult {
    let columns = [
        ColumnSpec::new(b"A", ColumnType::Long)
            .with_required()
            .with_default_value(b"1"),
        ColumnSpec::new(b"B", TEXT20).with_validation_rule(b"<>\"x\""),
    ];
    let validation = TableValidation {
        rule: Some(b"[A]>0 Or [B] Is Null"),
        text: Some(b"table says no"),
    };
    assert_eq!(
        created(&columns, validation)?,
        hex(
            "4b4b44004f0000008000080052657175697265640c0044656661756c7456616c75650f00416c6c6f775a65726f4c656e6774680e0056616c69646174696f6e52756c650e0056616c69646174696f6e546578741f0000000100070000000100410900010100000100ff0900010c01000100312c0000000100070000000100420900010102000100000900010100000100000d00010c030005003c3e2278223d00000000000600000000001c00010a030014005b415d3e30204f72205b425d204973204e756c6c1500010a04000d007461626c652073617973206e6f"
        )
    );
    let columns = [ColumnSpec::new(b"X", ColumnType::Text { max_len: nz(50) })
        .with_required()
        .with_allow_zero_length()
        .with_default_value(b"\"d\"")
        .with_validation_rule(b"<>\"z\"")
        .with_validation_text(b"not z")
        .with_description(b"About")];
    assert_eq!(
        created(&columns, TableValidation::NONE)?,
        hex(
            "4b4b44005c00000080000f00416c6c6f775a65726f4c656e677468080052657175697265640e0056616c69646174696f6e52756c650e0056616c69646174696f6e546578740c0044656661756c7456616c75650b004465736372697074696f6e510000000100070000000100580900010100000100ff0900010101000100ff0d00010c020005003c3e227a220d00010a030005006e6f74207a0b00010c040003002264220d00000a0500050041626f7574"
        )
    );
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::AutoIncrement),
        ColumnSpec::new(b"Qty", ColumnType::Long)
            .with_default_value(b"0")
            .with_validation_rule(b">=0"),
        ColumnSpec::new(b"Name", ColumnType::Text { max_len: nz(40) })
            .with_validation_text(b"bad name")
            .with_description(b"The name"),
        ColumnSpec::new(b"Plain", ColumnType::Long),
        ColumnSpec::new(b"Notes", ColumnType::Memo).with_default_value(b"\"n\""),
    ];
    assert_eq!(
        created(&columns, TableValidation::NONE)?,
        hex(
            "4b4b44005c0000008000080052657175697265640e0056616c69646174696f6e52756c650c0044656661756c7456616c75650f00416c6c6f775a65726f4c656e6774680e0056616c69646174696f6e546578740b004465736372697074696f6e2c00000001000900000003005174790900010100000100000b00010c010003003e3d300900010c02000100304200000001000a00000004004e616d650900010103000100000900010100000100001000010a04000800626164206e616d651000000a05000800546865206e616d651a00000001000b0000000500506c61696e0900010100000100002e00000001000b00000005004e6f7465730900010103000100000900010100000100000b00010c02000300226e22"
        )
    );
    let columns = [
        ColumnSpec::new(b"Id", ColumnType::Long),
        ColumnSpec::new(b"Name", TEXT20)
            .with_validation_rule(b"<>\"x\"")
            .with_default_value(b"\"n\""),
    ];
    let validation = TableValidation {
        rule: None,
        text: Some(b"u text"),
    };
    assert_eq!(
        created(&columns, validation)?,
        hex(
            "4b4b44004f0000008000080052657175697265640f00416c6c6f775a65726f4c656e6774680e0056616c69646174696f6e52756c650c0044656661756c7456616c75650e0056616c69646174696f6e5465787417000000010008000000020049640900010100000100003a00000001000a00000004004e616d650900010101000100000900010100000100000d00010c020005003c3e2278220b00010c03000300226e221a00000000000600000000000e00010a04000600752074657874"
        )
    );
    Ok(())
}

const F_SEED: &str = "4b4b44005c00000080000f00416c6c6f775a65726f4c656e677468080052657175697265640e0056616c69646174696f6e52756c650e0056616c69646174696f6e546578740c0044656661756c7456616c75650b004465736372697074696f6e510000000100070000000100580900010100000100000900010101000100ff0d00010c020005003c3e227a220d00010a030005006e6f74207a0b00010c040003002264220d00000a0500050041626f75741f0000000100070000000100590900010101000100000900010c04000100322900000000000600000000000d00010a020005005b595d3e301000010a03000800706f736974697665";

#[test]
fn existing_field_and_table_edits_reproduce_native_payloads() -> TestResult {
    let seed = hex(F_SEED);
    let columns = [
        ColumnSpec::new(b"X", ColumnType::Text { max_len: nz(50) })
            .with_required()
            .with_default_value(b"\"d\"")
            .with_validation_rule(b"<>\"z\"")
            .with_validation_text(b"not z")
            .with_description(b"About"),
        ColumnSpec::new(b"Y", ColumnType::Long).with_default_value(b"2"),
    ];
    let validation = TableValidation {
        rule: Some(b"[Y]>0"),
        text: Some(b"positive"),
    };
    assert_eq!(created(&columns, validation)?, seed);
    let set = PropertyChange::Set;
    let changed = edited(
        &seed,
        &[
            field(
                b"X",
                [set(b"Is Not Null"), KEEP, set(b"\"longer default\""), KEEP],
            ),
            table_edit(set(b"[Y]<100"), KEEP),
        ],
    )?;
    assert_eq!(
        changed,
        hex(
            "4b4b44005c00000080000f00416c6c6f775a65726f4c656e677468080052657175697265640e0056616c69646174696f6e52756c650e0056616c69646174696f6e546578740c0044656661756c7456616c75650b004465736372697074696f6e650000000100070000000100580900010100000100000900010101000100ff1400010c02000c004973204e6f74204e756c6c000d00010a030005006e6f74207a1800010c04001000226c6f6e6765722064656661756c74220d00000a0500050041626f75741f0000000100070000000100590900010101000100000900010c04000100322b00000000000600000000000f00010c020007005b595d3c3130301000010a03000800706f736974697665"
        )
    );
    let cleared = edited(
        &seed,
        &[field(b"X", [PropertyChange::Clear, KEEP, KEEP, KEEP])],
    )?;
    assert_eq!(
        cleared,
        hex(
            "4b4b44005c00000080000f00416c6c6f775a65726f4c656e677468080052657175697265640e0056616c69646174696f6e52756c650e0056616c69646174696f6e546578740c0044656661756c7456616c75650b004465736372697074696f6e440000000100070000000100580900010100000100000900010101000100ff0d00010a030005006e6f74207a0b00010c040003002264220d00000a0500050041626f75741f0000000100070000000100590900010101000100000900010c04000100322900000000000600000000000d00010a020005005b595d3e301000010a03000800706f736974697665"
        )
    );
    Ok(())
}

#[test]
fn edits_after_a_table_block_reproduce_native_placement() -> TestResult {
    let set = PropertyChange::Set;
    let clear = PropertyChange::Clear;
    let seed = created(&seed_columns(), SEED_RULE)?;
    let cases: [(&[PropertyEdit<'_>], &str); 4] = [
        (
            &[table_edit(clear, KEEP)],
            "4b4b4400410000008000080052657175697265640f00416c6c6f775a65726f4c656e6774680e0056616c69646174696f6e52756c650e0056616c69646174696f6e54657874160000000100070000000100410900010100000100001f0000000100070000000100420900010101000100000900010100000100001c00000000000600000000001000010a03000800706f736974697665",
        ),
        (
            &[
                table_edit(clear, KEEP),
                table_edit(set(b"[A]>1"), set(b"bigger")),
            ],
            "4b4b4400410000008000080052657175697265640f00416c6c6f775a65726f4c656e6774680e0056616c69646174696f6e52756c650e0056616c69646174696f6e54657874160000000100070000000100410900010100000100001f0000000100070000000100420900010101000100000900010100000100002700000000000600000000000e00010a030006006269676765720d00010c020005005b415d3e31",
        ),
        (
            &[field(
                b"Auto",
                [set(b">0"), set(b"pos"), set(b"1"), set(b"auto")],
            )],
            "4b4b44005c0000008000080052657175697265640f00416c6c6f775a65726f4c656e6774680e0056616c69646174696f6e52756c650e0056616c69646174696f6e546578740c0044656661756c7456616c75650b004465736372697074696f6e160000000100070000000100410900010100000100001f0000000100070000000100420900010101000100000900010100000100002900000000000600000000000d00010a020005005b415d3e301000010a03000800706f7369746976653b00000001000a00000004004175746f0b00010c020003003e30000b00010a03000300706f730900010c04000100310c00000a050004006175746f",
        ),
        (
            &[
                field(
                    b"B",
                    [
                        set(b"Is Not Null"),
                        set(b"needed"),
                        set(b"\"b\""),
                        set(b"about B"),
                    ],
                ),
                field(b"B", [set(b"Like \"b*\""), clear, KEEP, KEEP]),
            ],
            "4b4b44005c0000008000080052657175697265640f00416c6c6f775a65726f4c656e6774680e0056616c69646174696f6e52756c650e0056616c69646174696f6e546578740c0044656661756c7456616c75650b004465736372697074696f6e160000000100070000000100410900010100000100004b0000000100070000000100420900010101000100000900010100000100001200010c02000a004c696b652022622a22000b00010c040003002262220f00000a0500070061626f757420422900000000000600000000000d00010a020005005b415d3e301000010a03000800706f736974697665",
        ),
    ];
    for (edits, expected) in cases {
        assert_eq!(edited(&seed, edits)?, hex(expected));
    }
    let plain = created(&seed_columns(), TableValidation::NONE)?;
    let cleared = edited(
        &plain,
        &[
            field(b"Auto", [set(b">0"), KEEP, set(b"1"), KEEP]),
            field(b"Auto", [clear, KEEP, clear, KEEP]),
        ],
    )?;
    assert_eq!(
        cleared,
        hex(
            "4b4b44003f0000008000080052657175697265640f00416c6c6f775a65726f4c656e6774680e0056616c69646174696f6e52756c650c0044656661756c7456616c7565160000000100070000000100410900010100000100001f000000010007000000010042090001010100010000090001010000010000"
        )
    );
    Ok(())
}

#[test]
fn appended_columns_reproduce_native_placement() -> TestResult {
    let seed = created(&seed_columns(), SEED_RULE)?;
    let column = ColumnSpec::new(b"C", ColumnType::Text { max_len: nz(30) })
        .with_required()
        .with_validation_rule(b"<>\"x\"")
        .with_validation_text(b"no x")
        .with_default_value(b"\"d\"")
        .with_description(b"about C");
    assert_eq!(
        crate::schema::properties::add(&seed, column, &mut budget())?,
        hex(
            "4b4b44005c0000008000080052657175697265640f00416c6c6f775a65726f4c656e6774680e0056616c69646174696f6e52756c650e0056616c69646174696f6e546578740c0044656661756c7456616c75650b004465736372697074696f6e160000000100070000000100410900010100000100001f0000000100070000000100420900010101000100000900010100000100002900000000000600000000000d00010a020005005b415d3e301000010a03000800706f736974697665520000000100070000000100430900010101000100000900010100000100ff0d00010c020005003c3e2278220c00010a030004006e6f20780b00010c040003002264220f00000a0500070061626f75742043"
        )
    );
    let column =
        ColumnSpec::new(b"C", ColumnType::Text { max_len: nz(30) }).with_default_value(b"\"d\"");
    assert_eq!(
        crate::schema::properties::add(&[], column, &mut budget())?,
        hex(
            "4b4b44002f00000080000f00416c6c6f775a65726f4c656e677468080052657175697265640c0044656661756c7456616c75652a0000000100070000000100430900010100000100000900010101000100000b00010c02000300226422"
        )
    );
    Ok(())
}
