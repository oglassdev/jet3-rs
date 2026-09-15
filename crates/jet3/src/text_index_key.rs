//! EXP-0248 English-US/CP1252 collation, preserving the stored row bytes.
use crate::IndexDirection;
use crate::numeric_index_key::{KeyPrefix, MAX_COMPONENT_BYTES};

#[path = "text_index_weights.rs"]
mod weights;

pub(crate) const ENCODING_CONTEXT: [u8; 4] = [0x09, 0x04, 0xe4, 0x04];
pub(crate) const MAX_TEXT_COMPONENT: usize = 3 * 255 + 2;

pub(crate) fn encode(
    value: &[u8],
    maximum: u8,
    direction: IndexDirection,
    output: &mut [u8; MAX_COMPONENT_BYTES],
) -> Option<usize> {
    if value.len() > usize::from(maximum) {
        return None;
    }
    let mut end = value.len();
    while end > 0 && value[end - 1] == b' ' {
        end -= 1;
    }
    let value = &value[..end];
    let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
    output[0] = 0x7f ^ mask;
    let mut position = 1;
    let mut secondary = [0_u8; 2 * 255];
    let mut count = 0;
    for &byte in value {
        let primary = weights::PRIMARY[usize::from(byte)];
        if primary == 0 {
            return None;
        }
        let bytes = primary.to_be_bytes();
        for weight in bytes.into_iter().filter(|weight| *weight != 0) {
            output[position] = weight ^ mask;
            position += 1;
            if accented_primary(weight) {
                secondary[count] = accent(byte).unwrap_or(2);
                count += 1;
            }
        }
    }
    while count > 0 && secondary[count - 1] == 2 {
        count -= 1;
    }
    let nibble_count = count + 2;
    let bytes = nibble_count.div_ceil(2);
    output[position..position + bytes].fill(mask);
    for (ordinal, nibble) in secondary[..count].iter().enumerate() {
        let index = ordinal + 1;
        output[position + index / 2] ^= nibble << if index % 2 == 0 { 4 } else { 0 };
    }
    Some(position + bytes)
}

pub(crate) fn prefix(key: &[u8], maximum: u8, direction: IndexDirection) -> Option<KeyPrefix> {
    let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
    let mut primary = [0_u8; 2 * 255];
    let mut secondary = [2_u8; 2 * 255];
    let mut count = 0;
    for (position, &byte) in key.iter().enumerate().skip(1) {
        let byte = byte ^ mask;
        if byte >= 0x10 {
            if !matches!(byte, 0x10..=0x62 | 0x64..=0x6d | 0x6f..=0x70 | 0x72..=0x78 | 0x7a..=0x7f | 0x81)
                || count >= 2 * usize::from(maximum)
            {
                return None;
            }
            primary[count] = byte;
            count += 1;
            continue;
        }
        let mut current = 0;
        let mut last = 0;
        for index in position * 2 + 1..key.len() * 2 {
            let byte = key[index / 2] ^ mask;
            let nibble = if index % 2 == 0 { byte >> 4 } else { byte & 15 };
            if nibble == 0 {
                if last == 2
                    || (index % 2 == 0 && byte & 15 != 0)
                    || minimum_source_bytes(&primary[..count], &secondary[..count])
                        > usize::from(maximum)
                {
                    return None;
                }
                return Some(KeyPrefix::Complete(index / 2 + 1));
            }
            while current < count && !accented_primary(primary[current]) {
                current += 1;
            }
            if current == count || !allowed_secondary(primary[current], nibble) {
                return None;
            }
            secondary[current] = nibble;
            current += 1;
            last = nibble;
        }
        break;
    }
    if minimum_source_bytes(&primary[..count], &secondary[..count]) > usize::from(maximum) {
        return None;
    }
    Some(KeyPrefix::Partial {
        maximum: 3 * usize::from(maximum) + 2,
    })
}

// EXP-0248: only AE, OE and SS can originate from a single source byte,
// and those expansions have neutral secondary weights on both halves.
fn minimum_source_bytes(primary: &[u8], secondary: &[u8]) -> usize {
    let mut count = 0;
    let mut index = 0;
    while index < primary.len() {
        let expansion = primary.get(index..index + 2).is_some_and(|pair| {
            matches!(pair, [0x60 | 0x72, 0x66] | [0x76, 0x76])
                && secondary[index] == 2
                && secondary[index + 1] == 2
        });
        index += if expansion { 2 } else { 1 };
        count += 1;
    }
    count
}

fn allowed_secondary(primary: u8, secondary: u8) -> bool {
    secondary == 2
        || matches!(
            (primary, secondary),
            (0x60, 3..=8)
                | (0x62, 9)
                | (0x66 | 0x6a | 0x78, 3..=6)
                | (0x70, 7)
                | (0x72, 3..=7)
                | (0x76, 10)
                | (0x7d, 4 | 6)
        )
}

fn accented_primary(weight: u8) -> bool {
    matches!(
        weight,
        0x60 | 0x62 | 0x66 | 0x6a | 0x70 | 0x72 | 0x76 | 0x78 | 0x7d
    )
}

fn accent(byte: u8) -> Option<u8> {
    Some(match byte {
        0xc0 | 0xc8 | 0xcc | 0xd2 | 0xd9 | 0xe0 | 0xe8 | 0xec | 0xf2 | 0xf9 => 3,
        0xc1 | 0xc9 | 0xcd | 0xd3 | 0xda | 0xdd | 0xe1 | 0xe9 | 0xed | 0xf3 | 0xfa | 0xfd => 4,
        0xc2 | 0xca | 0xce | 0xd4 | 0xdb | 0xe2 | 0xea | 0xee | 0xf4 | 0xfb => 5,
        0x9f | 0xc4 | 0xcb | 0xcf | 0xd6 | 0xdc | 0xe4 | 0xeb | 0xef | 0xf6 | 0xfc | 0xff => 6,
        0xc3 | 0xd1 | 0xd5 | 0xe3 | 0xf1 | 0xf5 => 7,
        0xc5 | 0xe5 => 8,
        0xc7 | 0xe7 => 9,
        0x8a | 0x9a => 10,
        _ => return None,
    })
}

#[cfg(test)]
#[path = "text_index_key_tests.rs"]
mod tests;
