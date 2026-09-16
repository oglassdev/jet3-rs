//! EXP-0294: enforced relationship cascade bits and reciprocal context bytes.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct RelationshipFlags {
    pub updates: bool,
    pub deletes: bool,
}

impl RelationshipFlags {
    const UPDATE: i32 = 256;
    const DELETE: i32 = 4096;

    pub const fn decode(raw: i32) -> Option<Self> {
        if raw & !(Self::UPDATE | Self::DELETE) != 0 {
            return None;
        }
        Some(Self {
            updates: raw & Self::UPDATE != 0,
            deletes: raw & Self::DELETE != 0,
        })
    }

    pub const fn raw(self) -> i32 {
        (if self.updates { Self::UPDATE } else { 0 })
            | (if self.deletes { Self::DELETE } else { 0 })
    }

    pub const fn context(self) -> [u8; 2] {
        [self.updates as u8, self.deletes as u8]
    }
}

impl crate::RelationshipSpec<'_> {
    pub(crate) const fn flags(&self) -> RelationshipFlags {
        RelationshipFlags {
            updates: self.cascade_updates,
            deletes: self.cascade_deletes,
        }
    }
}
