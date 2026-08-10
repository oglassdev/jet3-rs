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

import ci_evidence as evidence  # noqa: E402


class CiEvidenceTests(unittest.TestCase):
    commit = "a" * 40
    hosts = {
        "linux": "x86_64-unknown-linux-gnu",
        "macos": "aarch64-apple-darwin",
        "windows": "x86_64-pc-windows-msvc",
    }

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inputs = self.root / "inputs"
        for platform_name in evidence.PLATFORMS:
            self._write_platform(self.inputs / platform_name, platform_name)

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.write_text(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

    def _write_platform(self, directory: Path, platform_name: str) -> None:
        directory.mkdir(parents=True)
        commands = []
        for command_id, argv in evidence.COMMANDS:
            content = f"{platform_name} {command_id} passed\n".encode()
            relative = f"logs/{command_id}.log"
            log = directory / relative
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_bytes(content)
            commands.append(
                {
                    "id": command_id,
                    "argv": list(argv),
                    "environment": (
                        {"RUSTDOCFLAGS": "-D warnings -D missing-docs"}
                        if command_id == "public-docs"
                        else {}
                    ),
                    "exit_code": 0,
                    "log": relative,
                    "log_sha256": hashlib.sha256(content).hexdigest(),
                }
            )
        record = {
            "schema_version": evidence.SCHEMA_VERSION,
            "commit": self.commit,
            "dirty": False,
            "platform": platform_name,
            "toolchain": {
                "channel": evidence.TOOLCHAIN,
                "release": evidence.TOOLCHAIN,
                "commit_hash": evidence.RUST_COMMIT,
                "host": self.hosts[platform_name],
                "llvm_version": "21.1.8",
            },
            "commands": commands,
            "success": True,
        }
        self._write_json(directory / "platform-record.json", record)

    def _aggregate(self) -> Path:
        bundle = self.root / "bundle"
        evidence.aggregate(self.inputs, bundle, self.commit)
        return bundle

    def _rewrite_aggregate_record_hash(self, bundle: Path, platform_name: str) -> None:
        manifest_path = bundle / "aggregate.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["records"]:
            if entry["platform"] == platform_name:
                entry["record_sha256"] = evidence._sha256_file(  # noqa: SLF001
                    bundle / entry["record"]
                )
        self._write_json(manifest_path, manifest)

    def test_valid_bundle_verifies(self) -> None:
        bundle = self._aggregate()
        evidence.verify_aggregate(bundle, self.commit)

    def test_each_platform_label_must_match_rustc_host(self) -> None:
        wrong_hosts = {
            "linux": self.hosts["windows"],
            "macos": self.hosts["linux"],
            "windows": self.hosts["macos"],
        }
        for platform_name, wrong_host in wrong_hosts.items():
            with self.subTest(platform=platform_name):
                path = self.inputs / platform_name / "platform-record.json"
                record = json.loads(path.read_text(encoding="utf-8"))
                record["toolchain"]["host"] = wrong_host
                self._write_json(path, record)
                with self.assertRaisesRegex(
                    evidence.EvidenceError, "does not match platform"
                ):
                    evidence.validate_platform_record(path, self.commit)
                record["toolchain"]["host"] = self.hosts[platform_name]
                self._write_json(path, record)

    def test_source_size_recurses_only_through_contract_production_crates(self) -> None:
        repo = self.root / "source-repo"
        nested = repo / "crates/product/src/nested/large.rs"
        support = repo / "crates/support/src/large.rs"
        nested.parent.mkdir(parents=True)
        support.parent.mkdir(parents=True)
        nested.write_text("line\n" * 801, encoding="utf-8")
        support.write_text("line\n" * 900, encoding="utf-8")
        contract = {
            "workspace_packages": {
                "production": [
                    {
                        "crate_root": "crates/product/src/lib.rs",
                        "manifest": "crates/product/Cargo.toml",
                        "name": "product",
                    }
                ]
            }
        }
        contract_path = repo / "docs/validation/repository-contract.json"
        contract_path.parent.mkdir(parents=True)
        self._write_json(contract_path, contract)
        exit_code, output = evidence._source_size_check(repo)  # noqa: SLF001
        self.assertEqual(exit_code, 1)
        self.assertIn("crates/product/src/nested/large.rs: 801 lines", output)
        self.assertNotIn("crates/support", output)

    def test_command_inventory_has_no_vacuous_malformed_name_filter(self) -> None:
        commands = dict(evidence.COMMANDS)
        self.assertIn("test-inventory-reconciliation", commands)
        for argv in commands.values():
            self.assertNotEqual(argv[-1], "malformed")

    def test_aggregate_rejects_stale_missing_and_duplicate_records(self) -> None:
        stale = self.inputs / "linux/platform-record.json"
        record = json.loads(stale.read_text(encoding="utf-8"))
        record["commit"] = "b" * 40
        self._write_json(stale, record)
        with self.assertRaisesRegex(evidence.EvidenceError, "stale commit"):
            evidence.aggregate(self.inputs, self.root / "stale", self.commit)

        self.setUp()
        (self.inputs / "windows/platform-record.json").unlink()
        with self.assertRaisesRegex(evidence.EvidenceError, "missing required"):
            evidence.aggregate(self.inputs, self.root / "missing", self.commit)

        self.setUp()
        duplicate = self.inputs / "extra-linux"
        self._write_platform(duplicate, "linux")
        with self.assertRaisesRegex(evidence.EvidenceError, "duplicate platform"):
            evidence.aggregate(self.inputs, self.root / "duplicate", self.commit)

    def test_mutations_fail_closed(self) -> None:
        mutations = (
            ("log hash tampering", self._tamper_log, "log hash mismatch"),
            ("dirty run", self._mark_dirty, "dirty CI runs"),
            ("command drift", self._drift_command, "command inventory drift"),
            ("incomplete commands", self._remove_command, "incomplete command"),
            ("duplicate platform", self._duplicate_platform, "duplicate aggregate"),
            ("absent platform", self._remove_platform, "incomplete platform"),
            ("stale aggregate", self._stale_aggregate, "stale aggregate"),
            ("record hash tampering", self._tamper_record, "record hash mismatch"),
        )
        for label, mutate, message in mutations:
            with self.subTest(label=label):
                case_root = self.root / label.replace(" ", "-")
                case_inputs = case_root / "inputs"
                for platform_name in evidence.PLATFORMS:
                    self._write_platform(case_inputs / platform_name, platform_name)
                bundle = case_root / "bundle"
                evidence.aggregate(case_inputs, bundle, self.commit)
                mutate(bundle)
                with self.assertRaisesRegex(evidence.EvidenceError, message):
                    evidence.verify_aggregate(bundle, self.commit)

    def _mutate_record(
        self, bundle: Path, transform: callable, *, refresh_hash: bool = True
    ) -> None:
        path = bundle / "linux/platform-record.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        transform(record)
        self._write_json(path, record)
        if refresh_hash:
            self._rewrite_aggregate_record_hash(bundle, "linux")

    def _tamper_log(self, bundle: Path) -> None:
        (bundle / "linux/logs/tests.log").write_text("modified\n", encoding="utf-8")

    def _mark_dirty(self, bundle: Path) -> None:
        self._mutate_record(bundle, lambda record: record.__setitem__("dirty", True))

    def _drift_command(self, bundle: Path) -> None:
        self._mutate_record(
            bundle,
            lambda record: record["commands"][0]["argv"].append("--drift"),
        )

    def _remove_command(self, bundle: Path) -> None:
        self._mutate_record(bundle, lambda record: record["commands"].pop())

    def _duplicate_platform(self, bundle: Path) -> None:
        path = bundle / "aggregate.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["records"][2] = copy.deepcopy(manifest["records"][0])
        self._write_json(path, manifest)

    def _remove_platform(self, bundle: Path) -> None:
        path = bundle / "aggregate.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["records"].pop()
        self._write_json(path, manifest)

    def _stale_aggregate(self, bundle: Path) -> None:
        path = bundle / "aggregate.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["commit"] = "b" * 40
        self._write_json(path, manifest)

    def _tamper_record(self, bundle: Path) -> None:
        path = bundle / "linux/platform-record.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
