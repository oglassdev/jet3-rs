//! Fixed table-definition header offsets and record sizes (`EXP-0059`).

/// `EXP-0059`: fixed bytes before the first physical-index prefix.
pub(crate) const DEFINITION_HEADER_LEN: usize = 43;
/// `EXP-0073`: little-endian u32 live row count.
pub(crate) const ROW_COUNT: usize = 12;
/// `EXP-0059`/`EXP-0073`: header marker byte distinguishing user and system tables.
pub(crate) const HEADER_MARKER: usize = 20;
/// `EXP-0297`: little-endian u16 high-water count of physical column identities.
pub(crate) const STORAGE_COLUMN_COUNT: usize = 21;
/// `EXP-0297`: little-endian u16 high-water count of variable storage slots.
pub(crate) const STORAGE_VARIABLE_COUNT: usize = 23;
/// `EXP-0059`: little-endian u16 live column count.
pub(crate) const COLUMN_COUNT: usize = 25;
/// `EXP-0059`: little-endian u16 logical index count.
pub(crate) const LOGICAL_INDEX_COUNT: usize = 27;
/// `EXP-0059`: little-endian u16 reserved count, zero in every observed definition.
pub(crate) const RESERVED_COUNT: usize = 29;
/// `EXP-0059`: little-endian u16 physical index count.
pub(crate) const PHYSICAL_INDEX_COUNT: usize = 31;
/// `EXP-0059`: eight sourced prefix bytes per physical index, zero in controls.
pub(crate) const PHYSICAL_PREFIX_LEN: usize = 8;
/// `EXP-0105`: continuation pages carry definition payload from byte 8.
pub(crate) const CONTINUATION_PAYLOAD_OFFSET: usize = 8;
/// `EXP-0059`: the two-byte end-of-definition marker.
pub(crate) const TERMINATOR_LEN: usize = 2;

/// `EXP-0059`: one 18-byte physical record per column.
pub(crate) const COLUMN_RECORD_LEN: usize = 18;
/// `EXP-0059`: one 39-byte record per physical index.
pub(crate) const PHYSICAL_RECORD_LEN: usize = 39;
/// `EXP-0059`: one 20-byte record per logical index.
pub(crate) const LOGICAL_RECORD_LEN: usize = 20;
/// `EXP-0059`: ten three-byte key slots per physical index.
pub(crate) const KEY_SLOT_COUNT: usize = 10;

/// Offset of one physical index's prefix, which begins with its key counter.
pub(crate) const fn physical_prefix_offset(ordinal: u16) -> usize {
    DEFINITION_HEADER_LEN + ordinal as usize * PHYSICAL_PREFIX_LEN
}
