//! Long component bytes from EXP-0062/0126, including descending complement.
use crate::IndexDirection;

pub(crate) fn encode(value: i32, direction: IndexDirection) -> [u8; 5] {
    let mut key = [0x7f, 0, 0, 0, 0];
    key[1..].copy_from_slice(&value.to_be_bytes());
    key[1] ^= 0x80;
    if direction == IndexDirection::Descending {
        for byte in &mut key {
            *byte ^= 0xff;
        }
    }
    key
}
