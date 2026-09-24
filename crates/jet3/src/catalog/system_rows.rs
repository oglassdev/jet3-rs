//! `MSysObjects`, `MSysACEs` and `MSysRelationships` row values shared by
//! database creation and schema edits, as named columns in stored order.
use crate::RowValue;

// EXP-0084 preregisters only these fixed per-row candidate values; their SID
// meanings are not generalized.
pub(crate) const OWNER_0203: &[u8] = b"\x02\x03";
pub(crate) const OWNER_0301: &[u8] = b"\x03\x01";
pub(crate) const SID_0201: &[u8] = b"\x02\x01";
pub(crate) const SID_0204: &[u8] = b"\x02\x04";
// EXP-0058: system objects carry flags `0x80000000`.
pub(crate) const SYSTEM_FLAGS: i32 = i32::MIN;
/// `EXP-0093`: `MSysObjects` `Flags` of a created user table.
const USER_FLAGS: i32 = 0;
const TABLE_KIND: i16 = 1;
const RELATIONSHIP_KIND: i16 = 8;

/// One `MSysObjects` row with zero creation and update dates.
#[derive(Clone, Copy)]
pub(crate) struct ObjectRow<'a> {
    pub(crate) id: i32,
    pub(crate) parent: i32,
    pub(crate) name: &'a [u8],
    pub(crate) kind: i16,
    pub(crate) owner: &'static [u8],
    pub(crate) flags: i32,
}

impl<'a> ObjectRow<'a> {
    /// `EXP-0087`: an ordinary local table owned by 0301.
    pub(crate) const fn table(id: i32, parent: i32, name: &'a [u8]) -> Self {
        Self {
            id,
            parent,
            name,
            kind: TABLE_KIND,
            owner: OWNER_0301,
            flags: USER_FLAGS,
        }
    }

    /// `EXP-0114`/`EXP-0297`: a relationship object in the Relationships container.
    pub(crate) const fn relationship(id: i32, parent: i32, name: &'a [u8]) -> Self {
        Self {
            id,
            parent,
            name,
            kind: RELATIONSHIP_KIND,
            owner: OWNER_0301,
            flags: 0,
        }
    }

    /// The `Id` through `Flags` columns; later columns are null unless set separately.
    pub(crate) const fn columns(self) -> [(&'static [u8], RowValue<'a>); 8] {
        [
            (b"Id", RowValue::Long(self.id)),
            (b"ParentId", RowValue::Long(self.parent)),
            (b"Name", RowValue::Text(self.name)),
            (b"Type", RowValue::Integer(self.kind)),
            (b"DateCreate", RowValue::DateTime { days: 0.0 }),
            (b"DateUpdate", RowValue::DateTime { days: 0.0 }),
            (b"Owner", RowValue::Binary(self.owner)),
            (b"Flags", RowValue::Long(self.flags)),
        ]
    }
}

/// One `MSysACEs` grant.
#[derive(Clone, Copy)]
pub(crate) struct AceRow {
    pub(crate) object: i32,
    pub(crate) sid: &'static [u8],
    pub(crate) acm: i32,
    pub(crate) inheritable: bool,
}

impl AceRow {
    pub(crate) const fn new(object: i32, sid: &'static [u8], acm: i32, inheritable: bool) -> Self {
        Self {
            object,
            sid,
            acm,
            inheritable,
        }
    }

    /// `EXP-0087`: the two grants of an ordinary local table.
    pub(crate) const fn table_grants(object: i32) -> [Self; 2] {
        [
            Self::new(object, OWNER_0301, 983294, false),
            Self::new(object, SID_0201, 1048319, false),
        ]
    }

    /// `EXP-0114`/`EXP-0297`: the two grants of a relationship object.
    pub(crate) const fn relationship_grants(object: i32) -> [Self; 2] {
        [
            Self::new(object, OWNER_0301, 983294, false),
            Self::new(object, SID_0201, 1048575, false),
        ]
    }

    pub(crate) const fn columns(self) -> [(&'static [u8], RowValue<'static>); 4] {
        [
            (b"ObjectId", RowValue::Long(self.object)),
            (b"SID", RowValue::Binary(self.sid)),
            (b"ACM", RowValue::Long(self.acm)),
            (b"FInheritable", RowValue::Boolean(self.inheritable)),
        ]
    }
}

/// One `MSysRelationships` field row (`EXP-0073`).
#[derive(Clone, Copy)]
pub(crate) struct RelationshipRow<'a> {
    pub(crate) name: &'a [u8],
    pub(crate) flags: i32,
    pub(crate) field_count: i32,
    pub(crate) field_ordinal: i32,
    pub(crate) child_table: &'a [u8],
    pub(crate) child_column: &'a [u8],
    pub(crate) parent_table: &'a [u8],
    pub(crate) parent_column: &'a [u8],
}

impl<'a> RelationshipRow<'a> {
    pub(crate) const fn columns(self) -> [(&'static [u8], RowValue<'a>); 8] {
        [
            (b"szRelationship", RowValue::Text(self.name)),
            (b"grbit", RowValue::Long(self.flags)),
            (b"ccolumn", RowValue::Long(self.field_count)),
            (b"icolumn", RowValue::Long(self.field_ordinal)),
            (b"szObject", RowValue::Text(self.child_table)),
            (b"szColumn", RowValue::Text(self.child_column)),
            (b"szReferencedObject", RowValue::Text(self.parent_table)),
            (b"szReferencedColumn", RowValue::Text(self.parent_column)),
        ]
    }
}

/// Column values in stored order for encoding against a fixed layout.
pub(crate) fn values<'a, const N: usize>(columns: [(&[u8], RowValue<'a>); N]) -> [RowValue<'a>; N] {
    columns.map(|(_, value)| value)
}
