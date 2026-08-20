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

    def test_unknown_key_and_changed_external_boundary_fail(self) -> None:
        changed = copy.deepcopy(self.document)
        changed["unexpected"] = True
        changed["fixtures"]["external_observational"]["regenerable"] = True
        errors = contract.validate_contract_shape(changed)
        self.assertTrue(any("unknown=['unexpected']" in error for error in errors))
        self.assertTrue(any("regenerable: must be false" in error for error in errors))

    def test_workspace_package_must_be_classified_once(self) -> None:
        changed = copy.deepcopy(self.document)
        changed["workspace_packages"]["support"][0]["name"] = "jet3"
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
        self.assertTrue(any("build.rs is forbidden" in error for error in errors))

        metadata = self.metadata()
        metadata["packages"][0]["targets"].append({"kind": ["custom-build"]})
        errors = contract.validate_dependency_graph(
            self.root, self.document, metadata
        )
        self.assertTrue(any("custom-build target is forbidden" in error for error in errors))

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
        self.assertTrue(any("not allow-listed: proptest" in error for error in errors))
        self.assertTrue(any("reviewed workspace package: proptest" in error for error in errors))

    def test_native_links_and_prohibited_package_fail(self) -> None:
        metadata = self.metadata()
        metadata["packages"][0]["links"] = "native_jet"
        metadata["packages"].append(
            {
                "id": "registry+jni",
                "name": "jni",
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


class FormatKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        assertion = b"//! Jet assertion from SRC-0001.\nconst PAGE: usize = 1;\n"
        reviewed = b"//! Access scope text only.\n"
        (self.root / "src").mkdir()
        (self.root / "src/assertion.rs").write_bytes(assertion)
        (self.root / "src/reviewed.rs").write_bytes(reviewed)
        self.document = {
            "format_knowledge": {
                "assertion_files": [
                    {
                        "path": "src/assertion.rs",
                        "sha256": digest(assertion),
                        "provenance_ids": ["SRC-0001"],
                    }
                ],
                "reviewed_non_assertion_files": [
                    {
                        "path": "src/reviewed.rs",
                        "sha256": digest(reviewed),
                        "reason": "scope only",
                    }
                ],
            }
        }
        self.sources = {"src/assertion.rs", "src/reviewed.rs"}
        self.provenance = "### SRC-0001 — source\n\n- Origin: test\n"

    def test_hash_bound_inventory_and_existing_provenance_pass(self) -> None:
        self.assertEqual(
            contract.validate_format_knowledge(
                self.root, self.document, self.sources, self.provenance
            ),
            [],
        )

    def test_source_mutation_and_unknown_provenance_fail(self) -> None:
        (self.root / "src/assertion.rs").write_text(
            "//! Jet changed.\nconst PAGE: usize = 2;\n",
            encoding="utf-8",
        )
        changed = copy.deepcopy(self.document)
        changed["format_knowledge"]["assertion_files"][0]["provenance_ids"] = [
            "SRC-9999"
        ]
        errors = contract.validate_format_knowledge(
            self.root, changed, self.sources, self.provenance
        )
        self.assertTrue(any("SHA-256 mismatch" in error for error in errors))
        self.assertTrue(any("unknown provenance ID SRC-9999" in error for error in errors))

    def test_every_new_source_file_must_be_classified(self) -> None:
        (self.root / "src/new.rs").write_text("pub const VALUE: u16 = 42;\n", encoding="utf-8")
        errors = contract.validate_format_knowledge(
            self.root,
            self.document,
            {*self.sources, "src/new.rs"},
            self.provenance,
        )
        self.assertTrue(any("missing=['src/new.rs']" in error for error in errors))

    def test_assertion_provenance_id_must_appear_in_source(self) -> None:
        assertion = self.root / "src/assertion.rs"
        changed_bytes = assertion.read_bytes().replace(b"SRC-0001", b"source-id")
        assertion.write_bytes(changed_bytes)
        changed = copy.deepcopy(self.document)
        changed["format_knowledge"]["assertion_files"][0]["sha256"] = digest(
            changed_bytes
        )
        errors = contract.validate_format_knowledge(
            self.root, changed, self.sources, self.provenance
        )
        self.assertTrue(any("is absent from source" in error for error in errors))


class SourceUsageLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()

    def validate(
        self, provenance: str, tracked: set[str] | None = None
    ) -> list[str]:
        return repository_provenance.validate_source_usage_ledger(
            self.root,
            {"src/module.rs"} if tracked is None else tracked,
            provenance,
        )

    def test_exact_file_and_directory_usage_cover_citations(self) -> None:
        (self.root / "src/module.rs").write_text(
            "// Evidence: SRC-0001 and SRC-0002.\n", encoding="utf-8"
        )
        provenance = (
            "### SRC-0001 — one\n\n"
            "- Usage: `file:src/module.rs`\n- Rights: test\n\n"
            "### SRC-0002 — two\n\n"
            "- Usage: `dir:src/`\n- Rights: test\n"
        )
        self.assertEqual(self.validate(provenance), [])

    def test_missing_usage_path_fails(self) -> None:
        (self.root / "src/module.rs").write_text(
            "// Evidence: SRC-0001.\n", encoding="utf-8"
        )
        errors = self.validate(
            "### SRC-0001 — one\n\n- Usage: contextual only\n- Rights: test\n"
        )
        self.assertEqual(
            errors, ["SRC-0001: Usage does not cover citing path src/module.rs"]
        )

    def test_narrative_backticks_are_not_path_declarations(self) -> None:
        (self.root / "src/module.rs").write_text(
            "// Evidence: SRC-0001.\n", encoding="utf-8"
        )
        errors = self.validate(
            "### SRC-0001 — one\n\n"
            "- Usage: compare `src/module.rs` with `method/option`\n"
            "- Rights: test\n"
        )
        self.assertEqual(
            errors, ["SRC-0001: Usage does not cover citing path src/module.rs"]
        )

    def test_declared_file_and_directory_must_cover_a_citation(self) -> None:
        (self.root / "src/module.rs").write_text(
            "// Evidence: SRC-0001.\n", encoding="utf-8"
        )
        (self.root / "src/other.rs").write_text("// No citation.\n", encoding="utf-8")
        (self.root / "src/other").mkdir()
        (self.root / "src/other/note.md").write_text(
            "No citation.\n", encoding="utf-8"
        )
        provenance = (
            "### SRC-0001 — one\n\n"
            "- Usage: `file:src/module.rs`; `file:src/other.rs`; `dir:src/other/`\n"
            "- Rights: test\n"
        )
        errors = self.validate(
            provenance,
            {"src/module.rs", "src/other.rs", "src/other/note.md"},
        )
        self.assertEqual(
            errors,
            [
                "SRC-0001: Usage file has no matching citation `src/other.rs`",
                "SRC-0001: Usage dir has no matching citation `src/other/`",
            ],
        )

    def test_usage_declarations_are_canonical_and_tracked(self) -> None:
        (self.root / "src/module.rs").write_text(
            "// Evidence: SRC-0001.\n", encoding="utf-8"
        )
        provenance = (
            "### SRC-0001 — one\n\n"
            "- Usage: `file:./src/module.rs`; `file:src/missing.rs`; "
            "`dir:src`; `file:src/module.rs`; `file:src/module.rs`\n"
            "- Rights: test\n"
        )
        errors = self.validate(provenance)
        self.assertEqual(
            errors,
            [
                "SRC-0001: invalid repository-relative Usage declaration "
                "`file:./src/module.rs`",
                "SRC-0001: Usage file is not tracked `src/missing.rs`",
                "SRC-0001: Usage directory must end with `/` `src`",
                "SRC-0001: duplicate Usage declaration `file:src/module.rs`",
            ],
        )

    def test_unknown_source_id_fails(self) -> None:
        (self.root / "src/module.rs").write_text(
            "// Evidence: SRC-9999.\n", encoding="utf-8"
        )
        errors = self.validate(
            "### SRC-0001 — one\n\n"
            "- Usage: contextual only\n- Rights: test\n"
        )
        self.assertEqual(
            errors, ["src/module.rs: unknown source provenance ID SRC-9999"]
        )


class FixtureInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        path = self.root / "fixtures/malformed/bad.bin"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"bad")
        self.fixture = {
            "id": "FIX-1000",
            "scenario_id": "CORR-BAD-001",
            "provenance_id": "FIX-1000",
            "path": "fixtures/malformed/bad.bin",
            "sha256": digest(b"bad"),
            "origin": "project-authored",
            "generator": "literal bytes",
            "environment": "platform-independent",
            "license": "MIT OR Apache-2.0",
            "reproduction_command": "fixture generator command",
        }
        self.manifest = {"schema_version": 1, "fixtures": [self.fixture]}
        self.tracked = {"fixtures/malformed/bad.bin"}
        self.provenance = {"FIX-1000": "### FIX-1000\n"}
        self.tests = {
            "cases": [
                {
                    "id": "CORR-BAD-001",
                    "fixtures": [
                        {
                            "path": self.fixture["path"],
                            "sha256": self.fixture["sha256"],
                        }
                    ],
                }
            ]
        }

    def validate(self, manifest: dict | None = None, tracked: set[str] | None = None) -> list[str]:
        return contract.validate_repository_fixtures(
            self.root,
            manifest or self.manifest,
            tracked or self.tracked,
            self.provenance,
            self.tests,
        )

    def test_complete_repository_fixture_passes(self) -> None:
        self.assertEqual(self.validate(), [])

    def test_hash_metadata_and_inventory_mutations_fail(self) -> None:
        changed = copy.deepcopy(self.manifest)
        changed["fixtures"][0]["sha256"] = "0" * 64
        changed["fixtures"][0]["license"] = ""
        errors = self.validate(changed)
        self.assertTrue(any("hash mismatch" in error for error in errors))
        self.assertTrue(any(".license: expected non-empty" in error for error in errors))
        missing = self.validate({"schema_version": 1, "fixtures": []})
        self.assertTrue(any("inventory mismatch" in error for error in missing))


class SeedAndExternalCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        seed_path = self.root / "fuzz/corpus/example/seed"
        seed_path.parent.mkdir(parents=True)
        seed_path.write_bytes(b"seed")
        self.seed = {
            "id": "FUZZ-SEED-001",
            "path": "fuzz/corpus/example/seed",
            "size_bytes": 4,
            "sha256": digest(b"seed"),
            "purpose": "bounded scenario",
            "origin": "project-authored",
            "generator": "literal",
            "environment": {"os": "all"},
            "rights": "MIT OR Apache-2.0",
            "reproduction_command": "seed generator command",
        }
        self.seed_manifest = {
            "schema_version": 1,
            "protocol_version": 1,
            "seeds": [self.seed],
        }
        self.external = {
            "schema_version": 2,
            "environment_variable": "JET3_EXTERNAL_FIXTURE_ROOT",
            "purpose": "nonredistributable-read-only-corpus-verification",
            "fixtures": [
                {
                    "id": "FIX-0001",
                    "path": "outside/example.mdb",
                    "size_bytes": 2048,
                    "sha256": "1" * 64,
                }
            ],
            "comparisons": [],
        }
        self.external_policy = {
            "redistributable": False,
            "regenerable": False,
            "acceptance_fixture": False,
        }
        self.documentation = (
            "optional exploratory inputs; not distributed fixtures; "
            "not acceptance evidence; Do not commit"
        )
        self.provenance = {
            "FIX-0001": (
                "### FIX-0001\n- Origin: donor\n- Environment: unknown\n"
                "- Protocol: opt-in verification\n- Rights: not redistributable\n"
                "outside/example.mdb\n" + "1" * 64
            )
        }

    def test_complete_seed_and_external_observation_pass(self) -> None:
        self.assertEqual(
            contract.validate_seed_manifest(
                self.root,
                self.seed_manifest,
                {"fuzz/corpus/example/seed"},
            ),
            [],
        )
        self.assertEqual(
            contract.validate_external_observational_corpus(
                self.external,
                self.documentation,
                self.provenance,
                self.external_policy,
            ),
            [],
        )

    def test_seed_hash_rights_environment_and_inventory_mutations_fail(self) -> None:
        changed = copy.deepcopy(self.seed_manifest)
        changed["seeds"][0]["sha256"] = "0" * 64
        changed["seeds"][0]["rights"] = ""
        changed["seeds"][0]["environment"] = {}
        errors = contract.validate_seed_manifest(
            self.root,
            changed,
            {"fuzz/corpus/example/seed", "fuzz/corpus/example/unmanifested"},
        )
        self.assertTrue(any("hash mismatch" in error for error in errors))
        self.assertTrue(any(".rights: expected non-empty" in error for error in errors))
        self.assertTrue(any(".environment: expected non-empty" in error for error in errors))
        self.assertTrue(any("seed inventory mismatch" in error for error in errors))

    def test_external_observation_cannot_be_regenerable_or_lose_rights(self) -> None:
        policy = copy.deepcopy(self.external_policy)
        policy["regenerable"] = True
        provenance = copy.deepcopy(self.provenance)
        provenance["FIX-0001"] = provenance["FIX-0001"].replace(
            "- Rights: not redistributable\n", ""
        )
        errors = contract.validate_external_observational_corpus(
            self.external,
            self.documentation,
            provenance,
            policy,
        )
        self.assertTrue(any("regenerable must be false" in error for error in errors))
        self.assertTrue(any("lacks - Rights:" in error for error in errors))

    def test_external_comparison_references_must_be_distinct_and_known(self) -> None:
        changed = copy.deepcopy(self.external)
        changed["comparisons"] = [
            {
                "id": "CMP-0001",
                "left_fixture_id": "FIX-0001",
                "right_fixture_id": "FIX-0001",
                "page_size_bytes": 2048,
            }
        ]
        errors = contract.validate_external_observational_corpus(
            changed,
            self.documentation,
            self.provenance,
            self.external_policy,
        )
        self.assertTrue(any("fixture references are invalid" in error for error in errors))

    def test_external_comparison_size_order_and_pair_contract(self) -> None:
        changed = copy.deepcopy(self.external)
        changed["fixtures"].append(
            {
                "id": "FIX-0002",
                "path": "outside/example-2.mdb",
                "size_bytes": 2048,
                "sha256": "2" * 64,
            }
        )
        changed["comparisons"] = [
            {
                "id": "CMP-0002",
                "left_fixture_id": "FIX-0001",
                "right_fixture_id": "FIX-0002",
                "page_size_bytes": 1024,
            },
            {
                "id": "CMP-0001",
                "left_fixture_id": "FIX-0001",
                "right_fixture_id": "FIX-0002",
                "page_size_bytes": 2048,
            },
        ]
        provenance = copy.deepcopy(self.provenance)
        provenance["FIX-0002"] = (
            "### FIX-0002\n- Origin: donor\n- Environment: unknown\n"
            "- Protocol: opt-in verification\n- Rights: not redistributable\n"
            "outside/example-2.mdb\n" + "2" * 64
        )
        errors = contract.validate_external_observational_corpus(
            changed,
            self.documentation,
            provenance,
            self.external_policy,
        )
        self.assertTrue(any("expected integer 2048" in error for error in errors))
        self.assertTrue(any("duplicate directional fixture pair" in error for error in errors))
        self.assertTrue(any("comparisons must be sorted by ID" in error for error in errors))

        changed["comparisons"] = [changed["comparisons"][1]]
        changed["fixtures"][1]["size_bytes"] = 4096
        errors = contract.validate_external_observational_corpus(
            changed,
            self.documentation,
            provenance,
            self.external_policy,
        )
        self.assertTrue(any("fixtures must have equal sizes" in error for error in errors))

        changed["fixtures"][1]["size_bytes"] = 2048
        changed["fixtures"][0]["size_bytes"] = 2049
        errors = contract.validate_external_observational_corpus(
            changed,
            self.documentation,
            provenance,
            self.external_policy,
        )
        self.assertTrue(any("multiple of 2048" in error for error in errors))

    def test_external_fixture_ids_are_ordered_and_paths_are_unique(self) -> None:
        changed = copy.deepcopy(self.external)
        changed["fixtures"] = [
            {
                "id": "FIX-0002",
                "path": "outside/example.mdb",
                "size_bytes": 2048,
                "sha256": "2" * 64,
            },
            changed["fixtures"][0],
        ]
        provenance = copy.deepcopy(self.provenance)
        provenance["FIX-0002"] = (
            "### FIX-0002\n- Origin: donor\n- Environment: unknown\n"
            "- Protocol: opt-in verification\n- Rights: not redistributable\n"
            "outside/example.mdb\n" + "2" * 64
        )
        errors = contract.validate_external_observational_corpus(
            changed,
            self.documentation,
            provenance,
            self.external_policy,
        )
        self.assertTrue(any("duplicate external fixture path" in error for error in errors))
        self.assertTrue(any("fixtures must be sorted by ID" in error for error in errors))


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
