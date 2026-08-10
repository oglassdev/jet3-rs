from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
M4_ROOT = TESTS.parent / "experiments" / "m4"
sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location(
    "m4_analysis", SCRIPTS / "m4_analysis.py"
)
M4 = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = M4
SPEC.loader.exec_module(M4)

from protocol_validation import lint_schema, validate_schema_value  # noqa: E402


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class M4AnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = json.loads(
            (M4_ROOT / "m4-header-discriminator.plan.json").read_text(
                encoding="utf-8"
            )
        )
        self.records, self.prefixes = self._fixture(
            version=True,
            v30_encryption=True,
            all_encryption=True,
        )

    def _fixture(
        self,
        *,
        version: bool,
        v30_encryption: bool,
        all_encryption: bool,
    ) -> tuple[list[dict[str, object]], dict[str, bytes]]:
        condition_map = {
            item["condition_id"]: item for item in self.plan["conditions"]
        }
        records: list[dict[str, object]] = []
        prefixes: dict[str, bytes] = {}
        for sample in self.plan["samples"]:
            condition = condition_map[sample["condition_id"]]
            raw = bytearray(M4.PREFIX_BYTES)
            version_name = condition["version_option"]
            encrypted = condition["encryption_option"] == "dbEncrypt"
            if version:
                raw[10] = {
                    "dbVersion20": 1,
                    "dbVersion30": 2,
                    "dbVersion40": 3,
                }[version_name]
            if v30_encryption and version_name == "dbVersion30" and encrypted:
                raw[20] = 4
            if all_encryption:
                raw[30] = {
                    "dbVersion20": 1,
                    "dbVersion30": 2,
                    "dbVersion40": 3,
                }[version_name]
                if encrypted:
                    raw[30] ^= 8
            phases = {}
            for phase_ordinal, phase_id in enumerate(M4.PHASES, start=1):
                prefix_path = (
                    f"evidence/prefixes/{sample['sample_id']}-{phase_id}.bin"
                )
                value = bytes(raw)
                prefixes[prefix_path] = value
                phases[phase_id] = {
                    "phase_id": phase_id,
                    "phase_ordinal": phase_ordinal,
                    "dao_observations_while_open": {
                        "dao_version": condition["expected_dao_version"],
                    },
                    "post_close_file_observations": {
                        "database_path": sample[f"{phase_id}_database_path"],
                        "prefix_path": prefix_path,
                        "prefix_bytes": M4.PREFIX_BYTES,
                        "prefix_sha256": sha256(value),
                    },
                    "status": "pass",
                }
            records.append(
                {
                    "sample_id": sample["sample_id"],
                    "condition_id": sample["condition_id"],
                    "replica": sample["replica"],
                    "block": sample["block"],
                    "position_in_block": sample["position_in_block"],
                    "launch_ordinal": sample["launch_ordinal"],
                    "creation": {
                        "method": "DBEngine.CreateDatabase",
                        "version_option": condition["version_option"],
                        "version_api_value": condition["version_api_value"],
                        "encryption_option": condition["encryption_option"],
                        "encryption_api_value": condition["encryption_api_value"],
                        "create_option_value": condition["create_option_value"],
                        "compact_database_used": False,
                    },
                    "phases": phases,
                    "execution_status": "pass",
                }
            )
        return records, prefixes

    def _rehash_phase(
        self,
        records: list[dict[str, object]],
        prefixes: dict[str, bytes],
        sample_id: str,
        phase_id: str,
    ) -> None:
        record = next(item for item in records if item["sample_id"] == sample_id)
        post_close = record["phases"][phase_id][
            "post_close_file_observations"
        ]
        post_close["prefix_sha256"] = sha256(prefixes[post_close["prefix_path"]])

    def test_exact_counts_and_canonical_order(self) -> None:
        result = M4.build_analysis(self.plan, self.records, self.prefixes)
        comparisons = result["comparisons"]
        self.assertEqual(len(comparisons), 324)
        self.assertEqual(
            [item["comparison_id"] for item in comparisons],
            [f"M4-CMP-{index:03d}" for index in range(1, 325)],
        )
        counts = {
            kind: sum(item["kind"] == kind for item in comparisons)
            for kind in M4.COMPARISON_KINDS
        }
        self.assertEqual(
            counts,
            {
                "paired_phase": 36,
                "within_condition": 180,
                "matched_version": 72,
                "matched_encryption": 36,
            },
        )
        first = comparisons[0]
        self.assertEqual(first["kind"], "paired_phase")
        self.assertEqual(
            first["left"],
            {"sample_id": "M4-V20-U-01", "phase_id": "creator"},
        )
        self.assertEqual(
            first["right"],
            {"sample_id": "M4-V20-U-01", "phase_id": "reopen"},
        )
        self.assertEqual(comparisons[36]["kind"], "within_condition")
        self.assertEqual(comparisons[216]["kind"], "matched_version")
        self.assertEqual(comparisons[288]["kind"], "matched_encryption")

    def test_exact_candidate_predicates_and_occurrences(self) -> None:
        result = M4.build_analysis(self.plan, self.records, self.prefixes)
        candidates = {
            item["candidate_set_id"]: item for item in result["candidate_sets"]
        }
        self.assertEqual(result["scientific_outcome"], "candidate_offsets_observed")
        self.assertEqual(
            candidates["M4-CANDIDATE-VERSION-PAIRED"]["absolute_offsets"],
            [10],
        )
        self.assertEqual(
            candidates["M4-CANDIDATE-V30-ENCRYPTION"]["absolute_offsets"],
            [20, 30],
        )
        self.assertEqual(
            candidates["M4-CANDIDATE-ALL-VERSION-ENCRYPTION"][
                "absolute_offsets"
            ],
            [30],
        )
        for candidate in candidates.values():
            self.assertEqual(
                [item["offset"] for item in candidate["comparison_occurrences"]],
                candidate["absolute_offsets"],
            )
            self.assertTrue(
                all(
                    item["occurrences"] > 0
                    for item in candidate["comparison_occurrences"]
                )
            )

    def test_result_embeds_in_the_checked_analysis_schema(self) -> None:
        schema = json.loads(
            (M4_ROOT / "analysis-report.schema.json").read_text(
                encoding="utf-8"
            )
        )
        lint_schema(schema)
        result = {
            "protocol_version": "1.0.0",
            "document_type": "dao_m4_analysis_report",
            "experiment_id": self.plan["experiment_id"],
            "plan_sha256": "a" * 64,
            "sample_records": [
                {
                    "sample_id": sample["sample_id"],
                    "record_path": sample["record_path"],
                    "record_sha256": "b" * 64,
                }
                for sample in self.plan["samples"]
            ],
            **M4.build_analysis(self.plan, self.records, self.prefixes),
        }
        validate_schema_value(result, schema, schema, "$")

    def test_empty_and_partial_results_are_not_execution_failures(self) -> None:
        empty_records, empty_prefixes = self._fixture(
            version=False,
            v30_encryption=False,
            all_encryption=False,
        )
        empty = M4.build_analysis(self.plan, empty_records, empty_prefixes)
        self.assertEqual(empty["candidate_sets"], [])
        self.assertEqual(empty["execution_status"], "pass")
        self.assertEqual(empty["scientific_outcome"], "no_candidates_observed")

        partial_records, partial_prefixes = self._fixture(
            version=False,
            v30_encryption=True,
            all_encryption=False,
        )
        partial = M4.build_analysis(self.plan, partial_records, partial_prefixes)
        self.assertEqual(partial["execution_status"], "pass")
        self.assertEqual(partial["scientific_outcome"], "inconclusive")
        self.assertEqual(len(partial["candidate_sets"]), 1)

    def test_replica_or_reopen_instability_removes_candidate(self) -> None:
        prefix_path = next(
            record["phases"]["reopen"]["post_close_file_observations"][
                "prefix_path"
            ]
            for record in self.records
            if record["sample_id"] == "M4-V30-U-01"
        )
        changed = bytearray(self.prefixes[prefix_path])
        changed[10] ^= 0x40
        self.prefixes[prefix_path] = bytes(changed)
        self._rehash_phase(
            self.records,
            self.prefixes,
            "M4-V30-U-01",
            "reopen",
        )
        result = M4.build_analysis(self.plan, self.records, self.prefixes)
        self.assertNotIn(
            "M4-CANDIDATE-VERSION-PAIRED",
            {
                item["candidate_set_id"]
                for item in result["candidate_sets"]
            },
        )

    def test_v30_candidate_ignores_other_version_instability(self) -> None:
        prefix_path = next(
            record["phases"]["reopen"]["post_close_file_observations"][
                "prefix_path"
            ]
            for record in self.records
            if record["sample_id"] == "M4-V20-U-01"
        )
        changed = bytearray(self.prefixes[prefix_path])
        changed[20] ^= 0x40
        self.prefixes[prefix_path] = bytes(changed)
        self._rehash_phase(
            self.records,
            self.prefixes,
            "M4-V20-U-01",
            "reopen",
        )
        result = M4.build_analysis(self.plan, self.records, self.prefixes)
        v30 = next(
            item
            for item in result["candidate_sets"]
            if item["candidate_set_id"] == "M4-CANDIDATE-V30-ENCRYPTION"
        )
        self.assertIn(20, v30["absolute_offsets"])

    def test_stronger_encryption_candidate_requires_one_nonzero_effect(self) -> None:
        for record in self.records:
            if record["condition_id"] != "V20-E":
                continue
            for phase_id in M4.PHASES:
                post_close = record["phases"][phase_id][
                    "post_close_file_observations"
                ]
                path = post_close["prefix_path"]
                changed = bytearray(self.prefixes[path])
                changed[30] ^= 12
                self.prefixes[path] = bytes(changed)
                post_close["prefix_sha256"] = sha256(self.prefixes[path])
        result = M4.build_analysis(self.plan, self.records, self.prefixes)
        candidate_ids = {
            item["candidate_set_id"] for item in result["candidate_sets"]
        }
        self.assertNotIn(
            "M4-CANDIDATE-ALL-VERSION-ENCRYPTION",
            candidate_ids,
        )
        self.assertIn("M4-CANDIDATE-V30-ENCRYPTION", candidate_ids)

    def test_excluded_bytes_do_not_enter_any_result(self) -> None:
        baseline = M4.build_analysis(self.plan, self.records, self.prefixes)
        for record in self.records:
            for phase_id in M4.PHASES:
                post_close = record["phases"][phase_id][
                    "post_close_file_observations"
                ]
                path = post_close["prefix_path"]
                changed = bytearray(self.prefixes[path])
                changed[M4.ANALYZED_BYTES:] = bytes(
                    [record["launch_ordinal"]]
                ) * (M4.PREFIX_BYTES - M4.ANALYZED_BYTES)
                self.prefixes[path] = bytes(changed)
                post_close["prefix_sha256"] = sha256(self.prefixes[path])
        changed_result = M4.build_analysis(
            self.plan, self.records, self.prefixes
        )
        self.assertEqual(baseline, changed_result)
        for comparison in changed_result["comparisons"]:
            self.assertTrue(
                all(offset < M4.ANALYZED_BYTES for offset in comparison["differing_offsets"])
            )
        for candidate in changed_result["candidate_sets"]:
            self.assertTrue(
                all(offset < M4.ANALYZED_BYTES for offset in candidate["absolute_offsets"])
            )

    def test_order_independence_and_canonical_bytes(self) -> None:
        first = M4.build_analysis(self.plan, self.records, self.prefixes)
        reversed_records = list(reversed(copy.deepcopy(self.records)))
        reversed_prefixes = dict(reversed(list(self.prefixes.items())))
        second = M4.build_analysis(
            self.plan, reversed_records, reversed_prefixes
        )
        self.assertEqual(first, second)
        self.assertEqual(
            M4.canonical_analysis_bytes(first),
            M4.canonical_analysis_bytes(second),
        )

    def test_candidate_and_outcome_rules_are_plan_registered(self) -> None:
        changed = copy.deepcopy(self.plan)
        changed["analysis"]["candidate_predicates"][0][
            "candidate_set_id"
        ] = "UNREGISTERED"
        with self.assertRaises(M4.ValidationError):
            M4.build_analysis(changed, self.records, self.prefixes)
        changed = copy.deepcopy(self.plan)
        predicates = changed["analysis"]["candidate_predicates"]
        predicates[0], predicates[1] = predicates[1], predicates[0]
        with self.assertRaises(M4.ValidationError):
            M4.build_analysis(changed, self.records, self.prefixes)
        changed = copy.deepcopy(self.plan)
        changed["analysis"]["candidate_predicates"][1][
            "stability_scope"
        ] = "all_conditions_all_replicas_both_phases"
        with self.assertRaises(M4.ValidationError):
            M4.build_analysis(changed, self.records, self.prefixes)
        changed = copy.deepcopy(self.plan)
        changed["analysis"]["scientific_outcome_rules"][
            "candidate_offsets_observed_requires_all_nonempty"
        ] = ["UNREGISTERED"]
        with self.assertRaises(M4.ValidationError):
            M4.build_analysis(changed, self.records, self.prefixes)

    def test_missing_duplicate_extra_and_mismatched_inputs_reject(self) -> None:
        cases = (
            "missing_record",
            "duplicate_record",
            "missing_prefix",
            "extra_prefix",
            "prefix_hash",
            "projection",
            "short_prefix",
        )
        for case in cases:
            with self.subTest(case=case):
                records = copy.deepcopy(self.records)
                prefixes = dict(self.prefixes)
                if case == "missing_record":
                    records.pop()
                elif case == "duplicate_record":
                    records[-1] = copy.deepcopy(records[0])
                elif case == "missing_prefix":
                    prefixes.pop(next(iter(prefixes)))
                elif case == "extra_prefix":
                    prefixes["evidence/prefixes/extra.bin"] = bytes(M4.PREFIX_BYTES)
                elif case == "prefix_hash":
                    records[0]["phases"]["creator"][
                        "post_close_file_observations"
                    ]["prefix_sha256"] = "0" * 64
                elif case == "projection":
                    records[0]["condition_id"] = "V40-E"
                else:
                    path = records[0]["phases"]["creator"][
                        "post_close_file_observations"
                    ]["prefix_path"]
                    prefixes[path] = b"x"
                with self.assertRaises(M4.ValidationError):
                    M4.build_analysis(self.plan, records, prefixes)

    def test_plan_bound_and_excluded_range_tampering_reject(self) -> None:
        mutations = (
            lambda plan: plan["bounds"].__setitem__("max_comparisons", 323),
            lambda plan: plan["bounds"].__setitem__(
                "max_comparison_byte_visits", M4.EXPECTED_BYTE_VISITS - 1
            ),
            lambda plan: plan["analysis"]["analyzed_ranges"].__setitem__(
                0, {"start": 0, "end": M4.ANALYZED_BYTES + 1}
            ),
            lambda plan: plan["analysis"]["excluded_ranges"][0].__setitem__(
                "start", M4.ANALYZED_BYTES - 1
            ),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                plan = copy.deepcopy(self.plan)
                mutation(plan)
                with self.assertRaises(M4.ValidationError):
                    M4.build_analysis(plan, self.records, self.prefixes)

    def test_result_byte_ceiling_is_immutable(self) -> None:
        plan = copy.deepcopy(self.plan)
        plan["bounds"]["max_analysis_report_bytes"] = 1
        with self.assertRaisesRegex(M4.ValidationError, r"\$\.bounds"):
            M4.build_analysis(plan, self.records, self.prefixes)


if __name__ == "__main__":
    unittest.main()
