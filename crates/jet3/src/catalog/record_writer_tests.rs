use super::record_writer::{CatalogRecordWriteError, MAX_NAME_LEN, NAME_START, catalog_record_len};
use crate::{
    BinaryWriter, ByteCount, CatalogObjectClass, CatalogObjectKind, Error, ReadLimits,
    ResourceBudget, ResourceLimitKind, ResourceLimits, catalog::record::decode_catalog_record,
};

/// `EXP-0058`: every catalog record begins with column count 17.
const CATALOG_COLUMN_COUNT: u8 = 17;
/// `EXP-0058`: identifier at `[1,5)`, kind at `[9,11)`, flags at `[27,31)`.
const OBJECT_ID_OFFSET: usize = 1;
const OBJECT_KIND_OFFSET: usize = 9;
const OBJECT_FLAGS_OFFSET: usize = 27;
const FIXED_BOUNDARY: u8 = 11;
const TRAILER_MARKER: u8 = 0xff;
/// `EXP-0058`: user objects have flags 0, system objects `0x80000000`.
const USER_FLAGS: u32 = 0;
const SYSTEM_FLAGS: u32 = 0x8000_0000;

/// One catalog object row to encode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct CatalogRecordSpec<'a> {
    /// Object identifier; for tables, also the table-definition root page.
    id: u32,
    /// Object kind.
    kind: CatalogObjectKind,
    /// User or system classification.
    class: CatalogObjectClass,
    /// Raw database-code-page name bytes.
    name: &'a [u8],
}

/// Encodes one catalog row into `output`, returning the encoded length.
///
/// Bytes with no `EXP-0058` meaning are written as zero.
fn encode_catalog_record(
    spec: &CatalogRecordSpec<'_>,
    output: &mut [u8],
    budget: &mut ResourceBudget,
) -> Result<ByteCount, CatalogRecordWriteError> {
    validate_spec(spec)?;
    let length = catalog_record_len(spec.name.len())?;
    if output.len() < length {
        return Err(CatalogRecordWriteError::OutputTooSmall {
            needed: length,
            available: output.len(),
        });
    }
    let name_end = u8::try_from(NAME_START + spec.name.len()).map_err(|_| {
        CatalogRecordWriteError::NameTooLong {
            length: spec.name.len(),
            maximum: MAX_NAME_LEN,
        }
    })?;
    let flags = match spec.class {
        CatalogObjectClass::User => USER_FLAGS,
        CatalogObjectClass::System => SYSTEM_FLAGS,
    };
    let mut writer =
        BinaryWriter::new(output, budget).map_err(CatalogRecordWriteError::Resource)?;
    write_row(&mut writer, spec, name_end, flags).map_err(CatalogRecordWriteError::Resource)?;
    Ok(ByteCount::new(writer.position().get()))
}

fn validate_spec(spec: &CatalogRecordSpec<'_>) -> Result<(), CatalogRecordWriteError> {
    match spec.kind {
        CatalogObjectKind::Table if spec.id == 0 => {
            Err(CatalogRecordWriteError::NullTableDefinition)
        }
        CatalogObjectKind::Unknown(raw) if raw == CatalogObjectKind::Table.raw() => {
            Err(CatalogRecordWriteError::NonCanonicalObjectKind { raw })
        }
        CatalogObjectKind::Table | CatalogObjectKind::Unknown(_) => Ok(()),
    }
}

fn write_row(
    writer: &mut BinaryWriter<'_, '_>,
    spec: &CatalogRecordSpec<'_>,
    name_end: u8,
    flags: u32,
) -> Result<(), Error> {
    writer.write_u8(CATALOG_COLUMN_COUNT)?;
    writer.write_u32_le(spec.id)?;
    writer.write_exact(&[0; OBJECT_KIND_OFFSET - OBJECT_ID_OFFSET - 4])?;
    writer.write_u16_le(spec.kind.raw())?;
    writer.write_exact(&[0; OBJECT_FLAGS_OFFSET - OBJECT_KIND_OFFSET - 2])?;
    writer.write_u32_le(flags)?;
    writer.write_exact(spec.name)?;
    writer.write_u8(name_end)?;
    writer.write_u8(NAME_START as u8)?;
    writer.write_u8(FIXED_BOUNDARY)?;
    writer.write_u8(TRAILER_MARKER)?;
    writer.write_exact(&[0; 2])
}

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::default()))
}

#[test]
fn round_trips_cp1252_name_kind_and_flags() -> Result<(), Box<dyn std::error::Error>> {
    // EXP-0058: `Café_Euro€` stored as these exact CP1252 bytes.
    let name = b"\x43\x61\x66\xe9\x5f\x45\x75\x72\x6f\x80";
    let spec = CatalogRecordSpec {
        id: 23,
        kind: CatalogObjectKind::Table,
        class: CatalogObjectClass::User,
        name,
    };
    let mut output = [0xa5_u8; 64];
    let mut resources = budget();
    let length = encode_catalog_record(&spec, &mut output, &mut resources)?;
    assert_eq!(length.get() as usize, catalog_record_len(name.len())?);
    let row = &output[..length.get() as usize];
    let view = decode_catalog_record(row, &mut resources)?;
    assert_eq!(view.id().get(), 23);
    assert_eq!(view.kind(), CatalogObjectKind::Table);
    assert_eq!(view.class(), CatalogObjectClass::User);
    assert_eq!(view.name_bytes(), name);

    let system = CatalogRecordSpec {
        id: 2,
        kind: CatalogObjectKind::Unknown(6),
        class: CatalogObjectClass::System,
        name: b"MSysObjects",
    };
    let length = encode_catalog_record(&system, &mut output, &mut resources)?;
    let view = decode_catalog_record(&output[..length.get() as usize], &mut resources)?;
    assert_eq!(view.kind(), CatalogObjectKind::Unknown(6));
    assert_eq!(view.class(), CatalogObjectClass::System);
    assert_eq!(view.name_bytes(), b"MSysObjects");
    Ok(())
}

#[test]
fn rejects_bad_names_small_output_and_exhausted_budget() {
    let spec = CatalogRecordSpec {
        id: 1,
        kind: CatalogObjectKind::Table,
        class: CatalogObjectClass::User,
        name: b"T",
    };
    let mut output = [0_u8; 64];
    assert_eq!(
        catalog_record_len(0),
        Err(CatalogRecordWriteError::EmptyName)
    );
    assert_eq!(
        catalog_record_len(225),
        Err(CatalogRecordWriteError::NameTooLong {
            length: 225,
            maximum: 224,
        })
    );
    assert!(catalog_record_len(224).is_ok());
    assert_eq!(
        encode_catalog_record(&spec, &mut output[..10], &mut budget()),
        Err(CatalogRecordWriteError::OutputTooSmall {
            needed: 38,
            available: 10,
        })
    );
    let mut exhausted = ResourceBudget::new(
        ResourceLimits::new(ReadLimits::default()).with_max_encoded_bytes(ByteCount::new(4)),
    );
    assert_eq!(
        encode_catalog_record(&spec, &mut output, &mut exhausted),
        Err(CatalogRecordWriteError::Resource(
            Error::ResourceLimitExceeded {
                kind: ResourceLimitKind::EncodedBytes,
                requested: 5,
                maximum: 4,
            }
        ))
    );
    assert!(
        CatalogRecordWriteError::EmptyName
            .to_string()
            .contains("catalog record encoding failed")
    );
}

#[test]
fn rejects_noncanonical_kind_and_null_table_root_without_writing() {
    let mut output = [0xa5_u8; 64];
    let alias = CatalogRecordSpec {
        id: 1,
        kind: CatalogObjectKind::Unknown(CatalogObjectKind::Table.raw()),
        class: CatalogObjectClass::User,
        name: b"T",
    };
    assert_eq!(
        encode_catalog_record(&alias, &mut output, &mut budget()),
        Err(CatalogRecordWriteError::NonCanonicalObjectKind { raw: 1 })
    );
    assert_eq!(output, [0xa5; 64]);

    let null_root = CatalogRecordSpec {
        id: 0,
        kind: CatalogObjectKind::Table,
        class: CatalogObjectClass::User,
        name: b"T",
    };
    assert_eq!(
        encode_catalog_record(&null_root, &mut output, &mut budget()),
        Err(CatalogRecordWriteError::NullTableDefinition)
    );
    assert_eq!(output, [0xa5; 64]);
}
