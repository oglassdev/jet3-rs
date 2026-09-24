//! Table-definition allocation-map locators observed in `EXP-0057`.

use crate::{ClassifiedPage, Error, PageGeometry, PageKind, PageNumber, ResourceBudget};

// EXP-0057: adjacent row-then-u24-page locators on a Jet 3 TDEF page.
const OWNED_MAP_LOCATOR_OFFSET: usize = 35;
const AVAILABLE_MAP_LOCATOR_OFFSET: usize = 39;
const LOCATOR_LEN: usize = 4;

/// A row on a physical data page that contains one allocation map.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MapRowLocator {
    pub(super) page: PageNumber,
    pub(super) row: u8,
}

impl MapRowLocator {
    /// Creates a locator from an already checked physical page and row slot.
    #[must_use]
    pub const fn new(page: PageNumber, row: u8) -> Self {
        Self { page, row }
    }

    /// Returns the physical data page containing the row.
    #[must_use]
    pub const fn page(self) -> PageNumber {
        self.page
    }

    /// Returns the zero-based row slot on that data page.
    #[must_use]
    pub const fn row(self) -> u8 {
        self.row
    }
}

/// The owned-page and available-page map rows named by one table definition.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TableMapLocations {
    owned: MapRowLocator,
    available: MapRowLocator,
}

impl TableMapLocations {
    /// Returns the row whose set pages are owned by the table.
    #[must_use]
    pub const fn owned(self) -> MapRowLocator {
        self.owned
    }

    /// Returns the row whose set pages are currently available to the table.
    #[must_use]
    pub const fn available(self) -> MapRowLocator {
        self.available
    }
}

/// A structured table-map locator failure.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[non_exhaustive]
pub enum MapLocationError {
    /// The supplied page is not a table-definition page.
    #[error("expected page {} to be a table definition, found {actual:?}", .page.get())]
    ExpectedTableDefinition {
        /// Physical page supplied by the caller.
        page: PageNumber,
        /// Lossless classification actually found.
        actual: PageKind,
    },
    /// A decoded locator names a page beyond the captured input.
    #[error(
        "{role} map locator page {} row {} is invalid: {source}",
        .locator.page().get(),
        .locator.row()
    )]
    InvalidReference {
        /// Whether the rejected locator was the owned or available map.
        role: &'static str,
        /// Decoded locator.
        locator: MapRowLocator,
        /// Geometry failure.
        source: Error,
    },
    /// Resource policy rejected locator decoding.
    #[error("map location rejected: {0}")]
    Resource(#[source] Error),
}

/// Decodes both allocation-map row locators from a classified TDEF page.
pub fn locate_table_maps(
    page: ClassifiedPage<'_>,
    geometry: PageGeometry,
    budget: &mut ResourceBudget,
) -> Result<TableMapLocations, MapLocationError> {
    budget
        .charge_work_units(1)
        .map_err(MapLocationError::Resource)?;
    if page.kind() != PageKind::TableDefinition {
        return Err(MapLocationError::ExpectedTableDefinition {
            page: page.number(),
            actual: page.kind(),
        });
    }
    let raw = page.raw_bytes();
    let owned = decode_locator(&raw[OWNED_MAP_LOCATOR_OFFSET..][..LOCATOR_LEN]);
    let available = decode_locator(&raw[AVAILABLE_MAP_LOCATOR_OFFSET..][..LOCATOR_LEN]);
    for (role, locator) in [("owned", owned), ("available", available)] {
        geometry
            .validate_reference(locator.page())
            .map_err(|source| MapLocationError::InvalidReference {
                role,
                locator,
                source,
            })?;
    }
    Ok(TableMapLocations { owned, available })
}

fn decode_locator(raw: &[u8]) -> MapRowLocator {
    // EXP-0057: row byte followed by a three-byte little-endian page number.
    let page = u32::from_le_bytes([raw[1], raw[2], raw[3], 0]);
    MapRowLocator::new(PageNumber::new(u64::from(page)), raw[0])
}
