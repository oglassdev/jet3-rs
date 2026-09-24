//! EXP-0309 single-byte locale keys, including Spanish contractions.
use crate::{
    IndexDirection, SortOrder,
    index::key::scalar::{KeyPrefix, MAX_COMPONENT_BYTES},
};

pub(super) struct Weights {
    pub(super) primary: [u16; 256],
    pub(super) secondary: [u8; 256],
    pub(super) accents: [u16; 256],
    pub(super) source_cost: [u8; 256],
    pub(super) expansions: &'static [[u8; 2]],
}

fn weights(order: SortOrder) -> Option<&'static Weights> {
    Some(match order {
        SortOrder::Nordic => &super::locale_text_weights::NORDIC,
        SortOrder::Spanish => &super::locale_text_weights::SPANISH,
        SortOrder::Dutch => &super::locale_text_weights::DUTCH,
        SortOrder::Cyrillic => &super::locale_text_weights::CYRILLIC,
        SortOrder::Greek => &super::locale_text_weights::GREEK,
        _ => return None,
    })
}

pub(crate) fn encode(
    value: &[u8],
    maximum: u8,
    direction: IndexDirection,
    order: SortOrder,
    output: &mut [u8; MAX_COMPONENT_BYTES],
) -> Option<usize> {
    if order == SortOrder::General {
        return crate::index::key::text::encode(value, maximum, direction, output);
    }
    if value.len() > usize::from(maximum) {
        return None;
    }
    let weights = weights(order)?;
    let page = order.code_page()?;
    if value
        .iter()
        .any(|&byte| crate::format::text::mapped_character(page, byte).is_none())
    {
        return None;
    }
    let mut end = value.len();
    while end > 0 && value[end - 1] == b' ' {
        end -= 1;
    }
    let value = &value[..end];
    encode_bytes(value, direction, order, weights, output)
}

fn encode_bytes(
    value: &[u8],
    direction: IndexDirection,
    order: SortOrder,
    weights: &Weights,
    output: &mut [u8; MAX_COMPONENT_BYTES],
) -> Option<usize> {
    let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
    output[0] = 0x7f ^ mask;
    let mut position = 1;
    let mut secondary = [0; 510];
    let mut count = 0;
    let mut input = 0;
    while input < value.len() {
        let byte = value[input];
        let contraction: Option<u8> = if order == SortOrder::Spanish {
            value.get(input..input + 2).and_then(|pair| {
                if pair.eq_ignore_ascii_case(b"ch") {
                    Some(0x63)
                } else if pair.eq_ignore_ascii_case(b"ll") {
                    Some(0x6e)
                } else {
                    None
                }
            })
        } else {
            None
        };
        let primary = contraction.map_or(weights.primary[usize::from(byte)], u16::from);
        input += if contraction.is_some() { 2 } else { 1 };
        for weight in primary.to_be_bytes().into_iter().filter(|&v| v != 0) {
            output[position] = weight ^ mask;
            position += 1;
            if weights.accents[usize::from(weight)] != 0 {
                secondary[count] = if contraction.is_some() {
                    2
                } else {
                    weights.secondary[usize::from(byte)]
                };
                count += 1;
            }
        }
    }
    while count > 0 && secondary[count - 1] == 2 {
        count -= 1;
    }
    let length = (count + 2).div_ceil(2);
    output[position..position + length].fill(mask);
    for (ordinal, nibble) in secondary[..count].iter().enumerate() {
        let index = ordinal + 1;
        output[position + index / 2] ^= nibble << if index % 2 == 0 { 4 } else { 0 };
    }
    Some(position + length)
}

pub(crate) fn prefix(
    key: &[u8],
    maximum: u8,
    direction: IndexDirection,
    order: SortOrder,
) -> Option<KeyPrefix> {
    if order == SortOrder::General {
        return crate::index::key::text::prefix(key, maximum, direction);
    }
    let weights = weights(order)?;
    let mask = u8::from(direction == IndexDirection::Descending).wrapping_neg();
    let mut primary = [0; 510];
    let mut secondary = [2; 510];
    let mut count = 0;
    for (position, &byte) in key.iter().enumerate().skip(1) {
        let byte = byte ^ mask;
        if byte >= 0x10 {
            if weights.source_cost[usize::from(byte)] == 0 || count >= 2 * usize::from(maximum) {
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
                    || source_bytes(weights, &primary[..count], &secondary[..count])
                        > usize::from(maximum)
                {
                    return None;
                }
                return Some(KeyPrefix::Complete(index / 2 + 1));
            }
            while current < count && weights.accents[usize::from(primary[current])] == 0 {
                current += 1;
            }
            if current == count
                || weights.accents[usize::from(primary[current])] & (1 << nibble) == 0
            {
                return None;
            }
            secondary[current] = nibble;
            current += 1;
            last = nibble;
        }
        break;
    }
    if source_bytes(weights, &primary[..count], &secondary[..count]) > usize::from(maximum) {
        return None;
    }
    Some(KeyPrefix::Partial {
        maximum: 3 * usize::from(maximum) + 2,
    })
}

fn source_bytes(weights: &Weights, primary: &[u8], secondary: &[u8]) -> usize {
    let mut cost = 0;
    let mut index = 0;
    while index < primary.len() {
        let expansion = primary.get(index..index + 2).is_some_and(|pair| {
            weights.expansions.iter().any(|allowed| pair == allowed)
                && secondary[index] == 2
                && secondary[index + 1] == 2
        });
        cost += if expansion {
            1
        } else {
            usize::from(weights.source_cost[usize::from(primary[index])])
        };
        index += if expansion { 2 } else { 1 };
    }
    cost
}
