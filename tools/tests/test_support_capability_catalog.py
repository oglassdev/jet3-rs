from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
REPOSITORY = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from validation import support  # noqa: E402


class SupportCapabilityCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.matrix = json.loads(
            (REPOSITORY / "docs/validation/support-matrix.json").read_text(
                encoding="utf-8"
            )
        )

    def errors(self, document: object) -> list[str]:
        return support.validate_support_matrix(document, REPOSITORY)

    def test_checked_matrix_matches_schema_catalog(self) -> None:
        expected, errors = support._catalog(REPOSITORY)
        self.assertEqual(errors, [])
        self.assertEqual(
            [row["id"] for row in self.matrix["capabilities"]], expected
        )
        self.assertEqual(self.errors(self.matrix), [])

    def test_capabilities_have_only_the_four_status_fields(self) -> None:
        for capability in self.matrix["capabilities"]:
            self.assertEqual(set(capability), support.CAPABILITY_KEYS)

    def test_catalog_deletion_rename_and_reordering_fail(self) -> None:
        deleted = copy.deepcopy(self.matrix)
        deleted["capabilities"].pop()
        self.assertTrue(any("catalog mismatch" in item for item in self.errors(deleted)))

        renamed = copy.deepcopy(self.matrix)
        renamed["capabilities"][0]["id"] = "database.renamed"
        self.assertTrue(any("catalog mismatch" in item for item in self.errors(renamed)))

        reordered = copy.deepcopy(self.matrix)
        reordered["capabilities"][0], reordered["capabilities"][1] = (
            reordered["capabilities"][1],
            reordered["capabilities"][0],
        )
        self.assertTrue(any("noncanonical order" in item for item in self.errors(reordered)))

    def test_state_and_evidence_invariants_fail_closed(self) -> None:
        changed = copy.deepcopy(self.matrix)
        changed["capabilities"][0]["evidence"] = []
        self.assertTrue(any("requires evidence" in item for item in self.errors(changed)))

        changed = copy.deepcopy(self.matrix)
        unstarted = next(row for row in changed["capabilities"] if row["implementation"] == "not_started")
        unstarted["verification"] = "internal_only"
        self.assertTrue(any("not_started" in item for item in self.errors(changed)))


if __name__ == "__main__":
    unittest.main()
