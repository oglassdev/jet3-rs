use super::{
    CatalogRecordSpec, CatalogRecordWriteError, catalog_record_len, encode_catalog_record,
};
use crate::catalog_record::decode_catalog_record;
use crate::{
    ByteCount, CatalogObjectClass, CatalogObjectKind, Error, ReadLimits, ResourceBudget,
    ResourceLimitKind, ResourceLimits,
};

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
