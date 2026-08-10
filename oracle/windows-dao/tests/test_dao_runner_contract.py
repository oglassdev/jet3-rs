from pathlib import Path
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RUNNER = (
    REPOSITORY_ROOT
    / "oracle"
    / "windows-dao"
    / "scripts"
    / "run-dao-gen-probe.ps1"
)


class DaoRunnerContractTests(unittest.TestCase):
    def test_system_attribute_is_treated_as_a_bitmask(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")

        self.assertIn(
            "($attributes -band $DbSystemObject) -ne 0",
            source,
        )
        self.assertNotIn(
            "($attributes -band $DbSystemObject) -eq $DbSystemObject",
            source,
        )


if __name__ == "__main__":
    unittest.main()
