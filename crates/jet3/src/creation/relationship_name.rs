//! EXP-0279/0297: parent names encode the selector low nibble first; zero is `.r`.
#[derive(Clone, Copy)]
pub(crate) struct HiddenName {
    bytes: [u8; 4],
    len: usize,
}

impl HiddenName {
    pub(crate) fn for_selector(selector: u32) -> Option<Self> {
        if selector > 31 {
            return None;
        }
        Some(Self {
            bytes: [b'.', b'r', b'A' + (selector & 15) as u8, b'B'],
            len: if selector == 0 {
                2
            } else if selector < 16 {
                3
            } else {
                4
            },
        })
    }

    pub(crate) fn bytes(&self) -> &[u8] {
        &self.bytes[..self.len]
    }

    pub(crate) fn matches(name: &[u8]) -> bool {
        matches!(
            name,
            [b'.', b'r'] | [b'.', b'r', b'B'..=b'P'] | [b'.', b'r', b'A'..=b'P', b'B']
        )
    }
}
