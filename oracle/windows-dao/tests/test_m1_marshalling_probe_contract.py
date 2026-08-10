from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "experiments" / "m1-marshalling-probe.ps1"


class M1MarshallingProbeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PROBE.read_text(encoding="utf-8")

    def test_probe_is_clean_commit_and_provider_bound(self) -> None:
        self.assertIn("status --porcelain", self.source)
        self.assertIn("rev-parse HEAD", self.source)
        self.assertIn("Get-LowerSha256 -Path $provider.server_path", self.source)
        self.assertIn("environment_sha256", self.source)

    def test_probe_covers_exact_binary_cases_and_ladder(self) -> None:
        self.assertIn(
            '@("value", "append_chunk", "value_unary_comma")',
            self.source,
        )
        self.assertIn(
            "@(1, 2047, 2048, 2049, 32767, 32768, 32769)",
            self.source,
        )
        self.assertIn("return ,$bytes", self.source)
        self.assertIn("$target.AppendChunk($bytes)", self.source)

    def test_probe_cannot_publish_protocol_evidence(self) -> None:
        self.assertNotIn("bundle-manifest.json", self.source)
        self.assertNotIn("dao_evidence_report", self.source)
        self.assertIn("not protocol", self.source)


if __name__ == "__main__":
    unittest.main()
