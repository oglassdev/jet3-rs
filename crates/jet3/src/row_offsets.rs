//! EXP-0257 trailer framing and EXP-0260/0261 ordinary row size limits.

/// Complete encoded rows include the column count, offsets and presence bits.
pub(crate) const fn maximum_length(variable_count: usize) -> usize {
    if variable_count == 0 { 2_003 } else { 2_012 }
}

/// The smallest trailer satisfying L <= 256 * (jump_count + 1).
pub(crate) const fn jump_count(length_without_jumps: usize) -> usize {
    length_without_jumps.saturating_sub(256).div_ceil(255)
}
