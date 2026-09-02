from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "bootstrap_composer_semantics",
    SCRIPTS / "bootstrap_composer_semantics.py",
)
assert SPEC is not None and SPEC.loader is not None
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)


def catalog_row(name: str, row: int) -> dict[str, object]:
    values: list[object] = [20, 0x0F000001, name]
    values.extend([None] * 14)
    return {"page": 18, "row": row, "values": values}


def catalog_definition() -> dict[str, object]:
    columns = [
        {"name": f"Column{ordinal}", "ordinal": ordinal} for ordinal in range(15)
    ]
    columns[2]["name"] = "Name"
    columns[14]["name"] = "LvProp"
    return {"columns": columns}


def dao_snapshot(alpha: bool) -> dict[str, object]:
    tabledefs = []
    if alpha:
        tabledefs.append(
            {
                "attributes": 0,
                "date_created": 0.0,
                "error": None,
                "last_updated": 0.0,
                "name": "Alpha",
            }
        )
    return {
        "containers": [],
        "properties": [],
        "querydefs": [],
        "relations": [],
        "tabledefs": tabledefs,
    }


def checkpoint(
    root: Path, replica: int, name: str, *, repaired: bool = False
) -> dict[str, object]:
    pages = 20 if name == "empty" else 23
    raw = bytes(pages * ANALYZER.PAGE_BYTES)
    database = f"bootstrap-composer-semantics-r{replica}-{name}.mdb"
    (root / database).write_bytes(raw)
    after = hashlib.sha256(raw).hexdigest()
    before = hashlib.sha256(b"pre-metadata").hexdigest() if repaired else after
    return {
        "dao": dao_snapshot(name == "alpha"),
        "database": database,
        "name": name,
        "sha256": before,
        "sha256_after_metadata": after,
        "size": len(raw),
    }


def job_document(root: Path, *, repaired: bool = False) -> dict[str, object]:
    return {
        "development_only": True,
        "document_type": "dao_bootstrap_composer_semantics_job_result",
        "plan_sha256": "a" * 64,
        "replicas": [
            {
                "checkpoints": [
                    checkpoint(root, replica, "empty", repaired=repaired),
                    checkpoint(root, replica, "alpha", repaired=repaired),
                ],
                "error": None,
                "replica": replica,
                "status": "pass",
            }
            for replica in range(1, 4)
        ],
        "run_id": "20260901T120000Z-bootstrap-composer",
        "status": "pass",
    }


class BootstrapComposerSemanticsTests(unittest.TestCase):
    def test_parent_name_key_stays_lossless_and_correlates_its_row(self) -> None:
        data = bytearray(23 * ANALYZER.PAGE_BYTES)
        page = memoryview(data)[9 * ANALYZER.PAGE_BYTES : 10 * ANALYZER.PAGE_BYTES]
        key = bytes.fromhex("7f8f0000017f606d73696000")
        trailer = (18).to_bytes(3, "big") + b"\x08"
        end = len(key) + len(trailer)
        page[0] = 4
        page[1] = 1
        page[2:4] = (ANALYZER.ENTRY_AREA_LENGTH - end).to_bytes(2, "little")
        page[4:8] = (2).to_bytes(4, "little")
        page[22 + end // 8] = 1 << (end % 8)
        page[ANALYZER.ENTRY_AREA_OFFSET : ANALYZER.ENTRY_AREA_OFFSET + end] = key + trailer

        self.assertEqual(
            ANALYZER.parent_name_keys(bytes(data), [catalog_row("Alpha", 8)]),
            [
                {
                    "id": 20,
                    "key_hex": key.hex(),
                    "name": "Alpha",
                    "parent_id": 0x0F000001,
                    "row_page": 18,
                    "row_slot": 8,
                }
            ],
        )

    def test_parent_name_key_rejects_missing_catalog_locator(self) -> None:
        data = bytearray(23 * ANALYZER.PAGE_BYTES)
        page = memoryview(data)[9 * ANALYZER.PAGE_BYTES : 10 * ANALYZER.PAGE_BYTES]
        page[0] = 4
        page[1] = 1
        page[2:4] = (ANALYZER.ENTRY_AREA_LENGTH - 6).to_bytes(2, "little")
        page[4:8] = (2).to_bytes(4, "little")
        page[22] = 1 << 6
        page[ANALYZER.ENTRY_AREA_OFFSET : ANALYZER.ENTRY_AREA_OFFSET + 6] = (
            b"\x7f\x00\x00\x12\x00\x08"
        )
        with self.assertRaisesRegex(ANALYZER.DecodeError, "no catalog row"):
            ANALYZER.parent_name_keys(bytes(data), [])

    def test_parent_name_key_reconstructs_nonzero_common_prefix(self) -> None:
        data = bytearray(23 * ANALYZER.PAGE_BYTES)
        page = memoryview(data)[9 * ANALYZER.PAGE_BYTES : 10 * ANALYZER.PAGE_BYTES]
        prefix = b"\xaa\xbb"
        suffix = b"\xcc"
        trailer = (18).to_bytes(3, "big") + b"\x08"
        end = len(prefix) + len(suffix) + len(trailer)
        page[0] = 4
        page[1] = 1
        page[2:4] = (ANALYZER.ENTRY_AREA_LENGTH - end).to_bytes(2, "little")
        page[4:8] = (2).to_bytes(4, "little")
        page[20] = len(prefix)
        page[22 + end // 8] = 1 << (end % 8)
        page[ANALYZER.ENTRY_AREA_OFFSET : ANALYZER.ENTRY_AREA_OFFSET + end] = (
            prefix + suffix + trailer
        )

        observation = ANALYZER.parent_name_keys(
            bytes(data), [catalog_row("Alpha", 8)]
        )
        self.assertEqual(observation[0]["key_hex"], (prefix + suffix).hex())

    def test_parent_name_definition_linkage_is_exact(self) -> None:
        definition = {
            "columns": [{"name": "ParentId"}, {"name": "Name"}],
            "logical_indexes": [
                {"name": "ParentIdName", "physical_index": 0}
            ],
            "physical_indexes": [
                {"keys": [{"column": 0}, {"column": 1}], "root": 9}
            ],
        }
        self.assertEqual(ANALYZER.parent_name_root(definition), 9)
        definition["physical_indexes"][0]["root"] = 8
        with self.assertRaisesRegex(ANALYZER.DecodeError, "not fixed page 9"):
            ANALYZER.parent_name_keys(bytes(23 * ANALYZER.PAGE_BYTES), [], 8)

    def test_alpha_lvprop_follows_exact_bounded_external_row(self) -> None:
        payload = bytes.fromhex(
            "4b4b440010000000800008005265717569726564170000000100080000000200"
            "4964090001010000010000"
        )
        data = bytearray(23 * ANALYZER.PAGE_BYTES)
        page = memoryview(data)[22 * ANALYZER.PAGE_BYTES : 23 * ANALYZER.PAGE_BYTES]
        page[0] = 1
        page[4:8] = b"LVAL"
        page[8:10] = (1).to_bytes(2, "little")
        start = ANALYZER.PAGE_BYTES - len(payload)
        page[10:12] = start.to_bytes(2, "little")
        page[start:] = payload
        header = (0x40000000 | len(payload)).to_bytes(4, "little")
        header += b"\x00" + (22).to_bytes(3, "little") + b"\x00" * 4
        alpha = catalog_row("Alpha", 8)
        alpha["values"][14] = {
            "inline_length": 12,
            "long_value_header_hex": header.hex(),
        }

        definition = catalog_definition()
        observation = ANALYZER.alpha_lvprop(bytes(data), definition, [alpha])
        self.assertEqual(observation["header_hex"], header.hex())
        self.assertEqual(observation["payload_hex"], payload.hex())
        self.assertEqual(observation["page"], 22)
        self.assertEqual(observation["row"], 0)

        alpha["values"][14]["long_value_header_hex"] = (
            header[:8] + b"\x01\x00\x00\x00"
        ).hex()
        with self.assertRaisesRegex(ANALYZER.DecodeError, "reserved"):
            ANALYZER.alpha_lvprop(bytes(data), definition, [alpha])

    def test_alpha_lvprop_rejects_short_schema_and_invalid_external_targets(self) -> None:
        payload = b"opaque"
        base = bytearray(23 * ANALYZER.PAGE_BYTES)
        page = memoryview(base)[22 * ANALYZER.PAGE_BYTES : 23 * ANALYZER.PAGE_BYTES]
        page[0] = 1
        page[4:8] = b"LVAL"
        page[8:10] = (1).to_bytes(2, "little")
        start = ANALYZER.PAGE_BYTES - len(payload)
        page[10:12] = start.to_bytes(2, "little")
        page[start:] = payload
        alpha = catalog_row("Alpha", 8)

        def set_header(length: int, row: int, target: int) -> None:
            header = (0x40000000 | length).to_bytes(4, "little")
            header += bytes([row]) + target.to_bytes(3, "little") + b"\0" * 4
            alpha["values"][14] = {
                "inline_length": 12,
                "long_value_header_hex": header.hex(),
            }

        set_header(len(payload), 0, 22)
        with self.assertRaisesRegex(ANALYZER.DecodeError, "Name or LvProp"):
            ANALYZER.alpha_lvprop(bytes(base), {"columns": []}, [alpha])

        set_header(len(payload), 0, 23)
        with self.assertRaisesRegex(ANALYZER.DecodeError, "outside the bound"):
            ANALYZER.alpha_lvprop(bytes(base), catalog_definition(), [alpha])

        set_header(len(payload), 1, 22)
        with self.assertRaisesRegex(ANALYZER.DecodeError, "row slot is absent"):
            ANALYZER.alpha_lvprop(bytes(base), catalog_definition(), [alpha])

        set_header(len(payload), 0, 22)
        page[10:12] = (start | 0x8000).to_bytes(2, "little")
        with self.assertRaisesRegex(ANALYZER.DecodeError, "flagged row"):
            ANALYZER.alpha_lvprop(bytes(base), catalog_definition(), [alpha])
        page[10:12] = start.to_bytes(2, "little")

        page[4:8] = b"NOPE"
        with self.assertRaisesRegex(ANALYZER.DecodeError, "LVAL data page"):
            ANALYZER.alpha_lvprop(bytes(base), catalog_definition(), [alpha])
        page[4:8] = b"LVAL"

        set_header(len(payload) + 1, 0, 22)
        with self.assertRaisesRegex(ANALYZER.DecodeError, "payload length"):
            ANALYZER.alpha_lvprop(bytes(base), catalog_definition(), [alpha])

    def test_report_requires_replica_agreement(self) -> None:
        base = {
            "empty_keys": [{"key_hex": "01"}],
            "alpha_keys": [{"key_hex": "02"}],
            "lvprop": {"payload_hex": "03"},
            "page0": {"empty": 0, "alpha": 2, "changed_offsets": [1538]},
        }
        document = {"status": "pass", "plan_sha256": "a" * 64}
        accepted = ANALYZER.build_report(document, [base, base, base])
        self.assertEqual(accepted["status"], "accepted")

        changed = {**base, "lvprop": {"payload_hex": "04"}}
        no_outcome = ANALYZER.build_report(document, [base, base, changed])
        self.assertEqual(no_outcome["status"], "no_outcome")
        self.assertEqual(
            no_outcome["questions"]["fixed_alpha_lvprop"]["status"],
            "no_outcome",
        )

    def test_metadata_repair_is_an_honest_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = root / "job.json"
            output = root / "report.json"
            job.write_text(json.dumps(job_document(root, repaired=True)), encoding="utf-8")

            report = ANALYZER.evaluate(job, "a" * 64, output)

            self.assertEqual(report["status"], "no_outcome")
            self.assertEqual(
                report["questions"]["fixed_alpha_lvprop"]["reason"],
                "DAO metadata access changed at least one checkpoint",
            )

    def test_malformed_dao_snapshot_rejects_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = job_document(root, repaired=True)
            document["replicas"][0]["checkpoints"][0]["dao"] = None
            job = root / "job.json"
            job.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(ANALYZER.AnalysisError):
                ANALYZER.evaluate(job, "a" * 64, root / "report.json")

    def test_replica_permutation_and_duplicate_json_fields_reject(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = job_document(root, repaired=True)
            document["replicas"][0], document["replicas"][1] = (
                document["replicas"][1],
                document["replicas"][0],
            )
            job = root / "job.json"
            job.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "numbered 1 through 3"):
                ANALYZER.evaluate(job, "a" * 64, root / "report.json")

            job.write_text('{"status":"pass","status":"fail"}', encoding="utf-8")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "duplicate JSON field"):
                ANALYZER.load_document(job)

    def test_status_mismatch_rejects_and_accepted_output_is_deterministic(self) -> None:
        observation = {
            "alpha_keys": [{"key_hex": "02"}],
            "empty_keys": [{"key_hex": "01"}],
            "lvprop": {"payload_hex": "03"},
            "page0": {"alpha": 2, "changed_offsets": [1538], "empty": 0},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = job_document(root)
            job = root / "job.json"
            job.write_text(json.dumps(document), encoding="utf-8")
            first = root / "first.json"
            second = root / "second.json"
            with mock.patch.object(ANALYZER, "analyze_replica", return_value=observation):
                report = ANALYZER.evaluate(job, "a" * 64, first)
                ANALYZER.evaluate(job, "a" * 64, second)
            self.assertEqual(report["status"], "accepted")
            self.assertEqual(first.read_bytes(), second.read_bytes())

            document["status"] = "fail"
            job.write_text(json.dumps(document), encoding="utf-8")
            with mock.patch.object(ANALYZER, "analyze_replica", return_value=observation):
                with self.assertRaisesRegex(ANALYZER.AnalysisError, "disagrees"):
                    ANALYZER.evaluate(job, "a" * 64, root / "mismatch.json")


if __name__ == "__main__":
    unittest.main()
