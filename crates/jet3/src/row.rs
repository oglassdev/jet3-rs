//! Bounded streaming Jet 3 row access from `EXP-0060` with `EXP-0061` value composition.
//!
//! This layer validates row storage, returns lossless physical field slices,
//! and delegates typed interpretation to the value layer.

use crate::row_directory::{RowDirectory, RowDirectoryError};
use crate::row_layout::RowLayout;
use crate::{
    AllocationTraversalError, ColumnOrdinal, ColumnPhysicalType, ColumnStorageClass, DecodedValue,
    Error, JET3_PAGE_SIZE, OwnedPages, PageKind, PageNumber, ResourceBudget, RowLocator,
    TableDefinition, TextCodePage, ValueError,
};
use std::fmt;

pub(crate) const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;

/// A sourced field that is either null or represented by exact physical bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RawField<'row> {
    /// The row marks the field absent.
    Null,
    /// The exact physical field bytes.
    Bytes(&'row [u8]),
}

impl RawField<'_> {
    #[must_use]
    /// Reports whether the field is physically absent.
    pub const fn is_null(self) -> bool {
        matches!(self, Self::Null)
    }
}

impl<'row> RawField<'row> {
    #[must_use]
    /// Returns physical bytes, or `None` for a null field.
    pub const fn raw_bytes(self) -> Option<&'row [u8]> {
        match self {
            Self::Null => None,
            Self::Bytes(bytes) => Some(bytes),
        }
    }
}

/// One validated logical row borrowed from a streaming cursor.
#[derive(Debug)]
pub struct RowView<'row, 'schema> {
    locator: RowLocator,
    storage_locator: RowLocator,
    raw: &'row [u8],
    definition: &'schema TableDefinition,
    layout: RowLayout,
    budget: &'row mut ResourceBudget,
}

impl<'row, 'schema> RowView<'row, 'schema> {
    pub(crate) fn new(
        locator: RowLocator,
        storage_locator: RowLocator,
        raw: &'row [u8],
        definition: &'schema TableDefinition,
        layout: RowLayout,
        budget: &'row mut ResourceBudget,
    ) -> Self {
        Self {
            locator,
            storage_locator,
            raw,
            definition,
            layout,
            budget,
        }
    }

    #[must_use]
    /// Returns the logical row locator requested by the stream.
    pub const fn locator(&self) -> RowLocator {
        self.locator
    }

    #[must_use]
    /// Returns the row that physically stores the returned bytes.
    pub const fn storage_locator(&self) -> RowLocator {
        self.storage_locator
    }

    #[must_use]
    /// Returns the complete physical row bytes.
    pub const fn raw_bytes(&self) -> &'row [u8] {
        self.raw
    }

    #[must_use]
    /// Returns a lossless field view, or `None` for an unknown ordinal.
    pub fn field(&self, ordinal: ColumnOrdinal) -> Option<RawField<'row>> {
        let column = self.definition.columns().get(usize::from(ordinal.get()))?;
        if column.physical_type() == ColumnPhysicalType::Boolean {
            let empty = self.layout.fixed_boundary..self.layout.fixed_boundary;
            return Some(RawField::Bytes(&self.raw[empty]));
        }
        if !self.layout.present(self.raw, ordinal) {
            return Some(RawField::Null);
        }
        let range = match column.storage() {
            ColumnStorageClass::Fixed { offset } => {
                let start = 1 + usize::from(offset);
                start..start + usize::from(column.size())
            }
            ColumnStorageClass::Variable { index } => {
                self.layout.variable_range(self.raw, index)?
            }
        };
        Some(RawField::Bytes(&self.raw[range]))
    }

    /// Decodes one field with an explicitly selected text code page.
    ///
    /// The same operation-wide budget used by the row cursor is charged before
    /// decoded output is produced. No database code page is inferred.
    pub fn value(
        &mut self,
        ordinal: ColumnOrdinal,
        code_page: TextCodePage,
    ) -> Result<Option<DecodedValue<'row>>, ValueError> {
        let Some(column) = self.definition.columns().get(usize::from(ordinal.get())) else {
            return Ok(None);
        };
        let physical_type = column.physical_type();
        let boolean_bit = self.layout.present(self.raw, ordinal);
        let field = if physical_type == ColumnPhysicalType::Boolean {
            let empty = self.layout.fixed_boundary..self.layout.fixed_boundary;
            RawField::Bytes(&self.raw[empty])
        } else if !boolean_bit {
            RawField::Null
        } else {
            let range = match column.storage() {
                ColumnStorageClass::Fixed { offset } => {
                    let start = 1 + usize::from(offset);
                    start..start + usize::from(column.size())
                }
                ColumnStorageClass::Variable { index } => self
                    .layout
                    .variable_range(self.raw, index)
                    .ok_or(ValueError::Resource(Error::Arithmetic {
                        operation: "access validated variable field",
                    }))?,
            };
            RawField::Bytes(&self.raw[range])
        };
        crate::value::decode_value(
            physical_type,
            field,
            boolean_bit,
            self.locator,
            code_page,
            self.budget,
        )
        .map(Some)
    }

    /// Borrows the operation-wide budget held by the row view.
    ///
    /// Snapshot adapters use this only to charge retained output while the
    /// view owns the cursor's budget borrow.
    pub fn budget_mut(&mut self) -> &mut ResourceBudget {
        self.budget
    }
}

/// A structured failure while traversing or validating rows.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum RowError {
    /// Table allocation-map traversal failed.
    Allocation(AllocationTraversalError),
    /// A data-page row directory is malformed.
    Directory(RowDirectoryError),
    /// An owned table page has the wrong classification.
    UnexpectedOwnedPageKind {
        /// Owned page that violated the data-page invariant.
        page: PageNumber,
        /// Observed page classification.
        actual: PageKind,
    },
    /// The schema has more columns than the physical row count can represent.
    ColumnCountNotRepresentable {
        /// Schema column count.
        count: usize,
    },
    /// The physical row column count differs from the schema.
    ColumnCountMismatch {
        /// Schema column count.
        expected: u8,
        /// Sourced row column count.
        actual: u8,
    },
    /// The row is shorter than its fixed header and null bitmap.
    RowTooShort {
        /// Observed row length.
        length: usize,
        /// Minimum required length.
        minimum: usize,
    },
    /// The fixed-value region does not end where the schema requires.
    InvalidFixedBoundary {
        /// Boundary derived from the schema.
        expected: usize,
        /// Boundary sourced from the row trailer.
        actual: usize,
    },
    /// The row's variable-field count differs from the schema.
    VariableCountMismatch {
        /// Schema variable-field count.
        expected: u8,
        /// Sourced row variable-field count.
        actual: u8,
    },
    /// The row requires the unimplemented wide-offset representation.
    UnsupportedWideVariableOffsets {
        /// Sourced variable-field count.
        variable_count: u8,
        /// Complete row length.
        row_length: usize,
    },
    /// One variable field has reversed or out-of-range bounds.
    InvalidVariableBounds {
        /// Zero-based variable-field index.
        index: u16,
        /// Inclusive field start.
        start: usize,
        /// Exclusive field end.
        end: usize,
        /// Exclusive end of the variable-data region.
        data_end: usize,
    },
    /// Unused high bits in the final null-bitmap byte are nonzero.
    NonzeroUnusedNullBits {
        /// Sourced final bitmap byte.
        raw: u8,
        /// Mask selecting unused bits.
        mask: u8,
    },
    /// An overflow pointer names the null locator or an invalid page.
    InvalidOverflowTarget {
        /// Sourced overflow target.
        locator: RowLocator,
    },
    /// An overflow row links to itself.
    SelfLink {
        /// Self-referential row.
        locator: RowLocator,
    },
    /// Overflow traversal repeats an earlier row.
    Cycle {
        /// Repeated row.
        locator: RowLocator,
    },
    /// Resource policy rejected row traversal or validation work.
    Resource(Error),
}

impl fmt::Display for RowError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "row stream failed: {self:?}")
    }
}

impl std::error::Error for RowError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Allocation(source) => Some(source),
            Self::Directory(source) => Some(source),
            Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

/// Forward-only access to validated logical rows of one table definition.
#[derive(Debug)]
pub struct RowCursor<'operation, 'schema, S> {
    pub(crate) root: PageNumber,
    pub(crate) definition: &'schema TableDefinition,
    pub(crate) owned: OwnedPages<'operation, S>,
    pub(crate) page: [u8; PAGE_BYTES],
    pub(crate) current_page: Option<PageNumber>,
    pub(crate) resume_page: Option<PageNumber>,
    pub(crate) directory: Option<RowDirectory>,
    pub(crate) chain: Vec<RowLocator>,
    pub(crate) failed: bool,
    pub(crate) coverage: RowCoverage,
}

/// Storage branches observed by one logical-row stream.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct RowCoverage {
    pub(crate) deleted_skip: bool,
    pub(crate) direct: bool,
    pub(crate) overflow_pointer: bool,
    pub(crate) wide_variable_layout: bool,
}

impl RowCoverage {
    /// Reports whether deleted or hidden storage entries were skipped.
    #[must_use]
    pub const fn deleted_skip(self) -> bool {
        self.deleted_skip
    }

    /// Reports whether an active row was read directly from its source page.
    #[must_use]
    pub const fn direct(self) -> bool {
        self.direct
    }

    /// Reports whether an active row followed an overflow pointer.
    #[must_use]
    pub const fn overflow_pointer(self) -> bool {
        self.overflow_pointer
    }

    /// Reports whether a row used the wide variable-offset layout.
    #[must_use]
    pub const fn wide_variable_layout(self) -> bool {
        self.wide_variable_layout
    }
}

#[cfg(test)]
#[path = "row_tests.rs"]
mod tests;
