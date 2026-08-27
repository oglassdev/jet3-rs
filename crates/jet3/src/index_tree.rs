//! Bounded, lossless Jet 3 index-tree traversal from `EXP-0062`.
//!
//! Tree order comes only from the physical branch children and leaf chain.
//! Key bytes that are not in the observed single-field inventory remain
//! available as [`IndexKeyEncoding::Unsupported`].

use std::fmt;
use std::mem::size_of;

use crate::index_tree_page::{
    ENTRY_AREA_OFFSET, ParsedNode, boundaries, parse_node, u24_at_be, u32_at_be,
};
use crate::index_tree_rows::RowReferenceValidator;
use crate::{
    ByteCount, ColumnPhysicalType, DatabasePageError, DatabaseReader, Error, JET3_PAGE_SIZE,
    PageGeometry, PageKind, PageNumber, ReadAt, ResourceBudget, RowDirectoryError, RowLocator,
    TableDefinition, VisitedPages,
};

const PAGE_BYTES: usize = JET3_PAGE_SIZE.get() as usize;
const LEAF_TRAILER_LEN: usize = 4;
const BRANCH_CHILD_LEN: usize = 4;

/// Physical class of one visited index node.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum IndexNodeKind {
    /// Tag-`03` node whose entries end in child references.
    Intermediate,
    /// Tag-`04` node whose entries end in row locators.
    Leaf,
}

/// One visited node and its sourced sibling links.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct IndexNode {
    pub(crate) page: PageNumber,
    pub(crate) kind: IndexNodeKind,
    pub(crate) depth: u64,
    pub(crate) previous: Option<PageNumber>,
    pub(crate) next: Option<PageNumber>,
}

impl IndexNode {
    /// Returns the physical page number.
    #[must_use]
    pub const fn page(self) -> PageNumber {
        self.page
    }

    /// Returns whether this node is intermediate or leaf.
    #[must_use]
    pub const fn kind(self) -> IndexNodeKind {
        self.kind
    }

    /// Returns the one-based depth, with the root at depth one.
    #[must_use]
    pub const fn depth(self) -> u64 {
        self.depth
    }

    /// Returns the sourced previous-sibling reference.
    #[must_use]
    pub const fn previous(self) -> Option<PageNumber> {
        self.previous
    }

    /// Returns the sourced next-sibling reference.
    #[must_use]
    pub const fn next(self) -> Option<PageNumber> {
        self.next
    }
}

/// Observed semantic class of retained physical key bytes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum IndexKeyEncoding {
    /// One-byte null marker.
    Null,
    /// Boolean ordering bytes.
    Boolean,
    /// Unsigned byte ordering bytes.
    Byte,
    /// Signed 16-bit integer ordering bytes.
    Integer,
    /// Signed 32-bit integer ordering bytes.
    Long,
    /// Signed scaled-currency ordering bytes.
    Currency,
    /// IEEE-754 single ordering bytes.
    Single,
    /// IEEE-754 double ordering bytes.
    Double,
    /// OLE Automation date ordering bytes.
    DateTime,
    /// Fixed binary bytes plus their sourced length marker.
    Binary,
    /// Database-collation text bytes. They are not decoded as text.
    TextCollation,
    /// A composite, GUID, malformed-known, or otherwise unobserved encoding.
    Unsupported,
}

/// Lossless physical key bytes from one leaf entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexKey {
    raw: Vec<u8>,
    encoding: IndexKeyEncoding,
}

impl IndexKey {
    /// Returns every sourced key byte, including its leading presence marker.
    #[must_use]
    pub fn raw_bytes(&self) -> &[u8] {
        &self.raw
    }

    /// Returns the narrow observed encoding class.
    #[must_use]
    pub const fn encoding(&self) -> IndexKeyEncoding {
        self.encoding
    }
}

/// One leaf entry in physical tree order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexEntry {
    key: IndexKey,
    row: RowLocator,
}

impl IndexEntry {
    /// Returns the lossless key.
    #[must_use]
    pub const fn key(&self) -> &IndexKey {
        &self.key
    }

    /// Returns the referenced data-page row.
    #[must_use]
    pub const fn row(&self) -> RowLocator {
        self.row
    }
}

/// Complete bounded traversal result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexTree {
    root: PageNumber,
    nodes: Vec<IndexNode>,
    entries: Vec<IndexEntry>,
}

impl IndexTree {
    /// Returns the physical root from the table definition.
    #[must_use]
    pub const fn root(&self) -> PageNumber {
        self.root
    }

    /// Returns nodes grouped by depth in left-to-right tree order.
    #[must_use]
    pub fn nodes(&self) -> &[IndexNode] {
        &self.nodes
    }

    /// Returns leaf entries in physical index order.
    #[must_use]
    pub fn entries(&self) -> &[IndexEntry] {
        &self.entries
    }
}

/// Structured corruption or resource rejection during index traversal.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum IndexTreeError {
    /// The caller selected no physical index at this ordinal.
    InvalidPhysicalIndexOrdinal {
        /// Requested zero-based physical-index ordinal.
        ordinal: u16,
        /// Number of physical indexes present in the table definition.
        count: usize,
    },
    /// A node page could not be read or classified.
    Page(DatabasePageError),
    /// A followed child was neither an intermediate nor a leaf node.
    UnexpectedPageKind {
        /// Referenced node page.
        page: PageNumber,
        /// Classified kind found on that page.
        actual: PageKind,
    },
    /// The node owner did not match the table-definition root.
    UnexpectedOwner {
        /// Referenced node page.
        page: PageNumber,
        /// Table-definition page that owns the selected index.
        expected: PageNumber,
        /// Owner stored by the node.
        actual: PageNumber,
    },
    /// A fixed header or node-class marker was not observed.
    InvalidHeaderMarker {
        /// Referenced node page.
        page: PageNumber,
        /// Page-local marker offset.
        offset: usize,
        /// Sourced marker byte.
        raw: u8,
    },
    /// Free-space accounting disagreed with the boundary bitmap.
    InvalidFreeSpace {
        /// Referenced node page.
        page: PageNumber,
        /// Sourced free-space value.
        raw: u16,
        /// Free space derived from the last entry boundary.
        expected: usize,
    },
    /// An entry boundary was outside or reversed within the fixed entry area.
    InvalidEntryBoundary {
        /// Referenced node page.
        page: PageNumber,
        /// Rejected boundary offset within the entry area.
        boundary: usize,
        /// Immediately preceding boundary or common-prefix length.
        previous: usize,
    },
    /// A set boundary bit named one of the eight nonexistent trailing bytes.
    BoundaryOutsideEntryArea {
        /// Referenced node page.
        page: PageNumber,
        /// Rejected boundary offset within the entry area.
        boundary: usize,
    },
    /// A branch or leaf entry was too short for its required trailer.
    TruncatedEntry {
        /// Referenced node page.
        page: PageNumber,
        /// Zero-based entry ordinal on the page.
        entry: usize,
        /// Reconstructed entry length.
        length: usize,
    },
    /// An intermediate node contained no child-bearing separator entries.
    EmptyIntermediate {
        /// Referenced intermediate page.
        page: PageNumber,
    },
    /// A required child was null or a leaf carried an unexpected child.
    InvalidTailChild {
        /// Referenced node page.
        page: PageNumber,
        /// Sourced tail-child reference.
        child: PageNumber,
    },
    /// A leaf row locator referenced a page that is not a data page.
    UnexpectedRowPageKind {
        /// Referenced row page.
        page: PageNumber,
        /// Classified kind found on that page.
        actual: PageKind,
    },
    /// A leaf row locator's data page or slot failed row-directory validation.
    RowDirectory {
        /// Referenced row page.
        page: PageNumber,
        /// Row-directory error for that page or slot.
        source: RowDirectoryError,
    },
    /// A sourced page reference was outside captured geometry.
    InvalidReference {
        /// Page containing the rejected reference.
        page: PageNumber,
        /// Stable semantic role of the reference.
        role: &'static str,
        /// Sourced page reference.
        reference: PageNumber,
        /// Geometry error that rejected the reference.
        source: Error,
    },
    /// A child or sibling link referred to its containing node.
    SelfReference {
        /// Page containing the self-reference.
        page: PageNumber,
        /// Stable semantic role of the reference.
        role: &'static str,
    },
    /// A child graph reached one page twice, including cycles.
    RepeatedPage {
        /// Repeated page reference.
        page: PageNumber,
    },
    /// Leaves occurred at different depths or below an already reached leaf.
    InconsistentLeafDepth {
        /// Page found at the inconsistent depth.
        page: PageNumber,
        /// Depth established by the first leaf.
        expected: u64,
        /// Depth of this page.
        actual: u64,
    },
    /// A sibling link did not match left-to-right child traversal.
    InvalidSiblingLink {
        /// Page containing the inconsistent link.
        page: PageNumber,
        /// Stable semantic role of the sibling link.
        role: &'static str,
        /// Link required by left-to-right traversal order.
        expected: Option<PageNumber>,
        /// Sourced sibling link.
        actual: Option<PageNumber>,
    },
    /// Resource policy rejected traversal work or retained output.
    Resource(Error),
}

impl fmt::Display for IndexTreeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "index traversal failed: {self:?}")
    }
}

impl std::error::Error for IndexTreeError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Page(source) => Some(source),
            Self::RowDirectory { source, .. } => Some(source),
            Self::InvalidReference { source, .. } | Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct PendingNode {
    pub(crate) page: PageNumber,
    pub(crate) depth: u64,
}

impl<S: ReadAt> DatabaseReader<S> {
    /// Traverses one physical index from a previously decoded table definition.
    pub fn index_tree(
        &mut self,
        table: &TableDefinition,
        physical_index: u16,
        budget: &mut ResourceBudget,
    ) -> Result<IndexTree, IndexTreeError> {
        let physical = table
            .physical_indexes()
            .get(usize::from(physical_index))
            .ok_or(IndexTreeError::InvalidPhysicalIndexOrdinal {
                ordinal: physical_index,
                count: table.physical_indexes().len(),
            })?;
        let geometry = self.geometry();
        let mut visited = VisitedPages::new(geometry, budget).map_err(IndexTreeError::Resource)?;
        let mut pending = Vec::new();
        push_charged(
            &mut pending,
            PendingNode {
                page: physical.root(),
                depth: 1,
            },
            budget,
            "reserve pending index node",
        )?;
        let mut nodes = Vec::new();
        let mut entries = Vec::new();
        let mut rows = RowReferenceValidator::default();
        let mut page_bytes = [0_u8; PAGE_BYTES];
        let mut cursor = 0_usize;
        let mut leaf_depth = None;

        while let Some(pending_node) = pending.get(cursor).copied() {
            cursor = cursor
                .checked_add(1)
                .ok_or_else(|| resource_arithmetic("advance index queue"))?;
            budget.charge_items(1).map_err(IndexTreeError::Resource)?;
            budget
                .check_chain_depth(pending_node.depth)
                .map_err(IndexTreeError::Resource)?;
            geometry
                .validate_reference(pending_node.page)
                .map_err(|source| IndexTreeError::InvalidReference {
                    page: pending_node.page,
                    role: "tree node",
                    reference: pending_node.page,
                    source,
                })?;
            if visited
                .insert(pending_node.page)
                .map_err(IndexTreeError::Resource)?
            {
                return Err(IndexTreeError::RepeatedPage {
                    page: pending_node.page,
                });
            }
            let classified = self
                .read_classified_page(pending_node.page, &mut page_bytes, budget)
                .map_err(IndexTreeError::Page)?;
            let parsed = parse_node(
                classified.kind(),
                pending_node,
                table.root(),
                geometry,
                &page_bytes,
                budget,
            )?;
            match parsed.node.kind {
                IndexNodeKind::Leaf => {
                    match leaf_depth {
                        Some(expected) if expected != pending_node.depth => {
                            return Err(IndexTreeError::InconsistentLeafDepth {
                                page: pending_node.page,
                                expected,
                                actual: pending_node.depth,
                            });
                        }
                        None => leaf_depth = Some(pending_node.depth),
                        _ => {}
                    }
                    let first_new = entries.len();
                    append_leaf_entries(
                        &page_bytes,
                        &parsed,
                        physical,
                        table,
                        geometry,
                        &mut entries,
                        budget,
                    )?;
                    for entry in &entries[first_new..] {
                        rows.validate(self, table.root(), entry.row, &mut page_bytes, budget)?;
                    }
                }
                IndexNodeKind::Intermediate => {
                    if let Some(expected) = leaf_depth {
                        return Err(IndexTreeError::InconsistentLeafDepth {
                            page: pending_node.page,
                            expected,
                            actual: pending_node.depth,
                        });
                    }
                    append_children(&page_bytes, &parsed, geometry, &mut pending, budget)?;
                }
            }
            push_charged(&mut nodes, parsed.node, budget, "reserve index node result")?;
        }
        validate_sibling_links(&nodes)?;
        Ok(IndexTree {
            root: physical.root(),
            nodes,
            entries,
        })
    }
}

fn append_leaf_entries(
    page: &[u8; PAGE_BYTES],
    parsed: &ParsedNode,
    physical: &crate::PhysicalIndexDefinition,
    table: &TableDefinition,
    geometry: PageGeometry,
    output: &mut Vec<IndexEntry>,
    budget: &mut ResourceBudget,
) -> Result<(), IndexTreeError> {
    let data = &page[ENTRY_AREA_OFFSET..];
    let prefix = &data[..parsed.prefix_len];
    let mut start = parsed.prefix_len;
    for (entry_index, end) in boundaries(page).enumerate() {
        let suffix = &data[start..end];
        let full_len = prefix
            .len()
            .checked_add(suffix.len())
            .ok_or_else(|| resource_arithmetic("size leaf index entry"))?;
        if full_len <= LEAF_TRAILER_LEN || suffix.len() < LEAF_TRAILER_LEN {
            return Err(IndexTreeError::TruncatedEntry {
                page: parsed.node.page,
                entry: entry_index,
                length: full_len,
            });
        }
        let key_len = full_len - LEAF_TRAILER_LEN;
        let mut raw = Vec::new();
        budget
            .charge_allocation(ByteCount::from_usize(key_len).map_err(IndexTreeError::Resource)?)
            .map_err(IndexTreeError::Resource)?;
        raw.try_reserve_exact(key_len)
            .map_err(|_| allocation_failure("reserve raw index key"))?;
        raw.extend_from_slice(prefix);
        let key_suffix_len = suffix.len() - LEAF_TRAILER_LEN;
        raw.extend_from_slice(&suffix[..key_suffix_len]);
        let trailer = &suffix[key_suffix_len..];
        let row_page = PageNumber::new(u64::from(u24_at_be(trailer, 0)));
        validate_reference(geometry, parsed.node.page, row_page, "leaf row page")?;
        let encoding = classify_key(&raw, physical, table);
        push_charged(
            output,
            IndexEntry {
                key: IndexKey { raw, encoding },
                row: RowLocator::new(row_page, trailer[3]),
            },
            budget,
            "reserve leaf index entry",
        )?;
        start = end;
    }
    Ok(())
}

fn append_children(
    page: &[u8; PAGE_BYTES],
    parsed: &ParsedNode,
    geometry: PageGeometry,
    pending: &mut Vec<PendingNode>,
    budget: &mut ResourceBudget,
) -> Result<(), IndexTreeError> {
    let data = &page[ENTRY_AREA_OFFSET..];
    let mut start = parsed.prefix_len;
    let child_depth = parsed
        .node
        .depth
        .checked_add(1)
        .ok_or_else(|| resource_arithmetic("advance index child depth"))?;
    budget
        .check_chain_depth(child_depth)
        .map_err(IndexTreeError::Resource)?;
    for (entry_index, end) in boundaries(page).enumerate() {
        let suffix = &data[start..end];
        let full_len = parsed
            .prefix_len
            .checked_add(suffix.len())
            .ok_or_else(|| resource_arithmetic("size branch index entry"))?;
        if full_len <= LEAF_TRAILER_LEN + BRANCH_CHILD_LEN
            || suffix.len() < LEAF_TRAILER_LEN + BRANCH_CHILD_LEN
        {
            return Err(IndexTreeError::TruncatedEntry {
                page: parsed.node.page,
                entry: entry_index,
                length: full_len,
            });
        }
        let row_trailer = suffix.len() - LEAF_TRAILER_LEN - BRANCH_CHILD_LEN;
        let row_page = PageNumber::new(u64::from(u24_at_be(suffix, row_trailer)));
        validate_reference(geometry, parsed.node.page, row_page, "branch row page")?;
        let child = PageNumber::new(u64::from(u32_at_be(suffix, suffix.len() - 4)));
        validate_child(parsed.node.page, child, geometry)?;
        push_charged(
            pending,
            PendingNode {
                page: child,
                depth: child_depth,
            },
            budget,
            "reserve branch child",
        )?;
        start = end;
    }
    validate_child(parsed.node.page, parsed.tail_child, geometry)?;
    push_charged(
        pending,
        PendingNode {
            page: parsed.tail_child,
            depth: child_depth,
        },
        budget,
        "reserve branch tail child",
    )
}

fn classify_key(
    raw: &[u8],
    physical: &crate::PhysicalIndexDefinition,
    table: &TableDefinition,
) -> IndexKeyEncoding {
    let [field] = physical.fields() else {
        return IndexKeyEncoding::Unsupported;
    };
    if raw == [0] {
        return IndexKeyEncoding::Null;
    }
    let Some(column) = table.columns().get(usize::from(field.column().get())) else {
        return IndexKeyEncoding::Unsupported;
    };
    let supported = raw.first() == Some(&0x7f);
    match column.physical_type() {
        ColumnPhysicalType::Boolean if supported && raw.len() == 2 => IndexKeyEncoding::Boolean,
        ColumnPhysicalType::Byte if supported && raw.len() == 2 => IndexKeyEncoding::Byte,
        ColumnPhysicalType::Integer if supported && raw.len() == 3 => IndexKeyEncoding::Integer,
        ColumnPhysicalType::Long if supported && raw.len() == 5 => IndexKeyEncoding::Long,
        ColumnPhysicalType::Currency if supported && raw.len() == 9 => IndexKeyEncoding::Currency,
        ColumnPhysicalType::Single if supported && raw.len() == 5 => IndexKeyEncoding::Single,
        ColumnPhysicalType::Double if supported && raw.len() == 9 => IndexKeyEncoding::Double,
        ColumnPhysicalType::DateTime if supported && raw.len() == 9 => IndexKeyEncoding::DateTime,
        ColumnPhysicalType::Binary
            if supported
                && raw.len() == usize::from(column.size()).saturating_add(2)
                && raw.last() == Some(&(column.size() as u8)) =>
        {
            IndexKeyEncoding::Binary
        }
        ColumnPhysicalType::Text if supported && raw.len() >= 3 && raw.last() == Some(&0) => {
            IndexKeyEncoding::TextCollation
        }
        _ => IndexKeyEncoding::Unsupported,
    }
}

/// Checks sibling links over nodes whose visits were already charged as items.
fn validate_sibling_links(nodes: &[IndexNode]) -> Result<(), IndexTreeError> {
    let mut group_start = 0;
    while group_start < nodes.len() {
        let depth = nodes[group_start].depth;
        let mut group_end = group_start + 1;
        while group_end < nodes.len() && nodes[group_end].depth == depth {
            group_end += 1;
        }
        for index in group_start..group_end {
            let expected_previous = (index > group_start).then(|| nodes[index - 1].page);
            let expected_next = (index + 1 < group_end).then(|| nodes[index + 1].page);
            if nodes[index].previous != expected_previous {
                return Err(IndexTreeError::InvalidSiblingLink {
                    page: nodes[index].page,
                    role: "previous sibling",
                    expected: expected_previous,
                    actual: nodes[index].previous,
                });
            }
            if nodes[index].next != expected_next {
                return Err(IndexTreeError::InvalidSiblingLink {
                    page: nodes[index].page,
                    role: "next sibling",
                    expected: expected_next,
                    actual: nodes[index].next,
                });
            }
        }
        group_start = group_end;
    }
    Ok(())
}

fn validate_child(
    containing: PageNumber,
    child: PageNumber,
    geometry: PageGeometry,
) -> Result<(), IndexTreeError> {
    if child == containing {
        return Err(IndexTreeError::SelfReference {
            page: containing,
            role: "branch child",
        });
    }
    validate_reference(geometry, containing, child, "branch child")
}

fn validate_reference(
    geometry: PageGeometry,
    containing: PageNumber,
    reference: PageNumber,
    role: &'static str,
) -> Result<(), IndexTreeError> {
    geometry
        .validate_reference(reference)
        .map_err(|source| IndexTreeError::InvalidReference {
            page: containing,
            role,
            reference,
            source,
        })
}

const MIN_GROWTH_CAPACITY: usize = 4;

/// Pushes with amortized doubling, charging every reserved element up front.
pub(crate) fn push_charged<T>(
    values: &mut Vec<T>,
    value: T,
    budget: &mut ResourceBudget,
    operation: &'static str,
) -> Result<(), IndexTreeError> {
    if values.len() == values.capacity() {
        let target = values
            .capacity()
            .checked_mul(2)
            .ok_or_else(|| resource_arithmetic(operation))?
            .max(MIN_GROWTH_CAPACITY);
        let additional = target - values.len();
        let bytes = u64::try_from(additional)
            .ok()
            .and_then(|count| count.checked_mul(size_of::<T>() as u64))
            .ok_or_else(|| resource_arithmetic(operation))?;
        budget
            .charge_allocation(ByteCount::new(bytes))
            .map_err(IndexTreeError::Resource)?;
        values
            .try_reserve_exact(additional)
            .map_err(|_| allocation_failure(operation))?;
    }
    values.push(value);
    Ok(())
}

fn allocation_failure(operation: &'static str) -> IndexTreeError {
    IndexTreeError::Resource(Error::Io {
        operation,
        kind: std::io::ErrorKind::OutOfMemory,
    })
}

pub(crate) fn resource_arithmetic(operation: &'static str) -> IndexTreeError {
    IndexTreeError::Resource(Error::Arithmetic { operation })
}

#[cfg(test)]
#[path = "index_tree_tests.rs"]
mod tests;
