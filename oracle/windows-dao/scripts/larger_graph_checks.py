"""Complete relationship-graph checks, including branched system indexes."""
import relationship_index_checks as checks
import relationship_system_indexes as system_index_inventory

normalized_snapshot = checks.normalized_snapshot
normalized_raw_tables = checks.normalized_raw_tables


def evaluate_case(case, result, path, *, include_row_bytes=False):
    return checks.evaluate_case(
        case, result, path, include_row_bytes=include_row_bytes,
        system_inventory=system_index_inventory.inventory,
    )
