//! Explicit single-byte text decoding from `SRC-0025` and `EXP-0061`.

use std::fmt;

use crate::{ByteCount, Error, ResourceBudget};

const UNDEFINED: u32 = 0;

/// One explicitly selected Windows ANSI code page.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TextCodePage {
    Windows1251,
    Windows1252,
}

impl TextCodePage {
    #[must_use]
    pub const fn number(self) -> u16 {
        match self {
            Self::Windows1251 => 1251,
            Self::Windows1252 => 1252,
        }
    }
}

/// Lossless raw text bytes beside their decoded Unicode string.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodedText<'raw> {
    raw: &'raw [u8],
    text: String,
    code_page: TextCodePage,
}

impl<'raw> DecodedText<'raw> {
    #[must_use]
    pub const fn raw_bytes(&self) -> &'raw [u8] {
        self.raw
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.text
    }

    #[must_use]
    pub const fn code_page(&self) -> TextCodePage {
        self.code_page
    }
}

/// A text conversion or resource failure.
#[derive(Debug, Clone, PartialEq, Eq)]
#[non_exhaustive]
pub enum TextError {
    UndefinedByte {
        code_page: TextCodePage,
        index: usize,
        byte: u8,
    },
    Resource(Error),
}

impl fmt::Display for TextError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "text decoding failed: {self:?}")
    }
}

impl std::error::Error for TextError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Resource(source) => Some(source),
            _ => None,
        }
    }
}

pub(crate) fn decode_text<'raw>(
    raw: &'raw [u8],
    code_page: TextCodePage,
    budget: &mut ResourceBudget,
) -> Result<DecodedText<'raw>, TextError> {
    let decoded_bytes = decoded_text_length(raw, code_page)?;
    budget
        .charge_decoded_value(decoded_bytes)
        .map_err(TextError::Resource)?;
    budget
        .charge_allocation(decoded_bytes)
        .map_err(TextError::Resource)?;
    let capacity = usize::try_from(decoded_bytes.get()).map_err(|_| {
        TextError::Resource(Error::IntegerConversion {
            value: u128::from(decoded_bytes.get()),
            target: "usize",
        })
    })?;
    let mut text = String::new();
    text.try_reserve_exact(capacity).map_err(|_| {
        TextError::Resource(Error::Io {
            operation: "reserve decoded text",
            kind: std::io::ErrorKind::OutOfMemory,
        })
    })?;
    for (index, byte) in raw.iter().copied().enumerate() {
        let character = mapped_character(code_page, byte).ok_or(TextError::UndefinedByte {
            code_page,
            index,
            byte,
        })?;
        text.push(character);
    }
    Ok(DecodedText {
        raw,
        text,
        code_page,
    })
}

pub(crate) fn decoded_text_length(
    raw: &[u8],
    code_page: TextCodePage,
) -> Result<ByteCount, TextError> {
    let mut decoded_bytes = 0_usize;
    for (index, byte) in raw.iter().copied().enumerate() {
        let character = mapped_character(code_page, byte).ok_or(TextError::UndefinedByte {
            code_page,
            index,
            byte,
        })?;
        decoded_bytes =
            decoded_bytes
                .checked_add(character.len_utf8())
                .ok_or(TextError::Resource(Error::Arithmetic {
                    operation: "size decoded text",
                }))?;
    }
    ByteCount::from_usize(decoded_bytes).map_err(TextError::Resource)
}

fn mapped_character(code_page: TextCodePage, byte: u8) -> Option<char> {
    if byte < 0x80 {
        return char::from_u32(u32::from(byte));
    }
    let index = usize::from(byte - 0x80);
    let scalar = match code_page {
        TextCodePage::Windows1251 => CP1251[index],
        TextCodePage::Windows1252 => CP1252[index],
    };
    (scalar != UNDEFINED)
        .then_some(scalar)
        .and_then(char::from_u32)
}

#[rustfmt::skip]
const CP1252: [u32; 128] = [
    0x20ac, 0, 0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021,
    0x02c6, 0x2030, 0x0160, 0x2039, 0x0152, 0, 0x017d, 0,
    0, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014,
    0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0, 0x017e, 0x0178,
    0x00a0, 0x00a1, 0x00a2, 0x00a3, 0x00a4, 0x00a5, 0x00a6, 0x00a7,
    0x00a8, 0x00a9, 0x00aa, 0x00ab, 0x00ac, 0x00ad, 0x00ae, 0x00af,
    0x00b0, 0x00b1, 0x00b2, 0x00b3, 0x00b4, 0x00b5, 0x00b6, 0x00b7,
    0x00b8, 0x00b9, 0x00ba, 0x00bb, 0x00bc, 0x00bd, 0x00be, 0x00bf,
    0x00c0, 0x00c1, 0x00c2, 0x00c3, 0x00c4, 0x00c5, 0x00c6, 0x00c7,
    0x00c8, 0x00c9, 0x00ca, 0x00cb, 0x00cc, 0x00cd, 0x00ce, 0x00cf,
    0x00d0, 0x00d1, 0x00d2, 0x00d3, 0x00d4, 0x00d5, 0x00d6, 0x00d7,
    0x00d8, 0x00d9, 0x00da, 0x00db, 0x00dc, 0x00dd, 0x00de, 0x00df,
    0x00e0, 0x00e1, 0x00e2, 0x00e3, 0x00e4, 0x00e5, 0x00e6, 0x00e7,
    0x00e8, 0x00e9, 0x00ea, 0x00eb, 0x00ec, 0x00ed, 0x00ee, 0x00ef,
    0x00f0, 0x00f1, 0x00f2, 0x00f3, 0x00f4, 0x00f5, 0x00f6, 0x00f7,
    0x00f8, 0x00f9, 0x00fa, 0x00fb, 0x00fc, 0x00fd, 0x00fe, 0x00ff,
];

#[rustfmt::skip]
const CP1251: [u32; 128] = [
    0x0402, 0x0403, 0x201a, 0x0453, 0x201e, 0x2026, 0x2020, 0x2021,
    0x20ac, 0x2030, 0x0409, 0x2039, 0x040a, 0x040c, 0x040b, 0x040f,
    0x0452, 0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014,
    0, 0x2122, 0x0459, 0x203a, 0x045a, 0x045c, 0x045b, 0x045f,
    0x00a0, 0x040e, 0x045e, 0x0408, 0x00a4, 0x0490, 0x00a6, 0x00a7,
    0x0401, 0x00a9, 0x0404, 0x00ab, 0x00ac, 0x00ad, 0x00ae, 0x0407,
    0x00b0, 0x00b1, 0x0406, 0x0456, 0x0491, 0x00b5, 0x00b6, 0x00b7,
    0x0451, 0x2116, 0x0454, 0x00bb, 0x0458, 0x0405, 0x0455, 0x0457,
    0x0410, 0x0411, 0x0412, 0x0413, 0x0414, 0x0415, 0x0416, 0x0417,
    0x0418, 0x0419, 0x041a, 0x041b, 0x041c, 0x041d, 0x041e, 0x041f,
    0x0420, 0x0421, 0x0422, 0x0423, 0x0424, 0x0425, 0x0426, 0x0427,
    0x0428, 0x0429, 0x042a, 0x042b, 0x042c, 0x042d, 0x042e, 0x042f,
    0x0430, 0x0431, 0x0432, 0x0433, 0x0434, 0x0435, 0x0436, 0x0437,
    0x0438, 0x0439, 0x043a, 0x043b, 0x043c, 0x043d, 0x043e, 0x043f,
    0x0440, 0x0441, 0x0442, 0x0443, 0x0444, 0x0445, 0x0446, 0x0447,
    0x0448, 0x0449, 0x044a, 0x044b, 0x044c, 0x044d, 0x044e, 0x044f,
];

#[cfg(test)]
#[path = "text_tests.rs"]
mod tests;
