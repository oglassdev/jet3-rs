from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

MANIFEST = Path(__file__).parents[1] / "manifest.json"
RAW_PAGE_STREAM_HARNESS = (
    Path(__file__).parents[1] / "raw_page_stream_benchmark.rs"
)
BINARY_WRITER_HARNESS = (
    Path(__file__).parents[1] / "binary_writer_benchmark.rs"
)
SUITE_IDENTITY = Path(__file__).parents[1] / "scripts" / "suite_identity.py"
CI_WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
MASK_U64 = (1 << 64) - 1
MASK_U32 = (1 << 32) - 1
MULTIPLIER = 0x9E37_79B9_7F4A_7C15
WRITER_MULTIPLIER = 0x9E37_79B9
WRITER_MASK = 0xA5C3_1F27


def deterministic_bytes(length: int) -> bytes:
    generated = bytearray()
    for index in range(length):
        value = (index * MULTIPLIER) & MASK_U64
        value = ((value << 17) & MASK_U64) | (value >> (64 - 17))
        generated.append(value & 0xFF)
    return bytes(generated)


def deterministic_writer_bytes(length: int, *, inverted: bool) -> bytes:
    if length % 4:
        raise ValueError("writer output length must be word-aligned")

    generated = bytearray()
    for index in range(length // 4):
        value = (index * WRITER_MULTIPLIER) & MASK_U32
        value = ((value << 13) & MASK_U32) | (value >> (32 - 13))
        value ^= WRITER_MASK
        if inverted:
            value = (~value) & MASK_U32
        generated.extend(value.to_bytes(4, "little"))
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

    def test_sequential_raw_page_stream_metadata_is_exact(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        stream = manifest["sequential_raw_page_stream_inputs"]
        page_size = stream["page_size_bytes"]
        page_counts = stream["page_counts"]
        totals = stream["total_bytes_by_page_count"]

        self.assertEqual(page_size, 2048)
        self.assertEqual(page_counts, [1, 16, 1024])
        self.assertEqual(
            totals,
            {str(page_count): page_count * page_size for page_count in page_counts},
        )

        stream_benchmarks = [
            entry
            for entry in manifest["benchmarks"]
            if entry["criterion_group"] == "raw_page_stream"
        ]
        self.assertEqual(
            [entry["id"] for entry in stream_benchmarks],
            ["BENCH-RAW-PAGE-STREAM-001"],
        )
        harness = RAW_PAGE_STREAM_HARNESS.read_text(encoding="utf-8")
        self.assertIn(
            "const STREAM_PAGE_COUNTS: [u64; 3] = [1, 16, 1024];",
            harness,
        )
        self.assertIn('benchmark_group("raw_page_stream")', harness)

    def test_binary_writer_metadata_matches_bounded_harness(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        writer = manifest["binary_writer_inputs"]
        self.assertEqual(writer["output_sizes_bytes"], [64, 4096, 65536, 1048576])
        self.assertEqual(writer["word_size_bytes"], 4)
        self.assertIn("checked u32 conversion", writer["generator"])
        self.assertIn("2 * output_size", writer["limits"])

        writer_benchmarks = [
            entry
            for entry in manifest["benchmarks"]
            if entry["criterion_group"] == "binary_writer"
        ]
        self.assertEqual(
            [entry["id"] for entry in writer_benchmarks],
            ["BENCH-WRITER-ENCODE-001"],
        )
        harness = BINARY_WRITER_HARNESS.read_text(encoding="utf-8")
        self.assertIn(
            "const OUTPUT_SIZES: [usize; 4] = [64, 4 * 1024, 64 * 1024, 1024 * 1024];",
            harness,
        )
        self.assertIn("const WORD_MULTIPLIER: u32 = 0x9e37_79b9;", harness)
        self.assertIn("const WORD_MASK: u32 = 0xa5c3_1f27;", harness)
        self.assertIn('benchmark_group("binary_writer")', harness)
        self.assertIn('BenchmarkId::new("write_u32_le"', harness)
        self.assertIn('BenchmarkId::new("rewrite_u32_le"', harness)
        self.assertIn("let first_words = precomputed_words", harness)
        self.assertIn("let rewrite_words = precomputed_words", harness)
        self.assertIn("fn verify_preflight(", harness)
        self.assertIn("result.budget.encoded_bytes()", harness)
        self.assertIn("result.budget.total_work_units()", harness)
        self.assertIn("|(output, budget)| write_once(output, budget", harness)
        self.assertNotIn("black_box(output)", harness)
        self.assertIn(
            '"benches/binary_writer_benchmark.rs"',
            SUITE_IDENTITY.read_text(encoding="utf-8"),
        )

    def test_binary_writer_output_hashes_match_manifest(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        writer = manifest["binary_writer_inputs"]
        sizes = writer["output_sizes_bytes"]
        first_hashes = writer["sha256_first_pass_by_size"]
        rewrite_hashes = writer["sha256_rewrite_pass_by_size"]
        expected_keys = {str(size) for size in sizes}

        self.assertEqual(set(first_hashes), expected_keys)
        self.assertEqual(set(rewrite_hashes), expected_keys)
        for size in sizes:
            first = deterministic_writer_bytes(size, inverted=False)
            rewrite = deterministic_writer_bytes(size, inverted=True)
            self.assertEqual(
                hashlib.sha256(first).hexdigest(),
                first_hashes[str(size)],
            )
            self.assertEqual(
                hashlib.sha256(rewrite).hexdigest(),
                rewrite_hashes[str(size)],
            )

    def test_ci_compiles_and_executes_every_registered_benchmark(self) -> None:
        workflow = CI_WORKFLOW.read_text(encoding="utf-8")
        benchmark_job = workflow.split("  benchmarks:", maxsplit=1)[1].split(
            "  fuzz:", maxsplit=1
        )[0]
        self.assertEqual(
            benchmark_job.count("--benches --locked"),
            2,
        )
        self.assertNotIn("--bench format_primitives", benchmark_job)
        self.assertIn("--benches --locked --no-run", benchmark_job)
        self.assertIn("--benches --locked -- --test", benchmark_job)

    def test_scope_limit_remains_explicit(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        limitations = " ".join(manifest["limitations"])
        self.assertIn("100000-row", limitations)
        self.assertIn("No checked performance baseline", limitations)
        self.assertIn("does not identify Jet 3", limitations)
        self.assertIn("contemporaneous .ldb lock evidence", limitations)


if __name__ == "__main__":
    unittest.main()
