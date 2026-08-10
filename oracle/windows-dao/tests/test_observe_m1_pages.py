import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "observe_m1_pages.py"
)
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("observe_m1_pages", SCRIPT)
OBSERVER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(OBSERVER)


class M1PageObserverTests(unittest.TestCase):
    def test_page_hashes_cover_exact_pages_in_order(self):
        first = bytes([0x11]) * OBSERVER.PAGE_SIZE
        second = bytes([0x22]) * OBSERVER.PAGE_SIZE
        self.assertEqual(
            OBSERVER.page_hashes(first + second),
            [
                OBSERVER.sha256_bytes(first),
                OBSERVER.sha256_bytes(second),
            ],
        )

    def test_page_hashes_reject_empty_and_partial_inputs(self):
        for value in (b"", b"x", b"x" * (OBSERVER.PAGE_SIZE + 1)):
            with self.subTest(length=len(value)), self.assertRaises(
                OBSERVER.ValidationError
            ):
                OBSERVER.page_hashes(value)

    def test_pair_analysis_records_page_and_byte_boundaries(self):
        left = bytearray(b"\x00" * (OBSERVER.PAGE_SIZE * 2))
        right = bytearray(left)
        right[7] = 1
        right[OBSERVER.PAGE_SIZE + 9] = 2
        right.extend(b"\x33" * OBSERVER.PAGE_SIZE)
        observed = OBSERVER.analyze_pair(bytes(left), bytes(right))
        self.assertEqual(observed["differing_page_indices"], [0, 1, 2])
        self.assertEqual(observed["differing_byte_count_in_common_length"], 2)
        self.assertEqual(observed["first_differing_byte_offset"], 7)
        self.assertEqual(
            observed["last_differing_byte_offset"],
            OBSERVER.PAGE_SIZE + 9,
        )
        self.assertEqual(observed["right_only_bytes"], OBSERVER.PAGE_SIZE)

    def test_atomic_publication_refuses_collision_and_cleans_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "observation.json"
            document = {"document_type": "test", "value": 1}
            OBSERVER.publish_atomic(output, document)
            retained = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(retained, document)
            with self.assertRaisesRegex(
                OBSERVER.ValidationError, "collision"
            ):
                OBSERVER.publish_atomic(output, document)
            self.assertEqual(list(root.glob(".m2-stage-*")), [])

    def test_atomic_publication_cleans_stage_after_commit_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "observation.json"
            with mock.patch.object(
                OBSERVER.os,
                "link",
                side_effect=OSError("injected commit failure"),
            ):
                with self.assertRaisesRegex(OSError, "injected"):
                    OBSERVER.publish_atomic(output, {"value": 1})
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".m2-stage-*")), [])

    def test_database_reader_enforces_the_m1_size_ceiling(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "oversized.mdb"
            path.write_bytes(b"\x00" * (OBSERVER.MAX_DATABASE_BYTES + 1))
            with self.assertRaisesRegex(
                OBSERVER.ValidationError, "exceeds"
            ):
                OBSERVER.read_database(path)


if __name__ == "__main__":
    unittest.main()
