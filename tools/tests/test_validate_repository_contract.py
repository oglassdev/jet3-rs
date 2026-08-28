from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
REPOSITORY = TOOLS.parent
sys.path.insert(0, str(TOOLS))
import validate_repository_contract as contract  # noqa: E402
from validation import (  # noqa: E402
    repository_common,
    repository_fixture_external,
    repository_provenance,
    repository_shape,
    repository_workspace_dependency,
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ValidatorModuleBoundaryTests(unittest.TestCase):
    def test_cli_preserves_validation_function_api_by_reexport(self) -> None:
        self.assertIs(
            contract.validate_contract_shape,
            repository_shape.validate_contract_shape,
        )
        self.assertIs(
            contract.validate_workspace_and_sources,
            repository_workspace_dependency.validate_workspace_and_sources,
        )
        self.assertIs(
            contract.validate_dependency_graph,
            repository_workspace_dependency.validate_dependency_graph,
        )
        self.assertIs(
            contract.validate_format_knowledge,
            repository_provenance.validate_format_knowledge,
        )
        self.assertIs(
            contract.validate_repository_fixtures,
            repository_fixture_external.validate_repository_fixtures,
        )
        self.assertIs(
            contract.validate_seed_manifest,
            repository_fixture_external.validate_seed_manifest,
        )
        self.assertIs(
            contract.validate_external_observational_corpus,
            repository_fixture_external.validate_external_observational_corpus,
        )

    def test_repository_validation_modules_stay_below_800_lines(self) -> None:
        modules = (
            contract,
            repository_common,
            repository_fixture_external,
            repository_provenance,
            repository_shape,
            repository_workspace_dependency,
        )
        for module in modules:
            with self.subTest(module=module.__name__):
                path = Path(module.__file__)
                self.assertLess(
                    len(path.read_text(encoding="utf-8").splitlines()),
                    800,
                    f"{path} must be decomposed before reaching 800 lines",
                )


class ContractShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = json.loads(
            (REPOSITORY / contract.CONTRACT_PATH).read_text(encoding="utf-8")
        )

    def test_checked_contract_shape_is_valid(self) -> None:
        self.assertEqual(contract.validate_contract_shape(self.document), [])

    def test_checked_build_script_and_locked_external_closure_are_exact(self) -> None:
        workspace_errors, _ = contract.validate_workspace_and_sources(
            REPOSITORY, self.document
        )
        self.assertEqual(workspace_errors, [])
        metadata = repository_workspace_dependency.cargo_metadata(REPOSITORY)
        self.assertEqual(
            contract.validate_dependency_graph(REPOSITORY, self.document, metadata),
            [],
        )

    def test_unknown_key_and_changed_external_boundary_fail(self) -> None:
        changed = copy.deepcopy(self.document)
        changed["unexpected"] = True
        changed["fixtures"]["external_observational"]["regenerable"] = True
        errors = contract.validate_contract_shape(changed)
        self.assertTrue(any("unknown=['unexpected']" in error for error in errors))
        self.assertTrue(any("regenerable: must be false" in error for error in errors))

    def test_workspace_package_must_be_classified_once(self) -> None:
        changed = copy.deepcopy(self.document)
        duplicate = copy.deepcopy(changed["workspace_packages"]["production"][0])
        duplicate.pop("crate_root")
        changed["workspace_packages"]["support"].append(duplicate)
        errors = contract.validate_contract_shape(changed)
        self.assertTrue(any("duplicate package jet3" in error for error in errors))

    def test_runtime_allowlist_cannot_include_unclassified_dependency(self) -> None:
        changed = copy.deepcopy(self.document)
        changed["allowed_runtime_packages"].append("serde")
        errors = contract.validate_contract_shape(changed)
        self.assertTrue(any("exactly equal production" in error for error in errors))

    def test_assertion_file_requires_valid_provenance_id_and_hash(self) -> None:
        changed = copy.deepcopy(self.document)
        entry = changed["format_knowledge"]["assertion_files"][0]
        entry["provenance_ids"] = ["NOT-EVIDENCE"]
        entry["sha256"] = "bad"
        errors = contract.validate_contract_shape(changed)
        self.assertTrue(any("invalid provenance ID" in error for error in errors))
        self.assertTrue(any("invalid SHA-256" in error for error in errors))

    def test_reviewed_runtime_entries_are_closed_and_unique(self) -> None:
        changed = copy.deepcopy(self.document)
        changed["reviewed_build_scripts"].append(
            copy.deepcopy(changed["reviewed_build_scripts"][0])
        )
        changed["reviewed_external_runtime_packages"].append(
            copy.deepcopy(changed["reviewed_external_runtime_packages"][0])
        )
        changed["reviewed_external_runtime_packages"][0]["unexpected"] = True
        errors = contract.validate_contract_shape(changed)
        self.assertTrue(any("duplicate reviewed build script" in error for error in errors))
        self.assertTrue(
            any("duplicate reviewed external package" in error for error in errors)
        )
        self.assertTrue(any("unknown=['unexpected']" in error for error in errors))


class WorkspaceAndDependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.document = {
            "workspace_packages": {
                "production": [
                    {
                        "name": "jet3",
                        "manifest": "crates/jet3/Cargo.toml",
                        "crate_root": "crates/jet3/src/lib.rs",
                    },
                    {
                        "name": "jet3-cli",
                        "manifest": "crates/jet3-cli/Cargo.toml",
                        "crate_root": "crates/jet3-cli/src/main.rs",
                    },
                ],
                "support": [
                    {
                        "name": "jet3-testkit",
                        "manifest": "crates/jet3-testkit/Cargo.toml",
                    }
                ],
            },
            "allowed_runtime_packages": ["jet3", "jet3-cli"],
            "reviewed_build_scripts": [],
            "reviewed_external_runtime_packages": [],
        }
        (self.root / "Cargo.toml").write_text(
            '[workspace]\nmembers=["crates/jet3","crates/jet3-cli",'
            '"crates/jet3-testkit"]\n[workspace.lints.rust]\n'
            'unsafe_code="forbid"\n',
            encoding="utf-8",
        )
        for name in ("jet3", "jet3-cli", "jet3-testkit"):
            crate = self.root / "crates" / name
            (crate / "src").mkdir(parents=True)
            (crate / "Cargo.toml").write_text(
                f'[package]\nname="{name}"\nversion="0.0.0"\n'
                'edition="2024"\n[lints]\nworkspace=true\n',
                encoding="utf-8",
            )
        (self.root / "crates/jet3/src/lib.rs").write_text(
            "#![forbid(unsafe_code)]\npub fn safe() {}\n",
            encoding="utf-8",
        )
        (self.root / "crates/jet3-cli/src/main.rs").write_text(
            "#![forbid(unsafe_code)]\nfn main() {}\n",
            encoding="utf-8",
        )

    def metadata(self) -> dict:
        jet3_manifest = str((self.root / "crates/jet3/Cargo.toml").resolve())
        cli_manifest = str((self.root / "crates/jet3-cli/Cargo.toml").resolve())
        return {
            "packages": [
                {
                    "id": "path+jet3",
                    "name": "jet3",
                    "manifest_path": jet3_manifest,
                    "source": None,
                    "links": None,
                    "targets": [{"kind": ["lib"]}],
                },
                {
                    "id": "path+jet3-cli",
                    "name": "jet3-cli",
                    "manifest_path": cli_manifest,
                    "source": None,
                    "links": None,
                    "targets": [{"kind": ["bin"]}],
                },
                {
                    "id": "registry+proptest",
                    "name": "proptest",
                    "version": "1.0.0",
                    "manifest_path": "/registry/proptest/Cargo.toml",
                    "source": "registry+https://github.com/rust-lang/crates.io-index",
                    "links": None,
                    "targets": [{"kind": ["lib"]}],
                },
            ],
            "resolve": {
                "nodes": [
                    {
                        "id": "path+jet3",
                        "deps": [
                            {
                                "pkg": "registry+proptest",
                                "dep_kinds": [{"kind": "dev", "target": None}],
                            }
                        ],
                    },
                    {
                        "id": "path+jet3-cli",
                        "deps": [
                            {
                                "pkg": "path+jet3",
                                "dep_kinds": [{"kind": None, "target": None}],
                            }
                        ],
                    },
                    {"id": "registry+proptest", "deps": []},
                ]
            },
        }

    def approve_cli_build_script(self) -> bytes:
        script = b"fn main() {}\n"
        path = self.root / "crates/jet3-cli/build.rs"
        path.write_bytes(script)
        self.document["reviewed_build_scripts"] = [
            {
                "package": "jet3-cli",
                "manifest": "crates/jet3-cli/Cargo.toml",
                "path": "crates/jet3-cli/build.rs",
                "sha256": digest(script),
                "purpose": "build identity only",
            }
        ]
        return script

    def rustix_metadata(self) -> dict:
        metadata = self.metadata()
        source = "registry+https://github.com/rust-lang/crates.io-index"
        checksum = "1" * 64
        metadata["packages"].append(
            {
                "id": "registry+rustix",
                "name": "rustix",
                "version": "1.1.4",
                "manifest_path": "/registry/rustix/Cargo.toml",
                "source": source,
                "checksum": checksum,
                "links": None,
                "targets": [
                    {"kind": ["lib"]},
                    {"kind": ["custom-build"], "src_path": "/registry/rustix/build.rs"},
                ],
            }
        )
        metadata["resolve"]["nodes"].append(
            {"id": "registry+rustix", "deps": []}
        )
        metadata["resolve"]["nodes"][1]["deps"].append(
            {
                "pkg": "registry+rustix",
                "dep_kinds": [{"kind": None, "target": "cfg(unix)"}],
            }
        )
        self.document["reviewed_external_runtime_packages"] = [
            {
                "name": "rustix",
                "version": "1.1.4",
                "source": source,
                "checksum": checksum,
                "allow_custom_build": True,
                "purpose": "atomic publication only",
            }
        ]
        (self.root / "Cargo.lock").write_text(
            'version = 4\n\n[[package]]\nname = "rustix"\nversion = "1.1.4"\n'
            f'source = "{source}"\nchecksum = "{checksum}"\n',
            encoding="utf-8",
        )
        return metadata

    def test_safe_workspace_and_dev_only_dependency_pass(self) -> None:
        errors, _ = contract.validate_workspace_and_sources(self.root, self.document)
        self.assertEqual(errors, [])
        self.assertEqual(
            contract.validate_dependency_graph(
                self.root, self.document, self.metadata()
            ),
            [],
        )

    def test_missing_crate_forbid_and_external_command_fail(self) -> None:
        crate_root = self.root / "crates/jet3/src/lib.rs"
        crate_root.write_text(
            "use std::process::Command;\npub fn run() { let _ = Command::new(\"java\"); }\n",
            encoding="utf-8",
        )
        errors, _ = contract.validate_workspace_and_sources(self.root, self.document)
        self.assertTrue(any("missing #![forbid" in error for error in errors))
        self.assertTrue(any("external runtime programs" in error for error in errors))

    def test_build_script_and_custom_build_target_fail(self) -> None:
        (self.root / "crates/jet3/build.rs").write_text(
            "fn main() {}\n", encoding="utf-8"
        )
        errors, _ = contract.validate_workspace_and_sources(self.root, self.document)
        self.assertTrue(any("unreviewed" in error for error in errors))

        metadata = self.metadata()
        metadata["packages"][0]["targets"].append({"kind": ["custom-build"]})
        errors = contract.validate_dependency_graph(
            self.root, self.document, metadata
        )
        self.assertTrue(any("custom-build target is forbidden" in error for error in errors))

    def test_reviewed_build_script_passes_and_byte_or_extra_script_fails(self) -> None:
        script = self.approve_cli_build_script()
        metadata = self.metadata()
        metadata["packages"][1]["targets"].append(
            {
                "kind": ["custom-build"],
                "src_path": str((self.root / "crates/jet3-cli/build.rs").resolve()),
            }
        )
        self.assertEqual(
            contract.validate_workspace_and_sources(self.root, self.document)[0], []
        )
        self.assertEqual(
            contract.validate_dependency_graph(self.root, self.document, metadata), []
        )

        (self.root / "crates/jet3-cli/build.rs").write_bytes(script + b"// changed\n")
        (self.root / "crates/jet3/build.rs").write_text(
            "fn main() {}\n", encoding="utf-8"
        )
        errors, _ = contract.validate_workspace_and_sources(self.root, self.document)
        self.assertTrue(any("SHA-256 mismatch" in error for error in errors))
        self.assertTrue(any("unreviewed" in error for error in errors))

    def test_unclassified_workspace_member_fails(self) -> None:
        cargo = self.root / "Cargo.toml"
        cargo.write_text(
            cargo.read_text(encoding="utf-8").replace(
                '"crates/jet3-testkit"]',
                '"crates/jet3-testkit","crates/unknown"]',
            ),
            encoding="utf-8",
        )
        errors, _ = contract.validate_workspace_and_sources(self.root, self.document)
        self.assertTrue(any("classification mismatch" in error for error in errors))

    def test_normal_registry_dependency_fails_but_dev_dependency_does_not(self) -> None:
        metadata = self.metadata()
        metadata["resolve"]["nodes"][0]["deps"][0]["dep_kinds"][0]["kind"] = None
        errors = contract.validate_dependency_graph(self.root, self.document, metadata)
        self.assertTrue(any("not exactly reviewed: proptest" in error for error in errors))

    def test_exact_external_identity_and_custom_build_permission(self) -> None:
        metadata = self.rustix_metadata()
        self.assertEqual(
            contract.validate_dependency_graph(self.root, self.document, metadata), []
        )

        changed = copy.deepcopy(metadata)
        changed["packages"][-1]["version"] = "1.1.5"
        errors = contract.validate_dependency_graph(self.root, self.document, changed)
        self.assertTrue(any("identity drift: rustix" in error for error in errors))

        changed = copy.deepcopy(metadata)
        changed["packages"][-1]["source"] = "registry+https://example.invalid/index"
        errors = contract.validate_dependency_graph(self.root, self.document, changed)
        self.assertTrue(any("identity drift: rustix" in error for error in errors))

        changed = copy.deepcopy(metadata)
        changed["packages"][-1]["checksum"] = "2" * 64
        errors = contract.validate_dependency_graph(self.root, self.document, changed)
        self.assertTrue(any("metadata checksum mismatch" in error for error in errors))

        changed = copy.deepcopy(metadata)
        changed["packages"][-1]["links"] = "native_rustix"
        duplicate = copy.deepcopy(changed["packages"][-1])
        duplicate["id"] = "registry+rustix-duplicate"
        changed["packages"].append(duplicate)
        changed["resolve"]["nodes"].append(
            {"id": "registry+rustix-duplicate", "deps": []}
        )
        changed["resolve"]["nodes"][1]["deps"].append(
            {
                "pkg": "registry+rustix-duplicate",
                "dep_kinds": [{"kind": None, "target": "cfg(unix)"}],
            }
        )
        errors = contract.validate_dependency_graph(self.root, self.document, changed)
        self.assertTrue(any("native-linked" in error for error in errors))
        self.assertTrue(any("duplicate external package identity" in error for error in errors))

        self.document["reviewed_external_runtime_packages"][0][
            "allow_custom_build"
        ] = False
        errors = contract.validate_dependency_graph(self.root, self.document, metadata)
        self.assertTrue(any("custom-build target is not permitted" in error for error in errors))

    def test_external_lock_checksum_and_stale_custom_permission_fail(self) -> None:
        metadata = self.rustix_metadata()
        lock = self.root / "Cargo.lock"
        lock.write_text(
            lock.read_text(encoding="utf-8").replace("1" * 64, "2" * 64),
            encoding="utf-8",
        )
        errors = contract.validate_dependency_graph(self.root, self.document, metadata)
        self.assertTrue(any("Cargo.lock checksum mismatch" in error for error in errors))

        self.rustix_metadata()
        metadata["packages"][-1]["targets"] = [{"kind": ["lib"]}]
        errors = contract.validate_dependency_graph(self.root, self.document, metadata)
        self.assertTrue(any("custom-build permission is stale" in error for error in errors))

    def test_native_links_and_prohibited_package_fail(self) -> None:
        metadata = self.metadata()
        metadata["packages"][0]["links"] = "native_jet"
        metadata["packages"].append(
            {
                "id": "registry+jni",
                "name": "jni",
                "version": "1.0.0",
                "manifest_path": "/registry/jni/Cargo.toml",
                "source": "registry+crates.io",
                "links": None,
                "targets": [{"kind": ["lib"]}],
            }
        )
        metadata["resolve"]["nodes"].append({"id": "registry+jni", "deps": []})
        metadata["resolve"]["nodes"][0]["deps"].append(
            {
                "pkg": "registry+jni",
                "dep_kinds": [{"kind": "build", "target": None}],
            }
        )
        errors = contract.validate_dependency_graph(self.root, self.document, metadata)
        self.assertTrue(any("native-linked" in error for error in errors))
        self.assertTrue(any("prohibited runtime dependency package: jni" in error for error in errors))




class FullRepositoryTests(unittest.TestCase):
    def test_support_matrix_failure_is_propagated(self) -> None:
        metadata = contract._cargo_metadata(REPOSITORY)
        tracked = contract._tracked_files(REPOSITORY)
        with mock.patch.object(
            contract,
            "validate_support_matrix",
            return_value=["injected support mismatch"],
        ):
            errors = contract.validate_repository(
                REPOSITORY,
                metadata=metadata,
                tracked=tracked,
            )
        self.assertTrue(any("injected support mismatch" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
