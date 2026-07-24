from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/fuzz_campaign.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("fuzz_campaign", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
fuzz_campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fuzz_campaign)
ValidationError = fuzz_campaign.ValidationError


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class FuzzCampaignValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.evidence_temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = Path(self.evidence_temporary.name)
        (self.root / "fuzz/fuzz_targets").mkdir(parents=True)
        (self.root / "fuzz/corpus/example").mkdir(parents=True)
        (self.root / "fuzz/fuzz_targets/example.rs").write_text("// target\n", encoding="utf-8")
        (self.root / "fuzz/Cargo.toml").write_text(
            '[package]\nname = "jet3-fuzz"\n\n'
            '[[bin]]\nname = "example"\npath = "fuzz_targets/example.rs"\n',
            encoding="utf-8",
        )
        self.seed_path = self.root / "fuzz/corpus/example/seed"
        self.seed_path.write_bytes(b"seed\n")
        self.registry = {
            "schema_version": 1,
            "deterministic_seed": 789231,
            "targets": [{
                "name": "example",
                "source": "fuzz/fuzz_targets/example.rs",
                "corpus": "fuzz/corpus/example",
                "smoke_seconds": 60,
                "max_len": 4096,
                "max_corpus_bytes": 1024,
                "peak_rss_limit_bytes": 268435456,
            }],
        }
        self.manifest = {
            "schema_version": 1,
            "protocol_version": 1,
            "seeds": [{
                "id": "FUZZ-SEED-EXAMPLE-001",
                "path": "fuzz/corpus/example/seed",
                "size_bytes": 5,
                "sha256": hashlib.sha256(b"seed\n").hexdigest(),
                "purpose": "Exercise the format-neutral example target.",
                "origin": "Project-authored literal bytes.",
                "generator": "POSIX printf",
                "environment": {
                    "os": "platform-independent",
                    "architecture": "platform-independent",
                    "encoding": "ASCII",
                    "line_endings": "LF",
                },
                "rights": "Project-authored; MIT OR Apache-2.0.",
                "reproduction_command": "printf 'seed\\n' > fuzz/corpus/example/seed",
            }],
        }
        self._write_artifacts()

    def tearDown(self) -> None:
        self.evidence_temporary.cleanup()
        self.temporary.cleanup()

    def _write_artifacts(self) -> None:
        write_json(self.root / "fuzz/targets.json", self.registry)
        write_json(self.root / "fuzz/corpus/manifest.json", self.manifest)

    def _initialize_git(self) -> str:
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Fuzz Test"], cwd=self.root, check=True)
        subprocess.run(
            ["git", "config", "user.email", "fuzz-test@example.invalid"],
            cwd=self.root,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "test fixture"], cwd=self.root, check=True)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True,
        ).strip()

    def _valid_report(self, commit: str) -> dict[str, object]:
        log_path = self.bundle / "producer.log"
        log_path.write_text(
            "Running `/tmp/fuzz-example /tmp/corpus`\n"
            "#1000 DONE cov: 1 ft: 1 corp: 1/5b lim: 5 exec/s: 16 rss: 1Mb\n",
            encoding="utf-8",
        )
        toolchain = {
            "cargo": {
                "path": "/usr/bin/cargo",
                "sha256": "a" * 64,
                "version": "cargo 1.96.0",
            },
            "rustc": {
                "path": "/usr/bin/rustc",
                "sha256": "b" * 64,
                "version": "rustc 1.96.0",
            },
        }
        executable = {"path": "/tmp/fuzz-example", "sha256": "c" * 64}
        command = [
            "/usr/bin/cargo", "fuzz", "run", "--fuzz-dir", "fuzz", "example",
            "--sanitizer", "address", "/tmp/corpus", "--",
            "-max_total_time=60", "-seed=789231", "-max_len=4096",
            "-rss_limit_mb=256",
        ]
        observer = {
            "schema_version": 1,
            "producer_log_sha256": fuzz_campaign.sha256(log_path),
            "command": command,
            "started_at": "2026-07-24T12:00:00Z",
            "finished_at": "2026-07-24T12:01:00.200000Z",
            "wall_clock_seconds": 60.2,
            "peak_rss_bytes": 1048576,
            "runs": 1000,
            "result": "clean",
            "exit_code": 0,
            "timed_out": False,
            "toolchain": toolchain,
            "executable": executable,
        }
        observer_path = self.bundle / "observer.json"
        write_json(observer_path, observer)
        return {
            "schema_version": 2,
            "commit": {"sha": commit, "dirty": False},
            "target": "example",
            "target_registry_sha256": fuzz_campaign.sha256(
                self.root / "fuzz/targets.json"
            ),
            "target_source_sha256": fuzz_campaign.sha256(
                self.root / "fuzz/fuzz_targets/example.rs"
            ),
            "corpus": {
                "manifest_sha256": fuzz_campaign.sha256(
                    self.root / "fuzz/corpus/manifest.json"
                ),
                "seeds": [{
                    "id": self.manifest["seeds"][0]["id"],
                    "path": self.manifest["seeds"][0]["path"],
                    "sha256": self.manifest["seeds"][0]["sha256"],
                }],
            },
            "campaign": {
                "duration_seconds": 60,
                "kind": "smoke",
                "deterministic_seed": 789231,
                "sanitizer": "address",
            },
            "result": "clean",
            "limits": {
                "wall_clock_seconds": 150,
                "peak_rss_bytes": 268435456,
            },
            "observed": {
                "wall_clock_seconds": 60.2,
                "peak_rss_bytes": 1048576,
                "runs": 1000,
                "started_at": "2026-07-24T12:00:00Z",
                "finished_at": "2026-07-24T12:01:00.200000Z",
                "exit_code": 0,
            },
            "producer": {
                "log": {
                    "path": "producer.log",
                    "sha256": fuzz_campaign.sha256(log_path),
                },
                "observer": {
                    "path": "observer.json",
                    "sha256": fuzz_campaign.sha256(observer_path),
                },
                "command": command,
                "toolchain": toolchain,
                "executable": executable,
            },
        }

    def _validate_report(self, report: dict[str, object]) -> None:
        report_path = self.bundle / "report.json"
        write_json(report_path, report)
        fuzz_campaign.validate_report(self.root, report_path)

    def _rewrite_observer(
        self,
        report: dict[str, object],
        observer: dict[str, object],
    ) -> None:
        observer_path = self.bundle / "observer.json"
        write_json(observer_path, observer)
        report["producer"]["observer"]["sha256"] = fuzz_campaign.sha256(observer_path)

    def test_checked_repository_is_accepted(self) -> None:
        fuzz_campaign.validate_repository(self.root)

    def test_vacuous_registry_is_rejected(self) -> None:
        self.registry["targets"] = []
        self._write_artifacts()
        with self.assertRaisesRegex(ValidationError, "at least one target"):
            fuzz_campaign.validate_repository(self.root)

    def test_missing_registered_target_is_rejected(self) -> None:
        (self.root / "fuzz/fuzz_targets/example.rs").unlink()
        with self.assertRaisesRegex(ValidationError, "no matching fuzz target source"):
            fuzz_campaign.validate_repository(self.root)

    def test_unregistered_cargo_target_is_rejected(self) -> None:
        with (self.root / "fuzz/Cargo.toml").open("a", encoding="utf-8") as cargo:
            cargo.write('\n[[bin]]\nname = "missing"\npath = "fuzz_targets/missing.rs"\n')
        (self.root / "fuzz/fuzz_targets/missing.rs").write_text("// target\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "disagree"):
            fuzz_campaign.validate_repository(self.root)

    def test_missing_seed_file_is_rejected(self) -> None:
        self.seed_path.unlink()
        with self.assertRaisesRegex(ValidationError, "missing"):
            fuzz_campaign.validate_repository(self.root)

    def test_target_without_seeds_is_rejected(self) -> None:
        self.manifest["seeds"] = []
        self.seed_path.unlink()
        self._write_artifacts()
        with self.assertRaisesRegex(ValidationError, "at least one seed"):
            fuzz_campaign.validate_repository(self.root)

    def test_seed_hash_drift_is_rejected(self) -> None:
        self.seed_path.write_bytes(b"mutated\n")
        with self.assertRaisesRegex(ValidationError, "(size|hash) drift"):
            fuzz_campaign.validate_repository(self.root)

    def test_seed_reproduction_metadata_is_required(self) -> None:
        self.manifest["seeds"][0]["reproduction_command"] = ""
        self._write_artifacts()
        with self.assertRaisesRegex(ValidationError, "reproduction_command"):
            fuzz_campaign.validate_repository(self.root)

    def test_smoke_duration_below_ci_contract_is_rejected(self) -> None:
        self.registry["targets"][0]["smoke_seconds"] = 59
        self._write_artifacts()
        with self.assertRaisesRegex(ValidationError, "integer >= 60"):
            fuzz_campaign.validate_repository(self.root)

    def test_valid_campaign_report_is_accepted(self) -> None:
        commit = self._initialize_git()
        self._validate_report(self._valid_report(commit))

    def test_stale_commit_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["commit"]["sha"] = "0" * 40
        with self.assertRaisesRegex(ValidationError, "stale"):
            self._validate_report(report)

    def test_stale_dirty_state_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["commit"]["dirty"] = True
        with self.assertRaisesRegex(ValidationError, "stale"):
            self._validate_report(report)

    def test_malformed_report_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        del report["campaign"]["sanitizer"]
        with self.assertRaisesRegex(ValidationError, "missing fields"):
            self._validate_report(report)

    def test_wall_clock_breach_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["observed"]["wall_clock_seconds"] = 150.1
        with self.assertRaisesRegex(ValidationError, "wall-clock"):
            self._validate_report(report)

    def test_peak_rss_breach_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["observed"]["peak_rss_bytes"] = 268435457
        with self.assertRaisesRegex(ValidationError, "peak-RSS"):
            self._validate_report(report)

    def test_clean_short_campaign_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["observed"]["wall_clock_seconds"] = 59.9
        with self.assertRaisesRegex(ValidationError, "ended before"):
            self._validate_report(report)

    def test_corpus_hash_mutation_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["corpus"]["seeds"][0]["sha256"] = "f" * 64
        with self.assertRaisesRegex(ValidationError, "corpus hashes"):
            self._validate_report(report)

    def test_target_registry_hash_mutation_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["target_registry_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValidationError, "registry hash is stale"):
            self._validate_report(report)

    def test_target_source_hash_mutation_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["target_source_sha256"] = "f" * 64
        with self.assertRaisesRegex(ValidationError, "source hash is stale"):
            self._validate_report(report)

    def test_unbound_version_one_wrapper_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["schema_version"] = 1
        with self.assertRaisesRegex(ValidationError, "unbound version-1 wrappers"):
            self._validate_report(report)

    def test_producer_log_corruption_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        with (self.bundle / "producer.log").open("a", encoding="utf-8") as log:
            log.write("corrupted\n")
        with self.assertRaisesRegex(ValidationError, "producer-log hash is stale"):
            self._validate_report(report)

    def test_rehashed_run_count_forgery_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        log_path = self.bundle / "producer.log"
        log_path.write_text(
            log_path.read_text(encoding="utf-8").replace("#1000", "#999"),
            encoding="utf-8",
        )
        observer_path = self.bundle / "observer.json"
        observer = json.loads(observer_path.read_text(encoding="utf-8"))
        observer["producer_log_sha256"] = fuzz_campaign.sha256(log_path)
        self._rewrite_observer(report, observer)
        report["producer"]["log"]["sha256"] = fuzz_campaign.sha256(log_path)
        with self.assertRaisesRegex(ValidationError, "run count disagrees"):
            self._validate_report(report)

    def test_rehashed_executable_forgery_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        log_path = self.bundle / "producer.log"
        log_path.write_text(
            log_path.read_text(encoding="utf-8").replace("fuzz-example", "other-fuzzer"),
            encoding="utf-8",
        )
        observer_path = self.bundle / "observer.json"
        observer = json.loads(observer_path.read_text(encoding="utf-8"))
        observer["producer_log_sha256"] = fuzz_campaign.sha256(log_path)
        self._rewrite_observer(report, observer)
        report["producer"]["log"]["sha256"] = fuzz_campaign.sha256(log_path)
        with self.assertRaisesRegex(ValidationError, "executable identity disagrees"):
            self._validate_report(report)

    def test_rehashed_outcome_forgery_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        log_path = self.bundle / "producer.log"
        with log_path.open("a", encoding="utf-8") as log:
            log.write("ERROR: AddressSanitizer: heap-use-after-free\n")
        observer_path = self.bundle / "observer.json"
        observer = json.loads(observer_path.read_text(encoding="utf-8"))
        observer["producer_log_sha256"] = fuzz_campaign.sha256(log_path)
        self._rewrite_observer(report, observer)
        report["producer"]["log"]["sha256"] = fuzz_campaign.sha256(log_path)
        with self.assertRaisesRegex(ValidationError, "result disagrees"):
            self._validate_report(report)

    def test_report_toolchain_forgery_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["producer"]["toolchain"]["rustc"]["sha256"] = "d" * 64
        with self.assertRaisesRegex(ValidationError, "producer.toolchain disagrees"):
            self._validate_report(report)

    def test_observer_symlink_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        observer_path = self.bundle / "observer.json"
        observer_copy = self.bundle / "observer-copy.json"
        observer_path.replace(observer_copy)
        observer_path.symlink_to(observer_copy)
        with self.assertRaisesRegex(ValidationError, "symlink"):
            self._validate_report(report)

    def test_atomic_publication_refuses_existing_destination(self) -> None:
        temporary = self.bundle / "temporary"
        output = self.bundle / "existing"
        temporary.mkdir()
        output.mkdir()
        (temporary / "report.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValidationError, "refusing to replace"):
            fuzz_campaign.publish_directory(temporary, output)
        self.assertTrue((temporary / "report.json").is_file())

    def test_short_smoke_report_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["campaign"]["duration_seconds"] = 1
        report["observed"]["wall_clock_seconds"] = 1.0
        with self.assertRaisesRegex(ValidationError, "at least 60 seconds"):
            self._validate_report(report)

    def test_short_full_report_is_rejected(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["campaign"]["kind"] = "full"
        report["campaign"]["duration_seconds"] = 599
        with self.assertRaisesRegex(ValidationError, "at least 600 seconds"):
            self._validate_report(report)

    def test_qualifying_full_report_is_accepted(self) -> None:
        commit = self._initialize_git()
        report = self._valid_report(commit)
        report["campaign"]["kind"] = "full"
        report["campaign"]["duration_seconds"] = 600
        report["limits"]["wall_clock_seconds"] = 690
        report["observed"]["wall_clock_seconds"] = 600.2
        report["observed"]["finished_at"] = "2026-07-24T12:10:00.200000Z"
        observer_path = self.bundle / "observer.json"
        observer = json.loads(observer_path.read_text(encoding="utf-8"))
        observer["command"][-4] = "-max_total_time=600"
        observer["finished_at"] = "2026-07-24T12:10:00.200000Z"
        observer["wall_clock_seconds"] = 600.2
        write_json(observer_path, observer)
        report["producer"]["command"] = observer["command"]
        report["producer"]["observer"]["sha256"] = fuzz_campaign.sha256(observer_path)
        self._validate_report(report)


if __name__ == "__main__":
    unittest.main()
