//! EXP-0243/0245 Binary framing and whole-key shortening, without allocations.
use crate::IndexDirection;
use crate::numeric_index_key::{KeyPrefix, MAX_COMPONENT_BYTES};

pub(crate) const MAX_KEY_BYTES: usize = 255;
const PREFIX_BYTES: usize = MAX_KEY_BYTES - 2;
const CHUNK_BYTES: usize = 8;
const FRAMED_CHUNK_BYTES: usize = CHUNK_BYTES + 1;
const CONTINUED: u8 = 9;

pub(crate) fn encode(
    value: &[u8],
    maximum: u8,
    direction: IndexDirection,
    output: &mut [u8; MAX_COMPONENT_BYTES],
) -> Option<usize> {
    if value.len() > usize::from(maximum) {
        return None;
    }
    let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
    output.fill(mask);
    if value.is_empty() {
        output[0] = mask;
        return Some(1);
    }
    output[0] = 0x7f ^ mask;
    let mut offset = 1;
    for (ordinal, chunk) in value.chunks(CHUNK_BYTES).enumerate() {
        for (slot, byte) in chunk.iter().enumerate() {
            output[offset + slot] = byte ^ mask;
        }
        output[offset + CHUNK_BYTES] = if (ordinal + 1) * CHUNK_BYTES < value.len() {
            CONTINUED
        } else {
            chunk.len() as u8 ^ mask
        };
        offset += FRAMED_CHUNK_BYTES;
    }
    Some(offset)
}

/// Checks a present component, including a prefix cut by whole-key shortening.
pub(crate) fn prefix(key: &[u8], maximum: u8, direction: IndexDirection) -> Option<KeyPrefix> {
    let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
    let maximum = usize::from(maximum);
    let mut offset = 1;
    let mut consumed = 0;
    loop {
        let available = maximum.checked_sub(consumed)?.min(CHUNK_BYTES);
        if available == 0 {
            return None;
        }
        let chunk = key
            .get(offset..)?
            .get(..FRAMED_CHUNK_BYTES.min(key.len() - offset))?;
        if chunk.len() < FRAMED_CHUNK_BYTES {
            if chunk
                .get(available..)
                .is_some_and(|padding| padding.iter().any(|byte| *byte != mask))
            {
                return None;
            }
            return Some(KeyPrefix::Partial {
                maximum: 1 + FRAMED_CHUNK_BYTES * maximum.div_ceil(CHUNK_BYTES),
            });
        }
        let suffix = chunk[CHUNK_BYTES];
        if suffix == CONTINUED {
            consumed += CHUNK_BYTES;
            offset += FRAMED_CHUNK_BYTES;
            continue;
        }
        let length = usize::from(suffix ^ mask);
        if !(1..=available).contains(&length)
            || chunk[length..CHUNK_BYTES].iter().any(|byte| *byte != mask)
        {
            return None;
        }
        return Some(KeyPrefix::Complete(offset + FRAMED_CHUNK_BYTES));
    }
}

/// EXP-0245: retain 253 bytes and hash the remaining directed, framed bytes.
pub(crate) fn shorten(key: &mut [u8]) -> usize {
    if key.len() <= MAX_KEY_BYTES {
        return key.len();
    }
    let mut checksum = 0_u16;
    for byte in &key[PREFIX_BYTES..] {
        for _ in 0..8 {
            checksum = (checksum << 1) ^ if checksum & 0x8000 != 0 { 0x8005 } else { 0 };
        }
        checksum ^= u16::from(*byte);
    }
    key[PREFIX_BYTES..MAX_KEY_BYTES].copy_from_slice(&checksum.to_le_bytes());
    MAX_KEY_BYTES
}
