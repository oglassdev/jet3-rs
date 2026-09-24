//! SRC-0026 attribute inputs with EXP-0294 cascade context bytes. EXP-0301:
//! DontEnforce and join bits live only in `MSysRelationships.grbit`.
use crate::RelationshipJoin;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct RelationshipFlags {
    pub updates: bool,
    pub deletes: bool,
    pub enforced: bool,
    pub unique: bool,
    pub join: RelationshipJoin,
}

impl Default for RelationshipFlags {
    fn default() -> Self {
        Self {
            updates: false,
            deletes: false,
            enforced: true,
            unique: false,
            join: RelationshipJoin::Inner,
        }
    }
}

impl RelationshipFlags {
    const UNIQUE: i32 = 1;
    const DONT_ENFORCE: i32 = 2;
    const UPDATE: i32 = 256;
    const DELETE: i32 = 4096;
    const LEFT: i32 = 0x0100_0000;
    const RIGHT: i32 = 0x0200_0000;

    /// Unknown bits and cascades on unenforced relationships
    /// (refused by DAO) are not interpreted.
    pub const fn decode(raw: i32) -> Option<Self> {
        if raw
            & !(Self::UNIQUE
                | Self::DONT_ENFORCE
                | Self::UPDATE
                | Self::DELETE
                | Self::LEFT
                | Self::RIGHT)
            != 0
        {
            return None;
        }
        let enforced = raw & Self::DONT_ENFORCE == 0;
        if !enforced && raw & (Self::UPDATE | Self::DELETE) != 0 {
            return None;
        }
        Some(Self {
            updates: raw & Self::UPDATE != 0,
            deletes: raw & Self::DELETE != 0,
            enforced,
            unique: raw & Self::UNIQUE != 0,
            join: RelationshipJoin::from_bits(raw & Self::LEFT != 0, raw & Self::RIGHT != 0),
        })
    }

    pub const fn raw(self) -> i32 {
        let (left, right) = self.join.bits();
        (if self.updates { Self::UPDATE } else { 0 })
            | (if self.unique { Self::UNIQUE } else { 0 })
            | (if self.deletes { Self::DELETE } else { 0 })
            | (if self.enforced { 0 } else { Self::DONT_ENFORCE })
            | (if left { Self::LEFT } else { 0 })
            | (if right { Self::RIGHT } else { 0 })
    }

    pub const fn context(self) -> [u8; 2] {
        [self.updates as u8, self.deletes as u8]
    }
}

impl RelationshipJoin {
    pub(crate) const fn from_bits(left: bool, right: bool) -> Self {
        match (left, right) {
            (false, false) => Self::Inner,
            (true, false) => Self::Left,
            (false, true) => Self::Right,
            (true, true) => Self::LeftAndRight,
        }
    }

    pub(crate) const fn bits(self) -> (bool, bool) {
        match self {
            Self::Inner => (false, false),
            Self::Left => (true, false),
            Self::Right => (false, true),
            Self::LeftAndRight => (true, true),
        }
    }
}

impl crate::RelationshipSpec<'_> {
    pub(crate) const fn flags(&self) -> RelationshipFlags {
        RelationshipFlags {
            updates: self.cascade_updates,
            deletes: self.cascade_deletes,
            enforced: self.enforce,
            unique: self.unique,
            join: self.join,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn observed_attribute_bits_round_trip_and_others_are_refused() {
        for raw in [
            0,
            1,
            2,
            3,
            257,
            4097,
            4353,
            256,
            4096,
            4352,
            0x0100_0000,
            0x0200_0000,
            0x0300_0000,
            0x0100_1100,
            0x0200_1100,
            0x0100_0002,
            0x0200_0002,
        ] {
            assert_eq!(
                RelationshipFlags::decode(raw).map(RelationshipFlags::raw),
                Some(raw)
            );
        }
        let unenforced = RelationshipFlags::decode(2).unwrap_or_default();
        assert!(!unenforced.enforced);
        assert_eq!(unenforced.context(), [0, 0]);
        for raw in [4, 8, 16, 258, 259, 512, 4098, 4354, 65536, i32::MIN] {
            assert_eq!(RelationshipFlags::decode(raw), None, "{raw}");
        }
    }
}
