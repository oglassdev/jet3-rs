//! Protocol 1.2 scenario inventories that `jet3-cli snapshot` can evaluate.

/// Field-update inventory.
pub const UPDATE_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/update-scenarios.json");

/// Public row mutation inventory.
pub const ROW_UPDATE_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/row-update-scenarios.json");

/// Row allocation and compaction inventory.
pub const ROW_ALLOCATION_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/row-allocation-scenarios.json");

/// Indexed field-update inventory.
pub const INDEXED_UPDATE_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/indexed-update-scenarios.json");

/// Creation index boundary inventory.
pub const CREATION_INDEX_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/creation-index-scenarios.json");

/// Row release and full-row replacement inventory.
pub const ROW_REPLACEMENT_SCENARIOS: &str =
    include_str!("../../../oracle/windows-dao/protocol/v1_2/row-replacement-scenarios.json");
