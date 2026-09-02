from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "schema_generalization", SCRIPTS / "schema_generalization.py"
)
assert SPEC is not None and SPEC.loader is not None
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)

PAGE = ANALYZER.PAGE_BYTES
# The exact Alpha.LvProp payload recorded losslessly by EXP-0079.
ALPHA_LVPROP = bytes.fromhex(
    "4b4b440010000000800008005265717569726564170000000100080000000200"
    "4964090001010000010000"
)


def leaf_page(entries: list[tuple[bytes, int, int]], owner: int) -> bytes:
    page = bytearray(PAGE)
    page[0] = 4
    page[1] = 1
    page[4:8] = owner.to_bytes(4, "little")
    end = 0
    for key, row_page, row_slot in entries:
        record = key + row_page.to_bytes(3, "big") + bytes([row_slot])
        page[ANALYZER.ENTRY_AREA_OFFSET + end : ANALYZER.ENTRY_AREA_OFFSET + end + len(record)] = record
        end += len(record)
        page[22 + end // 8] |= 1 << (end % 8)
    page[2:4] = (ANALYZER.ENTRY_AREA_LENGTH - end).to_bytes(2, "little")
    return bytes(page)


def composite_key(parent: int, text: bytes) -> bytes:
    return (
        bytes([0x7F])
        + ((parent ^ 0x8000_0000) & 0xFFFF_FFFF).to_bytes(4, "big")
        + bytes([0x7F])
        + text
        + b"\x00"
    )


def catalog_row(identity: int, parent: int, name: str, row: int) -> dict[str, object]:
    values: list[object] = [identity, parent, name]
    values.extend([None] * 14)
    return {"page": 18, "row": row, "values": values}


class KeyDecodingTests(unittest.TestCase):
    def test_leaf_entry_is_lossless_and_correlates_its_catalog_row(self) -> None:
        key = bytes.fromhex("7f8f0000017f606d73696000")
        data = bytearray(23 * PAGE)
        data[9 * PAGE : 10 * PAGE] = leaf_page([(key, 18, 8)], 2)

        entries = ANALYZER.leaf_index_entries(
            bytes(data), 9, 2, [catalog_row(20, 0x0F00_0001, "Alpha", 8)]
        )

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["key"], key)
        parent, primary, secondary = ANALYZER.split_parent_name_key(key, "key")
        self.assertEqual(parent, 0x0F00_0001)
        self.assertEqual(primary.hex(), "606d736960")
        self.assertEqual(secondary, [])

    def test_branched_root_and_missing_locator_are_decode_errors(self) -> None:
        key = composite_key(0x0F00_0001, b"\x60\x6d")
        data = bytearray(23 * PAGE)
        data[9 * PAGE : 10 * PAGE] = leaf_page([(key, 18, 8)], 2)
        with self.assertRaisesRegex(ANALYZER.DecodeError, "no catalog row"):
            ANALYZER.leaf_index_entries(bytes(data), 9, 2, [catalog_row(20, 1, "Alpha", 3)])

        branched = bytearray(data)
        branched[9 * PAGE + 21] = 1
        with self.assertRaisesRegex(ANALYZER.DecodeError, "not a leaf"):
            ANALYZER.leaf_index_entries(
                bytes(branched), 9, 2, [catalog_row(20, 0x0F00_0001, "Alpha", 8)]
            )

    def test_secondary_weights_are_decoded_as_a_nibble_stream(self) -> None:
        # One primary weight per name byte, then 0 3 4 5 7 6 8 0 as nibbles.
        key = b"\x7f\x8f\x00\x00\x01\x7f\x60\x60\x60" + bytes.fromhex("03457680")

        parent, primary, secondary = ANALYZER.split_parent_name_key(key, "key")

        self.assertEqual(parent, 0x0F00_0001)
        self.assertEqual(primary.hex(), "606060")
        self.assertEqual(secondary, [3, 4, 5, 7, 6, 8])

    def test_key_framing_requires_markers_a_primary_and_a_terminator(self) -> None:
        for broken, expected in (
            (b"\x00" + b"\x8f\x00\x00\x01" + b"\x7f\x60\x00", "key markers"),
            (b"\x7f" + b"\x8f\x00\x00\x01" + b"\x7f\x60\x60", "no secondary section"),
            (b"\x7f" + b"\x8f\x00\x00\x01" + b"\x7f\x00\x60", "no primary weight"),
            (b"\x7f" + b"\x8f\x00\x00\x01" + b"\x7f\x60\x0f\xff", "unterminated"),
            (b"\x7f" + b"\x8f\x00\x00\x01" + b"\x7f\x60\x03\x40\x0f", "padding is nonzero"),
        ):
            with self.assertRaisesRegex(ANALYZER.DecodeError, expected):
                ANALYZER.split_parent_name_key(broken, "key")


class CollationTests(unittest.TestCase):
    def observation(
        self, name: str, primary: bytes, secondary: list[int] | None = None
    ) -> dict[str, object]:
        return {
            "name": name,
            "name_hex": name.encode("cp1252").hex(),
            "primary_hex": primary.hex(),
            "secondary_nibbles": secondary or [],
        }

    def test_context_free_map_is_derived_from_two_orderings(self) -> None:
        derived = ANALYZER.collation_map(
            [
                self.observation("PabQ", b"\x53\x60\x61\x54"),
                self.observation("PbaR", b"\x53\x61\x60\x55"),
            ]
        )

        self.assertEqual(derived["conflicts"], [])
        self.assertEqual(derived["length_mismatches"], [])
        self.assertEqual(derived["names_with_secondary_weights"], [])
        self.assertEqual(derived["map"]["61"], "60")
        self.assertEqual(derived["map"]["62"], "61")

    def test_conflicts_expansions_and_secondaries_are_recorded_not_hidden(self) -> None:
        derived = ANALYZER.collation_map(
            [
                self.observation("ab", b"\x60\x61"),
                self.observation("ba", b"\x61\x99"),
                self.observation("cd", b"\x62\x63\x64"),
                self.observation("ef", b"\x66\x67", [3]),
            ]
        )

        self.assertEqual(
            derived["conflicts"],
            [{"name": "ba", "observed": 0x99, "previous": 0x60, "source": 0x61}],
        )
        self.assertEqual(
            derived["length_mismatches"],
            [{"name": "cd", "name_bytes": 2, "primary_bytes": 3}],
        )
        self.assertEqual(derived["names_with_secondary_weights"], ["ef"])
        self.assertNotIn("66", derived["map"])

    def test_extended_names_are_separated_from_the_ascii_map(self) -> None:
        ascii_name = self.observation("ab", b"\x60\x61")
        extended = self.observation("a\xe9", b"\x60\x66", [3])

        self.assertTrue(ANALYZER.is_ascii_name(ascii_name))
        self.assertFalse(ANALYZER.is_ascii_name(extended))


class PropertyFramingTests(unittest.TestCase):
    def test_recorded_alpha_payload_decomposes_into_two_chunks(self) -> None:
        chunks = ANALYZER.property_chunks(ALPHA_LVPROP)

        self.assertEqual([chunk["kind"] for chunk in chunks], [0x0080, 0x0001])
        self.assertEqual([chunk["length"] for chunk in chunks], [16, 23])
        self.assertEqual(
            chunks[0]["name_entries_hex"], [b"Required".hex()]
        )
        self.assertEqual(sum(chunk["length"] for chunk in chunks) + 4, len(ALPHA_LVPROP))

    def test_missing_magic_and_overrunning_chunk_are_decode_errors(self) -> None:
        with self.assertRaisesRegex(ANALYZER.DecodeError, "KKD magic"):
            ANALYZER.property_chunks(b"XXX\x00\x06\x00\x00\x00\x80\x00")
        with self.assertRaisesRegex(ANALYZER.DecodeError, "invalid length"):
            ANALYZER.property_chunks(b"KKD\x00" + b"\xff\x00\x00\x00\x80\x00")


class RowDifferenceTests(unittest.TestCase):
    def test_added_and_removed_rows_are_both_reported(self) -> None:
        difference = ANALYZER.row_difference(
            [{"Name": "Alpha"}, {"Name": "Tables"}],
            [{"Name": "Tables"}, {"Name": "Beta"}],
        )

        self.assertEqual(difference["added"], [{"Name": "Beta"}])
        self.assertEqual(difference["removed"], [{"Name": "Alpha"}])


def dao_snapshot(names: list[str]) -> dict[str, object]:
    return {
        "tabledefs": [
            {
                "attributes": 0,
                "date_created": 0.0,
                "error": None,
                "fields": [],
                "indexes": [],
                "last_updated": 0.0,
                "name": name,
            }
            for name in names
        ]
    }


def checkpoint(root: Path, replica: int, name: str, *, repaired: bool = False) -> dict[str, object]:
    raw = bytes(20 * PAGE)
    database = f"schema-generalization-r{replica}-{name}.mdb"
    (root / database).write_bytes(raw)
    after = hashlib.sha256(raw).hexdigest()
    before = hashlib.sha256(b"pre-metadata").hexdigest() if repaired else after
    return {
        "dao": dao_snapshot(["MSysObjects"]),
        "database": database,
        "name": name,
        "sha256": before,
        "sha256_after_metadata": after,
        "size": len(raw),
    }


def job_document(root: Path, *, repaired: bool = False) -> dict[str, object]:
    return {
        "development_only": True,
        "document_type": ANALYZER.DOCUMENT_TYPE,
        "plan_sha256": "a" * 64,
        "replicas": [
            {
                "checkpoints": [
                    checkpoint(root, replica, name, repaired=repaired)
                    for name in ANALYZER.CHECKPOINTS
                ],
                "error": None,
                "probe_attempts": [
                    {
                        "code_points": entry["code_points"],
                        "created": True,
                        "error": None,
                        "name": entry["name"],
                    }
                    for entry in ANALYZER.expected_probe_inventory()
                ],
                "replica": replica,
                "status": "pass",
            }
            for replica in range(1, 4)
        ],
        "run_id": "20260902T120000Z-schema-generalization",
        "status": "pass",
    }


class ProbeInventoryTests(unittest.TestCase):
    def test_pinned_inventory_covers_every_probed_byte_in_two_orderings(self) -> None:
        inventory = ANALYZER.expected_probe_inventory()
        covered: dict[int, int] = {}
        for entry in inventory:
            for point in entry["code_points"]:
                covered[point] = covered.get(point, 0) + 1

        self.assertEqual(len(inventory), ANALYZER.MAX_PROBE_TABLES)
        self.assertEqual(sorted(set(covered.values())), [2])
        self.assertEqual(
            sorted(covered),
            [
                value
                for first, last in ANALYZER.PROBE_RANGES
                for value in range(first, last + 1)
                if value not in ANALYZER.EXCLUDED_PROBE_BYTES
            ],
        )
        for entry in inventory:
            with self.subTest(name=entry["name"]):
                # No probed name may mix the two ranges, or its ASCII bytes
                # would be excluded from the ASCII collation map.
                ranges = {
                    (first, last)
                    for first, last in ANALYZER.PROBE_RANGES
                    for point in entry["code_points"]
                    if first <= point <= last
                }
                self.assertEqual(len(ranges), 1)

    def test_incomplete_or_altered_inventory_is_an_inventory_violation(self) -> None:
        expected = ANALYZER.expected_probe_inventory()
        attempts = [
            {"code_points": entry["code_points"], "created": True, "error": None, "name": entry["name"]}
            for entry in expected
        ]

        self.assertEqual(len(ANALYZER.read_probe_attempts(attempts, 1)), len(expected))

        with self.assertRaisesRegex(ANALYZER.AnalysisError, "exactly 24 probed names"):
            ANALYZER.read_probe_attempts(attempts[:-1], 1)
        with self.assertRaisesRegex(ANALYZER.AnalysisError, "exactly 24 probed names"):
            ANALYZER.read_probe_attempts([], 1)

        renamed = [dict(entry) for entry in attempts]
        renamed[3]["name"] = "PxxQ"
        with self.assertRaisesRegex(ANALYZER.AnalysisError, "preregistered inventory"):
            ANALYZER.read_probe_attempts(renamed, 1)

        repointed = [dict(entry) for entry in attempts]
        repointed[3] = dict(repointed[3], code_points=repointed[3]["code_points"][:-1])
        with self.assertRaisesRegex(ANALYZER.AnalysisError, "preregistered inventory"):
            ANALYZER.read_probe_attempts(repointed, 1)

    def test_a_rejected_name_stays_a_recordable_observation(self) -> None:
        expected = ANALYZER.expected_probe_inventory()
        attempts = [
            {"code_points": entry["code_points"], "created": True, "error": None, "name": entry["name"]}
            for entry in expected
        ]
        attempts[5] = dict(attempts[5], created=False, error="rejected by the provider")

        self.assertFalse(ANALYZER.read_probe_attempts(attempts, 1)[5]["created"])


class KeyFramingQuestionTests(unittest.TestCase):
    def key(self, name: str) -> dict[str, object]:
        return {"name": name, "name_hex": name.encode("cp1252").hex()}

    def test_both_images_are_reported_and_compared(self) -> None:
        names = [[self.key("P01Q")] for _ in range(3)]
        schema = [[self.key("Alpha")] for _ in range(3)]

        answered = ANALYZER.question_name_key_framing(names, schema)

        self.assertEqual(answered["status"], "answered")
        self.assertEqual(answered["names"], names[0])
        self.assertEqual(answered["schema"], schema[0])

    def test_disagreement_in_either_image_is_a_no_outcome(self) -> None:
        names = [[self.key("P01Q")] for _ in range(3)]
        schema = [[self.key("Alpha")] for _ in range(3)]
        divergent_schema = [schema[0], [self.key("Beta")], schema[2]]
        divergent_names = [names[0], [self.key("P02Q")], names[2]]

        self.assertEqual(
            ANALYZER.question_name_key_framing(names, divergent_schema)["status"],
            "no_outcome",
        )
        self.assertEqual(
            ANALYZER.question_name_key_framing(divergent_names, schema)["status"],
            "no_outcome",
        )


class EvaluationTests(unittest.TestCase):
    def evaluate(self, root: Path, document: dict[str, object]) -> dict[str, object]:
        job = root / "schema-generalization-job-result.json"
        job.write_text(json.dumps(document), encoding="utf-8")
        return ANALYZER.evaluate(job, "a" * 64, root / "report.json")

    def test_undecodable_checkpoints_are_an_honest_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = self.evaluate(root, job_document(root))

        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(sorted(report["questions"]), sorted(ANALYZER.QUESTION_NAMES))
        self.assertFalse(report["compatibility_claim"])
        self.assertFalse(report["support_movement"])

    def test_metadata_repair_is_reported_before_any_decode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = self.evaluate(root, job_document(root, repaired=True))

        self.assertEqual(
            report["questions"]["name_key_framing"]["reason"],
            "DAO metadata access changed at least one checkpoint",
        )

    def test_plan_digest_and_retained_inventory_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = job_document(root)
            job = root / "schema-generalization-job-result.json"
            job.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "plan digest"):
                ANALYZER.evaluate(job, "b" * 64, root / "report.json")

            (root / "unexpected.mdb").write_bytes(b"extra")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "inventory"):
                self.evaluate(root, document)

    def test_tampered_checkpoint_and_duplicate_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = job_document(root)
            (root / "schema-generalization-r1-empty.mdb").write_bytes(bytes(20 * PAGE + 1))
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "differ from metadata"):
                self.evaluate(root, document)

            job = root / "duplicate.json"
            job.write_text('{"status":"pass","status":"fail"}', encoding="utf-8")
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "duplicate JSON field"):
                ANALYZER.load_document(job)

    def test_probe_attempt_shape_and_replica_order_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = job_document(root)
            document["replicas"][0]["probe_attempts"][0]["created"] = False
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "creation and failure"):
                self.evaluate(root, document)

            document = job_document(root)
            document["replicas"].reverse()
            with self.assertRaisesRegex(ANALYZER.AnalysisError, "numbered 1 through 3"):
                self.evaluate(root, document)


if __name__ == "__main__":
    unittest.main()
