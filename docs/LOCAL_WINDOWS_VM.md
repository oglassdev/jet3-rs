# Local Windows DAO VM

The local VM runs the independent DAO oracle. Its disk, provider, credentials,
SSH keys, MDB files and raw captures stay outside the repository. Local and
hosted differential runs use the same evidence standard in
[validation/README.md](validation/README.md).

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
python3 oracle/windows-dao/dao.py list
python3 oracle/windows-dao/dao.py run --out /path/outside/repo/new-run            # every suite
python3 oracle/windows-dao/dao.py run locale-updates wide-rows --out /path/outside/repo/new-run
```

`dao.py run` builds and freezes a `jet3-cli` for the run (`--cli` reuses one),
prepares Rust candidates, builds native inputs and edits under DAO, reads every
image back through DAO and compares the pair. Each suite writes its spec,
inputs, VM outboxes, logs and `report.json` under the new `--out` directory,
including failed stages; `summary.json` lists every suite outcome. Retained
external inputs (archived native images and key inventories) resolve under
`$JET3_WINDOWS_SHARED_ROOT/checks` unless `--archive` or `JET3_DAO_ARCHIVE`
names another root, and are checked against their recorded SHA-256. Fix a
failure and rerun with another output directory. `dao.py compare RUN/SUITE`
re-evaluates retained outputs offline into a new report file. The suites are
listed in `oracle/windows-dao/README.md`; these finite suites do not establish
general Jet 3 compatibility.

For an ad-hoc x86 PowerShell script:

```sh
just dao ps /path/to/check.ps1 --with input.mdb --out /path/outside/repo/probe
```

The script runs next to `Common.ps1` and receives guest-local `$env:JET3_WORK`
and shared `$env:JET3_OUTBOX`. A successful script alone is not differential
evidence; retain its inputs, provider environment and complete comparisons
before making a support claim.
