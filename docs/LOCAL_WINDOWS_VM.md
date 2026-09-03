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
just windows-dev-index
just windows-dev-bootstrap-layout
just windows-dev-system-catalog
just windows-dev-bootstrap-composer-semantics
just windows-dev-schema-generalization
just windows-dev-multiple-indexes
just windows-dev-definition-continuation
just windows-dev-extended-names
just windows-dev-lvprop-null
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

`index` creates bounded Long ascending, descending, and permuted trees; a
mixed-direction composite tree; isolated key-type definitions; and
relationship checkpoints for each cascade-option combination and deletion.
It closes DAO before publishing the fixed 11 MDB filenames, never compacts,
and keeps every database below 16 MiB. Populated GUID keys remain outside the
observed inventory because this local provider rejected their first indexed
insertion.

`bootstrap-layout` is the SHA-256-pinned, development-only writer-bootstrap
experiment for issue #100. It creates three fresh databases containing one
empty Long-column table, performs the single preregistered rename used to
separate candidate timestamps, and evaluates a finite set of read-only DAO
ablation controls once each. Its analyzer may establish necessity, never
sufficiency. Honest `no_outcome` results are retained without retry. The job
does not test user rows, user indexes, relationships, publication by Rust, or
DAO interoperability.

`system-catalog` is the SHA-256-pinned, development-only successor for the
system-table semantics question in issue #100. Three replicas each create a
fresh database and capture it closed after each of five steps: empty, table
`Alpha`, indexed table `Beta`, saved query `QueryOne`, and relation
`BetaAlpha`. Each closed checkpoint is reopened read-only to record the
DAO-visible TableDef, Container, QueryDef, Relation, and Property metadata;
refused reads are recorded per item, never retried. Its analyzer decodes the
system tables from the captured bytes under the plan's pinned hypotheses and
makes no writer, compatibility, or support claim.

`bootstrap-composer-semantics` is the SHA-256-pinned, development-only fixed
bootstrap successor for issue #100. Three replicas each capture only a fresh
20-page empty database and the 23-page result of adding empty table `Alpha`
with one Long field `Id`. Its analyzer losslessly records page 9's raw keys
while correlating their row locators, follows Alpha's external `LvProp` header
to one bounded opaque LVAL row, and records the fixed page-0 transition. It
does not infer a general key encoding, property format, page-0 counter, writer
correctness, compatibility, or support.

`schema-generalization` is the SHA-256-pinned, development-only successor that
the typed schema planner in #100 depends on. Three replicas each capture one
fresh database after adding `Alpha`, `Beta`, `Gamma`, and `Delta`, then a
second fresh database holding the probed table names. Its analyzer records the
lossless catalog name keys, derives the ASCII collation weight map, diffs the
catalog and access-control rows and appended page roles per create, and
decomposes the long-value property payloads under one pinned chunk framing. It
infers no property semantics, allocation policy, writer correctness,
compatibility, or support.

`multiple-indexes` is the SHA-256-pinned, development-only experiment for issue
#150. Three replicas each create one fresh empty database, identity-check four
pre-mutation copies, and retain closed checkpoints for the empty database plus
one-index, two-index, three-index, and composite-index arms. Publication accepts
exactly those 15 MDBs on success and only an ordered per-replica prefix plus at
most the next checkpoint's identified recovery image after a post-mutation
failure. The experiment tests this bounded page-assignment matrix;
it does not establish arbitrary index shapes, writer correctness, compatibility,
or support.

`definition-continuation` is the development-only experiment for issue #151.
`EXP-0104` preregistered the final successor after `EXP-0095`, `EXP-0100`, and
`EXP-0103` produced valid `no_outcome` results. Three replicas each create one
fresh empty database, identity-check three pre-mutation copies, and retain closed
checkpoints for the empty database plus the established `Alpha(Id Long)`
small-definition control and exact 70- and 140-field wide-definition targets.
Their predicted logical lengths are 66, 2,075, and 4,105 bytes, with required
continuation counts zero, one, and two. Publication accepts exactly those 12
MDBs on success and only an
ordered per-replica prefix plus the active checkpoint's one bounded recovery
image after failure. Ordered `arm_baselines` record every successfully checked
working copy before any table append; append and capture phases require all
three, while a `copy_arms` failure may record only its checked prefix.
Ephemeral base and arm MDBs live under one non-published, non-reparse working
subdirectory, so a cleanup failure cannot add an unrecorded root artifact.
Completed checkpoints are limited to 256 pages. Before
policy enforcement, the producer records exact raw byte length, divisibility,
derived page count, and failed predicate. An aligned recovery-only image may be
salvaged through 512 pages with exact size/hash and `interpreted=false`; this
may include the active `empty` checkpoint after `CreateDatabase` marks mutation
started. Recovery is not opened or decoded, and every post-mutation failure
remains `no_outcome`.
The analyzer records chain pointers, logical chunks,
every appended page's decoded role record, owners, raw tag, and globally-free
status derived from the decoded global allocation map, plus map locators,
raw `LvProp` framing, its bounded external
locator chain when present, referenced versus unreferenced appended LVAL pages,
and the complete page-0 changed-offset set plus counter values. It presumes neither consecutive
placement nor a single-page `LvProp`, and establishes no broader allocation,
writer, compatibility, or support claim. An explicit `unassigned` page-role
record is a bounded observation only when the decoded global map marks that
page free. Every globally-free page's decoded role and owners describe retained
bytes only and establish no current owner, purpose, reuse history, or semantic
role.
An in-use `unassigned` page, a globally-free definition page, or a missing
appended-page role record remains `no_outcome`.
Every appended LVAL page referenced by the catalog `LvProp` must be globally in
use. A decoder-labeled but unreferenced LVAL page may be globally free and is
reported as a retained-byte classification without a current owner or purpose
inference; a referenced globally-free LVAL page remains `no_outcome`.

`EXP-0100` records that the former 2,046-byte `zero` definition used one
continuation page, invalidating that run's control. Its diagnostic chains are
non-promotable under the all-or-`no_outcome` rule and provide no evidence for
the current wide targets. `EXP-0103` records the single authorized `EXP-0102`
run as a valid `no_outcome`: all three producers completed all checkpoints and
baselines without recovery, but every analyzer replica reported `one appended
page 22 is unattributed`. The all-or-`no_outcome` rule promotes no continuation
count, placement, page role, tag, or ownership diagnostic. That replica-stable
diagnosis is design input only for `EXP-0104`'s explicit `unassigned` inventory;
it supplies no format evidence. `EXP-0105` records the exact successor run as
accepted, with all five questions answered identically across three complete
replicas. The exact 66-, 2,075-, and 4,105-byte definitions use chains `[20]`,
`[20, 68]`, and `[20, 219, 218]`; all definition pages are globally in use.
For both wide shapes, pages 22--47 are globally free `unassigned` pages with
raw tag 9. The `one` arm's pages 48--65 and the `two` arm's pages 48--214 are
globally free, decoder-labeled LVAL pages not referenced by the catalog
`LvProp`; their labels establish no current owner, purpose, reuse history, or
semantic role. Issue #151 is evidence-complete for these exact shapes and is
close-ready, without a compatibility or support claim.

`extended-names` is the SHA-256-pinned issue #152 successor for every defined
CP1252 byte above `0x7E`. Three replicas each retained an empty checkpoint, the
same 41 identity-isolated three-byte batch checkpoints used by the first run,
and one metadata-only controls checkpoint. The analyzer decoded only the 41
defined-byte checkpoints. For the controls arm it validates exact UTF-16LE BSTR
identity, append/failure operation, and post-close TableDefs names without
making a CP1252 mapping or physical-key claim. It preserves raw object IDs and
row locators per replica but compares only question-bearing name outcomes,
primary/secondary observations, and pair conclusions. A fixed non-ASCII
sentinel must also survive the child result and the explicit-UTF-8 dispatch and
top-level result pass-through. `EXP-0097` records the first dispatch as
`no_outcome`; `EXP-0099` preregistered the correction. `EXP-0101` records its
one authorized acquisition as `accepted`: all 2,358 name attempts succeeded,
all 129 images remained unchanged through metadata access, every defined byte's
six exact forms decoded, the transport sentinel remained exact, and all five
questions agreed across three replicas. The seven controls establish only
exact Unicode BSTR append and post-close metadata acceptance. The bounded
catalog-key evidence resolves issue #152's six-form experiment question, not
general planner widening. It does not justify arbitrary names, more than two
non-ASCII bytes, or untested byte pairs or contexts; any implementation must
retain the blanket rejection or fail closed to evidenced or positively
composable tested contexts. It makes no general collation, writer,
compatibility, public-support, or support-matrix claim.

`lvprop-null` is the SHA-256-pinned, development-only acceptance experiment for
issue #149. Three replicas each compare the fixed accepted Alpha composer image,
an otherwise equivalent composer image whose catalog `LvProp` is null and whose
mapped long-value page is empty, and a fresh DAO-created Alpha control. It runs
the same bounded read-only Alpha endpoints against each image, records file
identity before and after access, and makes no claim about omitting the mapped
page, a general property grammar, arbitrary schemas, compatibility, or support.

## Interactive discovery loop

`just windows-dev-ps <script.ps1> [--with <file>]...` stages one local
PowerShell script (plus any extra files) in the shared inbox, runs it under
x86 Windows PowerShell on the guest's local disk, and prints its log inline.
The script sees `$env:JET3_WORK` (guest working directory) and
`$env:JET3_OUTBOX` (shared outbox directory for MDBs or JSON to bring back).
Pair it with `jet3-cli inspect <file> [--rows]` and
`jet3-cli inspect <file> --page <n> --hex` on the returned MDBs to decode
pages, catalog records, table definitions, owned pages, and rows through the
reader. Both are development aids for sharpening a hypothesis before it is
pinned into a preregistered plan; nothing they produce is evidence.

Outputs under the external `shared/outbox/` directory are marked
`development_only`. They are disposable diagnostics, not release evidence, and
must not be committed or redistributed.
