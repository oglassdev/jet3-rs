# Controlled M1 physical page observation

Status: **PASS — descriptive only**

Provenance usage: `EXP-0008`.

The bounded observer at exact clean pushed commit
`550ddc266eddf7e6765cf929ef50fd5aac19c542` analyzed only the passing
project-generated M1 bundle recorded by `EXP-0007`. Its exact input manifest
SHA-256 was:

```text
9bc59d5db419e7283d8013d34e4fea16c3a9add8830c392294b8a8b6b1c32685
```

The retained observation is outside the repository:

```text
%TEMP%\jet3-rs-m2-observation\
  550ddc266eddf7e6765cf929ef50fd5aac19c542\
  20260725T012548Z-m1-pages.json
```

It is 21,302 bytes with SHA-256:

```text
59d38601f5c8214a3eaa85b140461de0b54d83bf8664d314c35cba8e5be6f445
```

The checked M1 bundle validator passed again before an independent
PowerShell/.NET recomputation reproduced all seven ordered page-hash
sequences and both pair byte-bound summaries.

| Controlled pair | Lengths | Differing complete pages | Common-byte differences |
| --- | ---: | --- | ---: |
| Repeated empty A/B | 40,960 / 40,960 | 2, 3, 4, 5, 18, 19 | 151 |
| Text baseline/indexed | 49,152 / 51,200 | 1, 3, 4, 5, 18–24 | 740 |

For the repeated empty pair, the first and last differing absolute offsets are
4,206 and 40,691. For the text pair, the first and last differing offsets in
the 49,152-byte common length are 3,971 and 49,151; page 24 is the indexed
file's additional final page.

These are exact-file, physical-position observations only. They do not name
page classes, fields, rows, indexes, allocation structures, or stable format
offsets. No product capability or verification state changes, and the support
matrix remains unchanged. M3 subsequently isolated a narrower controlled
variable and obtained three fresh-process DAO samples per cohort, but still
authorized no physical interpretation; any such proposal requires a separately
reviewed experiment and provenance update.
