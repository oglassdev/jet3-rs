from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import validate_g6_evidence as g6  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class G6EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / "crates/jet3/src"
        source.mkdir(parents=True)
        self.paths = ["crates/jet3/src/binary.rs", "crates/jet3/src/limits.rs"]
        for index, path in enumerate(self.paths):
            (self.root / path).write_text(
                f"pub fn core_{index}() {{}}\n", encoding="utf-8"
            )
        (self.root / "rust-toolchain.toml").write_text(
            '[toolchain]\nchannel = "1.96.0"\n', encoding="utf-8"
        )
        inventory_dir = self.root / "docs/validation/g6"
        inventory_dir.mkdir(parents=True)
        self.inventory_path = inventory_dir / "core-modules.json"
        self.inventory = {
            "schema_version": 1,
            "source_root": "crates/jet3/src",
            "modules": [
                {
                    "path": path,
                    "classification": (
                        "format_safety" if path.endswith("binary.rs") else "safety"
                    ),
                    "sha256": digest(self.root / path),
                }
                for path in self.paths
            ],
        }
        self.write_json(self.inventory_path, self.inventory)
        self.observed = {
            "git_commit": "a" * 40,
            "git_dirty": False,
            "rust_toolchain_sha256": digest(self.root / "rust-toolchain.toml"),
        }

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

    def envelope(self, kind: str, report: Path, report_format: str) -> dict:
        return {
            "schema_version": 1,
            "kind": kind,
            "git_commit": self.observed["git_commit"],
            "git_dirty": False,
            "rust_toolchain_sha256": self.observed["rust_toolchain_sha256"],
            "tool": "test-tool 1.0",
            "command": "test-tool --frozen",
            "inventory_sha256": digest(self.inventory_path),
            "sources": [
                {"path": path, "sha256": digest(self.root / path)}
                for path in self.paths
            ],
            "report": {
                "path": report.relative_to(self.root).as_posix(),
                "sha256": digest(report),
                "format": report_format,
            },
        }

    def json_coverage(
        self, lines: tuple[int, int] = (10, 9), regions: tuple[int, int] = (10, 8)
    ) -> dict:
        files = []
        for path in self.paths:
            files.append(
                {
                    "filename": str((self.root / path).resolve()),
                    "summary": {
                        "lines": {"count": lines[0], "covered": lines[1]},
                        "regions": {"count": regions[0], "covered": regions[1]},
                    },
                }
            )
        return {
            "type": "llvm.coverage.json.export",
            "version": "2.0.1",
            "data": [{"files": files}],
        }

    def validate_json(self, report: dict) -> dict[str, tuple[int, int]]:
        report_path = self.root / "reports/coverage.json"
        self.write_json(report_path, report)
        envelope_path = self.root / "reports/coverage-evidence.json"
        self.write_json(
            envelope_path,
            self.envelope("coverage", report_path, "llvm-cov-json"),
        )
        return g6.validate_coverage(
            self.root, envelope_path, self.inventory_path, self.observed
        )

    def disposition(self, confirmation: bool = False) -> dict:
        artifact = self.root / "reports/equivalence-confirmation.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("tool-confirmed unreachable mutant\n", encoding="utf-8")
        return {
            "owner": "core-team",
            "rationale": "reviewed result",
            "risk": "low",
            "action": "add test before release",
            "tool_confirmation": (
                {
                    "tool": "mutation-analyzer 1.0",
                    "path": artifact.relative_to(self.root).as_posix(),
                    "sha256": digest(artifact),
                }
                if confirmation
                else None
            ),
        }

    def mutant(
        self,
        index: int,
        status: str = "killed",
        *,
        path: str | None = None,
        invariant_kind: str = "none",
        invariant_ids: list[str] | None = None,
    ) -> dict:
        disposition = (
            self.disposition(status in {"equivalent", "unreachable"})
            if status in g6.DISPOSITION_STATUSES
            else None
        )
        return {
            "id": f"MUT-{index:03}",
            "path": path or self.paths[index % len(self.paths)],
            "line": index + 1,
            "status": status,
            "scope": sorted(g6.MUTATION_SCOPES)[index % len(g6.MUTATION_SCOPES)],
            "invariant_kind": invariant_kind,
            "invariant_ids": invariant_ids or [],
            "disposition": disposition,
        }

    def validate_mutants(self, mutants: list[dict]) -> tuple[int, int]:
        report_path = self.root / "reports/mutation.json"
        producer_path = self.root / "reports/native-mutants.json"
        self.write_json(
            producer_path,
            {"tool": "test-mutator", "results": [item["id"] for item in mutants]},
        )
        self.write_json(
            report_path,
            {
                "schema_version": 1,
                "scopes": sorted(g6.MUTATION_SCOPES),
                "producer_report": {
                    "path": producer_path.relative_to(self.root).as_posix(),
                    "sha256": digest(producer_path),
                    "format": "test-mutator-json-v1",
                },
                "mutants": mutants,
            },
        )
        envelope_path = self.root / "reports/mutation-evidence.json"
        self.write_json(
            envelope_path,
            self.envelope("mutation", report_path, "g6-mutation-json"),
        )
        return g6.validate_mutation(
            self.root, envelope_path, self.inventory_path, self.observed
        )

    def assert_rejected(self, message: str, action: object) -> None:
        with self.assertRaisesRegex(g6.EvidenceError, message):
            action()  # type: ignore[operator]

    def test_checked_inventory_is_complete_sorted_and_hash_bound(self) -> None:
        modules = g6.load_inventory(self.root, self.inventory_path)
        self.assertEqual([module.path for module in modules], self.paths)

        (self.root / "crates/jet3/src/new_format.rs").write_text(
            "pub fn new() {}\n", encoding="utf-8"
        )
        self.assert_rejected(
            "core inventory mismatch",
            lambda: g6.load_inventory(self.root, self.inventory_path),
        )

    def test_inventory_rejects_stale_hash_test_module_and_extra_entry(self) -> None:
        (self.root / self.paths[0]).write_text("changed\n", encoding="utf-8")
        self.assert_rejected(
            "stale source hash",
            lambda: g6.load_inventory(self.root, self.inventory_path),
        )
        (self.root / self.paths[0]).write_text(
            "pub fn core_0() {}\n", encoding="utf-8"
        )
        (self.root / "crates/jet3/src/binary_tests.rs").write_text(
            "mod tests {}\n", encoding="utf-8"
        )
        self.assertEqual(len(g6.load_inventory(self.root, self.inventory_path)), 2)

        changed = copy.deepcopy(self.inventory)
        changed["modules"].append(copy.deepcopy(changed["modules"][0]))
        changed["modules"][-1]["path"] = "crates/jet3/src/binary_tests.rs"
        changed["modules"][-1]["sha256"] = digest(
            self.root / "crates/jet3/src/binary_tests.rs"
        )
        self.write_json(self.inventory_path, changed)
        self.assert_rejected(
            "core inventory mismatch",
            lambda: g6.load_inventory(self.root, self.inventory_path),
        )

    def test_json_coverage_accepts_exact_thresholds(self) -> None:
        metrics = self.validate_json(self.json_coverage())
        self.assertEqual(metrics, {"lines": (20, 18), "regions": (20, 16)})

    def test_json_coverage_rejects_one_below_each_threshold(self) -> None:
        self.assert_rejected(
            "line coverage is below 90",
            lambda: self.validate_json(self.json_coverage(lines=(10, 8))),
        )
        self.assert_rejected(
            "regions coverage is below 80",
            lambda: self.validate_json(self.json_coverage(regions=(10, 7))),
        )

    def test_json_coverage_rejects_empty_and_excluded_core_files(self) -> None:
        empty = self.json_coverage(lines=(0, 0))
        self.assert_rejected(
            "invalid or vacuous counter", lambda: self.validate_json(empty)
        )
        excluded = self.json_coverage()
        excluded["data"][0]["files"].pop()  # type: ignore[index]
        self.assert_rejected(
            "excluded core files", lambda: self.validate_json(excluded)
        )

    def test_json_coverage_ignores_noncore_totals_and_cannot_be_inflated(self) -> None:
        report = self.json_coverage(lines=(10, 8))
        report["data"][0]["totals"] = {  # type: ignore[index]
            "lines": {"count": 1000, "covered": 1000},
            "regions": {"count": 1000, "covered": 1000},
        }
        self.assert_rejected(
            "line coverage is below 90", lambda: self.validate_json(report)
        )

    def test_lcov_accepts_exact_line_and_branch_thresholds(self) -> None:
        report_path = self.root / "reports/coverage.lcov"
        report_path.parent.mkdir(parents=True)
        records = []
        for path in self.paths:
            records.extend(
                [
                    f"SF:{self.root / path}",
                    "LF:10",
                    "LH:9",
                    "BRF:10",
                    "BRH:8",
                    "end_of_record",
                ]
            )
        report_path.write_text("\n".join(records) + "\n", encoding="utf-8")
        envelope_path = self.root / "reports/coverage-evidence.json"
        self.write_json(
            envelope_path, self.envelope("coverage", report_path, "lcov")
        )
        metrics = g6.validate_coverage(
            self.root, envelope_path, self.inventory_path, self.observed
        )
        self.assertEqual(metrics, {"lines": (20, 18), "branches": (20, 16)})

    def test_lcov_rejects_missing_or_vacuous_branch_data(self) -> None:
        report_path = self.root / "reports/coverage.lcov"
        report_path.parent.mkdir(parents=True)
        report_path.write_text(
            "".join(
                f"SF:{self.root / path}\nLF:10\nLH:10\nBRF:0\nBRH:0\nend_of_record\n"
                for path in self.paths
            ),
            encoding="utf-8",
        )
        envelope_path = self.root / "reports/coverage-evidence.json"
        self.write_json(
            envelope_path, self.envelope("coverage", report_path, "lcov")
        )
        self.assert_rejected(
            "invalid or vacuous branches",
            lambda: g6.validate_coverage(
                self.root, envelope_path, self.inventory_path, self.observed
            ),
        )

    def test_binding_rejects_dirty_commit_toolchain_inventory_source_and_report(self) -> None:
        report_path = self.root / "reports/coverage.json"
        self.write_json(report_path, self.json_coverage())
        base = self.envelope("coverage", report_path, "llvm-cov-json")
        mutations = [
            ("git_commit", "b" * 40, "does not match current HEAD"),
            ("git_dirty", True, "release evidence must be clean"),
            ("rust_toolchain_sha256", "b" * 64, "stale toolchain binding"),
            ("inventory_sha256", "b" * 64, "stale inventory binding"),
        ]
        for field, value, message in mutations:
            with self.subTest(field=field):
                changed = copy.deepcopy(base)
                changed[field] = value
                envelope_path = self.root / "reports/evidence.json"
                self.write_json(envelope_path, changed)
                self.assert_rejected(
                    message,
                    lambda: g6.validate_coverage(
                        self.root, envelope_path, self.inventory_path, self.observed
                    ),
                )
        changed = copy.deepcopy(base)
        changed["sources"][0]["sha256"] = "b" * 64
        envelope_path = self.root / "reports/evidence.json"
        self.write_json(envelope_path, changed)
        self.assert_rejected(
            "exactly match checked core inventory",
            lambda: g6.validate_coverage(
                self.root, envelope_path, self.inventory_path, self.observed
            ),
        )
        changed = copy.deepcopy(base)
        changed["report"]["sha256"] = "b" * 64
        self.write_json(envelope_path, changed)
        self.assert_rejected(
            "stale report hash",
            lambda: g6.validate_coverage(
                self.root, envelope_path, self.inventory_path, self.observed
            ),
        )

    def test_mutation_accepts_exact_85_percent_with_disposed_survivors(self) -> None:
        mutants = [self.mutant(index) for index in range(17)]
        mutants.extend(self.mutant(index, "survived") for index in range(17, 20))
        self.assertEqual(self.validate_mutants(mutants), (17, 20))

    def test_mutation_rejects_one_below_85_percent(self) -> None:
        mutants = [self.mutant(index) for index in range(16)]
        mutants.extend(self.mutant(index, "survived") for index in range(16, 20))
        self.assert_rejected(
            "below 85", lambda: self.validate_mutants(mutants)
        )

    def test_mutation_rejects_empty_excluded_and_unscored_reports(self) -> None:
        self.assert_rejected(
            "non-empty array", lambda: self.validate_mutants([])
        )
        only_first = [self.mutant(0, path=self.paths[0])]
        self.assert_rejected(
            "excluded core files", lambda: self.validate_mutants(only_first)
        )
        unscored = [
            self.mutant(0, "unviable", path=self.paths[0]),
            self.mutant(1, "unviable", path=self.paths[1]),
        ]
        self.assert_rejected(
            "scopes without scored mutants", lambda: self.validate_mutants(unscored)
        )

    def test_scope_declarations_require_scored_mutants_in_every_scope(self) -> None:
        mutants = [
            self.mutant(index, path=self.paths[index % 2]) for index in range(4)
        ]
        mutants[-1]["scope"] = mutants[0]["scope"]
        self.assert_rejected(
            "scopes without scored mutants", lambda: self.validate_mutants(mutants)
        )

    def test_mostly_unviable_report_cannot_claim_complete_scope_coverage(self) -> None:
        mutants = [
            self.mutant(0, path=self.paths[0]),
            self.mutant(1, "unviable", path=self.paths[1]),
            self.mutant(2, "unviable", path=self.paths[0]),
            self.mutant(3, "unviable", path=self.paths[1]),
        ]
        self.assert_rejected(
            "scopes without scored mutants", lambda: self.validate_mutants(mutants)
        )

    def test_survivor_requires_complete_disposition(self) -> None:
        mutants = [
            self.mutant(0, path=self.paths[0]),
            self.mutant(1, "survived", path=self.paths[1]),
        ]
        mutants[1]["disposition"] = None
        self.assert_rejected(
            "disposition: required", lambda: self.validate_mutants(mutants)
        )
        mutants[1]["disposition"] = self.disposition()
        mutants[1]["disposition"]["owner"] = " "
        self.assert_rejected(
            "owner: expected non-empty", lambda: self.validate_mutants(mutants)
        )

    def test_format_or_safety_invariant_survivor_always_blocks(self) -> None:
        mutants = [
            self.mutant(
                0,
                "survived",
                path=self.paths[0],
                invariant_kind="format",
                invariant_ids=["PHYS-01"],
            ),
            self.mutant(1, path=self.paths[1]),
        ]
        self.assert_rejected(
            "survivor affects a format/safety invariant",
            lambda: self.validate_mutants(mutants),
        )

    def test_invariant_classification_cannot_hide_ids(self) -> None:
        mutants = [
            self.mutant(0, path=self.paths[0], invariant_ids=["SAFE-01"]),
            self.mutant(1, path=self.paths[1]),
        ]
        self.assert_rejected(
            "invariant_kind and invariant_ids disagree",
            lambda: self.validate_mutants(mutants),
        )

    def test_equivalent_and_unreachable_require_tool_confirmation(self) -> None:
        for status in ("equivalent", "unreachable"):
            with self.subTest(status=status):
                mutants = [
                    self.mutant(0, status, path=self.paths[0]),
                    self.mutant(1, path=self.paths[1]),
                ]
                mutants[0]["disposition"]["tool_confirmation"] = None
                self.assert_rejected(
                    "tool_confirmation: required hash-bound artifact",
                    lambda: self.validate_mutants(mutants),
                )

    def test_equivalence_confirmation_is_hash_bound(self) -> None:
        mutants = [
            self.mutant(0, "equivalent", path=self.paths[0]),
            self.mutant(1, path=self.paths[1]),
        ]
        mutants[0]["disposition"]["tool_confirmation"]["sha256"] = "b" * 64
        self.assert_rejected(
            "stale artifact hash", lambda: self.validate_mutants(mutants)
        )

    def test_equivalent_and_unreachable_are_removed_from_score(self) -> None:
        mutants = [
            self.mutant(0, path=self.paths[0]),
            self.mutant(1, path=self.paths[1]),
            self.mutant(2, path=self.paths[0]),
            self.mutant(3, path=self.paths[1]),
            self.mutant(4, "equivalent", path=self.paths[0]),
            self.mutant(5, "unreachable", path=self.paths[1]),
        ]
        self.assertEqual(self.validate_mutants(mutants), (4, 4))

    def test_native_producer_report_is_required_nonempty_and_hash_bound(self) -> None:
        mutants = [
            self.mutant(index, path=self.paths[index % 2]) for index in range(4)
        ]
        report_path = self.root / "reports/mutation.json"
        producer_path = self.root / "reports/native-mutants.json"
        producer_path.parent.mkdir(parents=True, exist_ok=True)
        producer_path.write_bytes(b"")
        report = {
            "schema_version": 1,
            "scopes": sorted(g6.MUTATION_SCOPES),
            "producer_report": {
                "path": producer_path.relative_to(self.root).as_posix(),
                "sha256": digest(producer_path),
                "format": "test-mutator-json-v1",
            },
            "mutants": mutants,
        }
        self.write_json(report_path, report)
        envelope_path = self.root / "reports/mutation-evidence.json"
        self.write_json(
            envelope_path,
            self.envelope("mutation", report_path, "g6-mutation-json"),
        )
        self.assert_rejected(
            "empty producer report",
            lambda: g6.validate_mutation(
                self.root, envelope_path, self.inventory_path, self.observed
            ),
        )

    def test_timeout_is_scored_as_surviving_and_requires_disposition(self) -> None:
        mutants = [self.mutant(index) for index in range(17)]
        mutants.extend(self.mutant(index, "timeout") for index in range(17, 20))
        self.assertEqual(self.validate_mutants(mutants), (17, 20))
        mutants[-1]["disposition"] = None
        self.assert_rejected(
            "disposition: required", lambda: self.validate_mutants(mutants)
        )


if __name__ == "__main__":
    unittest.main()
