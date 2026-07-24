"""Pure shape validation for the checked G0 repository contract."""

from __future__ import annotations

from typing import Any

from .repository_common import (
    ContractError,
    PROVENANCE_ID,
    SHA256,
    exact_keys,
    nonempty,
    safe_path,
    unique_strings,
)


def validate_contract_shape(document: dict[str, Any]) -> list[str]:
    """Validate the checked G0 inventory shape without consulting the filesystem."""
    errors: list[str] = []
    exact_keys(
        document,
        {
            "schema_version",
            "workspace_packages",
            "allowed_runtime_packages",
            "format_knowledge",
            "fixtures",
        },
        "$",
        errors,
    )
    if document.get("schema_version") != 1:
        errors.append("$.schema_version: expected integer 1")

    workspace = document.get("workspace_packages")
    if not isinstance(workspace, dict):
        errors.append("$.workspace_packages: expected object")
    else:
        exact_keys(workspace, {"production", "support"}, "$.workspace_packages", errors)
        seen_names: set[str] = set()
        seen_manifests: set[str] = set()
        for role in ("production", "support"):
            entries = workspace.get(role)
            if not isinstance(entries, list) or (role == "production" and not entries):
                errors.append(f"$.workspace_packages.{role}: invalid package array")
                continue
            for index, entry in enumerate(entries):
                context = f"$.workspace_packages.{role}[{index}]"
                if not isinstance(entry, dict):
                    errors.append(f"{context}: expected object")
                    continue
                keys = (
                    {"name", "manifest", "crate_root"}
                    if role == "production"
                    else {"name", "manifest"}
                )
                exact_keys(entry, keys, context, errors)
                name = entry.get("name")
                if not nonempty(name):
                    errors.append(f"{context}.name: expected non-empty string")
                elif name in seen_names:
                    errors.append(f"{context}.name: duplicate package {name}")
                else:
                    seen_names.add(name)
                for field in keys - {"name"}:
                    try:
                        path = safe_path(
                            entry.get(field), f"{context}.{field}"
                        ).as_posix()
                    except ContractError as error:
                        errors.append(str(error))
                        continue
                    if field == "manifest":
                        if path in seen_manifests:
                            errors.append(f"{context}.manifest: duplicate path {path}")
                        seen_manifests.add(path)

    allowed = unique_strings(
        document.get("allowed_runtime_packages"),
        "$.allowed_runtime_packages",
        errors,
    )
    production_names = {
        entry.get("name")
        for entry in (
            workspace.get("production", [])
            if isinstance(workspace, dict)
            else []
        )
        if isinstance(entry, dict)
    }
    if set(allowed) != production_names:
        errors.append(
            "$.allowed_runtime_packages: must exactly equal production package names"
        )

    knowledge = document.get("format_knowledge")
    if not isinstance(knowledge, dict):
        errors.append("$.format_knowledge: expected object")
    else:
        exact_keys(
            knowledge,
            {"assertion_files", "reviewed_non_assertion_files"},
            "$.format_knowledge",
            errors,
        )
        seen_paths: set[str] = set()
        for category in ("assertion_files", "reviewed_non_assertion_files"):
            entries = knowledge.get(category)
            if not isinstance(entries, list) or (
                category == "assertion_files" and not entries
            ):
                errors.append(f"$.format_knowledge.{category}: invalid array")
                continue
            for index, entry in enumerate(entries):
                context = f"$.format_knowledge.{category}[{index}]"
                if not isinstance(entry, dict):
                    errors.append(f"{context}: expected object")
                    continue
                keys = (
                    {"path", "sha256", "provenance_ids"}
                    if category == "assertion_files"
                    else {"path", "sha256", "reason"}
                )
                exact_keys(entry, keys, context, errors)
                try:
                    path = safe_path(entry.get("path"), f"{context}.path").as_posix()
                except ContractError as error:
                    errors.append(str(error))
                    path = ""
                if path in seen_paths:
                    errors.append(f"{context}.path: duplicate format inventory path")
                seen_paths.add(path)
                if not isinstance(entry.get("sha256"), str) or SHA256.fullmatch(
                    entry.get("sha256", "")
                ) is None:
                    errors.append(f"{context}.sha256: invalid SHA-256")
                if category == "assertion_files":
                    identifiers = unique_strings(
                        entry.get("provenance_ids"),
                        f"{context}.provenance_ids",
                        errors,
                    )
                    if any(
                        PROVENANCE_ID.fullmatch(identifier) is None
                        for identifier in identifiers
                    ):
                        errors.append(f"{context}.provenance_ids: invalid provenance ID")
                elif not nonempty(entry.get("reason")):
                    errors.append(f"{context}.reason: expected non-empty string")

    fixtures = document.get("fixtures")
    if not isinstance(fixtures, dict):
        errors.append("$.fixtures: expected object")
    else:
        exact_keys(
            fixtures,
            {"repository_manifest", "seed_manifest", "external_observational"},
            "$.fixtures",
            errors,
        )
        for field in ("repository_manifest", "seed_manifest"):
            try:
                safe_path(fixtures.get(field), f"$.fixtures.{field}")
            except ContractError as error:
                errors.append(str(error))
        external = fixtures.get("external_observational")
        if not isinstance(external, dict):
            errors.append("$.fixtures.external_observational: expected object")
        else:
            exact_keys(
                external,
                {
                    "manifest",
                    "documentation",
                    "provenance",
                    "redistributable",
                    "regenerable",
                    "acceptance_fixture",
                },
                "$.fixtures.external_observational",
                errors,
            )
            for field in ("manifest", "documentation", "provenance"):
                try:
                    safe_path(
                        external.get(field),
                        f"$.fixtures.external_observational.{field}",
                    )
                except ContractError as error:
                    errors.append(str(error))
            for field in ("redistributable", "regenerable", "acceptance_fixture"):
                if external.get(field) is not False:
                    errors.append(
                        f"$.fixtures.external_observational.{field}: must be false"
                    )
    return errors
