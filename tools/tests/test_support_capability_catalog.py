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

    def test_checked_matrix_exactly_matches_canonical_catalog(self) -> None:
        policies, errors = support._load_capability_policies(REPOSITORY)
        self.assertEqual(errors, [])
        self.assertEqual(
            [row["id"] for row in self.matrix["capabilities"]],
            [policy.capability_id for policy in policies],
        )
        self.assertEqual(self.errors(self.matrix), [])

    def test_schema_definitions_encode_scope_classification(self) -> None:
        schema = json.loads(
            (
                REPOSITORY
                / "docs/validation/schema/support-matrix.schema.json"
            ).read_text(encoding="utf-8")
        )
        in_scope = schema["$defs"]["inScopeCapability"]["allOf"][1]["properties"]
        self.assertEqual(
            in_scope["implementation"], {"not": {"const": "out_of_scope_v1"}}
        )
        out_of_scope = schema["$defs"]["outOfScopeCapability"]["allOf"][1][
            "properties"
        ]
        self.assertEqual(
            out_of_scope,
            {
                "implementation": {"const": "out_of_scope_v1"},
                "verification": {"const": "not_applicable"},
                "required_verification": {"const": "not_applicable"},
            },
        )

    def test_deleting_any_capability_fails_closed(self) -> None:
        for index, capability in enumerate(self.matrix["capabilities"]):
            with self.subTest(capability=capability["id"]):
                changed = copy.deepcopy(self.matrix)
                changed["capabilities"].pop(index)
                self.assertTrue(
                    any(
                        "capability catalog mismatch" in error
                        for error in self.errors(changed)
                    )
                )

    def test_rename_insertion_and_reordering_fail_closed(self) -> None:
        renamed = copy.deepcopy(self.matrix)
        renamed["capabilities"][0]["id"] = "database.renamed"
        self.assertTrue(
            any(
                "capability catalog mismatch" in error
                for error in self.errors(renamed)
            )
        )

        inserted = copy.deepcopy(self.matrix)
        extra = copy.deepcopy(inserted["capabilities"][0])
        extra["id"] = "database.inserted"
        inserted["capabilities"].append(extra)
        self.assertTrue(
            any(
                "capability catalog mismatch" in error
                for error in self.errors(inserted)
            )
        )

        reordered = copy.deepcopy(self.matrix)
        reordered["capabilities"][0], reordered["capabilities"][1] = (
            reordered["capabilities"][1],
            reordered["capabilities"][0],
        )
        self.assertTrue(
            any("canonical order" in error for error in self.errors(reordered))
        )

    def test_scope_and_required_verification_are_catalog_policies(self) -> None:
        in_scope = copy.deepcopy(self.matrix)
        in_scope["capabilities"][0].update(
            implementation="out_of_scope_v1",
            verification="not_applicable",
            required_verification="not_applicable",
            reason="Attempted scope contraction.",
            evidence=[],
        )
        self.assertTrue(
            any(
                "capability catalog requires an in-scope state" in error
                for error in self.errors(in_scope)
            )
        )

        out_of_scope = copy.deepcopy(self.matrix)
        out_of_scope["capabilities"][-1].update(
            implementation="not_started",
            verification="unverified",
            required_verification="independent_check",
            evidence=[],
        )
        self.assertTrue(
            any(
                "capability catalog requires out_of_scope_v1" in error
                for error in self.errors(out_of_scope)
            )
        )

        requirement = copy.deepcopy(self.matrix)
        requirement["capabilities"][0]["required_verification"] = "independent_check"
        self.assertTrue(
            any(
                "capability catalog requires 'dao_differential'" in error
                for error in self.errors(requirement)
            )
        )


if __name__ == "__main__":
    unittest.main()
