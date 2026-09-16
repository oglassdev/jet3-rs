//! Lossless conversion of JSON schema names to the creation code page.
use jet3::TextCodePage;
use serde::{Deserialize, Deserializer, de::Error as _};

pub(crate) struct Name {
    text: String,
    bytes: Vec<u8>,
}

impl Name {
    pub(crate) fn bytes(&self) -> &[u8] {
        &self.bytes
    }
}

impl std::fmt::Display for Name {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.text.fmt(formatter)
    }
}

impl<'de> Deserialize<'de> for Name {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let text = String::deserialize(deserializer)?;
        let bytes = TextCodePage::Windows1252
            .encode(&text, &mut crate::values::budget())
            .map_err(D::Error::custom)?;
        Ok(Self { text, bytes })
    }
}
