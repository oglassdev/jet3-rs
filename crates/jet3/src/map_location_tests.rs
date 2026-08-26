use super::{MapLocationError, MapRowLocator, locate_table_maps};
use crate::{
    ByteCount, Error, PageGeometry, PageKind, PageNumber, ReadLimits, ResourceBudget,
    ResourceLimits, classify_page,
};

const PAGE_BYTES: usize = 2048;

fn budget() -> ResourceBudget {
    ResourceBudget::new(ResourceLimits::new(ReadLimits::default()))
}

#[test]
fn decodes_adjacent_row_then_u24_page_locators() -> Result<(), Box<dyn std::error::Error>> {
    let mut raw = [0_u8; PAGE_BYTES];
    raw[0] = 2;
    raw[35..39].copy_from_slice(&[7, 0x56, 0x34, 0x12]);
    raw[39..43].copy_from_slice(&[8, 0x57, 0x34, 0x12]);
    let geometry = PageGeometry::new(
        ByteCount::new((0x12_3458_u64 + 1) * PAGE_BYTES as u64),
        ByteCount::new(PAGE_BYTES as u64),
    )?;
    let mut resources = budget();
    let page = classify_page(PageNumber::new(1), &raw, &mut resources)?;
    let locations = locate_table_maps(page, geometry, &mut resources)?;
    assert_eq!(
        locations.owned(),
        MapRowLocator {
            page: PageNumber::new(0x12_3456),
            row: 7,
        }
    );
    assert_eq!(locations.available().page(), PageNumber::new(0x12_3457));
    assert_eq!(locations.available().row(), 8);
    assert_eq!(resources.total_work_units(), 2);
    Ok(())
}

#[test]
fn requires_table_definition_classification() -> Result<(), Box<dyn std::error::Error>> {
    let mut raw = [0_u8; PAGE_BYTES];
    raw[0] = 1;
    let mut resources = budget();
    let page = classify_page(PageNumber::new(1), &raw, &mut resources)?;
    let geometry = PageGeometry::new(ByteCount::new(4096), ByteCount::new(2048))?;
    assert_eq!(
        locate_table_maps(page, geometry, &mut resources),
        Err(MapLocationError::ExpectedTableDefinition {
            page: PageNumber::new(1),
            actual: PageKind::Data,
        })
    );
    Ok(())
}

#[test]
fn rejects_locator_at_and_one_beyond_page_count() -> Result<(), Box<dyn std::error::Error>> {
    let geometry = PageGeometry::new(ByteCount::new(4 * 2048), ByteCount::new(2048))?;
    for invalid in [4_u32, 5] {
        let mut raw = [0_u8; PAGE_BYTES];
        raw[0] = 2;
        raw[35] = 0;
        raw[35 + 1..35 + 4].copy_from_slice(&invalid.to_le_bytes()[..3]);
        raw[39..43].copy_from_slice(&[1, 1, 0, 0]);
        let mut resources = budget();
        let page = classify_page(PageNumber::new(2), &raw, &mut resources)?;
        assert!(matches!(
            locate_table_maps(page, geometry, &mut resources),
            Err(MapLocationError::InvalidReference { role: "owned", .. })
        ));
    }
    Ok(())
}

#[test]
fn errors_report_context_and_preserve_sources() {
    let invalid_source = Error::PageOutOfBounds {
        page: 4,
        page_count: 4,
    };
    let resource_source = Error::Arithmetic {
        operation: "test map location",
    };
    let cases = [
        (
            MapLocationError::ExpectedTableDefinition {
                page: PageNumber::new(1),
                actual: PageKind::Data,
            },
            "expected page 1 to be a table definition, found Data",
            false,
        ),
        (
            MapLocationError::InvalidReference {
                role: "owned",
                locator: MapRowLocator::new(PageNumber::new(4), 2),
                source: invalid_source,
            },
            "owned map locator page 4 row 2 is invalid",
            true,
        ),
        (
            MapLocationError::Resource(resource_source),
            "map location rejected",
            true,
        ),
    ];
    for (error, context, has_source) in &cases {
        assert!(error.to_string().contains(context));
        assert_eq!(std::error::Error::source(error).is_some(), *has_source);
    }
}
