from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from validation import release_evidence_model as model  # noqa: E402
from validation import release_evidence_tree as tree  # noqa: E402


class ReleaseEvidenceIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _limits(self) -> model.Limits:
        return model.Limits(
            max_overlay_bytes=1024,
            max_file_count=10,
            max_file_bytes=1024,
            max_total_file_bytes=1024,
            max_evidence_count=10,
            max_files_per_evidence=10,
            max_adapter_file_visits=10,
            max_adapter_input_bytes=1024,
            max_json_depth=10,
            max_json_nodes=100,
        )

    def test_windows_style_zero_inode_stat_identity_is_supported(self) -> None:
        metadata = mock.Mock(
            st_dev=0,
            st_ino=0,
            st_mode=stat.S_IFDIR | 0o700,
            st_birthtime_ns=1234,
        )
        with mock.patch.object(tree.os, "name", "nt"):
            identity = tree.stable_object_identity(metadata)
        self.assertEqual(identity.device, 0)
        self.assertEqual(identity.inode, 0)
        self.assertEqual(identity.file_type, stat.S_IFDIR)
        self.assertEqual(identity.platform_token, 1234)

    def test_path_to_handle_binding_excludes_variant_timestamp_fields(self) -> None:
        """Windows path/handle timestamp divergence cannot fail the binding."""

        def fake(**overrides: int) -> mock.Mock:
            fields = {
                "st_dev": 7,
                "st_ino": 11,
                "st_mode": stat.S_IFREG | 0o644,
                "st_size": 512,
                "st_mtime_ns": 1_000,
                "st_ctime_ns": 2_000,
            }
            fields.update(overrides)
            return mock.Mock(**fields)

        path_derived = fake()
        handle_derived = fake(st_mtime_ns=9_000, st_ctime_ns=8_000)
        self.assertEqual(
            tree.object_binding(path_derived), tree.object_binding(handle_derived)
        )
        self.assertNotEqual(
            tree.object_identity(path_derived), tree.object_identity(handle_derived)
        )
        for name, value in (
            ("st_dev", 8),
            ("st_ino", 12),
            ("st_mode", stat.S_IFDIR | 0o644),
            ("st_size", 513),
        ):
            with self.subTest(field=name):
                self.assertNotEqual(
                    tree.object_binding(path_derived),
                    tree.object_binding(fake(**{name: value})),
                )
        self.assertEqual(
            tree.object_binding(path_derived),
            tree.identity_binding(tree.object_identity(path_derived)),
        )
        self.assertEqual(
            tree.object_binding(fake(st_mode=stat.S_IFREG | 0o600)),
            tree.object_binding(path_derived),
        )
        binding = tree.object_binding(path_derived)
        self.assertIs(tree.identifying_binding(binding, "unit"), binding)
        with self.assertRaisesRegex(
            model.ReleaseEvidenceError,
            "filesystem does not report an identifying inode",
        ):
            tree.identifying_binding(tree.object_binding(fake(st_ino=0)), "unit")

    def _fake_stat(self, metadata: os.stat_result, **overrides: int) -> mock.Mock:
        """Copy a real stat result so single fields can be made to disagree."""

        fields = {
            "st_dev": metadata.st_dev,
            "st_ino": metadata.st_ino,
            "st_mode": metadata.st_mode,
            "st_nlink": metadata.st_nlink,
            "st_size": metadata.st_size,
            "st_mtime_ns": metadata.st_mtime_ns,
            "st_ctime_ns": metadata.st_ctime_ns,
            "st_file_attributes": getattr(metadata, "st_file_attributes", 0),
        }
        fields.update(overrides)
        return mock.Mock(**fields)

    def test_zero_inode_refuses_to_bind_a_path_to_a_handle(self) -> None:
        """A filesystem without inodes must refuse, never accept any object."""

        target = self.root / "inodeless.bin"
        target.write_bytes(b"real")
        replacement = self.root / "inodeless-replacement.bin"
        replacement.write_bytes(b"fake")
        real_metadata = tree.regular_metadata
        real_fstat = os.fstat

        def zero_inode_metadata(path: Path, location: str) -> mock.Mock:
            return self._fake_stat(
                real_metadata(path, location), st_dev=0, st_ino=0
            )

        def zero_inode_fstat(descriptor: int) -> mock.Mock:
            return self._fake_stat(real_fstat(descriptor), st_dev=0, st_ino=0)

        with (
            mock.patch.object(tree, "regular_metadata", zero_inode_metadata),
            mock.patch.object(tree.os, "fstat", zero_inode_fstat),
        ):
            with self.assertRaisesRegex(
                model.ReleaseEvidenceError,
                "filesystem does not report an identifying inode",
            ):
                self._read_with_open_hook(
                    tree.read_regular_snapshot,
                    target,
                    lambda: os.replace(replacement, target),
                )
        self.assertEqual(target.read_bytes(), b"fake")
        self.assertEqual(tree.regular_metadata, real_metadata)
        self.assertEqual(os.fstat, real_fstat)

    def _readers(self) -> tuple[tuple[str, Callable[..., object]], ...]:
        """Both bounded readers apply the same identity guards."""

        return (
            ("read_regular_snapshot", tree.read_regular_snapshot),
            ("hash_regular_bounded", tree.hash_regular_bounded),
        )

    def _read_with_open_hook(
        self,
        reader: Callable[..., object],
        path: Path,
        hook: Callable[[], None],
    ) -> None:
        real_open = os.open

        def hooked_open(*arguments: object, **keywords: object) -> int:
            hook()
            return real_open(*arguments, **keywords)

        with mock.patch.object(tree.os, "open", hooked_open):
            reader(path, 4096, "adversarial read")

    def test_same_byte_replacement_between_stat_and_open_is_rejected(self) -> None:
        for name, reader in self._readers():
            with self.subTest(reader=name):
                target = self.root / f"payload-{name}.bin"
                target.write_bytes(b"identical")
                replacement = self.root / f"replacement-{name}.bin"
                replacement.write_bytes(b"identical")
                with self.assertRaisesRegex(
                    model.ReleaseEvidenceError, "file changed while it was opened"
                ):
                    self._read_with_open_hook(
                        reader, target, lambda: os.replace(replacement, target)
                    )

    def test_hard_link_swap_between_stat_and_open_is_rejected(self) -> None:
        target = self.root / "linked-payload.bin"
        target.write_bytes(b"identical")
        other = self.root / "other-payload.bin"
        other.write_bytes(b"identical")
        alias = self.root / "alias.bin"
        try:
            os.link(other, alias)
        except (OSError, NotImplementedError, AttributeError) as error:
            self.skipTest(f"hard links are unavailable here: {error}")
        with self.assertRaisesRegex(
            model.ReleaseEvidenceError, "file changed while it was opened"
        ):
            self._read_with_open_hook(
                tree.read_regular_snapshot,
                target,
                lambda: os.replace(alias, target),
            )

    def test_hard_linked_file_is_rejected_before_it_is_opened(self) -> None:
        target = self.root / "shared-payload.bin"
        target.write_bytes(b"identical")
        try:
            os.link(target, self.root / "second-name.bin")
        except (OSError, NotImplementedError, AttributeError) as error:
            self.skipTest(f"hard links are unavailable here: {error}")
        with self.assertRaisesRegex(
            model.ReleaseEvidenceError, "hard-linked files are forbidden"
        ):
            tree.read_regular_snapshot(target, 4096, "adversarial read")

    def test_same_size_mutation_between_stat_and_open_is_rejected(self) -> None:
        """Identity binding alone cannot see this; the path recheck must."""

        for name, reader in self._readers():
            with self.subTest(reader=name):
                target = self.root / f"mutated-{name}.bin"
                target.write_bytes(b"before")
                stamp = target.lstat()

                def mutate(path: Path = target, before: os.stat_result = stamp) -> None:
                    with path.open("r+b") as handle:
                        handle.write(b"after!")
                    os.utime(
                        path,
                        ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
                    )

                binding = tree.object_binding(stamp)
                with self.assertRaisesRegex(
                    model.ReleaseEvidenceError, "file changed while it was read"
                ):
                    self._read_with_open_hook(reader, target, mutate)
                self.assertEqual(tree.object_binding(target.lstat()), binding)

    def test_handle_identity_drift_during_read_is_rejected(self) -> None:
        """Only the fstat-to-fstat guard sees this: path metadata never moves."""

        target = self.root / "rewritten.bin"
        target.write_bytes(b"before")
        readers = (
            ("read_regular_snapshot", tree.read_regular_snapshot),
            ("hash_regular_bounded", tree.hash_regular_bounded),
        )
        for name, reader in readers:
            with self.subTest(reader=name):
                before = target.lstat()
                real_fstat = os.fstat
                calls = 0

                def drifting_fstat(descriptor: int) -> object:
                    nonlocal calls
                    calls += 1
                    observed = real_fstat(descriptor)
                    if calls == 1:
                        return observed
                    return self._fake_stat(
                        observed, st_mtime_ns=observed.st_mtime_ns + 1
                    )

                with mock.patch.object(tree.os, "fstat", drifting_fstat):
                    with self.assertRaisesRegex(
                        model.ReleaseEvidenceError, "file changed while it was read"
                    ):
                        reader(target, 4096, "adversarial read")
                self.assertEqual(calls, 2)
                self.assertEqual(
                    tree.object_identity(target.lstat()),
                    tree.object_identity(before),
                )

    def _resolve_for_staging(self, name: str, content: bytes) -> model.ResolvedFile:
        source = self.root / name
        source.write_bytes(content)
        size, digest, identity = tree.hash_regular_bounded(source, 4096, "staged")
        return model.ResolvedFile(
            relative_path=name,
            path=source,
            size=size,
            sha256=digest,
            identity=identity,
        )

    def test_staging_copy_rechecks_the_full_identity_on_its_own_handle(self) -> None:
        """Metadata-only drift: binding, bytes, size and digest all still match."""

        resolved = self._resolve_for_staging("staged.bin", b"before")
        source = resolved.path
        stamp = source.lstat()
        os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000_000))
        self.assertEqual(source.read_bytes(), b"before")
        self.assertEqual(
            hashlib.sha256(source.read_bytes()).hexdigest(), resolved.sha256
        )
        self.assertEqual(source.lstat().st_size, resolved.size)
        self.assertEqual(
            tree.object_binding(source.lstat()),
            tree.identity_binding(resolved.identity),
        )
        with self.assertRaisesRegex(
            model.ReleaseEvidenceError,
            "file changed after inventory resolution",
        ):
            tree.copy_file_exclusive(resolved, self.root / "staged-copy.bin")

    def _stat_from_identity(
        self, identity: model.ObjectIdentity, **overrides: int
    ) -> mock.Mock:
        """A handle stat that reports exactly one recorded inventory identity."""

        fields = {
            "st_dev": identity.device,
            "st_ino": identity.inode,
            "st_mode": identity.mode,
            "st_nlink": 1,
            "st_size": identity.size,
            "st_mtime_ns": identity.modified_ns,
            "st_ctime_ns": identity.changed_ns,
            "st_file_attributes": 0,
        }
        fields.update(overrides)
        return mock.Mock(**fields)

    def test_staging_copy_rejects_a_same_byte_replacement_before_opening(self) -> None:
        """Pins the pre-open check: the handle is faked to look untouched."""

        resolved = self._resolve_for_staging("swapped.bin", b"before")
        replacement = self.root / "swap-source.bin"
        replacement.write_bytes(b"before")
        os.replace(replacement, resolved.path)
        self.assertNotEqual(
            tree.object_binding(resolved.path.lstat()),
            tree.identity_binding(resolved.identity),
        )
        legitimate = self._stat_from_identity(resolved.identity)
        with mock.patch.object(tree.os, "fstat", lambda descriptor: legitimate):
            with self.assertRaisesRegex(
                model.ReleaseEvidenceError,
                "file changed after inventory resolution",
            ):
                tree.copy_file_exclusive(resolved, self.root / "swapped-copy.bin")

    def test_staging_copy_rejects_a_handle_that_does_not_match_its_path(self) -> None:
        """Pins the path-to-handle bind inside the copy, not the inventory check."""

        resolved = self._resolve_for_staging("mismatched.bin", b"before")
        foreign = self._stat_from_identity(
            resolved.identity, st_ino=resolved.identity.inode + 1
        )
        with mock.patch.object(tree.os, "fstat", lambda descriptor: foreign):
            with self.assertRaisesRegex(
                model.ReleaseEvidenceError, "file changed while it was opened"
            ):
                tree.copy_file_exclusive(
                    resolved, self.root / "mismatched-copy.bin"
                )

    def test_staging_copy_rejects_handle_identity_drift_during_the_copy(self) -> None:
        resolved = self._resolve_for_staging("drifting.bin", b"before")
        real_fstat = os.fstat
        calls = 0

        def drifting_fstat(descriptor: int) -> object:
            nonlocal calls
            calls += 1
            observed = real_fstat(descriptor)
            if calls == 1:
                return observed
            return self._fake_stat(observed, st_mtime_ns=observed.st_mtime_ns + 1)

        with mock.patch.object(tree.os, "fstat", drifting_fstat):
            with self.assertRaisesRegex(
                model.ReleaseEvidenceError,
                "file changed after inventory resolution",
            ):
                tree.copy_file_exclusive(resolved, self.root / "drifting-copy.bin")
        self.assertEqual(calls, 2)

    def test_staging_copy_rejects_path_metadata_drift_during_the_copy(self) -> None:
        resolved = self._resolve_for_staging("path-drift.bin", b"before")
        real_metadata = tree.regular_metadata
        calls = 0

        def drifting_metadata(path: Path, location: str) -> object:
            nonlocal calls
            calls += 1
            observed = real_metadata(path, location)
            if calls == 1:
                return observed
            return self._fake_stat(observed, st_mtime_ns=observed.st_mtime_ns + 1)

        with mock.patch.object(tree, "regular_metadata", drifting_metadata):
            with self.assertRaisesRegex(
                model.ReleaseEvidenceError,
                "file changed after inventory resolution",
            ):
                tree.copy_file_exclusive(
                    resolved, self.root / "path-drift-copy.bin"
                )
        self.assertEqual(calls, 2)

    def test_directory_replacement_during_resolution_is_rejected(self) -> None:
        root = self.root / "swapped-tree"
        payload = root / "payload"
        payload.mkdir(parents=True)
        (payload / "file.bin").write_bytes(b"same")
        original_hash = tree.hash_regular_bounded

        def hash_then_swap(*arguments: object, **keywords: object) -> object:
            result = original_hash(*arguments, **keywords)
            moved = root / "payload-moved"
            payload.rename(moved)
            payload.mkdir()
            (moved / "file.bin").rename(payload / "file.bin")
            moved.rmdir()
            return result

        with mock.patch.object(tree, "hash_regular_bounded", hash_then_swap):
            with self.assertRaisesRegex(
                model.ReleaseEvidenceError, "directory changed during resolution"
            ):
                tree.scan_regular_files(root, self._limits())

    def test_tree_resolved_read_rechecks_hash(self) -> None:
        path = self.root / "resolved.txt"
        path.write_bytes(b"before")
        resolved = model.ResolvedFile(
            relative_path="resolved.txt",
            path=path,
            size=6,
            sha256=hashlib.sha256(b"before").hexdigest(),
            identity=tree.object_identity(path.stat()),
        )
        path.write_bytes(b"after!")
        with self.assertRaisesRegex(
            model.ReleaseEvidenceError, "changed after inventory resolution"
        ):
            tree.read_resolved_file(resolved, 100, "direct tree test")

    def test_tree_snapshot_retains_directory_identities(self) -> None:
        root = self.root / "tree"
        payload = root / "payload"
        payload.mkdir(parents=True)
        (payload / "file.bin").write_bytes(b"same")
        limits = self._limits()
        before = tree.scan_regular_files(root, limits)
        moved = self.root / "moved-payload"
        payload.rename(moved)
        payload.mkdir()
        (moved / "file.bin").rename(payload / "file.bin")
        after = tree.scan_regular_files(root, limits)
        self.assertNotEqual(before.directories, after.directories)


if __name__ == "__main__":
    unittest.main()
