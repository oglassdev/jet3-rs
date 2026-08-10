from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

import acceptance_report as report  # noqa: E402


def symlink_or_skip(
    case: unittest.TestCase, link: Path, target: Path
) -> None:
    try:
        link.symlink_to(target)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            case.skipTest("Windows symlink privilege is unavailable")
        raise


class AcceptanceReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.name", "Acceptance Test")
        self._git("config", "user.email", "acceptance@example.invalid")
        (self.repo / "README.md").write_text("fixture repository\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-qm", "test: initialize fixture")
        self.commit = self._git("rev-parse", "HEAD").stdout.strip()
        self.run_id = "test-run"

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def _log(self, gate: str, stream: str, content: str = "") -> str:
        relative = (
            Path("artifacts")
            / "acceptance"
            / self.commit
            / self.run_id
            / "logs"
            / f"{gate}.{stream}.txt"
        )
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return relative.as_posix()

    def _record(
        self,
        gate: str = "G0",
        status: str = "PASS",
        *,
        exit_code: int | None = None,
        stdout: str | None = None,
        stderr: str | None = None,
        reason: str = "recorded outcome",
        command: list[str] | None = None,
        require_clean: bool = False,
        expected_commit: str | None = None,
        started_at: str = "2026-07-23T12:00:00Z",
        finished_at: str = "2026-07-23T12:00:01.250Z",
    ) -> Path:
        if exit_code is None:
            exit_code = 0 if status == "PASS" else 1
        return report.record_gate(
            repo_root=self.repo,
            run_id=self.run_id,
            gate=gate,
            status=status,
            reason=reason,
            command=command if command is not None else ["cargo", "test", "--locked"],
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            stdout_artifact=stdout or self._log(gate, "stdout", f"{gate} output\n"),
            stderr_artifact=stderr or self._log(gate, "stderr"),
            expected_commit=expected_commit,
            require_clean=require_clean,
        )

    def _record_all(self, statuses: dict[str, str] | None = None) -> None:
        statuses = statuses or {}
        for gate in report.GATES:
            status = statuses.get(gate, "PASS")
            self._record(
                gate,
                status,
                exit_code=0 if status == "PASS" else 3 if status == "BLOCKED" else 1,
            )

    def test_record_pass_captures_commit_command_timing_and_artifact_hashes(self) -> None:
        path = self._record()
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["git_commit"], self.commit)
        self.assertFalse(document["dirty"])
        self.assertEqual(document["duration_ms"], 1250)
        self.assertEqual(document["command"]["argv"], ["cargo", "test", "--locked"])
        stdout = self.repo / document["stdout"]["path"]
        self.assertEqual(document["stdout"]["sha256"], report.sha256_file(stdout))
        self.assertEqual(document["stdout"]["size_bytes"], stdout.stat().st_size)

    def test_record_fail_and_blocked_require_nonzero_exit_and_reason(self) -> None:
        self._record("G0", "FAIL", exit_code=1, reason="test failed")
        with self.assertRaisesRegex(report.ReportError, "nonzero"):
            self._record("G1", "BLOCKED", exit_code=0, reason="provider missing")
        with self.assertRaisesRegex(report.ReportError, "non-empty reason"):
            self._record("G1", "BLOCKED", exit_code=3, reason=" ")

    def test_record_pass_rejects_nonzero_exit(self) -> None:
        with self.assertRaisesRegex(report.ReportError, "PASS requires exit code 0"):
            self._record(exit_code=2)

    def test_record_rejects_empty_command_and_nul(self) -> None:
        with self.assertRaisesRegex(report.ReportError, "must not be empty"):
            self._record(command=[])
        with self.assertRaisesRegex(report.ReportError, "NUL-free"):
            self._record(command=["bad\x00argument"])

    def test_record_rejects_reversed_or_naive_timestamps(self) -> None:
        with self.assertRaisesRegex(report.ReportError, "precedes"):
            self._record(
                started_at="2026-07-23T12:00:01Z",
                finished_at="2026-07-23T12:00:00Z",
            )
        with self.assertRaisesRegex(report.ReportError, "UTC offset"):
            self._record(started_at="2026-07-23T12:00:00")

    def test_record_rejects_stale_expected_commit(self) -> None:
        with self.assertRaisesRegex(report.ReportError, "stale commit"):
            self._record(expected_commit="0" * 40)

    def test_record_rejects_unsafe_run_and_artifact_paths(self) -> None:
        with self.assertRaisesRegex(report.ReportError, "run ID"):
            report.record_gate(
                repo_root=self.repo,
                run_id="../escape",
                gate="G0",
                status="PASS",
                reason="bad run",
                command=["true"],
                exit_code=0,
                started_at="2026-07-23T12:00:00Z",
                finished_at="2026-07-23T12:00:01Z",
                stdout_artifact="README.md",
                stderr_artifact="README.md",
            )
        with self.assertRaisesRegex(report.ReportError, "must be beneath"):
            self._record(stdout="README.md")
        with self.assertRaisesRegex(report.ReportError, "safe repository-relative"):
            self._record(stdout="../outside.txt")

    def test_record_rejects_missing_artifact_and_shared_stream_path(self) -> None:
        missing = (
            f"artifacts/acceptance/{self.commit}/{self.run_id}/logs/missing.txt"
        )
        with self.assertRaisesRegex(report.ReportError, "does not exist"):
            self._record(stdout=missing)
        shared = self._log("G0", "shared")
        with self.assertRaisesRegex(report.ReportError, "distinct"):
            self._record(stdout=shared, stderr=shared)

    def test_record_rejects_symlink_that_escapes_repository(self) -> None:
        outside = Path(self.temporary.name) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        relative = (
            Path("artifacts")
            / "acceptance"
            / self.commit
            / self.run_id
            / "logs"
            / "escape.txt"
        )
        link = self.repo / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        symlink_or_skip(self, link, outside)
        with self.assertRaisesRegex(report.ReportError, "escapes"):
            self._record(stdout=relative.as_posix())

    def test_record_rejects_symlink_that_escapes_run_directory(self) -> None:
        target = self.repo / "README.md"
        relative = (
            Path("artifacts")
            / "acceptance"
            / self.commit
            / self.run_id
            / "logs"
            / "escape-inside-repo.txt"
        )
        link = self.repo / relative
        link.parent.mkdir(parents=True, exist_ok=True)
        symlink_or_skip(self, link, target)
        with self.assertRaisesRegex(report.ReportError, "run directory"):
            self._record(stdout=relative.as_posix())

    def test_clean_record_rejects_dirty_source_tree(self) -> None:
        (self.repo / "README.md").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(report.ReportError, "dirty tree"):
            self._record(require_clean=True)

    def test_record_is_idempotent_but_rejects_conflicting_overwrite(self) -> None:
        path = self._record()
        original = path.read_bytes()
        self._record()
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaisesRegex(report.ReportError, "immutable"):
            self._record(reason="different result")

    def test_run_rejects_dirty_state_change_between_gate_reports(self) -> None:
        self._record("G0")
        (self.repo / "README.md").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(report.ReportError, "run metadata"):
            self._record("G1")

    def test_summary_counts_statuses_and_hashes_manifest(self) -> None:
        self._record_all({"G1": "FAIL", "G3": "BLOCKED"})
        path = report.summarize(repo_root=self.repo, run_id=self.run_id)
        summary = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "FAIL")
        self.assertFalse(summary["release_eligible"])
        self.assertEqual(summary["required_gates"], list(report.GATES))
        self.assertEqual(summary["counts"], {"PASS": 7, "FAIL": 1, "BLOCKED": 1})
        manifest = self.repo / summary["manifest_path"]
        self.assertEqual(summary["manifest_sha256"], report.sha256_file(manifest))
        manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest_document["files"]), 28)

    def test_only_all_pass_summary_is_release_eligible(self) -> None:
        self._record_all()
        passing = json.loads(
            report.summarize(repo_root=self.repo, run_id=self.run_id).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(passing["status"], "PASS")
        self.assertTrue(passing["release_eligible"])

    def test_summary_is_deterministic_and_idempotent(self) -> None:
        self._record_all()
        first = report.summarize(repo_root=self.repo, run_id=self.run_id)
        first_bytes = first.read_bytes()
        manifest_bytes = (first.parent / "manifest.json").read_bytes()
        second = report.summarize(repo_root=self.repo, run_id=self.run_id)
        self.assertEqual(first, second)
        self.assertEqual(second.read_bytes(), first_bytes)
        self.assertEqual((second.parent / "manifest.json").read_bytes(), manifest_bytes)

    def test_summary_rejects_missing_required_gate(self) -> None:
        self._record("G0")
        with self.assertRaisesRegex(report.ReportError, "missing G1 report"):
            report.summarize(repo_root=self.repo, run_id=self.run_id)

    def test_summary_rejects_gate_subset_without_publishing_release_files(self) -> None:
        self._record("G0")
        with self.assertRaisesRegex(report.ReportError, "exactly G0 through G8"):
            report.summarize(
                repo_root=self.repo,
                run_id=self.run_id,
                required_gates=["G0"],
            )
        run_root = (
            self.repo
            / "artifacts"
            / "acceptance"
            / self.commit
            / self.run_id
        )
        self.assertFalse((run_root / "summary.json").exists())
        self.assertFalse((run_root / "manifest.json").exists())

    def test_summary_rejects_noncanonical_full_gate_order(self) -> None:
        self._record_all()
        with self.assertRaisesRegex(report.ReportError, "canonical order"):
            report.summarize(
                repo_root=self.repo,
                run_id=self.run_id,
                required_gates=reversed(report.GATES),
            )
        run_root = (
            self.repo
            / "artifacts"
            / "acceptance"
            / self.commit
            / self.run_id
        )
        self.assertFalse((run_root / "summary.json").exists())
        self.assertFalse((run_root / "manifest.json").exists())

    def test_summary_rejects_tampered_artifact(self) -> None:
        self._record_all()
        artifact = (
            self.repo
            / "artifacts"
            / "acceptance"
            / self.commit
            / self.run_id
            / "logs"
            / "G4.stdout.txt"
        )
        artifact.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(report.ReportError, "SHA-256 no longer matches"):
            report.summarize(repo_root=self.repo, run_id=self.run_id)

    def test_summary_rejects_tampered_or_mismatched_report(self) -> None:
        self._record_all()
        gate_path = (
            self.repo
            / "artifacts"
            / "acceptance"
            / self.commit
            / self.run_id
            / "gates"
            / "G2.json"
        )
        document = json.loads(gate_path.read_text(encoding="utf-8"))
        document["git_commit"] = "0" * 40
        gate_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(report.ReportError, "commit-mismatched"):
            report.summarize(repo_root=self.repo, run_id=self.run_id)

    def test_summary_rejects_reused_artifact_across_gates(self) -> None:
        stdout = self._log("shared", "stdout")
        self._record("G0", stdout=stdout)
        self._record("G1", stdout=stdout)
        for gate in report.GATES[2:]:
            self._record(gate)
        with self.assertRaisesRegex(report.ReportError, "reused across reports"):
            report.summarize(repo_root=self.repo, run_id=self.run_id)

    def test_clean_summary_rejects_dirty_reports(self) -> None:
        (self.repo / "README.md").write_text("changed\n", encoding="utf-8")
        self._record_all()
        with self.assertRaisesRegex(report.ReportError, "dirty tree"):
            report.summarize(
                repo_root=self.repo,
                run_id=self.run_id,
                require_clean=True,
            )

    def test_summary_rejects_reports_after_repository_commit_changes(self) -> None:
        self._record_all()
        old_commit = self.commit
        (self.repo / "README.md").write_text("next commit\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-qm", "test: advance repository")
        with self.assertRaisesRegex(report.ReportError, "stale commit"):
            report.summarize(
                repo_root=self.repo,
                run_id=self.run_id,
                expected_commit=old_commit,
            )
        with self.assertRaisesRegex(report.ReportError, "stale run"):
            report.summarize(repo_root=self.repo, run_id=self.run_id)

    def test_immutable_write_cleans_temp_when_publication_is_interrupted(self) -> None:
        destination = self.repo / "artifacts" / "acceptance" / "result.json"
        with mock.patch.object(report.os, "link", side_effect=OSError("interrupted")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                report._write_immutable_json(destination, {"status": "PASS"})
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.glob(".result.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
