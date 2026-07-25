import json
import shutil
import subprocess
import unittest
from pathlib import Path


ORACLE = Path(__file__).resolve().parents[1]
DAO_MODULE = ORACLE / "scripts" / "m1" / "M1.Dao.ps1"
MARSHALLING_RESULT_HASHES = {
    1: "6922e93e3827642ce4b883c756b31abf80036649d3614bf5fcb3adda43b8ea32",
    2047: "752b1981531a137b1105c2078561189a343d0a22c16e01107f705cafa8ce6eb7",
    2048: "9c9b3365a5704fb1bbd5dbac227ecc2e878dedce86338eca2ec1278e21ac1a9e",
    2049: "b6092f4b8c3649de2da7202fe2012e2a0c61233eacdbb4eab66e617a651b6475",
    32767: "599d891ff36779a917933540bf0e833e681d11bc468cc8da5e6bc68a5a80be40",
    32768: "e755c415eba1d77c6a3b6de6b486ae16f1a2270d794fc12a1773e18e1ff94b94",
    32769: "f30bb191a6fc82ae5c9eca26dc4904fb946f02ab7101e7c7acd0b7b4ab8b2199",
}


def powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipIf(powershell() is None, "PowerShell is unavailable")
class M1DaoAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = DAO_MODULE.read_text(encoding="utf-8")

    def run_script(self, body: str):
        command = (
            f". '{DAO_MODULE.as_posix()}'; "
            "$ErrorActionPreference='Stop'; "
            + body
        )
        completed = subprocess.run(
            [
                powershell(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_binary_marker_is_exact_byte_array(self) -> None:
        result = self.run_script(
            "$plan=[pscustomobject]@{field='marker';dao_type='dbBinary';"
            "encoding='lowercase_hex';value='0011223344556677'};"
            "$value=Get-M1DeclaredValue $plan;"
            "[ordered]@{type=$value.GetType().FullName;"
            "length=$value.Length;hex=(Get-M1LowerHex $value)}"
            "|ConvertTo-Json -Compress"
        )
        self.assertEqual(
            result,
            {
                "type": "System.Byte[]",
                "length": 8,
                "hex": "0011223344556677",
            },
        )

    def test_long_binary_boundaries_match_retained_experiment(self) -> None:
        rendered = ",".join(str(value) for value in MARSHALLING_RESULT_HASHES)
        result = self.run_script(
            f"$out=@();foreach($length in @({rendered})){{"
            "$value=New-M1RepeatedBytes -Length $length -Value 0xa5;"
            "$out+=[ordered]@{length=$value.Length;"
            "type=$value.GetType().FullName;"
            "sha256=(Get-M1ByteSha256 $value)}};"
            "ConvertTo-Json @($out) -Compress"
        )
        self.assertEqual([item["length"] for item in result], list(MARSHALLING_RESULT_HASHES))
        for item in result:
            self.assertEqual(item["type"], "System.Byte[]")
            self.assertEqual(
                item["sha256"], MARSHALLING_RESULT_HASHES[item["length"]]
            )

    def test_value_observation_rejects_readback_drift(self) -> None:
        command = (
            f". '{DAO_MODULE.as_posix()}'; "
            "$ErrorActionPreference='Stop';"
            "$plan=[pscustomobject]@{field='payload';dao_type='dbLongBinary';"
            "encoding='repeat_byte';length=2048;byte=165};"
            "$wrong=New-M1RepeatedBytes -Length 2048 -Value 0xa4;"
            "try { New-M1ValueObservation $plan $wrong 0 | Out-Null; exit 9 } "
            "catch { [Console]::WriteLine($_.Exception.Message); exit 0 }"
        )
        completed = subprocess.run(
            [powershell(), "-NoProfile", "-NonInteractive", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("readback differs", completed.stdout)

    def test_exact_marshalling_calls_are_source_locked(self) -> None:
        self.assertIn("$field.Value = $value", self.source)
        self.assertIn("$field.AppendChunk($value)", self.source)
        self.assertIn("$value.GetType() -ne [byte[]]", self.source)
        self.assertNotIn("dbBinary.AppendChunk", self.source)
        self.assertNotIn("$field.Value = (,$value)", self.source)

    def test_primary_hresult_survives_bounded_cleanup_failures(self) -> None:
        result = self.run_script(
            "$exception=New-Object Runtime.InteropServices.COMException("
            "'primary DAO failure',-2146825029);"
            "$primary=New-Object Management.Automation.ErrorRecord("
            "$exception,'PrimaryDaoFailure',"
            "[Management.Automation.ErrorCategory]::InvalidOperation,$null);"
            "$cleanup=New-Object Collections.ArrayList;"
            "[void]$cleanup.Add('recordset.Close: cleanup failure');"
            "$normalized=$null;"
            "try {"
            "Complete-M1DaoHelper -PrimaryError $primary "
            "-CleanupErrors $cleanup -Label 'fake adapter';"
            "} catch { $normalized=Get-M1ExceptionRecord -ErrorRecord $_ };"
            "$normalized|ConvertTo-Json -Depth 8 -Compress"
        )
        self.assertEqual(result["hresult"], "0x800A0CBB")
        self.assertEqual(result["message"], "primary DAO failure")
        self.assertEqual(
            result["cleanup_errors"],
            ["recordset.Close: cleanup failure"],
        )


if __name__ == "__main__":
    unittest.main()
