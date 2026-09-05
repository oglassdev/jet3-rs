//! Fixed system-table definition inventory for the bootstrap composer.

use std::num::NonZeroU8;

use super::*;
use crate::IndexFieldSpec;

const OBJECT_COLUMNS: [ColumnSpec<'static>; 17] = [
    ColumnSpec::new(b"Id", ColumnType::Long),
    ColumnSpec::new(b"ParentId", ColumnType::Long),
    ColumnSpec::new(
        b"Name",
        ColumnType::Text {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(b"Type", ColumnType::Integer),
    ColumnSpec::new(b"DateCreate", ColumnType::DateTime),
    ColumnSpec::new(b"DateUpdate", ColumnType::DateTime),
    ColumnSpec::new(
        b"Owner",
        ColumnType::Binary {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(b"Flags", ColumnType::Long),
    ColumnSpec::new(b"Database", ColumnType::Memo),
    ColumnSpec::new(b"Connect", ColumnType::Memo),
    ColumnSpec::new(
        b"ForeignName",
        ColumnType::Text {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(
        b"RmtInfoShort",
        ColumnType::Binary {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(b"RmtInfoLong", ColumnType::LongBinary),
    ColumnSpec::new(b"Lv", ColumnType::LongBinary),
    ColumnSpec::new(b"LvProp", ColumnType::LongBinary),
    ColumnSpec::new(b"LvModule", ColumnType::LongBinary),
    ColumnSpec::new(b"LvExtra", ColumnType::LongBinary),
];
const OBJECT_CLASSES: [SystemColumnClassSpec; 17] = {
    use SystemColumnClassSpec::{Binary as B, Fixed as F, Variable as V};
    [F, F, V, F, F, F, B, F, V, V, V, V, V, V, V, V, V]
};

const OBJECT_MAPS: [LongValueMapSpec; 7] = [
    lv_map(9, 4, MSYS_OBJECTS_MAP_PAGE, 5, MSYS_OBJECTS_MAP_PAGE),
    lv_map(8, 2, MSYS_OBJECTS_MAP_PAGE, 3, MSYS_OBJECTS_MAP_PAGE),
    lv_map(13, 8, MSYS_OBJECTS_MAP_PAGE, 9, MSYS_OBJECTS_MAP_PAGE),
    lv_map(16, 14, MSYS_OBJECTS_MAP_PAGE, 0, LV_EXTRA_MAP_PAGE),
    lv_map(15, 12, MSYS_OBJECTS_MAP_PAGE, 13, MSYS_OBJECTS_MAP_PAGE),
    lv_map(14, 10, MSYS_OBJECTS_MAP_PAGE, 11, MSYS_OBJECTS_MAP_PAGE),
    lv_map(12, 6, MSYS_OBJECTS_MAP_PAGE, 7, MSYS_OBJECTS_MAP_PAGE),
];

const fn lv_map(
    column: u16,
    owned_row: u8,
    owned_page: u64,
    available_row: u8,
    available_page: u64,
) -> LongValueMapSpec {
    LongValueMapSpec {
        column,
        owned: MapRowLocator::new(PageNumber::new(owned_page), owned_row),
        available: MapRowLocator::new(PageNumber::new(available_page), available_row),
    }
}

pub(super) fn msys_objects_definition(
    row_count: u32,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let parent_name_fields = [field(1), field(2)];
    let id_fields = [field(0)];
    let physical = [
        physical(
            &parent_name_fields,
            OBJECTS_PARENT_NAME_MAP_PAGE,
            0,
            OBJECTS_PARENT_NAME_ROOT,
            PhysicalIndexFlagsSpec::Unique,
            row_count,
        ),
        physical(
            &id_fields,
            OBJECTS_ID_MAP_PAGE,
            0,
            OBJECTS_ID_ROOT,
            PhysicalIndexFlagsSpec::Unique,
            row_count,
        ),
    ];
    let logical = [
        LogicalIndexSpec {
            name: b"Id",
            physical_index: 1,
            kind: LogicalIndexKindSpec::Primary,
        },
        LogicalIndexSpec {
            name: b"ParentIdName",
            physical_index: 0,
            kind: LogicalIndexKindSpec::Ordinary,
        },
    ];
    definition_page(
        &TableDefinitionSpec {
            kind: TableDefinitionKind::System,
            columns: &OBJECT_COLUMNS,
            system_column_classes: &OBJECT_CLASSES,
            physical_indexes: &physical,
            indexes: &logical,
            owned_map: locator(MSYS_OBJECTS_MAP_PAGE, 0),
            available_map: locator(MSYS_OBJECTS_MAP_PAGE, 1),
            row_count,
            long_value_maps: &OBJECT_MAPS,
        },
        budget,
    )
}

const ACE_COLUMNS: [ColumnSpec<'static>; 4] = [
    ColumnSpec::new(b"ObjectId", ColumnType::Long),
    ColumnSpec::new(
        b"SID",
        ColumnType::Binary {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(b"ACM", ColumnType::Long),
    ColumnSpec::new(b"FInheritable", ColumnType::Boolean),
];
const ACE_CLASSES: [SystemColumnClassSpec; 4] = [
    SystemColumnClassSpec::Fixed,
    SystemColumnClassSpec::Binary,
    SystemColumnClassSpec::Fixed,
    SystemColumnClassSpec::Fixed,
];

pub(super) fn msys_aces_definition(
    row_count: u32,
    distinct: u32,
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let fields = [field(0)];
    let physical = [physical(
        &fields,
        SHARED_MAP_PAGE,
        2,
        ACES_OBJECT_ID_ROOT,
        PhysicalIndexFlagsSpec::Required,
        distinct,
    )];
    let logical = [LogicalIndexSpec {
        name: b"ObjectId",
        physical_index: 0,
        kind: LogicalIndexKindSpec::Ordinary,
    }];
    definition_page(
        &TableDefinitionSpec {
            kind: TableDefinitionKind::System,
            columns: &ACE_COLUMNS,
            system_column_classes: &ACE_CLASSES,
            physical_indexes: &physical,
            indexes: &logical,
            owned_map: locator(SHARED_MAP_PAGE, 0),
            available_map: locator(SHARED_MAP_PAGE, 1),
            row_count,
            long_value_maps: &[],
        },
        budget,
    )
}

const QUERY_COLUMNS: [ColumnSpec<'static>; 7] = [
    ColumnSpec::new(b"ObjectId", ColumnType::Long),
    ColumnSpec::new(b"Attribute", ColumnType::Byte),
    ColumnSpec::new(
        b"Order",
        ColumnType::Binary {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(
        b"Name1",
        ColumnType::Text {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(
        b"Name2",
        ColumnType::Text {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(b"Expression", ColumnType::Memo),
    ColumnSpec::new(b"Flag", ColumnType::Integer),
];
const QUERY_CLASSES: [SystemColumnClassSpec; 7] = {
    use SystemColumnClassSpec::{Fixed as F, Variable as V};
    [F, F, V, V, V, V, F]
};
const QUERY_MAPS: [LongValueMapSpec; 1] = [lv_map(5, 5, SHARED_MAP_PAGE, 6, SHARED_MAP_PAGE)];

pub(super) fn msys_queries_definition(
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let fields = [field(0), field(1), field(2)];
    let physical = [physical(
        &fields,
        SHARED_MAP_PAGE,
        7,
        QUERIES_INDEX_ROOT,
        PhysicalIndexFlagsSpec::Unique,
        0,
    )];
    let logical = [LogicalIndexSpec {
        name: b"ObjectIdAttribute",
        physical_index: 0,
        kind: LogicalIndexKindSpec::Primary,
    }];
    definition_page(
        &TableDefinitionSpec {
            kind: TableDefinitionKind::System,
            columns: &QUERY_COLUMNS,
            system_column_classes: &QUERY_CLASSES,
            physical_indexes: &physical,
            indexes: &logical,
            owned_map: locator(SHARED_MAP_PAGE, 3),
            available_map: locator(SHARED_MAP_PAGE, 4),
            row_count: 0,
            long_value_maps: &QUERY_MAPS,
        },
        budget,
    )
}

const RELATIONSHIP_COLUMNS: [ColumnSpec<'static>; 8] = [
    ColumnSpec::new(
        b"szRelationship",
        ColumnType::Text {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(b"grbit", ColumnType::Long),
    ColumnSpec::new(b"ccolumn", ColumnType::Long),
    ColumnSpec::new(b"icolumn", ColumnType::Long),
    ColumnSpec::new(
        b"szObject",
        ColumnType::Text {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(
        b"szColumn",
        ColumnType::Text {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(
        b"szReferencedObject",
        ColumnType::Text {
            max_len: NonZeroU8::MAX,
        },
    ),
    ColumnSpec::new(
        b"szReferencedColumn",
        ColumnType::Text {
            max_len: NonZeroU8::MAX,
        },
    ),
];
const RELATIONSHIP_CLASSES: [SystemColumnClassSpec; 8] = {
    use SystemColumnClassSpec::{Fixed as F, Variable as V};
    [V, F, F, F, V, V, V, V]
};

pub(super) fn msys_relationships_definition(
    row_count: u32,
    entry_counts: [u32; 3],
    budget: &mut ResourceBudget,
) -> Result<PageImage, ComposeError> {
    let name_fields = [field(0)];
    let object_fields = [field(4)];
    let referenced_fields = [field(6)];
    let physical = [
        physical(
            &name_fields,
            SHARED_MAP_PAGE,
            10,
            RELATIONSHIPS_NAME_ROOT,
            PhysicalIndexFlagsSpec::SystemUninterpreted,
            entry_counts[0],
        ),
        physical(
            &object_fields,
            SHARED_MAP_PAGE,
            11,
            RELATIONSHIPS_OBJECT_ROOT,
            PhysicalIndexFlagsSpec::SystemUninterpreted,
            entry_counts[1],
        ),
        physical(
            &referenced_fields,
            SHARED_MAP_PAGE,
            12,
            RELATIONSHIPS_REFERENCED_ROOT,
            PhysicalIndexFlagsSpec::SystemUninterpreted,
            entry_counts[2],
        ),
    ];
    let logical = [
        LogicalIndexSpec {
            name: b"szObject",
            physical_index: 1,
            kind: LogicalIndexKindSpec::Ordinary,
        },
        LogicalIndexSpec {
            name: b"szReferencedObject",
            physical_index: 2,
            kind: LogicalIndexKindSpec::Ordinary,
        },
        LogicalIndexSpec {
            name: b"szRelationship",
            physical_index: 0,
            kind: LogicalIndexKindSpec::Ordinary,
        },
    ];
    definition_page(
        &TableDefinitionSpec {
            kind: TableDefinitionKind::System,
            columns: &RELATIONSHIP_COLUMNS,
            system_column_classes: &RELATIONSHIP_CLASSES,
            physical_indexes: &physical,
            indexes: &logical,
            owned_map: locator(SHARED_MAP_PAGE, 8),
            available_map: locator(SHARED_MAP_PAGE, 9),
            row_count,
            long_value_maps: &[],
        },
        budget,
    )
}

const fn field(column: u16) -> IndexFieldSpec {
    IndexFieldSpec {
        column,
        direction: IndexDirection::Ascending,
    }
}

const fn locator(page: u64, row: u8) -> MapRowLocator {
    MapRowLocator::new(PageNumber::new(page), row)
}

const fn physical<'a>(
    fields: &'a [IndexFieldSpec],
    map: u64,
    row: u8,
    root: u64,
    flags: PhysicalIndexFlagsSpec,
    entry_count: u32,
) -> PhysicalIndexSpec<'a> {
    PhysicalIndexSpec {
        fields,
        usage_map_page: PageNumber::new(map),
        usage_map_row: row,
        root: PageNumber::new(root),
        flags,
        entry_count,
    }
}
