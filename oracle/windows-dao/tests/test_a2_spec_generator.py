"""Focused contracts for the checked A2 spec and schedule-derived generator."""

from __future__ import annotations

import ast
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from a2_generator import (  # noqa: E402
    SyntheticParameters,
    generate_synthetic_bundle,
    iter_parameter_combinations,
    run12_calibration_parameters,
    write_synthetic_bundle,
)
from a2_spec import (  # noqa: E402
    BIT_POLARITIES,
    BOUNDS,
    CHECKPOINT_IDS,
    CHECKPOINT_ORDINALS,
    EXPERIMENT_ID,
    PAGE_SIZE,
    PLAN_SHA256,
    POINTER_LAYOUTS,
    PREDICATE_IDS,
    REASON_IDS,
    ROLES,
    load_bounded_json,
    load_checked_plan,
    validate_analysis_report,
    validate_bundle_manifest,
    validate_document,
    validate_dry_run_report,
    validate_holdout_structure_receipt,
    validate_page_index,
)


class A2SpecGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = load_checked_plan()
        cls.bundle = generate_synthetic_bundle()

    def _payload(self, checkpoint_id: str, page: int) -> bytes:
        digest = self.bundle.ordered_page_sha256[checkpoint_id][page]
        return self.bundle.page_bytes(digest)

    def _global_in_use(self, checkpoint_id: str) -> set[int]:
        fixture = self.bundle.global_map
        payload = self._payload(checkpoint_id, fixture.page)
        bitmap = fixture.record_start + 1 + len(ROLES)
        limit = (fixture.inline_boundary - bitmap) * 8
        result = set()
        for page in range(limit):
            byte, shift = divmod(page, 8)
            is_set = bool(payload[bitmap + byte] & (1 << shift))
            in_use = is_set if fixture.bit_polarity == "set_means_in_use" else not is_set
            if in_use:
                result.add(page)
        return result

    def test_plan_projections_are_exact_and_plan_derived(self) -> None:
        document = self.plan.document
        self.assertEqual(CHECKPOINT_IDS, tuple(document["checkpoint_design"]["checkpoint_ids"]))
        self.assertEqual(CHECKPOINT_ORDINALS, {value: index for index, value in enumerate(CHECKPOINT_IDS)})
        self.assertEqual(PREDICATE_IDS, tuple(document["predicate_registry"]["ids"]))
        self.assertEqual(set(REASON_IDS), set(document["decision_rules"]["no_scientific_outcome_identifiers"]))
        self.assertEqual(BOUNDS, document["bounds"])

    def test_schedule_produces_d_abac_with_larger_regrowth(self) -> None:
        a = self._global_in_use("E0")
        b = self._global_in_use("D_GROW_0128")
        dropped = self._global_in_use("D_DROP")
        recreated = self._global_in_use("D_RECREATE_EMPTY")
        c = self._global_in_use("D_REGROW_0128")
        growth = b - a
        self.assertTrue(growth)
        self.assertEqual(dropped, a)
        self.assertEqual(recreated, a)
        self.assertLessEqual(growth, c)
        self.assertTrue(c - b)
        self.assertGreater(
            self.bundle.page_count["D_REGROW_0128"],
            self.bundle.page_count["D_GROW_0128"],
        )
        self.assertEqual(
            self.bundle.page_count["D_DROP"],
            self.bundle.page_count["D_GROW_0128"],
        )
        d_checkpoints = self.plan.document["checkpoint_design"]["transition_coverage"]["global_map_record_set_abac"]
        payloads = [self._payload(checkpoint, self.bundle.global_map.page) for checkpoint in d_checkpoints]
        changed = [
            offset
            for offset in range(PAGE_SIZE)
            if len({payload[offset] for payload in payloads}) > 1
        ]
        self.assertEqual(changed[-1], self.bundle.global_map.inline_boundary - 1)
        self.assertEqual(
            PAGE_SIZE - changed[-1] - 1,
            self.bundle.parameters.record_end_uniform_slack_bytes,
        )

    def test_targets_batches_and_overshoot_are_derived_for_every_checkpoint(self) -> None:
        schedule = self.bundle.schedule
        batch = self.plan.document["tables"]["row_algorithm"]["growth_batch_rows"]
        for row in schedule.checkpoints:
            self.assertEqual(row.inserted_rows_total % batch, 0)
            if row.target_threshold_pages is not None:
                self.assertGreaterEqual(row.actual_file_pages, row.target_threshold_pages)
                self.assertEqual(
                    row.target_overshoot_pages,
                    row.actual_file_pages - row.target_threshold_pages,
                )
        self.assertEqual(
            schedule.checkpoint("D_REGROW_0128").target_baseline_pages,
            schedule.checkpoint("D_RECREATE_EMPTY").actual_file_pages,
        )

    def test_tdef_growth_and_churn_windows_are_transition_selective(self) -> None:
        fixture = self.bundle.tdef
        pointer_bytes = len(ROLES)
        growth = fixture.growth_pointer_offset
        churn = fixture.delete_reinsert_pointer_offset

        def window(checkpoint: str, offset: int) -> bytes:
            return self._payload(checkpoint, fixture.page)[offset : offset + pointer_bytes]

        low_growth = self.plan.document["checkpoint_design"]["transition_coverage"]["tdef_low_growth"]
        self.assertTrue(
            any(window(left, growth) != window(right, growth) for left, right in zip(low_growth, low_growth[1:]))
        )
        self.assertEqual(window("L_REL_1280", growth), window("L_DELETE_ALL", growth))
        self.assertEqual(window("L_DELETE_ALL", growth), window("L_REINSERT_SAME", growth))
        before = window("L_REL_1280", churn)
        deleted = window("L_DELETE_ALL", churn)
        reinserted = window("L_REINSERT_SAME", churn)
        self.assertNotEqual(before, deleted)
        self.assertEqual(before, reinserted)

    def test_extended_maps_include_slot_zero_and_exact_growth_discriminators(self) -> None:
        global_fixture = self.bundle.global_map
        conversion = "P_ABS_16480"
        global_payload = self._payload(conversion, global_fixture.page)
        slots = global_fixture.record_start + 1
        references = [
            int.from_bytes(global_payload[slots + slot * len(ROLES) : slots + (slot + 1) * len(ROLES)], "little")
            for slot in range(len(BIT_POLARITIES))
        ]

        def in_use_bits(checkpoint: str, reference: int) -> set[int]:
            payload = self._payload(checkpoint, reference)
            result = set()
            for bit in range((PAGE_SIZE - len(ROLES)) * 8):
                byte, shift = divmod(bit, 8)
                is_set = bool(payload[len(ROLES) + byte] & (1 << shift))
                in_use = is_set if global_fixture.bit_polarity == "set_means_in_use" else not is_set
                if in_use:
                    result.add(bit)
            return result

        slot_zero_before = in_use_bits(conversion, references[0])
        slot_zero_after = in_use_bits("H_REL_0064", references[0])
        self.assertEqual(
            {references[0] + bit for bit in slot_zero_after - slot_zero_before},
            set(
                range(
                    self.bundle.page_count[conversion],
                    self.bundle.page_count["H_REL_0064"],
                )
            ),
        )
        before = in_use_bits("H_REL_0064", references[0])
        after = in_use_bits("H_REL_0896", references[0])
        predicted = {references[0] + bit for bit in after - before}
        self.assertEqual(
            predicted,
            set(
                range(
                    self.bundle.page_count["H_REL_0064"],
                    self.bundle.page_count["H_REL_0896"],
                )
            ),
        )

    def test_every_plan_idle_equality_is_generator_produced(self) -> None:
        for left, right in self.plan.document["checkpoint_design"]["idle_pairs"]:
            self.assertEqual(self.bundle.page_count[left], self.bundle.page_count[right])
            self.assertEqual(
                self.bundle.ordered_page_sha256[left],
                self.bundle.ordered_page_sha256[right],
            )

    def test_every_free_parameter_combination_is_enumerable(self) -> None:
        free = self.plan.document["analyzer_dry_run_contract"]["synthetic_input"]["free_parameters"]
        combinations = list(iter_parameter_combinations())
        expected = (
            len(CHECKPOINT_IDS)
            * len(free["slot_activation_at_conversion"])
            * len(free["bit_polarity"])
            * len(free["anchor_fill_state"])
            * len(free["record_end_uniform_slack_bytes"])
        )
        self.assertEqual(len(combinations), expected)
        self.assertEqual(len(combinations), len(set(combinations)))
        conversions = {item.conversion_ordinal for item in combinations}
        self.assertEqual(conversions, set(range(1, len(CHECKPOINT_IDS))) | {None})
        legacy = {item.conversion_ordinal for item in iter_parameter_combinations(legacy_projection=True)}
        self.assertEqual(legacy, set(range(1, 71)) | {None})

    def test_anchor_fill_and_slack_do_not_move_declared_boundary_formula(self) -> None:
        baseline = run12_calibration_parameters()
        free = self.plan.document["analyzer_dry_run_contract"]["synthetic_input"]["free_parameters"]
        for fill in free["anchor_fill_state"]:
            for slack in free["record_end_uniform_slack_bytes"]:
                parameters = SyntheticParameters(
                    baseline.conversion_ordinal,
                    baseline.slot_activation_at_conversion,
                    baseline.bit_polarity,
                    fill,
                    slack,
                    baseline.delete_page_delta,
                )
                bundle = generate_synthetic_bundle(parameters)
                self.assertEqual(bundle.global_map.record_end, PAGE_SIZE)
                self.assertEqual(bundle.global_map.inline_boundary, PAGE_SIZE - slack)
                anchor_id = CHECKPOINT_IDS[baseline.conversion_ordinal - 1]
                payload = bundle.page_bytes(
                    bundle.ordered_page_sha256[anchor_id][bundle.global_map.page]
                )
                bitmap = bundle.global_map.record_start + 1 + len(ROLES)
                capacity = (bundle.global_map.inline_boundary - bitmap) * 8
                in_use = 0
                for bit in range(capacity):
                    byte, shift = divmod(bit, 8)
                    is_set = bool(payload[bitmap + byte] & (1 << shift))
                    in_use += is_set if parameters.bit_polarity == "set_means_in_use" else not is_set
                expected = {"empty": 0, "partial": capacity // 2, "full": capacity}[fill]
                self.assertEqual(in_use, expected)

    def test_conversion_and_slot_parameters_are_encoded_at_the_requested_ordinal(self) -> None:
        baseline = run12_calibration_parameters()
        for ordinal in (1, len(CHECKPOINT_IDS) // 2, len(CHECKPOINT_IDS) - 1):
            for slots in self.plan.document["analyzer_dry_run_contract"]["synthetic_input"]["free_parameters"]["slot_activation_at_conversion"]:
                parameters = SyntheticParameters(
                    ordinal,
                    slots,
                    baseline.bit_polarity,
                    baseline.anchor_fill_state,
                    baseline.record_end_uniform_slack_bytes,
                    baseline.delete_page_delta,
                )
                bundle = generate_synthetic_bundle(parameters)
                before = CHECKPOINT_IDS[ordinal - 1]
                at = CHECKPOINT_IDS[ordinal]
                self.assertEqual(self._tag(bundle, before), 0)
                self.assertEqual(self._tag(bundle, at), 1)
                self.assertEqual(self._active_slot_count(bundle, at), slots)
                if ordinal <= CHECKPOINT_ORDINALS["H_REL_0904"]:
                    self.assertEqual(
                        self._active_slot_count(bundle, "H_REL_0904"), 2
                    )
        never = SyntheticParameters(
            None,
            baseline.slot_activation_at_conversion,
            baseline.bit_polarity,
            baseline.anchor_fill_state,
            baseline.record_end_uniform_slack_bytes,
            baseline.delete_page_delta,
        )
        bundle = generate_synthetic_bundle(never)
        self.assertTrue(all(self._tag(bundle, checkpoint) == 0 for checkpoint in CHECKPOINT_IDS))

    @staticmethod
    def _tag(bundle: object, checkpoint: str) -> int:
        digest = bundle.ordered_page_sha256[checkpoint][bundle.global_map.page]
        return bundle.page_bytes(digest)[bundle.global_map.record_start]

    @staticmethod
    def _active_slot_count(bundle: object, checkpoint: str) -> int:
        digest = bundle.ordered_page_sha256[checkpoint][bundle.global_map.page]
        payload = bundle.page_bytes(digest)
        start = bundle.global_map.record_start + 1
        return sum(
            int.from_bytes(
                payload[start + slot * len(ROLES) : start + (slot + 1) * len(ROLES)],
                "little",
            )
            != 0
            for slot in range(len(BIT_POLARITIES))
        )

    def test_generated_documents_and_disk_artifacts_validate(self) -> None:
        observation = self.bundle.documents["observations/replica-01.json"]
        prior: list[str] = []
        for checkpoint in observation["checkpoints"]:
            document = self.bundle.documents[checkpoint["page_index"]["path"]]
            prior = validate_page_index(document, observation, checkpoint, prior)
        for document in self.bundle.documents.values():
            validate_document(document)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_synthetic_bundle(root, self.bundle)
            for path, expected in self.bundle.documents.items():
                self.assertEqual(load_bounded_json(root / path), expected)
            for digest in self.bundle._payloads:
                payload = (root / f"page-store/{digest}.page").read_bytes()
                self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)

    def test_applicable_source_contracts_reject_inherited_constants_and_blacklists(self) -> None:
        for name in (
            "a2_spec.py",
            "a2_generator.py",
            "a2_generator_schedule.py",
            "a2_generator_pages.py",
        ):
            source = (SCRIPTS / name).read_text(encoding="utf-8")
            self.assertLess(len(source.splitlines()), 800)
            tree = ast.parse(source)
            imports = {
                alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            } | {
                node.module or ""
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            }
            self.assertFalse(any(module.startswith("a1") for module in imports))
            self.assertNotIn("CONVERSION_CHECKPOINT", source)
            self.assertNotIn("header_exclusion", source)
            self.assertNotIn("blacklist", source.lower())

    def test_remaining_schema_validators_accept_consistent_documents(self) -> None:
        report = self._analysis_report()
        validate_analysis_report(report)
        validate_dry_run_report(self._dry_run_report())
        validate_holdout_structure_receipt(self._holdout_receipt(report))
        validate_bundle_manifest(self._bundle_manifest(report))

    def _analysis_report(self) -> dict[str, object]:
        record = {"page": self.bundle.global_map.page, "start": self.bundle.global_map.record_start, "end": self.bundle.global_map.record_end}
        layer = lambda model: {"status": "decisive_predicts_holdout", "derivation_survivor_count": 1, "holdout_evaluated": True, "no_outcome_reasons": [], "model": model}
        claims = {
            key: value
            for key, value in self.plan.document["claims"].items()
            if key not in {"a1_exploratory_input_is_a2_evidence", "synthetic_dry_run_is_a2_evidence"}
        }
        return {
            "protocol_version": "1.0.0", "document_type": "dao_a2_analysis_report", "experiment_id": EXPERIMENT_ID,
            "plan_sha256": PLAN_SHA256, "campaign_id": "synthetic-contract", "producer_commit": PLAN_SHA256[:40],
            "derivation_replicas": [1, 2], "holdout_replica": 3, "input_checkpoint_count": 75,
            "qualified_page_counts": {"global_map": 1, "tdef": 1}, "record_candidates_examined": 2,
            "candidate_models_examined": 4, "derivation_survivor_counts": {"global_map_record": 1, "global_map_conversion_inline": 1, "global_map_extended_base": 1, "tdef_pointer_pair": 1},
            "derivation_candidate_set_sha256": "1" * 64, "analysis_work_units": 4,
            "holdout_structurally_validated_after_freeze": True, "holdout_opened_after_freeze": True, "holdout_evaluated": True,
            "predicate_results": [{"predicate_id": "A2-IDLE-EQUALITY", "status": "pass", "layer": "campaign"}],
            "terminal_predicate_ids": [], "scientific_outcome": "one_or_more_submodels_predict_holdout", "no_outcome_reasons": [],
            "submodels": {"global_map": {
                "record": layer({"record": record, "bit_polarity": self.bundle.parameters.bit_polarity, "zero_suffix_slack_bytes": self.bundle.parameters.record_end_uniform_slack_bytes}),
                "conversion_inline": layer({"conversion_checkpoint_id": CHECKPOINT_IDS[self.bundle.parameters.conversion_ordinal], "conversion_ordinal": self.bundle.parameters.conversion_ordinal, "active_slot_count_at_conversion": 2, "active_slot_count_at_h_rel_0904": 2, "inline_boundary": self.bundle.global_map.inline_boundary, "slot_reference_pages": [12288, 16480]}),
                "extended_base": layer({"extended_base_formula": self.bundle.global_map.extended_base_formula}),
            }, "tdef": {"pointer_pair": layer({"record": {"page": self.bundle.tdef.page, "start": self.bundle.tdef.record_start, "end": self.bundle.tdef.record_end}, "pointer_layout": POINTER_LAYOUTS[0], "growth_pointer_offset": self.bundle.tdef.growth_pointer_offset, "delete_reinsert_pointer_offset": self.bundle.tdef.delete_reinsert_pointer_offset})}},
            "claims": claims,
        }

    def _dry_run_report(self) -> dict[str, object]:
        free = self.plan.document["analyzer_dry_run_contract"]["synthetic_input"]["free_parameters"]
        required = self.plan.document["analyzer_dry_run_contract"]["synthetic_input"]["required_cases"]
        return {
            "protocol_version": "1.0.0", "document_type": "dao_a2_analyzer_dry_run_report", "experiment_id": EXPERIMENT_ID,
            "plan_sha256": PLAN_SHA256, "analyzer_commit": PLAN_SHA256[:40], "recorded_utc": "2026-08-21T00:00:00Z",
            "source_kind": "a2_schedule_synthetic", "source_identity": {"manifest_or_fixture_sha256": "2" * 64, "generator_sha256": "3" * 64},
            "checkpoint_schedule_source": "hash_pinned_a2_plan_checkpoint_design", "input_page_blob_count": 0, "holdout_opened": False,
            "parameter_coverage": {"conversion_ordinals": list(range(1, 71)), "conversion_never": True, "slot_activation_counts": free["slot_activation_at_conversion"], "bit_polarities": free["bit_polarity"], "anchor_fill_states": free["anchor_fill_state"], "run12_calibration": {"source_conversion_ordinal": 40, "conversion_checkpoint_id": "P_ABS_16480", "a2_conversion_ordinal": 20, "active_slot_count": 2, "bit_polarity": "set_means_not_in_use", "delete_page_delta": 1, "scientific_evidence": False}, "record_end_uniform_slack_bytes": free["record_end_uniform_slack_bytes"]},
            "predicted_terminal_states": required, "terminal_predicate_ids": list(PREDICATE_IDS), "result": "pass",
            "assertions": ["schedule_and_worker_arithmetic_generated_from_plan"], "scientific_evidence": False,
            "acquisition_authorized": False, "capability_advancement_authorized": False,
        }

    def _holdout_receipt(self, report: dict[str, object]) -> dict[str, object]:
        return {"protocol_version": "1.0.0", "document_type": "dao_a2_holdout_structure_receipt", "experiment_id": EXPERIMENT_ID, "plan_sha256": PLAN_SHA256, "producer_commit": PLAN_SHA256[:40], "campaign_id": report["campaign_id"], "derivation_candidate_set_sha256": report["derivation_candidate_set_sha256"], "replica": 3, "replica_artifact_manifest_sha256": "4" * 64, "validated_after_candidate_freeze": True, "page_bytes_exposed_to_analyzer": False, "result": "pass"}

    def _bundle_manifest(self, report: dict[str, object]) -> dict[str, object]:
        roles = [("plan", 1), ("environment", 3), ("replica_artifact_manifest", 3), ("replica_observation", 3), ("page_index", 75), ("frozen_candidate_set", 1), ("analysis_report", 1), ("holdout_structure_receipt", 1)]
        files = []
        counter = 1
        for role, count in roles:
            for _ in range(count):
                digest = f"{counter:064x}"
                files.append({"path": f"synthetic/{role}-{counter}.json", "role": role, "sha256": digest, "size_bytes": 1, "media_type": "application/json"})
                counter += 1
        page_digest = f"{counter:064x}"
        files.append({"path": f"page-store/{page_digest}.page", "role": "page_blob", "sha256": page_digest, "size_bytes": PAGE_SIZE, "media_type": "application/octet-stream"})
        return {"protocol_version": "1.0.0", "document_type": "dao_a2_bundle_manifest", "experiment_id": EXPERIMENT_ID, "campaign_id": report["campaign_id"], "producer_commit": PLAN_SHA256[:40], "repository_url": self.plan.document["repository_binding"]["canonical_https_url"], "created_utc": "2026-08-21T00:00:00Z", "plan_sha256": PLAN_SHA256, "replica_environment_sha256": ["5" * 64, "6" * 64, "7" * 64], "provider_sha256": "8" * 64, "replica_count": 3, "replica_artifact_manifest_sha256": ["9" * 64, "a" * 64, "b" * 64], "checkpoint_count": 75, "page_blob_count": 1, "bundle_size_bytes_excluding_manifest": sum(item["size_bytes"] for item in files), "inventory_closed": True, "hashes_verified": True, "paths_closed": True, "execution_status": "analysis_complete", "campaign_failed": False, "holdout_structure_receipt_sha256": "c" * 64, "analysis_report_retained": True, "analysis_scientific_outcome": "one_or_more_submodels_predict_holdout", "bundle_status": "decisive_pending_independent_validation", "independent_validation_status": "not_independently_validated", "files": files}


if __name__ == "__main__":
    unittest.main()
