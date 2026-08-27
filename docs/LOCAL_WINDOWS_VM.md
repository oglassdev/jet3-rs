# Local Windows DAO development VM

The Windows VM is a private development dependency, not part of the product or
release evidence. Keep its disk, licensed provider, credentials, private keys,
generated MDB files, and raw outputs outside this repository.

The recommended host directory is
`/home/alex/development/vms/jet3-windows/`, with persistent `storage/` and a
`shared/` directory mounted by dockur/windows as drive `Z:` on the interactive
desktop and as `\\host.lan\Data` in SSH sessions. Bind the web UI, RDP, and
SSH to loopback only. Create and open MDB files on the guest's local disk; the
checked development runner copies them to the shared path only after DAO closes
every object.

## Client configuration

Configure the host shell without committing actual values:

```sh
export JET3_WINDOWS_HOST=127.0.0.1
export JET3_WINDOWS_PORT=2222
export JET3_WINDOWS_USER=jet3runner
export JET3_WINDOWS_IDENTITY=/home/alex/.ssh/jet3-dao
export JET3_WINDOWS_SHARED_ROOT=/home/alex/development/vms/jet3-windows/shared
```

The Windows account must be a standard account with key-only OpenSSH access.
Pin its host key before invoking these commands:

```sh
just windows-dev-probe
just windows-dev-empty
just windows-dev-opening
just windows-dev-allocation
just windows-dev-catalog
just windows-dev-table-definition
just windows-dev-row
just windows-dev-value
```

`provider-probe` records the Windows, x86 PowerShell, locale, and registered
DAO candidates. `create-empty` additionally requires a ready
`DAO.DBEngine.36`, creates and reopens a Jet 3 database on `C:`, closes DAO,
then publishes the private MDB and result metadata through the Dockur share.
`opening-matrix` creates the private Jet 3/4, encryption, and password controls
used while developing fail-closed database opening.

`allocation-map` creates one private Jet 3 table, adds deterministic long-binary
rows in fixed bounded batches until one type-1 row contains two nonzero
references to type-`05` pages, then captures deletion and reinsertion across
that multi-slot state. It publishes eight closed checkpoint MDBs plus compact
page-count, type-`05`, and multi-slot metadata. The growth loop assumes no
format threshold: it stops on observed row and page structure or fails after
32,768 rows. Allow up to 15 minutes for this exploratory job.

`catalog` runs a fixed seven-checkpoint sequence over fresh Jet 3 databases:
empty, ASCII table create/drop/recreate, then CP1252-discriminating table
create/drop/recreate. The job snapshots bounded DAO table metadata, closes DAO
before every MDB copy, and constructs the non-ASCII name from code points so
script-file encoding cannot affect the observation.

`table-definition` probes the complete checked DAO `DataTypeEnum` input in
isolated disposable databases, then captures bounded column, index, and
relationship checkpoints in one fresh Jet 3 database. DAO schema snapshots are
collected and every DAO object is closed before the corresponding MDB copy.

`row` dispatches through the checked development-only helper allowlist and runs
fixed-only, variable-only, mixed, all-null, page-boundary, growing, shrinking,
deleted, and overflowing scenarios three times each in fresh databases. Each
database is bounded to 64 DAO-visible rows, no job compacts a database, and the
publication helper accepts only the 27 expected MDB filenames. The staged
dispatcher and publisher keep substantial row logic out of the host runner and
fail closed on unknown jobs or artifacts.

`value` uses the same staged allowlist for 33 fresh databases: scalar boundary
rows, CP1252 and diagnostic CP1251 text, and Memo/OLE lengths 32, 512, 2048,
and 4096, each repeated three times with DAO readback. Every database stays
under 4 MiB, no job compacts, and publication accepts only the fixed expected
filenames. The diagnostic CP1251 option is not evidence that Jet selected
CP1251 physical bytes; code-page selection remains explicit in Rust.

Outputs under the external `shared/outbox/` directory are marked
`development_only`. They are disposable diagnostics, not release evidence, and
must not be committed or redistributed.
