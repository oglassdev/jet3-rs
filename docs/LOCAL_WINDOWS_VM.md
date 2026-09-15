# Local Windows DAO VM

The local VM runs the independent DAO oracle. Its disk, provider, credentials,
SSH keys, MDB files and raw captures stay outside the repository. Local and
hosted differential runs use the same evidence standard in
[validation/EVIDENCE.md](validation/EVIDENCE.md).

## Configuration

The machine-local setup lives in `/home/alex/development/vms/jet3-windows/`.
`vm.sh start`, `vm.sh stop` and `vm.sh status` manage the named Podman container.
The current configuration is Windows Server 2022, six CPUs, 8 GB RAM and a
96 GB sparse disk. Web UI, SSH and RDP bind to loopback only. The `shared/`
directory appears as `Z:` interactively and `\\host.lan\Data` over SSH.

The OEM bootstrap creates a standard `jet3runner` account with key-only SSH,
checks the x86 DAO 3.6 provider and restores the privately retained provider
when the stock installation lacks it. It exports the new SSH public host key
through the private share so the host can pin it after a VM rebuild.

```sh
export JET3_WINDOWS_HOST=127.0.0.1
export JET3_WINDOWS_PORT=2222
export JET3_WINDOWS_USER=jet3runner
export JET3_WINDOWS_IDENTITY=/home/alex/.ssh/jet3-dao
export JET3_WINDOWS_SHARED_ROOT=/home/alex/development/vms/jet3-windows/shared
```

Create and open databases on the guest's local disk. Copy captures to the
share only after DAO closes every database and recordset.

## Repeatable verification

```sh
python3 scripts/dao-check.py \
  --out /path/outside/repo/new-run
```

The command builds current Rust examples, generates candidates, records runtime
inputs and the source revision, probes the exact provider, and compares DAO
rows, schema, traversal, lookups, native continuations and retained counters.
It also checks unrelated-byte preservation and duplicate refusal.
Omitting suite names runs all suites; provide names to select a subset.
Every attempt uses a new directory and retains logs and a summary, including
failures. No committed plan or separate authorization is required. Fix a failure
and run the same command with another output directory.

The boundary suite covers existing-page insertion, EOF-page insertion,
duplicate refusal with unrelated Memo data. The row
suite covers ascending/descending unique Long keys, the leaf capacity boundary,
repeated deletion, subsequent native insertion and duplicate rejection.
The `creation-tables` suite covers catalog capacity and multiple indexes on
later tables. The `index-trees` suite covers tree growth/shrinkage, complete row
replacement and empty-table reuse, then feeds DAO-compressed outputs back
through Rust and compares a second DAO round. The `practical-lifecycle` suite
creates an Items/Notes database, inserts 220 items, changes values on dense
pages, deletes rows and inserts more, while preserving unrelated Memo data.
A separate delete-all/reinsert arm checks released-page reuse without file growth.
These finite suites do not establish general Jet 3 compatibility.

`just windows-dev-probe` and the existing `windows-dev-*` recipes remain useful
for specific format discovery. Their historical acquisition plans and outcome
records describe earlier runs. New verification should use reproducible suites
without the old preregistration/approval workflow.

For an ad-hoc x86 PowerShell script:

```sh
just windows-dev-ps /path/to/check.ps1
```

The script receives guest-local `$env:JET3_WORK` and shared `$env:JET3_OUTBOX`.
A successful script alone is not differential evidence; retain its inputs,
provider environment and complete comparisons before making a support claim.
