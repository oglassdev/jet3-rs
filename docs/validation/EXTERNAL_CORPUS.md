# External donated corpus

This document identifies four inspection-authorized MDB candidates that remain
outside the repository. They are optional exploratory inputs, not distributed
fixtures and not acceptance evidence. Their fixture, observation, and
experiment records are `FIX-0001` through `FIX-0004`, `OBS-0001`, and
`EXP-0001` in `docs/PROVENANCE.md`.

## Handling rules

- Access is opt-in through `JET3_EXTERNAL_FIXTURE_ROOT`. No test or build may
  assume that the variable is set or that the corpus is present.
- Resolve only the exact relative locators listed below. Reject a missing file,
  size mismatch, or SHA-256 mismatch before inspection.
- The donor authorized local inspection but did not grant redistribution
  rights. Do not commit, publish, package, upload, copy into test artifacts, or
  retain derived file content.
- Treat every MDB as untrusted input. Inspection does not authorize opening it
  in a vulnerable Jet or Access environment.
- The donor states that the files are actual Access 97 databases. That statement
  is origin metadata only; it is not DAO evidence and does not advance any
  verification state.
- Environment details not recorded in the provenance entries remain unknown.
  Do not infer them from filenames or directory names.
- Review of the donor records and observations is pending.

The bundle paths `project-source/**` and `project-context/**` are explicitly
excluded and quarantined. Never inspect or cite their contents: they are not
format sources and create a prohibited implementation-contamination risk.

## Candidate inventory

| ID | Relative locator beneath `JET3_EXTERNAL_FIXTURE_ROOT` | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| `FIX-0001` | `controller-backups/full-2026-01-26/SN_7213_UnArchived/ETS3000.mdb` | 1,593,344 | `5c18e9d85c2c91a1afdd6d2ddc64c990fd1442c01c753a5d76d4b6d15259537b` |
| `FIX-0002` | `controller-backups/full-2026-07-23/SN_7213_UnArchived/ETS3000.mdb` | 1,593,344 | `0a68f70d901d4b519b765323c141c794b427f3d4ee25ef2bd390ce2a493378d9` |
| `FIX-0003` | `controller-backups/historical-2019/ETS3000.mdb` | 1,220,608 | `d8dba78c0ce51614f0099e9db7b2cd10790935ffb5db989db5fc766b7c5881fa` |
| `FIX-0004` | `controller-transfers/jobs-only-2026-07-23-three-jobs/extracted/ETS3000.MDB` | 2,129,920 | `42aa474ee656d3f1249af08424ed92c91be1388b308906cafb54b4e7ff812d61` |

The bundle's top-level README describes `FIX-0001` and `FIX-0002` as copies
from their named controller backup directories, `FIX-0003` as originating at
`E:\Accurpress Backup - 2019`, and `FIX-0004` as controller-exported and then
extracted. These statements are donor-supplied origin metadata. This project
does not cite that README for binary format knowledge.

## Canonical verification command

From the repository root, set the opt-in root and run the checked-in read-only
verifier:

```sh
export JET3_EXTERNAL_FIXTURE_ROOT=/absolute/path/to/corpus
./tools/inspect_external_corpus.py
```

`tools/inspect_external_corpus.py` is the canonical current verifier. It reads
`docs/validation/external-corpus.json`, restricts access to its exact relative
locators beneath the configured root, checks regular-file status, sizes, and
SHA-256 values, reads the offset-`0x4` signature, and performs the 1,024- and
2,048-byte stride sampling. It emits a deterministic JSON observation,
including the repository commit and dirty state, or reports the corpus as
blocked. It does not invoke `file(1)`, reproduce the historical generic file
classification, identify a Jet generation, or produce DAO evidence.

The snippets below explain the commands behind the recorded observations and
permit focused manual comparison. Prefer the verifier for current corpus
checks.

## Identity and signature check

For each inventory row, the `OBS-0001` procedure was:

1. Join the configured root and exact relative locator without searching the
   bundle.
2. Compute the byte count and SHA-256 and compare them with the inventory.
3. Invoke `file(1)` for a non-authoritative generic identification.
4. Read exactly 15 bytes beginning at offset `0x4` and render them as both
   bytes and hexadecimal.

The historical POSIX command sequence for one already selected candidate was:

```sh
test -n "${JET3_EXTERNAL_FIXTURE_ROOT:-}"
candidate="${JET3_EXTERNAL_FIXTURE_ROOT}/controller-backups/full-2026-01-26/SN_7213_UnArchived/ETS3000.mdb"
wc -c < "$candidate"
shasum -a 256 "$candidate"
file "$candidate"
dd if="$candidate" bs=1 skip=4 count=15 2>/dev/null
dd if="$candidate" bs=1 skip=4 count=15 2>/dev/null | od -An -tx1
```

Repeat it only with each other exact inventory locator. For current
verification, use the canonical tool above rather than repeating these shell
snippets. The recorded result was that sizes and hashes matched, `file(1)` gave
only a generic identification, and all four files contained exactly `Standard
Jet DB` at offset `0x4` (`53 74 61 6e 64 61 72 64 20 4a 65 74 20 44 42`).
Microsoft publishes that 15-byte sequence as one of three Jet signatures in
`SRC-0004`. The match is not proof of Jet generation, whole-file validity,
semantics, or DAO compatibility.

## Boundary-stride experiment

`EXP-0001` tested only the power-of-two strides 1,024 and 2,048. For each file
and stride, it sampled the unsigned first byte at offset zero, `stride`,
`2 * stride`, and so on while the offset remained below the file length, then
sorted and deduplicated the values. This matches the actual sampling with
`xxd -ps -c <stride>`; its first output line begins at offset zero. The
canonical verifier implements the same offsets. This Python 3 snippet explains
the sampling for an exact candidate:

```sh
python3 - "$candidate" <<'PY'
import os
import sys

with open(sys.argv[1], "rb") as database:
    length = os.fstat(database.fileno()).st_size
    for stride in (1024, 2048):
        values = set()
        for offset in range(0, length, stride):
            database.seek(offset)
            sample = database.read(1)
            if len(sample) != 1:
                raise RuntimeError(f"short read at offset {offset}")
            values.add(sample[0])
        rendered = ", ".join(f"{value:02x}" for value in sorted(values))
        print(stride, "{" + rendered + "}")
PY
```

At 2,048-byte boundaries the recorded first-byte sets were:

| Candidates | Boundary-byte set |
| --- | --- |
| `FIX-0001`, `FIX-0002` | `{00, 01, 02, 03, 04, 09}` |
| `FIX-0003`, `FIX-0004` | `{00, 01, 02, 04, 09}` |

The 1,024-byte sampling produced many values rather than a similarly restricted
family. Therefore, 2,048 is only the smallest tested power-of-two stride that
exhibited a restricted boundary-byte family across this four-file corpus. It
is consistent with the 2 KiB Jet 3.x page size documented by Microsoft in
`SRC-0005` for these donor-declared Jet 3 candidates, but it does not itself
identify a Jet generation or page types and is not universal proof of validity
or compatibility.
