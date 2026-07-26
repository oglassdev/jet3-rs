from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import run_g6_coverage as producer  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class G6CoverageProducerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # The repository is nested so tests own every ancestor Cargo could
        # read, and the ancestor walk is stopped at that owned base.
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "repo"
        self.root.mkdir()
        boundary = mock.patch.object(producer, "ANCESTOR_BOUNDARY", self.base)
        boundary.start()
        self.addCleanup(boundary.stop)
        ambient = tempfile.TemporaryDirectory()
        self.addCleanup(ambient.cleanup)
        # An empty, isolated Cargo home keeps the developer's own
        # ~/.cargo/config.toml from deciding these tests either way.
        self.cargo_home = Path(ambient.name).resolve()
        environment = mock.patch.dict(
            os.environ,
            {"CARGO_HOME": str(self.cargo_home), "HOME": str(self.cargo_home)},
        )
        environment.start()
        self.addCleanup(environment.stop)
        source = self.root / "crates/jet3/src"
        source.mkdir(parents=True)
        self.paths = [
            "crates/jet3/src/binary.rs",
            "crates/jet3/src/limits.rs",
        ]
        for index, relative in enumerate(self.paths):
            (self.root / relative).write_text(
                f"pub fn core_{index}() {{}}\n", encoding="utf-8"
            )
        (self.root / "rust-toolchain.toml").write_text(
            '[toolchain]\nchannel = "1.96.0"\n', encoding="utf-8"
        )
        (self.root / ".gitignore").write_text(
            "/target/\ncoverage/\n", encoding="utf-8"
        )
        inventory_directory = self.root / "docs/validation/g6"
        inventory_directory.mkdir(parents=True)
        self.inventory_path = inventory_directory / "core-modules.json"
        self.write_json(
            self.inventory_path,
            {
                "schema_version": 1,
                "source_root": "crates/jet3/src",
                "modules": [
                    {
                        "path": relative,
                        "classification": (
                            "format_safety"
                            if relative.endswith("binary.rs")
                            else "safety"
                        ),
                        "sha256": digest(self.root / relative),
                    }
                    for relative in self.paths
                ],
            },
        )
        self.git("init", "-q")
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=G6 Test",
            "-c",
            "user.email=g6@example.invalid",
            "commit",
            "-qm",
            "test fixture",
        )
        self.commit = self.git("rev-parse", "HEAD").strip()

    @staticmethod
    def write_json(path: Path, document: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

    def git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def coverage_report(self) -> dict:
        return {
            "type": "llvm.coverage.json.export",
            "version": "2.0.1",
            "data": [
                {
                    "files": [
                        {
                            "filename": str((self.root / relative).resolve()),
                            "summary": {
                                "lines": {"count": 10, "covered": 9},
                                "regions": {"count": 10, "covered": 8},
                            },
                        }
                        for relative in self.paths
                    ]
                }
            ],
        }

    def fake_campaign(
        self,
        root: Path,
        command: tuple[str, ...],
        report_path: Path,
        timeout_seconds: int,
    ) -> None:
        self.assertEqual(root, self.root)
        self.assertEqual(timeout_seconds, producer.DEFAULT_TIMEOUT_SECONDS)
        self.assertEqual(
            command[:5],
            ("rustup", "run", "1.96.0", "cargo", "llvm-cov"),
        )
        self.assertNotIn("sh", command)
        self.assertEqual(command[-2], "--output-path")
        self.assertEqual(command[-1], report_path.relative_to(root).as_posix())
        self.write_json(report_path, self.coverage_report())

    def produce(self, output: str = "coverage/g6/result") -> tuple[Path, dict]:
        with (
            mock.patch.object(
                producer, "_tool_version", return_value=producer.EXPECTED_TOOL
            ),
            mock.patch.object(
                producer, "_run_campaign", side_effect=self.fake_campaign
            ),
        ):
            return producer.produce(
                root=self.root,
                inventory_path=self.inventory_path,
                expected_commit=self.commit,
                output=output,
            )

    def test_produces_deterministic_commit_bound_envelope(self) -> None:
        envelope_path, metrics = self.produce()
        self.assertEqual(
            metrics, {"lines": (20, 18), "regions": (20, 16)}
        )
        envelope_bytes = envelope_path.read_bytes()
        envelope = json.loads(envelope_bytes)
        self.assertEqual(envelope_bytes, producer._canonical_json(envelope))
        self.assertEqual(envelope["git_commit"], self.commit)
        self.assertFalse(envelope["git_dirty"])
        self.assertEqual(envelope["tool"], "cargo-llvm-cov 0.8.6")
        self.assertEqual(
            envelope["command"],
            "rustup run 1.96.0 cargo llvm-cov --workspace --all-targets "
            "--all-features --locked --json --output-path "
            "coverage/g6/.result.staging/coverage.json",
        )
        self.assertEqual(
            envelope["sources"],
            [
                {"path": relative, "sha256": digest(self.root / relative)}
                for relative in self.paths
            ],
        )
        report_path = self.root / envelope["report"]["path"]
        self.assertEqual(envelope["report"]["sha256"], digest(report_path))
        self.assertEqual(
            envelope["rust_toolchain_sha256"],
            digest(self.root / "rust-toolchain.toml"),
        )
        self.assertEqual(
            envelope["inventory_sha256"], digest(self.inventory_path)
        )

    def refuses(
        self,
        output: str,
        expected: str,
        *,
        inventory: Path | None = None,
        commit: str | None = None,
    ) -> None:
        """Assert produce() fails closed before the tool runs, publishing nothing."""
        with (
            mock.patch.object(producer, "_tool_version") as version,
            self.assertRaisesRegex(producer.CoverageProducerError, expected),
        ):
            producer.produce(
                root=self.root,
                inventory_path=inventory or self.inventory_path,
                expected_commit=commit or self.commit,
                output=f"coverage/g6/{output}",
            )
        version.assert_not_called()
        self.assertFalse((self.root / f"coverage/g6/{output}").exists())

    def test_dirty_checkout_is_rejected_before_tool_execution(self) -> None:
        (self.root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        self.refuses("dirty", "clean checkout")

    def test_stale_expected_commit_is_rejected(self) -> None:
        self.refuses("stale", "stale checkout", commit="f" * 40)

    def test_clean_but_stale_source_inventory_is_rejected(self) -> None:
        inventory = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        inventory["modules"][0]["sha256"] = "f" * 64
        self.write_json(self.inventory_path, inventory)
        self.refuses(
            "stale-inventory",
            "stale source hash",
            commit=self.commit_all("stale inventory"),
        )

    def test_wrong_tool_version_is_rejected_verbatim(self) -> None:
        result = producer.ProcessResult(b"cargo-llvm-cov 0.8.5\n", b"")
        with mock.patch.object(
            producer, "_bounded_process", return_value=result
        ):
            with self.assertRaisesRegex(
                producer.CoverageProducerError, "expected 'cargo-llvm-cov 0.8.6'"
            ):
                producer._tool_version(self.root)

    def refuses_report(self, name: str, expected: str, write: object) -> None:
        """Assert a campaign writing `write(report_path)` publishes nothing."""

        def campaign(
            root: Path,
            command: tuple[str, ...],
            report_path: Path,
            timeout_seconds: int,
        ) -> None:
            del root, command, timeout_seconds
            write(report_path)  # type: ignore[operator]

        with (
            mock.patch.object(
                producer, "_tool_version", return_value=producer.EXPECTED_TOOL
            ),
            mock.patch.object(producer, "_run_campaign", side_effect=campaign),
            self.assertRaisesRegex(producer.CoverageProducerError, expected),
        ):
            producer.produce(
                root=self.root,
                inventory_path=self.inventory_path,
                expected_commit=self.commit,
                output=f"coverage/g6/{name}",
            )
        self.assertFalse((self.root / f"coverage/g6/{name}").exists())
        self.assertFalse((self.root / f"coverage/g6/.{name}.staging").exists())

    def test_malformed_or_below_threshold_report_is_not_published(self) -> None:
        below_threshold = self.coverage_report()
        for entry in below_threshold["data"][0]["files"]:
            entry["summary"]["lines"] = {"count": 10, "covered": 5}
        for name, expected, write in (
            (
                "malformed",
                "cannot load LLVM coverage report",
                lambda path: path.write_bytes(b'{"type": "llvm.coverage'),
            ),
            (
                "below-lines",
                "line coverage is below 90%",
                lambda path: self.write_json(path, below_threshold),
            ),
        ):
            with self.subTest(report=name):
                self.refuses_report(name, expected, write)

    def test_excluded_core_file_report_is_not_published(self) -> None:
        report = self.coverage_report()
        report["data"][0]["files"].pop()
        self.refuses_report(
            "excluded",
            "excluded core files",
            lambda path: self.write_json(path, report),
        )

    def test_stale_report_hash_fails_closed_and_removes_publication(self) -> None:
        original_copy = producer._copy_new

        def corrupt_copy(source: Path, destination: Path) -> None:
            source.write_text('{"changed":true}\n', encoding="utf-8")
            original_copy(source, destination)

        with (
            mock.patch.object(
                producer, "_tool_version", return_value=producer.EXPECTED_TOOL
            ),
            mock.patch.object(
                producer, "_run_campaign", side_effect=self.fake_campaign
            ),
            mock.patch.object(producer, "_copy_new", side_effect=corrupt_copy),
            self.assertRaisesRegex(
                producer.CoverageProducerError, "stale report hash"
            ),
        ):
            producer.produce(
                root=self.root,
                inventory_path=self.inventory_path,
                expected_commit=self.commit,
                output="coverage/g6/corrupt",
            )
        self.assertFalse((self.root / "coverage/g6/corrupt").exists())

    def test_campaign_change_to_tracked_source_fails_closed(self) -> None:
        def mutating_campaign(
            root: Path,
            command: tuple[str, ...],
            report_path: Path,
            timeout_seconds: int,
        ) -> None:
            del command, timeout_seconds
            self.write_json(report_path, self.coverage_report())
            (root / self.paths[0]).write_text("changed\n", encoding="utf-8")

        with (
            mock.patch.object(
                producer, "_tool_version", return_value=producer.EXPECTED_TOOL
            ),
            mock.patch.object(
                producer, "_run_campaign", side_effect=mutating_campaign
            ),
            self.assertRaisesRegex(
                producer.CoverageProducerError, "clean checkout"
            ),
        ):
            producer.produce(
                root=self.root,
                inventory_path=self.inventory_path,
                expected_commit=self.commit,
                output="coverage/g6/mutated",
            )
        self.assertFalse((self.root / "coverage/g6/mutated").exists())

    def test_publication_is_create_new_and_never_overwrites(self) -> None:
        envelope_path, _ = self.produce("coverage/g6/immutable")
        original = envelope_path.read_bytes()
        with self.assertRaisesRegex(
            producer.CoverageProducerError, "refusing to overwrite immutable"
        ):
            self.produce("coverage/g6/immutable")
        self.assertEqual(envelope_path.read_bytes(), original)

    def test_output_path_cannot_inject_a_command_or_escape_coverage(self) -> None:
        for output in (
            "../outside",
            "artifacts/g6",
            "coverage/g6/result;touch-pwned",
        ):
            with self.subTest(output=output), mock.patch.object(
                producer, "_tool_version"
            ) as version:
                with self.assertRaises(producer.CoverageProducerError):
                    producer.produce(
                        root=self.root,
                        inventory_path=self.inventory_path,
                        expected_commit=self.commit,
                        output=output,
                    )
                version.assert_not_called()
        self.assertFalse((self.root / "touch-pwned").exists())

    def assert_dead(self, pid: int) -> None:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                break
            time.sleep(0.05)
        self.fail(f"descendant {pid} survived process-group termination")

    @unittest.skipIf(os.name == "nt", "process groups are POSIX-only")
    def test_timeout_kills_the_whole_process_group(self) -> None:
        pid_path = self.root / "descendant.pid"
        descendant = self.root / "descendant.py"
        descendant.write_text(
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "Path(sys.argv[1]).write_text(str(os.getpid()), encoding='utf-8')\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        parent = self.root / "parent.py"
        parent.write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n"
            "time.sleep(120)\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            producer.CoverageProducerError, "exceeded 3s timeout"
        ):
            producer._bounded_process(
                (sys.executable, str(parent), str(descendant), str(pid_path)),
                cwd=self.root,
                timeout_seconds=3,
                stdout_limit=1024,
                stderr_limit=1024,
                environment=producer._environment(),
            )
        self.assertTrue(pid_path.is_file(), "descendant never started")
        self.assert_dead(int(pid_path.read_text(encoding="utf-8")))

    def test_subprocess_timeout_and_stdout_bounds(self) -> None:
        with self.assertRaisesRegex(
            producer.CoverageProducerError, "exceeded 0.1s timeout"
        ):
            producer._bounded_process(
                (
                    sys.executable,
                    "-c",
                    "import time; time.sleep(10)",
                ),
                cwd=self.root,
                timeout_seconds=0.1,
                stdout_limit=1024,
                stderr_limit=1024,
                environment=producer._environment(),
            )
        with self.assertRaisesRegex(
            producer.CoverageProducerError, "bounded subprocess output"
        ):
            producer._bounded_process(
                (
                    sys.executable,
                    "-c",
                    "import os; os.write(1, b'x' * 4096)",
                ),
                cwd=self.root,
                timeout_seconds=5,
                stdout_limit=64,
                stderr_limit=1024,
                environment=producer._environment(),
            )

    def test_subprocess_report_size_is_bounded(self) -> None:
        report = self.root / "coverage/oversized.json"
        report.parent.mkdir()
        with self.assertRaisesRegex(
            producer.CoverageProducerError, "report exceeded bounded size"
        ):
            producer._bounded_process(
                (
                    sys.executable,
                    "-c",
                    (
                        # A soft-only limit would let the child restore it here.
                        "import time\n"
                        "from pathlib import Path\n"
                        "try:\n"
                        "    import resource\n"
                        "    unlimited = resource.RLIM_INFINITY\n"
                        "    resource.setrlimit("
                        "resource.RLIMIT_FSIZE, (unlimited, unlimited))\n"
                        "except (ImportError, ValueError, OSError):\n"
                        "    pass\n"
                        f"Path({str(report)!r}).write_bytes(b'x' * 4096)\n"
                        "time.sleep(10)\n"
                    ),
                ),
                cwd=self.root,
                timeout_seconds=5,
                stdout_limit=1024,
                stderr_limit=1024,
                environment=producer._environment(),
                watched_file=report,
                watched_limit=64,
            )
        if os.name != "nt":
            # RLIMIT_FSIZE stops the write itself; polling is only a backstop.
            self.assertLessEqual(report.stat().st_size, 64)

    def test_pre_existing_oversized_report_is_polled_and_rejected(self) -> None:
        report = self.root / "coverage/prefilled.json"
        report.parent.mkdir()
        report.write_bytes(b"x" * 4096)
        with self.assertRaisesRegex(
            producer.CoverageProducerError, "report exceeded bounded size"
        ):
            producer._bounded_process(
                (sys.executable, "-c", "import time; time.sleep(10)"),
                cwd=self.root,
                timeout_seconds=5,
                stdout_limit=1024,
                stderr_limit=1024,
                environment=producer._environment(),
                watched_file=report,
                watched_limit=64,
            )

    def test_campaign_environment_forbids_network_and_prompts(self) -> None:
        environment = producer._environment()
        self.assertEqual(environment["CARGO_NET_OFFLINE"], "true")
        self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(environment["LC_ALL"], "C")
        self.assertEqual(environment["CARGO_TERM_COLOR"], "never")
        self.assertTrue(environment["PATH"])

    def test_campaign_environment_never_inherits_vcs_or_build_variables(
        self,
    ) -> None:
        hostile = {
            "GIT_DIR": str(self.root / "elsewhere/.git"),
            "GIT_WORK_TREE": str(self.root / "elsewhere"),
            "GIT_INDEX_FILE": str(self.root / "elsewhere/index"),
            "RUSTFLAGS": "--cfg pwned",
            "RUSTC_WRAPPER": "/bin/false",
            "CARGO_TARGET_DIR": str(self.root / "elsewhere/target"),
            "CARGO_BUILD_JOBS": "1",
            "CARGO_NET_OFFLINE": "false",
        }
        with mock.patch.dict(producer.os.environ, hostile, clear=False):
            environment = producer._environment()
            self.assertEqual(environment["CARGO_NET_OFFLINE"], "true")
            for name in hostile:
                if name != "CARGO_NET_OFFLINE":
                    self.assertNotIn(name, environment)
            observed = producer._bounded_process(
                (
                    sys.executable,
                    "-c",
                    "import json, os, sys;"
                    " sys.stdout.write(json.dumps(dict(os.environ)))",
                ),
                cwd=self.root,
                timeout_seconds=30,
                stdout_limit=64 * 1024,
                stderr_limit=1024,
                environment=environment,
            )
        child = json.loads(observed.stdout.decode("utf-8"))
        for name in hostile:
            if name != "CARGO_NET_OFFLINE":
                self.assertNotIn(name, child)
        self.assertEqual(child["CARGO_NET_OFFLINE"], "true")

    def test_untracked_in_repo_inventory_is_rejected(self) -> None:
        # coverage/ is ignored, so Git still reports a clean checkout here.
        ignored = self.root / "coverage/fake.json"
        ignored.parent.mkdir()
        ignored.write_text(
            self.inventory_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        self.assertFalse(self.git("status", "--porcelain=v1").strip())
        self.refuses("untracked", "inventory is not tracked", inventory=ignored)

    def test_locally_modified_inventory_is_rejected(self) -> None:
        self.assertEqual(
            producer._verify_committed_file(
                self.root, self.inventory_path, "inventory"
            ),
            "docs/validation/g6/core-modules.json",
        )
        self.inventory_path.write_bytes(self.inventory_path.read_bytes() + b"\n")
        with self.assertRaisesRegex(
            producer.CoverageProducerError, "differs from the version committed"
        ):
            producer._verify_committed_file(
                self.root, self.inventory_path, "inventory"
            )
        self.refuses("modified", "clean checkout")

    def test_ambient_cargo_configuration_blocks_the_campaign(self) -> None:
        producer._reject_ambient_cargo_config(
            producer._environment(), self.root, boundary=self.base
        )
        self.refuses_cargo_config(self.cargo_home, "ambient")

    def refuses_cargo_config(self, directory: Path, label: str) -> None:
        """Assert every Cargo config name in `directory` blocks the campaign."""
        directory.mkdir(parents=True, exist_ok=True)
        for name in producer.CARGO_CONFIG_NAMES:
            with self.subTest(name=name):
                config = directory / name
                config.write_text(
                    '[build]\nrustflags = ["--cfg", "pwned"]\n', encoding="utf-8"
                )
                self.addCleanup(config.unlink, missing_ok=True)
                self.refuses(
                    f"{label}-{name}", "ambient Cargo configuration is not commit-bound"
                )
                config.unlink()

    def commit_all(self, message: str) -> str:
        self.git("add", "-A")
        self.git(
            "-c",
            "user.name=G6 Test",
            "-c",
            "user.email=g6@example.invalid",
            "commit",
            "-qm",
            message,
        )
        return self.git("rev-parse", "HEAD").strip()

    def test_relative_cargo_home_is_rejected(self) -> None:
        # Ignored by .gitignore, so only the CARGO_HOME check can catch it:
        # Cargo would read it from the campaign cwd, not the producer's.
        relative = "coverage/cargo-home"
        (self.root / relative).mkdir(parents=True)
        (self.root / relative / "config.toml").write_text(
            '[build]\nrustflags = ["--cfg", "pwned"]\n', encoding="utf-8"
        )
        with mock.patch.dict(os.environ, {"CARGO_HOME": relative}):
            self.refuses(
                "relative-cargo-home", "CARGO_HOME must be an absolute path"
            )

    def test_ancestor_cargo_configuration_blocks_the_campaign(self) -> None:
        self.refuses_cargo_config(self.base / ".cargo", "ancestor")

    def test_ancestor_walk_does_not_stop_below_the_boundary(self) -> None:
        # Two levels above the checkout, and reached with no boundary at all.
        ancestor = self.base / ".cargo"
        ancestor.mkdir()
        (ancestor / "config.toml").write_text("[build]\n", encoding="utf-8")
        deeper = self.root / "nested/checkout"
        deeper.mkdir(parents=True)
        with self.assertRaisesRegex(
            producer.CoverageProducerError, "ambient Cargo configuration"
        ):
            producer._reject_ambient_cargo_config(producer._environment(), deeper)

    def test_untracked_in_repo_cargo_configuration_is_rejected(self) -> None:
        (self.root / ".cargo").mkdir()
        (self.root / ".cargo/config.toml").write_text(
            '[build]\nrustflags = ["--cfg", "pwned"]\n', encoding="utf-8"
        )
        self.refuses(
            "untracked-cargo", "in-repo Cargo configuration is not tracked"
        )

    def test_tracked_in_repo_cargo_configuration_is_allowed(self) -> None:
        (self.root / ".cargo").mkdir()
        config = self.root / ".cargo/config.toml"
        config.write_text("[build]\nincremental = false\n", encoding="utf-8")
        self.commit = self.commit_all("in-repo cargo config")
        envelope_path, _ = self.produce("coverage/g6/tracked-cargo")
        self.assertTrue(envelope_path.is_file())
        config.write_text("[build]\nincremental = true\n", encoding="utf-8")
        with self.assertRaisesRegex(
            producer.CoverageProducerError,
            "in-repo Cargo configuration differs from the version committed",
        ):
            producer._reject_ambient_cargo_config(
                producer._environment(), self.root, boundary=self.base
            )

    def test_oversized_inventory_is_rejected_by_the_streamed_read(self) -> None:
        # The bound comes from the single-descriptor read: this inventory is
        # tracked and byte-identical to HEAD, so nothing else can reject it.
        limit = self.inventory_path.stat().st_size - 1
        with (
            mock.patch.object(producer, "TRACKED_FILE_LIMIT", limit),
            self.assertRaisesRegex(
                producer.CoverageProducerError, "inventory exceeded bounded size"
            ),
        ):
            producer._verify_committed_file(
                self.root, self.inventory_path, "inventory"
            )
        with mock.patch.object(producer, "TRACKED_FILE_LIMIT", limit + 1):
            self.assertEqual(
                producer._verify_committed_file(
                    self.root, self.inventory_path, "inventory"
                ),
                "docs/validation/g6/core-modules.json",
            )

    @unittest.skipIf(os.name == "nt", "FIFOs are POSIX-only")
    def test_streamed_read_ignores_a_lying_stat_size(self) -> None:
        # A FIFO always stats as empty, so a stat-then-read bound would let
        # 32 KiB through; only the streamed read can reject it.
        fifo = self.base / "inventory.fifo"
        os.mkfifo(fifo)

        def feed() -> None:
            try:
                with open(fifo, "wb") as sink:
                    for _ in range(8):
                        sink.write(b"x" * 4096)
            except OSError:
                pass

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        self.addCleanup(writer.join, 5)
        with self.assertRaisesRegex(
            producer.CoverageProducerError, "inventory exceeded bounded size"
        ):
            producer._read_bounded(fifo, 64, "inventory")
        self.assertEqual(fifo.stat().st_size, 0)

    def test_ambient_cargo_configuration_is_found_through_home(self) -> None:
        (self.cargo_home / ".cargo").mkdir()
        (self.cargo_home / ".cargo/config.toml").write_text(
            '[build]\nrustc-wrapper = "/bin/false"\n', encoding="utf-8"
        )
        with mock.patch.dict(os.environ, {}, clear=False):
            del os.environ["CARGO_HOME"]
            with self.assertRaisesRegex(
                producer.CoverageProducerError, "ambient Cargo configuration"
            ):
                producer._reject_ambient_cargo_config(
                    producer._environment(), self.root, boundary=self.base
                )
            del os.environ["HOME"]
            with self.assertRaisesRegex(
                producer.CoverageProducerError,
                "cannot resolve the effective Cargo home",
            ):
                producer._reject_ambient_cargo_config(
                    producer._environment(), self.root, boundary=self.base
                )

    def test_inventory_outside_the_repository_is_rejected(self) -> None:
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        foreign = Path(outside.name).resolve() / "core-modules.json"
        foreign.write_text(
            self.inventory_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        with self.assertRaisesRegex(
            producer.CoverageProducerError, "inventory escapes repository root"
        ):
            producer._confined_inventory(self.root, foreign)
        self.assertEqual(
            producer._confined_inventory(self.root, None),
            self.root / "docs/validation/g6/core-modules.json",
        )
        argv = [
            "run_g6_coverage.py",
            "--repo-root",
            str(self.root),
            "--inventory",
            str(foreign),
            "--expected-commit",
            self.commit,
            "--output",
            "coverage/g6/foreign",
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(producer, "produce") as produce_call,
        ):
            self.assertEqual(producer.main(), 1)
        produce_call.assert_not_called()
        self.assertFalse((self.root / "coverage/g6/foreign").exists())


if __name__ == "__main__":
    unittest.main()
