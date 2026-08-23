"""Focused contracts for the A4 dry-run reachability harness (no full sweep here)."""

from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from a4_campaign import Campaign  # noqa: E402
from a4_dryrun import run_fixture  # noqa: E402
from a4_dryrun_core import FixtureRejected  # noqa: E402
from a4_dryrun_eval import evaluate  # noqa: E402
from a4_dryrun_fixtures import ADVERSARIAL, BASELINE, REGISTRY_FIXTURES, UNREACHABLE, all_fixtures  # noqa: E402
from a4_dryrun_h1 import MAX_SYNTACTIC_PAGE, WINDOW_OFFSETS, canonical_pair_count  # noqa: E402
from a4_generator import Params, generate  # noqa: E402
from a4_pages import (  # noqa: E402
    data_page, decode_directory, decode_locator, decode_map_row, encode_locator, type0_row, validate_directory,
)
from a4_spec import (  # noqa: E402
    LOCATOR_LAYOUTS, PLAN, PLAN_SHA256, PREDICATE_CONTRACTS, PREDICATE_ORDER, count_satisfies,
)

A3_BUNDLE = Path(PLAN["candidate_grammars"]["h1"]["a3_page_23_recomputed_work"]["source_bundle"]) / "jet3-a3-bundle"


def _fixture(fixture_id: str):
    return next(f for f in all_fixtures() if f.fixture_id == fixture_id)


class A4SpecTests(unittest.TestCase):
    def test_plan_pin_and_registry_cover_forty_predicates(self) -> None:
        self.assertEqual(len(PLAN_SHA256), 64)
        self.assertEqual(len(PREDICATE_ORDER), 40)
        self.assertEqual({f.predicate_id for f in REGISTRY_FIXTURES}, set(PREDICATE_ORDER))
        self.assertTrue(set(UNREACHABLE) <= set(PREDICATE_ORDER))

    def test_count_contract_semantics(self) -> None:
        self.assertTrue(count_satisfies({"exact": 0}, 0))
        self.assertFalse(count_satisfies({"exact": 0}, 1))
        self.assertTrue(count_satisfies({"minimum": 2}, 3))
        self.assertFalse(count_satisfies({"minimum": 2}, 1))
        replica_contract = {"per_replica_exact": 1, "replica_count": 2, "total_exact": 2}
        self.assertTrue(count_satisfies(replica_contract, 2))
        self.assertFalse(count_satisfies(replica_contract, 1))
        for contract in (c["failure_survivor_count"] for c in PREDICATE_CONTRACTS.values()):
            if "per_replica_exact" in contract:
                self.assertTrue({"per_replica_exact", "replica_count", "total_exact"} <= set(contract), contract)
                self.assertEqual(
                    int(contract["per_replica_exact"]) * int(contract["replica_count"]),
                    int(contract["total_exact"]),
                    contract,
                )
            else:
                self.assertTrue({"exact", "minimum", "allowed_range", "allowed_ranges"} & set(contract), contract)


class A4PageCodecTests(unittest.TestCase):
    def test_locator_roundtrip_both_layouts(self) -> None:
        for layout in LOCATOR_LAYOUTS:
            self.assertEqual(decode_locator(encode_locator(layout, 0x012345, 7), layout), (0x012345, 7))

    def test_directory_validation_rejects_overlap_and_out_of_page(self) -> None:
        page = data_page([type0_row(3, {3, 4}), type0_row(3, set())])
        self.assertIsNone(validate_directory(decode_directory(page, 0x1FFF)))
        broken = bytearray(page)
        broken[12:14] = (12).to_bytes(2, "little")
        self.assertIsNotNone(validate_directory(decode_directory(bytes(broken), 0x1FFF)))

    def test_map_row_rejects_unknown_type(self) -> None:
        self.assertIsInstance(decode_map_row(b"\x07\x00\x00\x00\x00"), str)
        row = decode_map_row(type0_row(10, {10, 12}))
        self.assertEqual(row.type0_pages("set_bit_owned_in_use"), {10, 12})

    def test_canonical_pair_count_matches_brute_force(self) -> None:
        offsets = {0, 1, 4, 5, 9, 40}
        brute = sum(1 for a in offsets for b in offsets if b - a >= 4)
        self.assertEqual(canonical_pair_count(offsets), brute)


class A4GeneratorTests(unittest.TestCase):
    def test_campaign_is_reproducible_and_exact_pages(self) -> None:
        a, b = generate(), generate()
        self.assertEqual(a.inventory()["campaign_sha256"], b.inventory()["campaign_sha256"])
        self.assertTrue(all(len(blob) == 2048 for blob in a.blobs.values()))
        self.assertEqual(sorted(a.replicas), [1, 2, 3])
        self.assertEqual(len(a.replicas[1].snapshots), 25)

    def test_baseline_passes_every_predicate(self) -> None:
        evaluation = evaluate(generate())
        self.assertIsNone(evaluation.first_failure)
        self.assertIsNotNone(evaluation.derivation_sha256)

    def test_schema_snapshot_requires_full_schema_and_exact_campaign(self) -> None:
        for field in ("document_type", "database_unchanged_by_read", "producer_commit", "environment_sha256"):
            campaign = generate()
            del campaign.replicas[1].snapshots["T2_CREATE"][field]
            self.assertEqual(evaluate(campaign).first_failure, "A4-SCHEMA-SNAPSHOT", field)

        for replicas in ((1, 2), (1, 3)):
            campaign = generate()
            campaign.replicas = {replica: campaign.replicas[replica] for replica in replicas}
            self.assertEqual(evaluate(campaign).first_failure, "A4-SCHEMA-SNAPSHOT", replicas)

        campaign = generate()
        campaign.replicas[4] = deepcopy(campaign.replicas[3])
        campaign.replicas[4].number = 4
        self.assertEqual(evaluate(campaign).first_failure, "A4-SCHEMA-SNAPSHOT")

    def test_h4_model_identity_excludes_replica_physical_root_page(self) -> None:
        evaluation = evaluate(generate(Params(system_prefix_pages_by_replica={2: 1, 3: 2})))
        self.assertIsNone(evaluation.first_failure)
        stages = evaluation.stages["h4_catalog_bootstrap"]
        self.assertNotEqual(stages["1"]["root_tdef_page"], stages["2"]["root_tdef_page"])
        self.assertEqual(stages["1"]["canonical_model_id"], stages["2"]["canonical_model_id"])
        self.assertNotEqual(stages["1"]["canonical_candidate_id"], stages["2"]["canonical_candidate_id"])


class A4HarnessTests(unittest.TestCase):
    def test_adversarial_cases_are_handled_as_registered(self) -> None:
        for fixture in ADVERSARIAL:
            entry = run_fixture(fixture)
            self.assertTrue(entry["accepted"], (fixture.fixture_id, entry.get("rejection")))
        counts = {f.fixture_id: run_fixture(f)["measured_count"] for f in ADVERSARIAL if f.fixture_id.startswith("A4-ADV-TDEF")}
        self.assertEqual(counts, {"A4-ADV-TDEF-MULTIPLE-3": 3, "A4-ADV-TDEF-MULTIPLE-4": 4})

    def test_multiple_two_and_encoding_zero_two(self) -> None:
        self.assertEqual(run_fixture(_fixture("A4-R06-H1-TDEF-MULTIPLE"))["measured_count"], 2)
        self.assertEqual(run_fixture(_fixture("A4-R37-H4-ENCODING"))["measured_count"], 0)
        self.assertEqual(run_fixture(_fixture("A4-ADV-ENCODING-2"))["measured_count"], 2)

    def test_unregistered_id_and_malformed_page_raise_before_evaluation(self) -> None:
        with self.assertRaises(FixtureRejected):
            evaluate(generate(), {"row_mask": ["0x0001"]})
        campaign = generate()
        digest = campaign.replicas[1].pages["EMPTY"][1]
        campaign.blobs[digest] = campaign.blobs[digest][:-1]
        with self.assertRaises(FixtureRejected):
            evaluate(campaign)

    def test_adversarial_rejection_reason_must_match(self) -> None:
        fixture = replace(_fixture("A4-ADV-MALFORMED-PAGE"),
                          grammar_selection={"base_formula": ["not-a-registered-formula"]})
        entry = run_fixture(fixture)
        self.assertTrue(entry["rejected"])
        self.assertEqual(entry["rejection_code"], "unregistered_candidate_id")
        self.assertFalse(entry["accepted"])

    def test_earlier_predicate_wins_and_fixture_is_rejected(self) -> None:
        entry = run_fixture(_fixture("A4-ADV-EARLIER-PREDICATE"))
        self.assertTrue(entry["rejected"])
        self.assertEqual(entry["first_failure"], "A4-H2-ROW-DIRECTORY-INVALID")

    def test_wrong_target_claim_is_rejected(self) -> None:
        fixture = replace(_fixture("A4-R15-H2-FLAGS"), predicate_id="A4-H3-BASE-NONE")
        entry = run_fixture(fixture)
        self.assertFalse(entry["accepted"])

    def test_unreachable_attempt_fixture_does_not_reach(self) -> None:
        for predicate_id in UNREACHABLE:
            entry = run_fixture(next(f for f in REGISTRY_FIXTURES if f.predicate_id == predicate_id))
            self.assertFalse(entry["accepted"])
            self.assertLessEqual(entry["measured_count"], 1)

    def test_baseline_fixture_has_no_patches_or_knobs(self) -> None:
        self.assertEqual(BASELINE.params, Params())
        self.assertEqual(BASELINE.patches, ())


class A4CalibrationTests(unittest.TestCase):
    @unittest.skipUnless(A3_BUNDLE.is_dir(), "retained A3 bundle is absent")
    def test_a3_page_23_preserved_windows_match_plan(self) -> None:
        index_dir = A3_BUNDLE / "page-indexes" / "replica-01"
        pages = []
        for index in sorted(index_dir.glob("*.json")):
            digest = json.loads(index.read_text())["ordered_page_sha256"][23]
            pages.append((A3_BUNDLE / "page-store" / f"{digest}.page").read_bytes())
        self.assertEqual(len(pages), 25)
        expected = PLAN["candidate_grammars"]["h1"]["a3_page_23_recomputed_work"]
        for layout, key in (("u24le_page_then_u8_row", "syntactically_preserved_page_row"),
                            ("u8_row_then_u24le_page", "syntactically_preserved_row_page")):
            preserved = 0
            for offset in WINDOW_OFFSETS:
                decoded = {decode_locator(p[offset: offset + 4], layout) for p in pages}
                preserved += len(decoded) == 1 and next(iter(decoded))[0] <= MAX_SYNTACTIC_PAGE
            self.assertEqual(preserved, expected[key], layout)


if __name__ == "__main__":
    unittest.main()
