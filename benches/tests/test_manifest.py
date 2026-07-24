from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

MANIFEST = Path(__file__).parents[1] / "manifest.json"
MASK_U64 = (1 << 64) - 1
MULTIPLIER = 0x9E37_79B9_7F4A_7C15


def deterministic_bytes(length: int) -> bytes:
    generated = bytearray()
    for index in range(length):
        value = (index * MULTIPLIER) & MASK_U64
        value = ((value << 17) & MASK_U64) | (value >> (64 - 17))
        generated.append(value & 0xFF)
    return bytes(generated)


class ManifestTests(unittest.TestCase):
    def test_generated_dataset_hashes_match_manifest(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        dataset = manifest["dataset"]
        expected_hashes = dataset["sha256_by_size"]
        self.assertEqual(
            {str(size) for size in dataset["sizes_bytes"]}, set(expected_hashes)
        )
        for size in dataset["sizes_bytes"]:
            digest = hashlib.sha256(deterministic_bytes(size)).hexdigest()
            self.assertEqual(digest, expected_hashes[str(size)])

    def test_benchmark_ids_are_unique_and_stable(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        identifiers = [entry["id"] for entry in manifest["benchmarks"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertTrue(all(identifier.startswith("BENCH-") for identifier in identifiers))

    def test_scope_limit_remains_explicit(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        limitations = " ".join(manifest["limitations"])
        self.assertIn("100000-row", limitations)
        self.assertIn("No checked performance baseline", limitations)


if __name__ == "__main__":
    unittest.main()
