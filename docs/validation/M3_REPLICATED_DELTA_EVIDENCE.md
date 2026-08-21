# Replicated DAO physical-delta evidence

Status: **PASS — historical, descriptive DAO evidence only**

Provenance usage: `EXP-0010`.

The checked M3 campaign ran from exact clean pushed producer commit
`9977745e6515363cbb179d8d949d34604554b2cd`. It launched nine fresh x86
Windows PowerShell processes in the fixed cyclic order `E-B-I`, `B-I-E`,
`I-E-B`: three empty databases, three text-baseline databases, and three
text databases with the single checked nonunique index addition.

The immutable bundle is retained outside the repository:

```text
%TEMP%\jet3-rs-dao-m3\evidence\
  9977745e6515363cbb179d8d949d34604554b2cd\
  20260725T024333Z-dao-m3
```

| Retained identity | Value |
| --- | --- |
| Manifest payloads | 75 files / 673,887 bytes |
| Complete bundle | 76 files / 689,640 bytes |
| Manifest SHA-256 | `15a7abb3b768ea94233dc3d525a069fb25e595b0ed649f063d117697a6e3c55e` |
| Report SHA-256 | `5fb2feebe9480e78ea1cda56077fc15ae4bfcd9a43ca1ff95ab321fed990419d` |
| Analysis SHA-256 | `d6d66afbe0500b5daa8d8cd22704c6208d1730d293bf3fa313ef9702b0fff0a8` |
| Plan SHA-256 | `5943e1a64a0b84916b814c76d87cb81192fb341da800769b1d7dbcb13378d9de` |
| Environment SHA-256 | `870ec9ceaaa6a5b9af0ebf16fbf0ef793b943718b49d9f003ed48cfd65af679f` |

All nine scenarios and all 18 declared comparisons passed. The checked Python
validator accepted the complete immutable bundle. A separate PowerShell/.NET
implementation then reproduced every manifest file hash, all nine ordered page
hash sequences, all 18 pair bitmaps, all three cohort-variance bitmaps, the
stable-cohort delta bitmap, both aggregate intersections/unions, and both
occurrence histograms.

The retained bundle was later copied without modification from the offline
Windows host and revalidated on macOS. The validator preserves and checks the
recorded absolute Windows path relationships while resolving only the retained
plan and environment payloads at their local archive paths; it does not rewrite
the invocation or relax the stricter live-run path checks.

## Descriptive results

| Cohort | Replicas | Size per replica | Variable absolute bytes |
| --- | ---: | ---: | ---: |
| Empty (`E`) | 3 | 40,960 bytes / 20 pages | 122 |
| Text baseline (`B`) | 3 | 49,152 bytes / 24 pages | 220 |
| Text plus index (`I`) | 3 | 51,200 bytes / 25 pages | 274 |

Within the 49,152-byte baseline/index common length:

- 485 absolute positions were stable within all three baseline replicas,
  stable within all three indexed replicas, and different between the two
  stable cohort values;
- the three paired comparisons had a 717-position intersection and
  796-position union;
- all nine baseline-by-index comparisons had a 717-position intersection and
  798-position union; and
- page 24 was present in every indexed replica and no baseline replica.

The complete exact masks, page hashes, occurrence histograms, first/last
offsets, and all nine content-addressed MDBs remain in the bundle. Those
retained values—not this summary—are authoritative.

## Claim boundary

The positions above are candidates associated with this exact controlled run.
M3 does not identify a page class, header, catalog field, row, index node,
allocation structure, or stable MDB offset. Three same-host replicas do not
establish cross-machine or universal invariants. M3 supplies no Rust behavior,
does not satisfy exact-release differential gate G3, and does not change the
support matrix.
