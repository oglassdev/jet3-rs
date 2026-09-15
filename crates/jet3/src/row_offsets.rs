//! EXP-0257: the smallest trailer satisfying L <= 256 * (jump_count + 1).

/// EXP-0260: complete ordinary variable rows, including their trailers.
pub(crate) const MAX_VARIABLE_ROW_LEN: usize = 2_012;

pub(crate) const fn jump_count(length_without_jumps: usize) -> usize {
    length_without_jumps.saturating_sub(256).div_ceil(255)
}
