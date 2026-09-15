//! EXP-0257: the smallest trailer satisfying L <= 256 * (jump_count + 1).

pub(crate) const fn jump_count(length_without_jumps: usize) -> usize {
    length_without_jumps.saturating_sub(256).div_ceil(255)
}
