# Atomic update guarantees and limits

The `jet3::atomic` module is a format-neutral publication primitive. It copies
an existing regular file to a private file in the same directory, applies a
caller mutation, preserves the original standard-library permissions, asks a
caller-supplied validator to reopen the private path, synchronizes the private
file, verifies that the path still names the retained open file, and renames
it over the target. On Unix it then synchronizes the containing directory.

This is not a Jet writer, transaction manager, or locking implementation.
Callers must exclude concurrent writers. The validation callback is a
publication precondition, not independent structural verification: a
project-owned validator or successful self-read cannot establish MDB
correctness or Access/DAO compatibility.

## Observable states and failures

The private file is never published before mutation and validation complete.
Every error through the `Publish` stage leaves the original target path in
place and attempts to remove the private file. Cleanup repeats the same file
identity check before unlinking. If validation substituted a different entry
at the private path, the publisher reports the cleanup refusal as secondary
error context and deliberately leaves that unowned entry untouched. `Drop`
retries removal only while the path still identifies the publisher's file.

After a successful rename, readers that reopen the target are intended to see
the complete old file or the complete validated replacement. The
`atomic_publication` integration test forces an observer to read the target
before and after the rename and rejects missing, partial, or unexpected bytes.
This test is filesystem evidence for the CI host where it ran; it is not a
universal guarantee for every filesystem.

A `DirectorySync` failure is different from every earlier failure. The stage
itself establishes that the complete validated replacement is visible, but the
new directory entry may not survive a crash. No separate publication-state
flag can disagree with that stage. Permission checks and private-file cleanup
are exercised at every injectable pre-publication stage and after the
post-publication directory-sync fault.

## Platform contract

Atomic update currently supports Unix hosts only. Its platform boundary has
two separate responsibilities: identify the open private file and perform
overwrite-replace publication followed by directory synchronization. On Unix,
safe standard-library device and inode metadata provide identity, while
`rename` and directory `sync_all` provide the publication operations. The
identity comparison runs immediately after the last caller hook and before
replacement. This closes validation-time path substitution under the
documented requirement that callers exclude concurrent writers. It is not a
locking primitive and cannot defend against a separate process racing the
identity check and replacement in violation of that requirement.

Rust documents `std::fs::rename` as replacing an existing destination:
<https://doc.rust-lang.org/std/fs/fn.rename.html>. Same-directory private-file
placement prevents the library from deliberately requesting a cross-filesystem
move.

On POSIX systems, replacement of an existing destination by `rename` is
atomic. Open references to the replaced file remain valid, and a new pathname
lookup resolves to the replacement after the operation:
<https://pubs.opengroup.org/onlinepubs/9799919799/functions/rename.html>.
Linux likewise states that an existing destination is atomically replaced
without an interval in which the destination pathname is missing:
<https://man7.org/linux/man-pages/man2/rename.2.html>.

File synchronization and directory synchronization serve different durability
purposes on Unix. Linux documents that `fsync` on the file does not necessarily
make its directory entry durable; the directory descriptor must also be
synchronized:
<https://man7.org/linux/man-pages/man2/fsync.2.html>. The implementation does
both, in that order. A successful return still inherits the guarantees and
failure modes of the mounted filesystem, storage hardware, and operating
system. Network, FUSE, removable, or otherwise unusual filesystems may be
weaker.

On non-Unix platforms, including Windows, the operation returns a structured
`PrivateCopyCreation` error with `Unsupported` before a private file is
created. Windows file identity is obtainable through Windows APIs; identity is
not the blocker. This implementation does not yet have an audited safe Windows
provider for overwrite-replacing the target while retaining the private handle
and establishing the requested post-replacement durability semantics. No
Windows atomic publication or crash-durability support is claimed.

## G4 evidence status

The deterministic Unix Rust tests cover every exposed stage: private-copy
creation, copy, mutation, metadata preservation, validation, file
synchronization, pre-publication barrier, publication, directory
synchronization, and cleanup. An adversarial test replaces the validated
private pathname with different bytes and proves that publication is rejected,
the original remains unchanged, and cleanup does not delete the substituted
entry.
The checked test manifest binds these cases to stable IDs; CI observations bind
the inventory and results to a commit and platform.

This is only the format-neutral atomic-publication portion of G4. No MDB writer
or independent MDB structural verifier exists in this evidence, and no DAO
result is produced. G4 therefore remains blocked.
