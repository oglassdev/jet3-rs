from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bootstrap_layout as bootstrap  # noqa: E402


PLAN_SHA256 = "a" * 64
CREATED_DATE = 45000.25
UPDATED_DATE = 45001.5
PAYLOAD = b"props!"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def endpoints(value: bool) -> dict[str, bool]:
    return {name: value for name in bootstrap.ENDPOINT_NAMES}


def artifact_observation(
    database: str,
    data: bytes,
    *,
    passes: bool,
    detail: str,
) -> dict:
    return {
        "database": database,
        "size_before": len(data),
        "size_after": len(data),
        "sha256_before": digest(data),
        "sha256_after": digest(data),
        "endpoints": {**endpoints(passes), "detail": detail},
        "detail": detail,
    }


def table_dao(name: str, *, with_lvprop: bool = False) -> dict:
    lvprop = (
        {
            "status": "captured",
            "detail": "captured once",
            "length": len(PAYLOAD),
            "bytes_hex": PAYLOAD.hex(),
        }
        if with_lvprop
        else {"status": "no_outcome", "detail": "property not set"}
    )
    return {
        "table_name": name,
        "date_created_oadate": CREATED_DATE,
        "last_updated_oadate": UPDATED_DATE,
        "fields": [{"name": "Id", "type": 4}],
        "lvprop": lvprop,
    }


def write_checkpoint(
    root: Path, replica: int, name: str, pages: list[bytearray], dao: dict
) -> dict:
    data = b"".join(bytes(page) for page in pages)
    database = f"r{replica}-{name}.mdb"
    (root / database).write_bytes(data)
    return {
        "name": name,
        "database": database,
        "size": len(data),
        "page_count": len(pages),
        "sha256": digest(data),
        "dao": dao,
    }


def synthetic_document(root: Path) -> dict:
    replicas = []
    date_created_offset = 18 * bootstrap.PAGE_BYTES + 100
    date_updated_offset = date_created_offset + 8
    header_offset = 18 * bootstrap.PAGE_BYTES + 200
    payload_page = 22
    payload_row = 0
    for replica in range(1, 4):
        empty = [bytearray(bootstrap.PAGE_BYTES) for _ in range(20)]
        for page, image in enumerate(empty):
            image[0] = page % 6
            image[20] = replica
        created = copy.deepcopy(empty)
        created[0][1538] = 7
        created[1][1922] = 0x0F
        created.extend(bytearray(bootstrap.PAGE_BYTES) for _ in range(3))
        created[18][10] = replica
        created[20][0] = 2
        created[21][0] = 1
        created[22][0] = 1
        renamed = copy.deepcopy(created)
        renamed[18][100:108] = struct.pack("<d", CREATED_DATE)
        renamed[18][108:116] = struct.pack("<d", UPDATED_DATE)
        renamed[22][100:108] = struct.pack("<d", CREATED_DATE)
        renamed[22][108:116] = struct.pack("<d", CREATED_DATE)
        property_set = copy.deepcopy(renamed)
        property_set[22][4:8] = b"LVAL"
        property_set[22][8:10] = (1).to_bytes(2, "little")
        payload_start = bootstrap.PAGE_BYTES - len(PAYLOAD)
        property_set[22][10:12] = payload_start.to_bytes(2, "little")
        property_set[22][payload_start:] = PAYLOAD
        header = struct.pack("<I", len(PAYLOAD) | 0x40000000)
        header += bytes([payload_row]) + payload_page.to_bytes(3, "little") + bytes(4)
        property_set[18][200:212] = header
        checkpoints = [
            write_checkpoint(root, replica, "empty", empty, {"table_definition_count": 4}),
            write_checkpoint(root, replica, "created", created, table_dao("BootstrapLayout")),
            write_checkpoint(root, replica, "renamed", renamed, table_dao("BootstrapRenamed")),
            write_checkpoint(
                root,
                replica,
                "property-set",
                property_set,
                table_dao("BootstrapRenamed", with_lvprop=True),
            ),
        ]
        created_bytes = b"".join(bytes(page) for page in created)
        renamed_bytes = b"".join(bytes(page) for page in renamed)
        empty_bytes = b"".join(bytes(page) for page in empty)
        changed_groups = [
            {"name": "existing-page-1", "page": 1, "ranges": [{"start": 3970, "end": 3971}]},
            {
                "name": "existing-page-18",
                "page": 18,
                "ranges": bootstrap._difference_ranges(empty_bytes, created_bytes, 18),
            },
        ]
        appended_groups = [
            {
                "name": f"appended-page-{page}",
                "page": page,
                "ranges": [
                    {
                        "start": page * bootstrap.PAGE_BYTES,
                        "end": (page + 1) * bootstrap.PAGE_BYTES,
                    }
                ],
            }
            for page in range(20, 23)
        ]

        def make_variant(
            name: str,
            kind: str,
            base_checkpoint: str,
            ranges: list[dict[str, int]],
            source: bytes | None,
            passes: bool,
            page: int | None = None,
        ) -> dict:
            base = renamed_bytes if base_checkpoint == "renamed" else created_bytes
            mutated = bytearray(base)
            for item in ranges:
                start, end = item["start"], item["end"]
                mutated[start:end] = bytes(end - start) if source is None else source[start:end]
            database = f"r{replica}-variant-{name}.mdb"
            data = bytes(mutated)
            (root / database).write_bytes(data)
            result = {
                **artifact_observation(
                    database,
                    data,
                    passes=passes,
                    detail="single read-only observation",
                ),
                "name": name,
                "kind": kind,
                "base_checkpoint": base_checkpoint,
                "ranges": ranges,
            }
            if page is not None:
                result["page"] = page
            return result

        variants = [
            make_variant(
                "page0-byte-1538",
                "candidate_page0",
                "created",
                [{"start": 1538, "end": 1539}],
                empty_bytes,
                False,
                0,
            ),
            make_variant(
                "date-created-zero",
                "candidate_date_created",
                "renamed",
                [{"start": date_created_offset, "end": date_created_offset + 8}],
                None,
                True,
                18,
            ),
            make_variant(
                "date-updated-zero",
                "candidate_date_updated",
                "renamed",
                [{"start": date_updated_offset, "end": date_updated_offset + 8}],
                None,
                False,
                18,
            ),
        ]
        for group in changed_groups:
            variants.append(
                make_variant(
                    group["name"],
                    "revert_existing_page",
                    "created",
                    group["ranges"],
                    empty_bytes,
                    group["page"] == 1,
                    group["page"],
                )
            )
        for group in appended_groups:
            variants.append(
                make_variant(
                    group["name"],
                    "zero_appended_page",
                    "created",
                    group["ranges"],
                    None,
                    False,
                    group["page"],
                )
            )
        baseline_database = f"r{replica}-variant-baseline-created.mdb"
        (root / baseline_database).write_bytes(created_bytes)
        sufficiency_database = f"r{replica}-variant-composed.mdb"
        (root / sufficiency_database).write_bytes(created_bytes)
        replicas.append(
            {
                "replica": replica,
                "status": "pass",
                "detail": "completed once",
                "checkpoints": checkpoints,
                "page0_values": {"empty": 0, "created": 7, "renamed": 7},
                "page0_changed_ranges": {
                    "empty_to_created": [{"start": 1538, "end": 1539}],
                    "created_to_renamed": [],
                },
                "baseline": {
                    **artifact_observation(
                        baseline_database,
                        created_bytes,
                        passes=True,
                        detail="all endpoints passed",
                    ),
                },
                "correlations": {
                    "date_created": {
                        "status": "resolved",
                        "detail": "unique",
                        "method": "last_updated_anchor",
                        "offsets": [date_created_offset],
                    },
                    "date_updated": {
                        "status": "resolved",
                        "detail": "unique",
                        "method": "unique_exact",
                        "offsets": [date_updated_offset],
                    },
                    "lvprop": {
                        "status": "resolved",
                        "detail": "unique row and header",
                        "header_offset": header_offset,
                        "payload_page": payload_page,
                        "payload_row": payload_row,
                    },
                },
                "changed_page_groups": changed_groups,
                "appended_page_groups": appended_groups,
                "sufficiency": {
                    **artifact_observation(
                        sufficiency_database,
                        created_bytes,
                        passes=True,
                        detail="all endpoints passed",
                    ),
                },
                "variants": variants,
            }
        )
    return {
        "development_only": True,
        "status": "pass",
        "detail": "attempted three replicas once",
        "plan_sha256": PLAN_SHA256,
        "replicas": replicas,
    }


def write_job(root: Path, document: dict) -> Path:
    path = root / "bootstrap-layout-job-result.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class BootstrapLayoutTests(unittest.TestCase):
    def test_endpoint_frontier_model_accepts_only_sequential_states(self) -> None:
        for bits in range(1 << len(bootstrap.ENDPOINT_NAMES)):
            frontier = {
                name: bool(bits & (1 << index))
                for index, name in enumerate(bootstrap.ENDPOINT_NAMES)
            }
            expected = tuple(frontier.values()) in bootstrap.VALID_ENDPOINT_FRONTIERS
            with self.subTest(frontier=tuple(frontier.values())):
                if expected:
                    self.assertEqual(
                        bootstrap._endpoint_frontier(frontier, "$.endpoints"),
                        frontier,
                    )
                else:
                    with self.assertRaisesRegex(
                        bootstrap.AnalysisError, "reachable DAO endpoint frontier"
                    ):
                        bootstrap._endpoint_frontier(frontier, "$.endpoints")

    def test_accepts_and_emits_deterministic_canonical_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            job = write_job(root, document)
            first = root / "first.json"
            second = root / "second.json"
            report = bootstrap.evaluate(job, PLAN_SHA256, first)
            bootstrap.evaluate(job, PLAN_SHA256, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.read_bytes(), bootstrap.canonical_bytes(report))
        self.assertEqual(report["status"], "accepted")
        self.assertFalse(report["compatibility_claim"])
        self.assertFalse(report["support_movement"])
        self.assertTrue(report["sufficiency_claim"])
        self.assertEqual(
            report["questions"]["composed_image_sufficiency"]["outcome"],
            "observed_sufficient",
        )
        self.assertEqual(report["questions"]["candidate_page0"]["outcome"], "necessary")
        self.assertEqual(
            report["questions"]["candidate_catalog_fields"]["fields"]["lvprop"]["outcome"],
            "resolved",
        )
        self.assertEqual(
            report["questions"]["candidate_catalog_fields"]["fields"]["date_created"][
                "evidence"
            ]["method"],
            "last_updated_anchor",
        )
        self.assertEqual(
            report["questions"]["candidate_catalog_fields"]["fields"]["lvprop"][
                "evidence"
            ]["header_offset"],
            18 * bootstrap.PAGE_BYTES + 200,
        )
        self.assertEqual(
            report["questions"]["composed_image_sufficiency"]["endpoints"],
            endpoints(True),
        )

    def test_partial_failed_replica_is_canonical_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            failed = document["replicas"][1]
            document["replicas"][1] = {
                "replica": 2,
                "status": "fail",
                "detail": "one-run acquisition stopped",
                "checkpoints": failed["checkpoints"][:1],
                "changed_page_groups": [],
                "appended_page_groups": [],
                "variants": [],
            }
            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "report.json"
            )
        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(report["replicas"][1]["status"], "no_outcome")

    def test_producer_shaped_early_failure_is_canonical_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            checkpoint = document["replicas"][0]["checkpoints"][0]
            document["replicas"][0] = {
                "replica": 1,
                "status": "fail",
                "detail": "failed after the empty checkpoint",
                "checkpoints": [checkpoint],
                "page0_values": None,
                "page0_changed_ranges": None,
                "baseline": {
                    "database": None,
                    "size_before": None,
                    "size_after": None,
                    "sha256_before": None,
                    "sha256_after": None,
                    "endpoints": {
                        **endpoints(False),
                        "detail": "baseline was not reached",
                    },
                    "detail": "baseline was not reached",
                },
                "correlations": {
                    name: {"status": "no_outcome", "detail": "not reached"}
                    for name in bootstrap.CORRELATION_NAMES
                },
                "changed_page_groups": [],
                "appended_page_groups": [],
                "variants": [],
            }
            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "report.json"
            )
        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(report["replicas"][0]["status"], "no_outcome")

    def test_accepts_producer_bounded_failure_and_repair_details(self) -> None:
        repair = "DAO changed the artifact during read-only endpoint checks."
        endpoint_detail = "System.Exception: " + "e" * 600
        endpoint_detail = endpoint_detail[:509] + "..."
        artifact_prefix_length = 512 - len(" " + repair) - len("...")
        artifact_detail = (
            endpoint_detail[:artifact_prefix_length] + "... " + repair
        )
        replica_detail = "System.Exception: " + "r" * 600
        replica_detail = replica_detail[:509] + "..."
        self.assertEqual(len(endpoint_detail), 512)
        self.assertEqual(len(artifact_detail), 512)
        self.assertEqual(len(replica_detail), 512)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            failed = document["replicas"][1]
            failed["status"] = "fail"
            failed["detail"] = replica_detail
            baseline = failed["baseline"]
            baseline_path = root / baseline["database"]
            repaired = bytearray(baseline_path.read_bytes())
            repaired[500] ^= 1
            baseline_path.write_bytes(repaired)
            baseline["sha256_after"] = digest(bytes(repaired))
            baseline["endpoints"]["detail"] = endpoint_detail
            baseline["detail"] = artifact_detail

            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "bounded.json"
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertEqual(report["replicas"][1]["detail"], replica_detail)

            failed["detail"] += "x"
            with self.assertRaisesRegex(
                bootstrap.AnalysisError, "at most 512 characters"
            ):
                bootstrap.evaluate(
                    write_job(root, document), PLAN_SHA256, root / "too-long.json"
                )

    def test_extra_created_to_renamed_page0_range_makes_q1_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            replica = document["replicas"][0]
            renamed = replica["checkpoints"][2]
            renamed_path = root / renamed["database"]
            renamed_bytes = bytearray(renamed_path.read_bytes())
            renamed_bytes[1500] = 9
            renamed_path.write_bytes(renamed_bytes)
            renamed["sha256"] = digest(bytes(renamed_bytes))
            replica["page0_changed_ranges"]["created_to_renamed"] = [
                {"start": 1500, "end": 1501}
            ]
            for variant in replica["variants"]:
                if not variant["kind"].startswith("candidate_date_"):
                    continue
                path = root / variant["database"]
                data = bytearray(path.read_bytes())
                data[1500] = 9
                path.write_bytes(data)
                variant["sha256_before"] = digest(bytes(data))
                variant["sha256_after"] = digest(bytes(data))
            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "report.json"
            )
        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(report["questions"]["candidate_page0"]["status"], "no_outcome")
        self.assertEqual(
            report["questions"]["candidate_page0"]["reason"],
            "create-and-rename page0 changes were not isolated to byte 1538",
        )

    def test_ambiguous_timestamp_is_honest_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            for replica in document["replicas"]:
                replica["checkpoints"][2]["dao"]["last_updated_oadate"] = CREATED_DATE
                replica["correlations"]["date_created"] = {
                    "status": "no_outcome",
                    "detail": "timestamps equal",
                }
                replica["correlations"]["date_updated"] = {
                    "status": "no_outcome",
                    "detail": "timestamps equal",
                }
                replica["variants"] = [
                    variant
                    for variant in replica["variants"]
                    if not variant["kind"].startswith("candidate_date_")
                ]
            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "report.json"
            )
        self.assertEqual(report["status"], "no_outcome")

    def test_rejects_timestamp_outside_the_preregistered_anchor_rule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["replicas"][0]["correlations"]["date_created"]["method"] = (
                "unique_exact"
            )
            with self.assertRaisesRegex(
                bootstrap.AnalysisError, "independently scanned OADate bytes"
            ):
                bootstrap.evaluate(
                    write_job(root, document), PLAN_SHA256, root / "report.json"
                )

    def test_last_updated_cannot_reverse_fallback_to_date_created(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            duplicate_offset = 22 * bootstrap.PAGE_BYTES + 300
            duplicate_created = slice(
                22 * bootstrap.PAGE_BYTES + 100,
                22 * bootstrap.PAGE_BYTES + 116,
            )
            for replica in document["replicas"]:
                renamed = replica["checkpoints"][2]
                path = root / renamed["database"]
                data = bytearray(path.read_bytes())
                data[duplicate_created] = bytes(16)
                data[duplicate_offset : duplicate_offset + 8] = struct.pack(
                    "<d", UPDATED_DATE
                )
                path.write_bytes(data)
                renamed["sha256"] = digest(bytes(data))
                replica["correlations"]["date_created"]["method"] = "unique_exact"
                replica["correlations"]["date_updated"] = {
                    "status": "no_outcome",
                    "detail": "LastUpdated is not uniquely exact",
                }
                updated_variants = []
                for variant in replica["variants"]:
                    if variant["kind"] == "candidate_date_updated":
                        continue
                    if variant["kind"] == "candidate_date_created":
                        variant_path = root / variant["database"]
                        variant_data = bytearray(variant_path.read_bytes())
                        variant_data[duplicate_created] = bytes(16)
                        variant_data[duplicate_offset : duplicate_offset + 8] = struct.pack(
                            "<d", UPDATED_DATE
                        )
                        variant_path.write_bytes(variant_data)
                        variant["sha256_before"] = digest(bytes(variant_data))
                        variant["sha256_after"] = digest(bytes(variant_data))
                    updated_variants.append(variant)
                replica["variants"] = updated_variants

            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "report.json"
            )
        self.assertEqual(
            report["questions"]["candidate_catalog_fields"]["fields"]["date_updated"][
                "status"
            ],
            "no_outcome",
        )

    def test_rejects_true_after_false_endpoint_evidence_on_every_surface(self) -> None:
        for surface in ("baseline", "sufficiency", "variant"):
            with self.subTest(surface=surface), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                document = synthetic_document(root)
                observation = (
                    document["replicas"][0]["variants"][0]
                    if surface == "variant"
                    else document["replicas"][0][surface]
                )
                observation["endpoints"].update(
                    {
                        "open_database": False,
                        "table_enumerated": True,
                        "field_enumerated": False,
                        "table_opened": False,
                    }
                )
                with self.assertRaisesRegex(
                    bootstrap.AnalysisError, "reachable DAO endpoint frontier"
                ):
                    bootstrap.evaluate(
                        write_job(root, document), PLAN_SHA256, root / "report.json"
                    )

    def test_bounded_size_change_is_repair_on_every_surface(self) -> None:
        for surface in ("baseline", "sufficiency", "variant"):
            with self.subTest(surface=surface), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                document = synthetic_document(root)
                observation = (
                    document["replicas"][0]["variants"][0]
                    if surface == "variant"
                    else document["replicas"][0][surface]
                )
                path = root / observation["database"]
                repaired = path.read_bytes() + bytes(bootstrap.PAGE_BYTES)
                path.write_bytes(repaired)
                observation["size_after"] = len(repaired)
                observation["sha256_after"] = digest(repaired)
                report = bootstrap.evaluate(
                    write_job(root, document), PLAN_SHA256, root / "report.json"
                )
                self.assertEqual(report["status"], "no_outcome")

    def test_rejects_malformed_or_out_of_bound_artifact_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["replicas"][0]["baseline"]["sha256_after"] = None
            with self.assertRaisesRegex(bootstrap.AnalysisError, "lowercase SHA-256"):
                bootstrap.evaluate(
                    write_job(root, document), PLAN_SHA256, root / "malformed.json"
                )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["replicas"][0]["sufficiency"]["size_after"] = (
                bootstrap.MAX_PAGES + 1
            ) * bootstrap.PAGE_BYTES
            with self.assertRaisesRegex(bootstrap.AnalysisError, "between"):
                bootstrap.evaluate(
                    write_job(root, document), PLAN_SHA256, root / "unbounded.json"
                )

    def test_composed_image_is_independently_reconstructed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            sufficiency = document["replicas"][0]["sufficiency"]
            path = root / sufficiency["database"]
            data = bytearray(path.read_bytes())
            data[500] ^= 1
            path.write_bytes(data)
            changed = digest(bytes(data))
            sufficiency["sha256_before"] = changed
            sufficiency["sha256_after"] = changed
            with self.assertRaisesRegex(
                bootstrap.AnalysisError, "expected image"
            ):
                bootstrap.evaluate(
                    write_job(root, document), PLAN_SHA256, root / "report.json"
                )

    def test_composed_image_repair_is_honest_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            sufficiency = document["replicas"][0]["sufficiency"]
            path = root / sufficiency["database"]
            repaired = bytearray(path.read_bytes())
            repaired[500] ^= 1
            path.write_bytes(repaired)
            sufficiency["sha256_after"] = digest(bytes(repaired))
            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "report.json"
            )
        self.assertEqual(report["status"], "no_outcome")
        self.assertFalse(report["sufficiency_claim"])

    def test_failed_created_baseline_blocks_sufficiency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["replicas"][0]["baseline"]["endpoints"]["table_opened"] = False
            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "report.json"
            )
        self.assertEqual(
            report["questions"]["composed_image_sufficiency"],
            {
                "reason": "at least one created baseline failed or changed during DAO open",
                "status": "no_outcome",
            },
        )
        self.assertFalse(report["sufficiency_claim"])

    def test_correlation_evidence_disagreement_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            replica = document["replicas"][1]
            duplicate_start = 22 * bootstrap.PAGE_BYTES + 100
            duplicate_end = duplicate_start + 16

            renamed = replica["checkpoints"][2]
            renamed_path = root / renamed["database"]
            renamed_bytes = bytearray(renamed_path.read_bytes())
            renamed_bytes[duplicate_start:duplicate_end] = bytes(16)
            renamed_path.write_bytes(renamed_bytes)
            renamed["sha256"] = digest(bytes(renamed_bytes))
            replica["correlations"]["date_created"]["method"] = "unique_exact"

            for variant in replica["variants"]:
                if not variant["kind"].startswith("candidate_date_"):
                    continue
                path = root / variant["database"]
                data = bytearray(path.read_bytes())
                data[duplicate_start:duplicate_end] = bytes(16)
                path.write_bytes(data)
                variant["sha256_before"] = digest(bytes(data))
                variant["sha256_after"] = digest(bytes(data))

            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "report.json"
            )
        self.assertEqual(
            report["questions"]["candidate_catalog_fields"]["fields"][
                "date_created"
            ],
            {
                "reason": "replicas disagree on the date_created correlation evidence",
                "status": "no_outcome",
            },
        )
        self.assertFalse(report["sufficiency_claim"])

    def test_lvprop_locator_disagreement_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            replica = document["replicas"][1]
            old_offset = 18 * bootstrap.PAGE_BYTES + 200
            new_offset = old_offset + 20
            property_set = replica["checkpoints"][3]
            path = root / property_set["database"]
            data = bytearray(path.read_bytes())
            header = bytes(data[old_offset : old_offset + 12])
            data[old_offset : old_offset + 12] = bytes(12)
            data[new_offset : new_offset + 12] = header
            path.write_bytes(data)
            property_set["sha256"] = digest(bytes(data))
            replica["correlations"]["lvprop"]["header_offset"] = new_offset

            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "report.json"
            )
        self.assertEqual(
            report["questions"]["candidate_catalog_fields"]["fields"]["lvprop"],
            {
                "reason": "replicas disagree on the lvprop correlation evidence",
                "status": "no_outcome",
            },
        )
        self.assertFalse(report["sufficiency_claim"])

    def test_composed_endpoint_frontier_disagreement_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            first_frontier = {
                "open_database": True,
                "table_enumerated": False,
                "field_enumerated": False,
                "table_opened": False,
            }
            second_frontier = {
                "open_database": True,
                "table_enumerated": True,
                "field_enumerated": False,
                "table_opened": False,
            }
            for replica, frontier in zip(
                document["replicas"],
                (first_frontier, second_frontier, first_frontier),
                strict=True,
            ):
                replica["sufficiency"]["endpoints"].update(frontier)
            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "report.json"
            )
        self.assertEqual(
            report["questions"]["composed_image_sufficiency"],
            {
                "reason": "replicas disagree on the composed-image DAO endpoint map",
                "status": "no_outcome",
            },
        )
        self.assertFalse(report["sufficiency_claim"])

    def test_repair_is_no_outcome_and_wrong_reconstruction_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            variant = document["replicas"][0]["variants"][0]
            path = root / variant["database"]
            repaired = bytearray(path.read_bytes())
            repaired[500] ^= 1
            path.write_bytes(repaired)
            variant["sha256_after"] = digest(bytes(repaired))
            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "a.json"
            )
            self.assertEqual(report["status"], "no_outcome")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            baseline = document["replicas"][0]["baseline"]
            path = root / baseline["database"]
            repaired = bytearray(path.read_bytes())
            repaired[501] ^= 1
            path.write_bytes(repaired)
            baseline["sha256_after"] = digest(bytes(repaired))
            report = bootstrap.evaluate(
                write_job(root, document), PLAN_SHA256, root / "baseline.json"
            )
            self.assertEqual(report["status"], "no_outcome")
            self.assertEqual(
                report["questions"]["composed_image_sufficiency"]["status"],
                "no_outcome",
            )
            self.assertFalse(report["sufficiency_claim"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            variant = document["replicas"][0]["variants"][0]
            path = root / variant["database"]
            data = bytearray(path.read_bytes())
            data[500] ^= 1
            path.write_bytes(data)
            changed = digest(bytes(data))
            variant["sha256_before"] = changed
            variant["sha256_after"] = changed
            with self.assertRaisesRegex(bootstrap.AnalysisError, "expected image"):
                bootstrap.evaluate(write_job(root, document), PLAN_SHA256, root / "b.json")

    def test_rejects_garbage_variant_in_partial_failed_replica(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            original = document["replicas"][0]
            garbage = copy.deepcopy(original["variants"][0])
            garbage["kind"] = "unregistered_garbage"
            document["replicas"][0] = {
                "replica": 1,
                "status": "fail",
                "detail": "stopped after one invalidly reported variant",
                "checkpoints": original["checkpoints"],
                "variants": [garbage],
            }
            with self.assertRaisesRegex(bootstrap.AnalysisError, "not preregistered"):
                bootstrap.evaluate(
                    write_job(root, document), PLAN_SHA256, root / "report.json"
                )

    def test_rejects_missing_variant_wrong_replica_and_checkpoint_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["replicas"][0]["variants"] = document["replicas"][0]["variants"][1:]
            with self.assertRaisesRegex(bootstrap.AnalysisError, "candidate-page0"):
                bootstrap.evaluate(write_job(root, document), PLAN_SHA256, root / "a.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["replicas"][2]["replica"] = 2
            with self.assertRaisesRegex(bootstrap.AnalysisError, "indexed exactly"):
                bootstrap.evaluate(write_job(root, document), PLAN_SHA256, root / "b.json")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["replicas"][0]["checkpoints"][0]["sha256"] = "c" * 64
            with self.assertRaisesRegex(bootstrap.AnalysisError, "digest differs"):
                bootstrap.evaluate(write_job(root, document), PLAN_SHA256, root / "c.json")

    def test_cli_rejects_malformed_plan_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["plan_sha256"] = "A" * 64
            code = bootstrap.main(
                [
                    str(write_job(root, document)),
                    "--expected-plan-sha256",
                    PLAN_SHA256,
                    "--output",
                    str(root / "report.json"),
                ]
            )
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
