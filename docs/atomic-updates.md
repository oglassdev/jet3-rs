# Atomic update guarantees and limits

The `jet3::atomic` module is a format-neutral publication primitive. It copies
an existing regular file to a private file in the same directory, applies a
caller mutation, preserves the original standard-library permissions, asks a
caller-supplied validator to reopen the private path, synchronizes the private
file, and renames it over the target. On Unix it then synchronizes the
containing directory.

This is not a Jet writer, transaction manager, or locking implementation.
Callers must exclude concurrent writers. The validation callback is a
publication precondition, not independent structural verification: a
project-owned validator or successful self-read cannot establish MDB
correctness or Access/DAO compatibility.

## Observable states and failures

The private file is never published before mutation and validation complete.
Every error through the `Publish` stage leaves the original target path in
place and attempts to remove the closed private file. A cleanup failure is
retained as secondary error context, and `Drop` retries removal, but cleanup
cannot be guaranteed if both attempts fail.

After a successful rename, readers that reopen the target are intended to see
the complete old file or the complete validated replacement. The
`atomic_publication` integration test forces an observer to read the target
before and after the rename and rejects missing, partial, or unexpected bytes.
This test is filesystem evidence for the CI host where it ran; it is not a
universal guarantee for every filesystem.

A `DirectorySync` failure is different from every earlier failure. It reports
`replacement_published = true`: the complete validated replacement is visible,
but the new directory entry may not survive a crash. Permission checks and
private-file cleanup are exercised at every injectable pre-publication stage
and after the post-publication directory-sync fault.

## Platform contract

Rust documents `std::fs::rename` as replacing an existing destination and
currently maps it to `rename` on Unix and `MoveFileExW` or
`SetFileInformationByHandle` on Windows:
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

On Windows, Rust's implementation and filesystem support determine which
documented rename API is used. Microsoft documents replacement for
`MOVEFILE_REPLACE_EXISTING`, subject to access-control requirements:
<https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw>.
The library synchronizes the replacement file before rename, but Rust exposes
no portable directory-sync operation used here on Windows, and this library
does not request the Windows `MOVEFILE_WRITE_THROUGH` flag directly.
Consequently it claims neither crash-durable directory publication nor
identical behavior across NTFS, ReFS, FAT, SMB, and other filesystems.

## G4 evidence status

The deterministic Rust tests cover every exposed stage: private-copy creation,
copy, mutation, metadata preservation, validation, file synchronization,
pre-publication barrier, publication, directory synchronization, and cleanup.
The checked test manifest binds these cases to stable IDs; CI observations bind
the inventory and results to a commit and platform.

This is only the format-neutral atomic-publication portion of G4. No MDB writer
or independent MDB structural verifier exists in this evidence, and no DAO
result is produced. G4 therefore remains blocked.
