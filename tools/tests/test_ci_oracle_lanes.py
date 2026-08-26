"""Static coverage contracts for the pull-request oracle CI lanes."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class CiOracleLaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_superseded_pull_request_runs_cancel_without_cancelling_main(self) -> None:
        self.assertIn(
            "group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
            self.workflow,
        )
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            self.workflow,
        )

    def test_portable_contracts_run_once_across_four_shards(self) -> None:
        self.assertIn("shard: [0, 1, 2, 3]", self.workflow)
        self.assertIn(
            "portable-shard --shard-index ${{ matrix.shard }} --shard-count 4",
            self.workflow,
        )
        self.assertNotIn(
            "python3 -m unittest discover -s oracle/windows-dao/tests -v",
            self.workflow,
        )

    def test_windows_pr_is_focused_and_main_retains_complete_replay(self) -> None:
        self.assertIn(
            "python oracle/windows-dao/scripts/run_contract_tests.py windows-pr",
            self.workflow,
        )
        self.assertEqual(
            self.workflow.count(
                "python -m unittest discover -s oracle/windows-dao/tests -v"
            ),
            1,
        )
        self.assertIn("if: github.event_name == 'push'", self.workflow)


if __name__ == "__main__":
    unittest.main()
