# Windows DAO oracle

Microsoft DAO is an optional black-box test oracle. It is never a production
dependency, and no MDB bytes or provider binaries are committed.

The retained tooling has three purposes:

- `protocol/v1_2/` defines the shared read scenario and snapshot contract.
- `scripts/dev/` plus `scripts/windows-dao-dev.py` support private exploratory
  runs in the local Windows VM described by `docs/LOCAL_WINDOWS_VM.md`.
- `scripts/Invoke-DaoReadV12.ps1` is the bounded DAO producer for #98;
  `scripts/dao_read_diff.py` canonicalizes, validates, and compares its output.
- `.github/workflows/windows-dao-hosted.yml` probes the x86 DAO environment
  and, only when explicitly approved against the pinned plan, runs the single
  protocol-v1.2 acquisition.
- `scripts/Invoke-DaoAllocationA9.ps1` is the bounded page-image generator for
  the A9 allocation experiment (#99); `scripts/dao_allocation_a9.py` checks
  its plan, evaluates its artifact, and runs the synthetic dry run.
  `.github/workflows/windows-dao-allocation-a9.yml` hosts it under the same
  gating as the read differential.
- `acquisition/bootstrap-layout.plan.json` retains the consumed acquisition
  rejected by `EXP-0067`; `acquisition/bootstrap-layout-floor.plan.json`,
  consumed by `EXP-0069`, also remains immutable.
  `acquisition/bootstrap-layout-sufficiency.plan.json`,
  `scripts/dev/BootstrapLayout.DevJob.ps1`, and `scripts/bootstrap_layout.py`
  define the active development-only local-VM successor for the unresolved
  correlations and bounded all-groups sufficiency question in #100.
- `acquisition/system-catalog.plan.json`, `scripts/dev/SystemCatalog.DevJob.ps1`,
  and `scripts/system_catalog.py` define the development-only local-VM
  system-catalog semantics experiment for #100: five closed checkpoints per
  replica with DAO-visible catalog metadata, analyzed against pinned
  system-table hypotheses.

Concluded A1-A4 and M3-M5 experiment machinery was removed after its results
were recorded in `docs/PROVENANCE.md`. Git history is the archive.

## Portable protocol checks

```sh
python3 -B oracle/windows-dao/scripts/build_v1_2_inventory.py --check
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py schemas
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory \
  oracle/windows-dao/protocol/v1_2/scenarios.json
python3 -B oracle/windows-dao/scripts/dao_read_diff.py synthetic-dry-run \
  /tmp/jet3-dao-read-dry-run.json
python3 -B oracle/windows-dao/scripts/dao_allocation_a9.py plan \
  oracle/windows-dao/acquisition/a9-allocation.plan.json .
python3 -B oracle/windows-dao/scripts/dao_allocation_a9.py synthetic-dry-run \
  /tmp/jet3-dao-a9-dry-run.json
python3 -B -m unittest oracle/windows-dao/tests/test_bootstrap_layout.py -v
python3 -B -m unittest discover -s oracle/windows-dao/tests -v
```

These checks execute no DAO operation and make no compatibility claim.
The committed `acquisition/read-v1_2.synthetic.json` is the reproducible output
of the synthetic command; it proves only that the comparison harness accepts a
match and rejects a controlled mismatch.

## Hosted acquisition

`oracle/windows-dao/acquisition/read-v1_2.plan.json` is the immutable consumed
plan for the accepted run recorded by `EXP-0064`. Its approved SHA-256 is
`b4a05fc381efdaf56011205063c07232a77d23f99837e021242ee199cda48570`.
It pins the inventory, producer, evaluator, validator, workflow, Rust source,
and dependency lock at the acquired revision; it is not re-pinned or validated
against later working trees. The retained evaluator checks every MDB digest,
Rust coverage verdict, snapshot pair, and comparison projection. Artifact MDB
bytes remain access-controlled and are never committed.

### A9 allocation (#99)

`oracle/windows-dao/acquisition/a9-allocation.plan.json` preregisters the
page-allocation questions (Q1 empty template, Q2 page append, Q3 free-page
reuse, Q4 table-map extension, Q5 index/long-value ownership) that the future
writer needs, and pins the generator, evaluator, probe, process helper, and
workflow. `windows-dao-allocation-a9.yml` uses the same explicit acquisition
approval and exact `plan_sha256` gate and refuses before any DAO mutation
unless all match. Workflow reruns are rejected; a new dispatch is a new
explicit human decision.

The workflow terminates the generator process tree at the preregistered
120-minute wall-clock ceiling. The evaluator requires the manifest to bind
both the approved plan digest and the checked-out source revision.

One job runs three independent replicas. Each checkpoint is a closed-file
capture of raw 2 KiB page images (hex plus SHA-256 per page) written under
`artifacts/dao-allocation-a9/r<n>/Q<k>/`. The evaluator verifies every digest,
decodes only the SRC-0020 / EXP-0051 / EXP-0057 primitives, requires the
structural answers to agree across replicas, and writes `report.json` with
`status` of `accepted` or `no_outcome`; an integrity failure exits non-zero
without a report. The committed `acquisition/a9-allocation.synthetic.json`
proves only that the evaluator accepts a consistent fabricated artifact and
rejects a tampered one.

## Experiment discipline

Preregister each experiment as one SHA-256-pinned plan before acquiring data.
For `bootstrap-layout`, the local client verifies every pinned input before
staging, the guest rechecks the plan and staged producer inputs before probing
DAO, and the host analyzer writes the canonical report after publication.
Record the validated outcome once as an additive `EXP-` entry. A failure after
the first DAO mutation is a scientific result and must not be retried without
a human decision.
