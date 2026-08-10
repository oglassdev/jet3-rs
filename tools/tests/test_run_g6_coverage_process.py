from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import run_g6_coverage as producer  # noqa: E402


class G6CoverageProcessTests(unittest.TestCase):
    """The bounded process layer: no commit-bound checkout is involved."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
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

    def test_wrong_tool_version_is_rejected_verbatim(self) -> None:
        result = producer.ProcessResult(b"cargo-llvm-cov 0.8.5\n", b"")
        with mock.patch.object(
            producer, "_bounded_process", return_value=result
        ):
            with self.assertRaisesRegex(
                producer.CoverageProducerError, "expected 'cargo-llvm-cov 0.8.6'"
            ):
                producer._tool_version(self.root)

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


if __name__ == "__main__":
    unittest.main()
