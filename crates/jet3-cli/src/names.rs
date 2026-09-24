//! Lossless conversion of JSON schema names to the database code page.
use jet3::TextCodePage;
use serde::{Deserialize, Deserializer, de::Error as _};
use std::{cell::Cell, ffi::OsStr, path::Path};

// Name deserialization stays lossless, including nested schema descriptions.
// The guard limits this context to a single request on the current CLI thread.
thread_local! {
    static REQUEST_CODE_PAGE: Cell<TextCodePage> = const { Cell::new(TextCodePage::Windows1252) };
}

struct RequestEncoding(TextCodePage);
impl Drop for RequestEncoding {
    fn drop(&mut self) {
        REQUEST_CODE_PAGE.with(|page| page.set(self.0));
    }
}

pub(crate) fn read_request<T: serde::de::DeserializeOwned>(
    input: &OsStr,
    path: &Path,
) -> Result<T, String> {
    let mut budget = crate::values::budget();
    let database = jet3::DatabaseReader::open(path, &mut budget).map_err(|e| e.to_string())?;
    let code_page = database
        .header()
        .sort_order()
        .code_page()
        .ok_or("unsupported database code page")?;
    let _encoding = RequestEncoding(REQUEST_CODE_PAGE.with(|page| page.replace(code_page)));
    crate::values::read_request(input)
}

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
        let bytes = REQUEST_CODE_PAGE
            .with(Cell::get)
            .encode(&text, &mut crate::values::budget())
            .map_err(D::Error::custom)?;
        Ok(Self { text, bytes })
    }
}

#[cfg(test)]
mod tests {
    use super::Name;

    #[test]
    fn json_names_encode_exactly_or_fail_without_replacement()
    -> Result<(), Box<dyn std::error::Error>> {
        let name: Name = serde_json::from_str("\"Café €\"")?;
        assert_eq!(name.bytes(), b"Caf\xe9 \x80");
        assert_eq!(name.to_string(), "Café €");
        for input in ["\"漢\"", "\"\\u0081\""] {
            assert!(serde_json::from_str::<Name>(input).is_err());
        }
        Ok(())
    }
}
