# Provenance register

This is the clean-room evidence ledger for `jet3-rs`. It records the origin of
every technical format claim, observed behavior, experiment, and fixture used
by production code or validation. A claim is not implementation evidence until
its stable ID is cited by the relevant format note, test, or source-code
constant.

This register begins with public Microsoft documentation, independently
observed donated files, and bounded page-stride experiments. Donor descriptions
are metadata, not proof of format or DAO compatibility.

## Rules

- Use public documentation, independently observed files, project-generated
  fixtures, and behavior measured through Microsoft DAO.
- Do not inspect, copy, translate, adapt, or derive implementation knowledge
  from MDB Tools, mdbtools-pure-rs, Jackcess, UCanAccess, or other MDB
  implementations.
- A third-party implementation may be invoked only as a licensed black-box
  oracle. Record the command, version, license basis, inputs, and outputs, but
  do not inspect or describe its implementation.
- Record raw observations separately from interpretations. Contradictory
  evidence remains in the ledger and is resolved by a new entry rather than
  rewriting history.
- Hash referenced files with SHA-256. Do not commit fixtures whose origin or
  redistribution rights are unknown.
- Changes to a cited source, protocol, fixture, or scenario create a new entry
  or revision; they do not silently replace prior evidence.

## Entry fields

Every entry contains:

- **ID:** `SRC-nnnn`, `OBS-nnnn`, `EXP-nnnn`, or `FIX-nnnn`.
- **Recorded:** ISO 8601 date and author.
- **Kind:** public source, observation, experiment, generated fixture, donated
  fixture, or black-box result.
- **Question:** the narrowly stated fact being investigated.
- **Origin:** citation or exact generation source; include access date for web
  sources.
- **Environment:** relevant OS, architecture, tool/provider versions, locale,
  code pages, and time zone.
- **Protocol:** exact reproducible steps or a repository path to the versioned
  protocol/scenario.
- **Artifacts:** repository paths and SHA-256 hashes of inputs and outputs.
- **Observation:** factual result, separated from inference.
- **Interpretation:** the limited conclusion used by the project.
- **Usage:** source constants, tests, and documents that cite this ID.
- **Rights:** license, redistribution status, and any restrictions.
- **Review:** reviewer and review date.

Use `not applicable` explicitly rather than omitting a field.

## Public sources

### SRC-0001 — DAO Jet 3 database creation option

- Recorded: 2026-07-23, OpenAI Codex
- Kind: public source
- Question: Which DAO option requests a Jet 3 database, and how is encryption
  avoided?
- Origin: Microsoft Learn, “DBEngine.CreateDatabase method (DAO),” accessed
  2026-07-23,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/dbengine-createdatabase-method-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited page from Microsoft Learn and inspect the
  `Option` parameter table and the remarks immediately following it
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft documents `dbVersion30` as creating the Microsoft Jet
  3.0 file format, compatible with version 3.5. It also documents that omitting
  the encryption constant creates an unencrypted database.
- Interpretation: authoritative empty and generated oracle fixtures must call
  DAO `CreateDatabase` with `dbVersion30` and without `dbEncrypt`. This source
  does not establish any physical file-layout fact or prove that a generated
  file is accepted by Rust.
- Usage: `docs/validation/ACCEPTANCE.md` G3;
  `oracle/windows-dao/README.md`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0002 — DAO `dbVersion30` numeric value

- Recorded: 2026-07-23, OpenAI Codex
- Kind: public source
- Question: What numeric value must a late-bound COM caller pass for the DAO
  `dbVersion30` enumeration member?
- Origin: Microsoft Learn, “DatabaseTypeEnum enumeration (DAO),” accessed
  2026-07-23,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/databasetypeenum-enumeration-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited page from Microsoft Learn and inspect the
  enumeration table row named `dbVersion30`
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft documents the numeric value of `dbVersion30` as 32 and
  describes it as Microsoft Jet database engine version 3.0.
- Interpretation: a PowerShell oracle using late-bound DAO COM may pass integer
  32 when the named enumeration constant is unavailable. This establishes only
  the oracle API argument, not a byte value or offset in an MDB file.
- Usage: `oracle/windows-dao/scripts/probe-provider.ps1`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0003 — DAO system-table attribute

- Recorded: 2026-07-23, OpenAI Codex
- Kind: public source
- Question: Which DAO `TableDef.Attributes` flag identifies a system table,
  and what value must a late-bound COM caller use?
- Origin: Microsoft Learn, “TableDefAttributeEnum enumeration (DAO),” accessed
  2026-07-23,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/tabledefattributeenum-enumeration-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited page from Microsoft Learn and inspect the
  enumeration table row named `dbSystemObject`
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft documents `dbSystemObject` as the flag for a system
  table and gives its numeric value as -2147483646.
- Interpretation: the DAO oracle may apply this flag to `TableDef.Attributes`
  when excluding system tables from an empty user-schema snapshot. This does
  not establish how a system-table attribute is encoded in an MDB file.
- Usage: `oracle/windows-dao/scripts/run-dao-gen-probe.ps1`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0004 — Microsoft Jet file signatures

- Recorded: 2026-07-23, OpenAI Codex
- Kind: public source
- Question: Which byte sequences does Microsoft publish for detecting a Jet
  database file independently of its filename extension?
- Origin: Microsoft Learn, “Microsoft Security Bulletin MS08-028 - Critical,”
  accessed 2026-07-23,
  https://learn.microsoft.com/en-us/security-updates/securitybulletins/2008/ms08-028
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited page and inspect the “Block MDB files from being
  processed through your mail infrastructure” workaround
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft calls all three listed values “15-byte signatures” at
  offset `0x4` and renders them as `Jet System DB `, `Standard Jet DB`, and
  `Temp Jet DB `. The rendered Standard literal is exactly 15 bytes, while the
  rendered System and Temp literals are shorter than 15 bytes.
- Interpretation: an exact 15-byte `Standard Jet DB` match is evidence that a
  file has a Microsoft-published Jet marker. The source does not expose the
  remaining bytes of the 15-byte window after the rendered System and Temp
  literals, so no padding bytes are inferred for them. A signature match does
  not identify a Jet generation, prove that the rest of the file is well
  formed, or establish DAO compatibility.
- Usage: `crates/jet3/src/header.rs`; `crates/jet3/src/candidate.rs`;
  `crates/jet3-cli/src/main.rs`; `OBS-0001`;
  `docs/validation/EXTERNAL_CORPUS.md`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0005 — Jet database page size

- Recorded: 2026-07-23, OpenAI Codex
- Kind: public source
- Question: What page-size change distinguishes the Jet 4.0 MDB format from
  the preceding Jet format generation?
- Origin: Microsoft Learn, “Database Size/Page Size,” accessed 2026-07-23,
  https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ms709815%28v%3Dvs.85%29
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited page and inspect its statement about the Jet 4.0
  page-size change
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft states that Jet 4.0 changed the page size from 2 KB to
  4 KB and that Jet 4.0 MDB files grow in 4-KB chunks.
- Interpretation: Jet 3.x uses 2 KiB pages and Jet 4.0 uses 4 KiB pages. This
  source establishes neither page-header semantics nor a version byte or other
  byte-level generation discriminator.
- Usage: `crates/jet3/src/header.rs`; `crates/jet3/src/jet3_page.rs`;
  `crates/jet3/src/candidate.rs`; `crates/jet3/src/raw_page_stream.rs`;
  `crates/jet3-cli/src/main.rs`; `EXP-0001`;
  `docs/validation/EXTERNAL_CORPUS.md`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0006 — DAO database-version mapping

- Recorded: 2026-07-23, OpenAI Codex
- Kind: public source
- Question: How does Microsoft map Jet engine releases to Access releases, and
  what does DAO `Database.Version` report?
- Origin: Microsoft Learn, “Database.Version property (DAO),” accessed
  2026-07-23,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/database-version-property-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited page, inspect the description of the read-only
  `Database.Version` string, and inspect the Microsoft product-version table
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft documents `Database.Version` as reporting the database
  engine version that created a database. Its table maps Microsoft Jet 3.5
  (1996) to Microsoft Access '97 (8.0), and Microsoft Jet 4.0 (2000) to
  Microsoft Access 2000 (9.0).
- Interpretation: a DAO oracle can use the documented API result as independent
  application-level evidence of the creating engine and can relate Jet 3.5 to
  Access 97. This is API behavior only; it establishes no file-layout field,
  version byte, or page-header semantic.
- Usage: contextual provenance for future DAO evidence; not currently cited by
  production code
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0007 — Jet 3.5 format continuity and physical-design concepts

- Recorded: 2026-07-24, OpenAI Codex
- Kind: public source
- Question: What physical-design facts does Microsoft publish for the Jet 3.5
  engine used by Access 97, and which of those facts are sufficient for binary
  implementation?
- Origin: Microsoft Support, “Rediscovered JET and ODBC white papers,” and the
  linked Microsoft download archive containing Kevin Collins, “Microsoft Jet
  3.5 Performance Overview and Optimization Techniques,” dated 1997-03-06;
  accessed 2026-07-24,
  https://support.microsoft.com/en-US/Access/rediscovered-jet-and-odbc-white-papers
- Environment: documentation retrieval; operating system and architecture are
  not material; provider version, locale, code pages, and time zone are not
  applicable
- Protocol: retrieve the Microsoft Support page, follow its Microsoft-hosted
  white-paper download, verify the archive and `v35perf.doc` hashes below, and
  inspect only the Microsoft-authored Jet 3.5 performance paper
- Artifacts: Microsoft download archive SHA-256
  `fcd3b414dc9c1053a1f7db97a132561924dff2631f14d600805f8f5dab32ffd8`;
  contained `v35perf.doc` SHA-256
  `4e5919f144f0b1d9be6481a452ed33c4332f3acc903d42ea78a7d52f07b7104e`;
  neither artifact is redistributed by this repository
- Observation: Microsoft states that Jet 3.5 introduces no data-format change
  from Jet 3.0 and requires no database data conversion. The paper names a
  database header page, data pages, index B-tree pages, long-value pages for
  Memo/OLE values, and directory pages. It describes allocation by extents of
  up to eight 2-KiB pages in Jet 3.0 and up to 32 pages in Jet 3.5 for large
  tables. It also describes compaction as recopying pages and recreating
  indexes.
- Interpretation: Access 97's Jet 3.5 data format may be treated as the Jet 3.x
  format requested by DAO `dbVersion30`, and the named page/allocation concepts
  may guide controlled oracle scenarios. The source publishes no binary page
  tags, header-field offsets, allocation-map encoding, catalog root, row
  layout, or long-value pointers, so none of those details may be inferred or
  implemented from this source.
- Usage: clean-room experiment planning and future DAO scenario design; not
  currently cited by production code
- Rights: citation to Microsoft-authored public material; no white-paper
  content is redistributed
- Review: pending independent review

### SRC-0008 — ESE/JET Blue is not the Access JET Red format

- Recorded: 2026-07-24, OpenAI Codex
- Kind: public source
- Question: May Microsoft's Extensible Storage Engine physical documentation
  be used as a format source for Access MDB files?
- Origin: Microsoft Learn, “Extensible Storage Engine,” accessed 2026-07-24,
  https://learn.microsoft.com/en-us/windows/win32/extensible-storage-engine/extensible-storage-engine
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited page and inspect the Notes section that
  distinguishes JET Blue/ESE from the JET Red engine used by Microsoft Access
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft documents JET Blue/ESE and Access JET Red as separate,
  independently maintained, non-interchangeable implementations.
- Interpretation: ESE file headers, page layouts, checksums, tags, APIs, and
  other physical details are inapplicable to this project and are prohibited
  as Jet 3 MDB format evidence unless Microsoft explicitly labels a fact as
  JET Red.
- Usage: clean-room research boundary; not cited by production code
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0009 — DAO data-type enumeration for controlled M1 fields

- Recorded: 2026-07-24, OpenAI Codex
- Kind: public source
- Question: Which DAO enumeration members and numeric values name the four
  field types used by the controlled M1 generation plans?
- Origin: Microsoft Learn, “DataTypeEnum enumeration (DAO),” accessed
  2026-07-24,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/datatypeenum-enumeration-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited Microsoft Learn page and inspect only the rows
  for `dbBinary`, `dbText`, `dbLongBinary`, and `dbMemo`
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft identifies `dbBinary` value 9 as binary data,
  `dbText` value 10 as variable-width text, `dbLongBinary` value 11 as binary
  data, and `dbMemo` value 12 as extended text.
- Interpretation: a future late-bound DAO adapter may use these names and
  numeric values when creating the four controlled field kinds. They are API
  enumeration values only; they do not identify MDB type bytes, physical
  layouts, long-value thresholds, page classes, or storage strategies.
- Usage: `oracle/windows-dao/protocol/v1_1/scenario.schema.json`;
  `oracle/windows-dao/examples/m1-inventory.json`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0010 — DAO field creation and documented text-size bound

- Recorded: 2026-07-24, OpenAI Codex
- Kind: public source
- Question: Which DAO API creates a table field, and is a size of eight a
  documented-safe value for a Microsoft Access `dbText` field?
- Origin: Microsoft Learn, “TableDef.CreateField method (DAO)” and “Field.Size
  property (DAO),” accessed 2026-07-24,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/tabledef-createfield-method-dao
  and
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/field-size-property-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve both cited pages; inspect the `CreateField(Name, Type,
  Size)` parameter descriptions and the Microsoft Access text-size remarks
- Artifacts: not applicable; the project stores citations, not redistributed
  copies
- Observation: Microsoft documents `TableDef.CreateField` as creating a field
  in a Microsoft Access workspace. For Microsoft Access Text fields,
  `Field.Size` may be an integer up to 255; Long Binary and Memo report size
  zero, and types other than Text determine their size from the type.
- Interpretation: `dbText` size 8 is within the documented API range. The M1
  plans therefore declare `size` only for `dbText`; they do not invent sizes
  for `dbBinary`, `dbMemo`, or `dbLongBinary`. The source does not specify an
  on-disk width field, encoding, row layout, or long-value cutoff.
- Usage: `oracle/windows-dao/protocol/v1_1/scenario.schema.json`;
  `oracle/windows-dao/examples/DAO-GEN-TEXT8-BASELINE-001.scenario.json`;
  `oracle/windows-dao/examples/DAO-GEN-TEXT8-INDEXED-001.scenario.json`
- Rights: citations to public Microsoft documentation; no documentation
  content is redistributed
- Review: pending independent review

### SRC-0011 — DAO nonunique secondary-index construction

- Recorded: 2026-07-24, OpenAI Codex
- Kind: public source
- Question: Which DAO object operations define an index, and what does the
  `Unique` property mean?
- Origin: Microsoft Learn, “Index object (DAO),” “Index.CreateField method
  (DAO),” and “Index.Unique property (DAO),” accessed 2026-07-24,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/index-object-dao,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/index-createfield-method-dao,
  and
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/index-unique-property-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the three cited pages and inspect the documented index
  construction sequence, index-field method, and `Unique` semantics
- Artifacts: not applicable; the project stores citations, not redistributed
  copies
- Observation: Microsoft directs callers to create an Index from a TableDef,
  create and append its field objects, set index properties, and append the
  Index. It documents `Unique = False` as a secondary index that does not serve
  as a unique identifier, and states that indexes affect record access order
  rather than base-table physical order.
- Interpretation: the controlled text pair may add one nonprimary index with
  `Unique = False` over the existing `dbText(8)` field. No physical record
  order, index page, B-tree encoding, page tag, or allocation effect is
  predicted from the API documentation.
- Usage: `oracle/windows-dao/examples/DAO-GEN-TEXT8-INDEXED-001.scenario.json`;
  `oracle/windows-dao/examples/DAO-PAIR-TEXT8-INDEX-001.pair.json`
- Rights: citations to public Microsoft documentation; no documentation
  content is redistributed
- Review: pending independent review

### SRC-0012 — DAO row and long-value APIs; PowerShell marshalling gap

- Recorded: 2026-07-24, OpenAI Codex
- Kind: public source
- Question: Which DAO APIs add rows and transfer Memo or Long Binary values,
  and do the reviewed Microsoft sources define late-bound PowerShell binary
  marshalling for those calls?
- Origin: Microsoft Learn, “Recordset.AddNew method (DAO),” “Field.Value
  property (DAO),” “Field.AppendChunk method (DAO),” and “Field.FieldSize
  property (DAO),” accessed 2026-07-24,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/recordset-addnew-method-dao,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/field-value-property-dao,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/field-appendchunk-method-dao,
  and
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/field-fieldsize-property-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited pages; inspect the AddNew/Update lifecycle,
  `Field.Value` value contract, `AppendChunk` parameter and sequencing remarks,
  and `FieldSize` scope
- Artifacts: not applicable; the project stores citations, not redistributed
  copies
- Observation: Microsoft documents `AddNew`, field assignment, and `Update` as
  the row-insertion lifecycle. `Field.Value` accepts a Variant appropriate to
  the field type. `AppendChunk` appends a Variant of String subtype to Memo or
  Long Binary fields, with the first call replacing and subsequent calls
  appending within one AddNew/Edit session. `FieldSize` reports bytes used by a
  Memo or Long Binary record field. The reviewed pages contain no contract for
  how PowerShell late-bound COM maps a .NET string or byte array into the exact
  Variant representation expected by a Jet 3 DAO provider.
- Interpretation: the M1 plans may name deterministic semantic input values
  and lengths, but an executor is blocked until a provisioned Windows
  experiment establishes binary and long-binary marshalling and readback. The
  planned lengths are experiment points, not documented storage thresholds.
  Nothing here establishes page, tag, pointer, row, or long-value layout.
- Usage: `oracle/windows-dao/protocol/v1_1/README.md`;
  `oracle/windows-dao/examples/DAO-GEN-BINARY-MARKER-001.scenario.json`;
  `oracle/windows-dao/examples/DAO-GEN-MEMO-LADDER-001.scenario.json`;
  `oracle/windows-dao/examples/DAO-GEN-LONGBINARY-LADDER-001.scenario.json`
- Rights: citations to public Microsoft documentation; no documentation
  content is redistributed
- Review: pending independent review

### SRC-0013 — Jet 3 database-header commit region and `.ldb` locking context

- Recorded: 2026-07-24, OpenAI Codex
- Kind: public source
- Question: Which portion of the Jet 3.0/3.5 database header page stores
  per-connection commit state, and what contextual limits apply when reading
  it?
- Origin: Microsoft Support, “Rediscovered JET and ODBC white papers,” and the
  linked Microsoft-hosted archive containing Kevin Collins, “Understanding
  Microsoft Jet Locking,” updated for Jet 3.5 and DAO 3.5; accessed
  2026-07-24,
  https://support.microsoft.com/en-US/Access/rediscovered-jet-and-odbc-white-papers
- Environment: documentation retrieval on macOS 26.3.1 arm64; provider
  version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the Microsoft-hosted
  `JetWhitePapers_UPDATE1.zip` from the Support page; verify the archive and
  contained `Jetlock.docx` hashes below; inspect the “Layout of the .Ldb File,”
  “Database Header Page,” and lock-description sections of only that
  Microsoft-authored paper
- Artifacts: Microsoft download archive SHA-256
  `fcd3b414dc9c1053a1f7db97a132561924dff2631f14d600805f8f5dab32ffd8`;
  contained `Jetlock.docx` SHA-256
  `2a4a9c00ea6e817751b7bb3d3d76f124156da2cafa61ddd4cdd8f44cde3383fe`;
  neither artifact is redistributed by this repository
- Observation: Microsoft identifies the first database page as the database
  header page. For Jet 3.0 and 3.5 it identifies the 512 bytes beginning at
  hexadecimal offset `0x600` and ending at the 2-KiB page boundary `0x800` as
  256 two-byte commit-state slots. The first pair is used for an exclusive
  connection and the remaining 255 pairs for shared users. It assigns
  `00 00` to a user physically writing to disk and `01 00` to a user that
  accessed a corrupted page, while warning that many other pair values are
  valid internal cache-coordination states. The applicable shared-user slot is
  determined by a corresponding operating-system byte-range lock on the
  companion `.ldb` file. The paper also documents up to 255 physical `.ldb`
  entries of 64 bytes—32 bytes for computer name and 32 for security name—and
  a maximum physical `.ldb` size of 16,320 bytes.
- Interpretation: production code may expose the complete 256 raw two-byte
  slots from the documented first-page range and may name the two documented
  pair values. It must preserve all other pairs without rejecting them and
  must not infer database validity, corruption, clean shutdown, Jet generation,
  user ownership, or compatibility from MDB bytes alone. Contextual state
  diagnosis requires contemporaneous `.ldb` lock evidence from a controlled
  Windows experiment. The source publishes no page tag, table-header location,
  catalog root, allocation encoding, row layout, index encoding, or long-value
  pointer.
- Usage: `crates/jet3/src/commit_state.rs`; future Windows `.ldb` correlation
  experiments
- Rights: citation to Microsoft-authored public material; no white-paper
  content is redistributed
- Review: pending independent review

## Observed behavior

### OBS-0001 — Donated-corpus identity and header bytes

- Recorded: 2026-07-23, OpenAI Codex
- Kind: observation
- Question: Do the four donated candidates have the donor-supplied identities,
  and what bytes occur at the Microsoft-published Jet signature offset?
- Origin: direct inspection of `FIX-0001` through `FIX-0004`; the files remain
  outside the repository beneath the opt-in `JET3_EXTERNAL_FIXTURE_ROOT`
- Environment: macOS 26.3.1 build 25D771280a on arm64; `file(1)` 5.41; locale
  `C.UTF-8`; time zone `America/New_York`; code pages are not applicable
- Protocol: for each exact relative locator in `FIX-0001` through `FIX-0004`,
  recompute its byte count and SHA-256, invoke `file(1)`, and read exactly 15
  bytes beginning at offset `0x4`; the reproducible command sequence is in
  `docs/validation/EXTERNAL_CORPUS.md`
- Artifacts: `FIX-0001`, 1,593,344 bytes,
  SHA-256 `5c18e9d85c2c91a1afdd6d2ddc64c990fd1442c01c753a5d76d4b6d15259537b`;
  `FIX-0002`, 1,593,344 bytes,
  SHA-256 `0a68f70d901d4b519b765323c141c794b427f3d4ee25ef2bd390ce2a493378d9`;
  `FIX-0003`, 1,220,608 bytes,
  SHA-256 `d8dba78c0ce51614f0099e9db7b2cd10790935ffb5db989db5fc766b7c5881fa`;
  `FIX-0004`, 2,129,920 bytes,
  SHA-256 `42aa474ee656d3f1249af08424ed92c91be1388b308906cafb54b4e7ff812d61`
- Observation: the recomputed sizes and SHA-256 values matched the fixture
  records. `file(1)` provided only a generic identification, not a specific Jet
  generation. Every candidate contained the exact 15 bytes `Standard Jet DB`
  (hexadecimal `53 74 61 6e 64 61 72 64 20 4a 65 74 20 44 42`) at offset
  `0x4`.
- Interpretation: all four candidates have stable local identities and match
  one Microsoft-published Jet signature. This does not prove the donor's Access
  97 claim, structural validity, semantic correctness, or DAO compatibility.
- Usage: `EXP-0001`; `docs/validation/EXTERNAL_CORPUS.md`
- Rights: local inspection only under the donor's authorization; no
  redistribution grant; the files and extracted bytes beyond the stated
  observation must not be committed
- Review: pending independent review

## Experiments

### EXP-0001 — Candidate page-stride boundary-byte survey

- Recorded: 2026-07-23, OpenAI Codex
- Kind: experiment
- Question: Does a 1,024-byte or 2,048-byte power-of-two stride produce a
  restricted family of first-byte values at boundaries in the four donated
  candidates?
- Origin: boundary-byte measurements on `FIX-0001` through `FIX-0004`, whose
  identities were checked in `OBS-0001`
- Environment: macOS 26.3.1 build 25D771280a on arm64; `xxd` 2025-08-24 for the
  recorded sampling; Python 3.14.3 for the documented reproduction; locale
  `C.UTF-8`; time zone `America/New_York`; code pages are not applicable
- Protocol: independently for strides 1,024 and 2,048, read the unsigned byte
  at offset zero and every positive multiple of the stride that is less than
  the file length, then sort and deduplicate the values for each file; the
  exact reproducible procedure is in
  `docs/validation/EXTERNAL_CORPUS.md`
- Artifacts: `FIX-0001` through `FIX-0004`; no derived corpus bytes are retained
- Observation: at the 2,048-byte boundaries, `FIX-0001` and `FIX-0002` each
  produced `{00, 01, 02, 03, 04, 09}`, while `FIX-0003` and `FIX-0004` each
  produced `{00, 01, 02, 04, 09}`. The 1,024-byte stride produced many values
  rather than a comparably restricted family.
- Interpretation: 2,048 bytes is the smallest tested power-of-two stride that
  exhibits a restricted boundary-byte family across this four-file corpus.
  That observation is consistent with `SRC-0005` for the donor-declared Jet 3
  candidates, but it does not itself identify their generation or page types
  and is not universal compatibility proof.
- Usage: `SRC-0005`; `docs/validation/EXTERNAL_CORPUS.md`; exploratory evidence
  only, not currently cited by production code
- Rights: local inspection only under the donor's authorization; no
  redistribution grant; no source files or derived byte corpus are committed
- Review: pending independent review

### EXP-0002 — Candidate 2-KiB boundary-prefix frequencies

- Recorded: 2026-07-24, OpenAI Codex
- Kind: experiment
- Question: How often does each first-byte value occur at 2,048-byte
  boundaries in the four donated candidates, and what second byte follows
  nonzero first bytes?
- Origin: boundary-prefix measurements on `FIX-0001` through `FIX-0004`, whose
  identities were checked in `OBS-0001`
- Environment: macOS 26.3.1 build 25D771280a on arm64; Python 3.14.3; locale
  `C.UTF-8`; time zone `America/New_York`; code pages are not applicable
- Protocol: set `JET3_EXTERNAL_FIXTURE_ROOT` and run
  `tools/inspect_external_corpus.py`. After verifying each exact fixture's
  regular-file status, size, and SHA-256, the tool reads up to two bytes at
  offset zero and every successive 2,048-byte boundary, reports a sorted
  first-byte histogram, counts nonzero first bytes, and counts those whose
  available second byte is `0x01`. It rejects unexpected short reads and a file
  that changes during inspection.
- Artifacts: `FIX-0001` through `FIX-0004`; the deterministic JSON observation
  is generated on demand and binds itself to the repository commit and dirty
  state; no corpus bytes are retained
- Observation: `FIX-0001` and `FIX-0002` each had 778 boundaries with
  histograms `{00:34, 01:646, 02:28, 03:7, 04:62, 09:1}` and
  `{00:34, 01:439, 02:28, 03:7, 04:62, 09:208}`, respectively. `FIX-0003`
  had 596 boundaries with `{00:1, 01:437, 02:79, 04:37, 09:42}`.
  `FIX-0004` had 1,040 boundaries with
  `{00:1, 01:778, 02:190, 04:37, 09:34}`. Across all four candidates, every
  one of the 3,122 boundaries with a nonzero first byte had second byte
  `0x01`.
- Interpretation: the restricted first-byte family and stable second byte are
  candidate invariants for future controlled DAO-generated differential
  experiments. Related controller files are not an independent or sufficiently
  varied population, so these values do not establish page-type tags, header
  semantics, validity, Jet generation, or compatibility and are not used by
  production code.
- Usage: `docs/validation/EXTERNAL_CORPUS.md`; exploratory evidence only
- Rights: local inspection only under the donor's authorization; no
  redistribution grant; no source files or derived byte corpus are committed
- Review: pending independent review

### EXP-0003 — Related-snapshot same-index page comparison

- Recorded: 2026-07-24, OpenAI Codex
- Kind: experiment
- Question: For the exact equal-length `FIX-0001` and `FIX-0002` donor
  snapshots, what byte-level differences occur between pages at the same
  2,048-byte index?
- Origin: positional comparison of `FIX-0001` and `FIX-0002`, whose identities
  were checked in `OBS-0001`; the manifest declares the directional comparison
  as `CMP-0001`
- Environment: macOS 26.3.1 build 25D771280a on arm64; Python 3.14.3; locale
  `C.UTF-8`; time zone `America/New_York`; code pages are not applicable
- Protocol: set `JET3_EXTERNAL_FIXTURE_ROOT` and run
  `tools/inspect_external_corpus.py`. The verifier independently rechecks both
  exact files' regular-file status, equal page-aligned size, and SHA-256, then
  reads corresponding complete 2,048-byte pages. It reports a sorted
  first-byte transition matrix, full-page equality counts, and an exact
  2,048-element vector counting differing page pairs at each byte offset. It
  rejects short reads and either file changing during the comparison.
- Artifacts: `FIX-0001`, `FIX-0002`, and manifest comparison `CMP-0001`; the
  deterministic JSON observation is generated on demand and binds itself to
  the repository commit and dirty state; no source or derived corpus bytes are
  retained
- Observation: the 778 same-index page pairs comprised 345 byte-identical and
  433 differing pairs. The first-byte transitions were `00→00:34`,
  `01→01:439`, `01→09:207`, `02→02:28`, `03→03:7`, `04→04:62`, and
  `09→09:1`. The differing pairs contained 89,078 differing byte positions;
  2,043 of the 2,048 page-relative byte offsets differed in at least one pair.
- Interpretation: this is an uncontrolled positional comparison of related
  donor snapshots. Equal length does not establish stable logical page
  identity. The first-byte values are candidate cohorts only and establish no
  page tag, page class, allocation state, header field, row meaning, validity,
  Jet generation, DAO result, or compatibility. Page semantics require
  replicated single-variable DAO-generated fixtures and commit-bound DAO
  evidence.
- Usage: `docs/validation/EXTERNAL_CORPUS.md`; exploratory evidence only; not
  cited by production code
- Rights: local inspection only under the donor's authorization; no
  redistribution grant; no source files or derived byte corpus are committed
- Review: pending independent review

### EXP-0004 — Bounded raw-candidate CLI scan

- Recorded: 2026-07-24, OpenAI Codex
- Kind: experiment
- Question: Does the commit-bound raw-candidate CLI read each exact donated
  candidate within explicit byte and page ceilings and produce deterministic,
  content-agnostic checksums?
- Origin: `FIX-0001` through `FIX-0004`, whose identities were independently
  rechecked against the manifest before the CLI run
- Environment: macOS 26.3.1 build 25D771280a on arm64; Rust 1.96.0; locale
  `C.UTF-8`; time zone `America/New_York`; code pages are not applicable;
  clean repository commit `8ef548bd7bfd64cf9a53068aded473adef75de85`
- Protocol: set `JET3_EXTERNAL_FIXTURE_ROOT`, run
  `tools/inspect_external_corpus.py`, then invoke `jet3-probe` separately for
  each exact manifest locator with `--max-input-bytes` and
  `--max-scan-bytes` equal to its verified size and `--max-pages` equal to its
  exact 2,048-byte page count. The CLI reads every complete candidate page
  once, charges every checksum byte as explicit work, and emits JSON containing
  its non-claim caveats.
- Artifacts: `FIX-0001` through `FIX-0004`; no corpus bytes or derived content
  are retained
- Observation: the four runs respectively reported 778, 778, 596, and 1,040
  pages; checksums `a8da368cdc8c57ae`, `fa78882d0bb856f7`,
  `b4cf9e2ba6d4ea1e`, and `8472e0756be09d7f`; and total work counts 1,594,122,
  1,594,122, 1,221,204, and 2,130,960. All four retained the generic
  `standard` signature classification and exact 2,048-byte arithmetic
  geometry.
- Interpretation: this reproduces bounded raw transfer and checksum accounting
  over four exact external candidates. A checksum match is a tool regression
  observation only. It does not identify Jet generation or encryption state,
  validate structure or semantics, prove safe DAO opening, or establish
  compatibility.
- Usage: `docs/validation/EXTERNAL_CORPUS.md`; exploratory tool evidence only;
  not cited by production code or the support matrix
- Rights: local inspection only under the donor's authorization; no
  redistribution grant; no source files or derived byte corpus are committed
- Review: pending independent review

### EXP-0005 — DAO system-table attribute mask on an empty Jet 3 database

- Recorded: 2026-07-24, OpenAI Codex
- Kind: experiment and black-box result
- Question: Does the checked M0 runner's equality test correctly recognize the
  system tables returned by DAO after creating and reopening an empty
  `dbVersion30` database?
- Origin: project protocol scenario `DAO-GEN-PROBE-001` executed through
  Microsoft DAO 3.6; no donated database or third-party MDB implementation was
  used
- Environment: Windows 11 Pro 10.0.22631 on x64; 32-bit Windows PowerShell
  5.1.22621.6133; culture `en-US`; ANSI code page 1252; Eastern Standard Time;
  `DAO.DBEngine.36` provider version 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  clean repository commit `0804fea6b04a4b3034b2d29cfd627c1e55d9b62b`
- Protocol: run the checked protocol-1.0 provider probe and require its
  disposable `dbVersion30` test to pass; run the checked M0 scenario; then, in
  a separate disposable database created with the same provider and arguments,
  enumerate `TableDefs` and record each name, signed `Attributes` value, and
  the result of bitwise AND with the Microsoft-documented `dbSystemObject`
  value. Delete the separate database after closing all DAO objects.
- Artifacts: retained outside the repository under
  `%TEMP%\jet3-rs-dao-m0\evidence\0804fea6b04a4b3034b2d29cfd627c1e55d9b62b\20260724T234224Z-dao-m0`;
  bundle manifest SHA-256
  `fa3b4cb700d07cdfbb4eeda53a41ecb80b7e2f8766754736c0ded067fccdb6fb`;
  operation log SHA-256
  `0b113956131e917e2812a039cc5698143a499030541e146353a7d441187f7fab`;
  diagnostic MDB SHA-256
  `46e4f7a30a6bac11c30eaa825acd4d47d0e5374eef62a1b5368c38b56773c0e0`
- Observation: the checked M0 run created and closed an unencrypted
  `dbVersion30` MDB, then failed after reopening because it reported
  `MSysACEs`, `MSysObjects`, `MSysQueries`, and `MSysRelationships` as user
  tables. The separate enumeration returned signed `Attributes`
  `-2147483648` (`0x80000000`) for each table. Bitwise AND with
  `dbSystemObject` value `-2147483646` returned `-2147483648`, which is
  nonzero but is not equal to the full enumeration value.
- Interpretation: `dbSystemObject` must be tested as a bitmask with a nonzero
  result; requiring equality with the complete enumeration value rejects the
  system tables observed here. This conclusion is limited to the DAO API
  filtering behavior of the M0 oracle and establishes no MDB byte layout,
  general table semantics, or product compatibility.
- Usage: `oracle/windows-dao/scripts/run-dao-gen-probe.ps1`;
  `oracle/windows-dao/tests/test_dao_runner_contract.py`
- Rights: generated locally through a licensed Microsoft DAO provider; the
  diagnostic database and evidence bundle are retained locally and are not
  redistributed
- Review: pending independent review

### EXP-0006 — PowerShell byte-array marshalling through DAO 3.6

- Recorded: 2026-07-24, OpenAI Codex
- Kind: experiment and black-box result
- Question: Which late-bound PowerShell runtime value and DAO API operation
  deterministically round-trip the controlled `dbBinary` marker and
  `dbLongBinary` boundary ladder?
- Origin: project-controlled values from protocol 1.1, executed by
  `oracle/windows-dao/experiments/m1-marshalling-probe.ps1`; no donated
  database or third-party MDB implementation was used
- Environment: Windows 11 Pro 10.0.22631 on x64; x86 Windows PowerShell
  5.1.22621.6133; CLR 4.0.30319.42000; culture `en-US`; ANSI code page 1252;
  Eastern Standard Time; `DAO.DBEngine.36` provider version 3.6 from
  `dao360.dll` file version 03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  clean repository commit `be8e0c9943fdab088a5a08be956435c897a4a1f2`
- Protocol: require a ready protocol-1.1 environment whose process
  architecture and provider binary hash match the running process; require the
  exact clean Git commit; create a disposable `dbVersion30` database; test the
  fixed eight-byte marker through direct `Field.Value`, `AppendChunk`, and a
  unary-comma wrapper; test locally constructed `System.Byte[]` values filled
  with `0xa5` through `dbLongBinary.AppendChunk` at lengths 1, 2047, 2048,
  2049, 32767, 32768, and 32769; read every successful value through DAO and
  record its CLR type and exact bytes or SHA-256; retain failure type, HRESULT,
  and message; delete the disposable database after hashing it
- Artifacts: ready environment record retained outside the repository,
  SHA-256
  `870ec9ceaaa6a5b9af0ebf16fbf0ef793b943718b49d9f003ed48cfd65af679f`;
  experiment result retained outside the repository at
  `%TEMP%\jet3-rs-m1-marshalling-be8e0c9943fdab088a5a08be956435c897a4a1f2-20260724T235348Z.json`,
  SHA-256
  `cf36d297d88d0d6d6f22b1d4a018479a335be7a38b4b301b0a195fc0e632ad28`
- Observation: direct assignment of the marker as `System.Byte[]` to
  `dbBinary.Value` returned `System.Byte[]` with exact hex
  `0011223344556677`. `dbBinary.AppendChunk(System.Byte[])` failed with COM
  HRESULT `0x800A0CBB` (“Invalid field data type”), and assigning the
  unary-comma wrapper failed with `0x800A0D5D` (“Data type conversion error”).
  `dbLongBinary.AppendChunk(System.Byte[])` passed at every controlled length;
  every DAO readback was `System.Byte[]` with the exact input length and
  SHA-256.
- Interpretation: on this exact x86 PowerShell/DAO environment, an M1 executor
  may use a locally constructed, non-enumerated `System.Byte[]` with direct
  `Value` assignment for `dbBinary` and `AppendChunk` for `dbLongBinary`.
  PowerShell functions returning a byte array must preserve it as one pipeline
  object. This establishes only the adapter representation and DAO API
  behavior; it establishes no MDB physical layout, general compatibility, or
  passing protocol scenario.
- Usage: `oracle/windows-dao/experiments/m1-marshalling-probe.ps1`;
  `oracle/windows-dao/scripts/m1/M1.DaoValues.ps1`;
  `oracle/windows-dao/scripts/m1/M1.Dao.ps1`;
  `oracle/windows-dao/scripts/run-m1-controlled.ps1`
- Rights: generated locally through a licensed Microsoft DAO provider; the
  result JSON is retained locally and the disposable MDB was deleted
- Review: the executor use of this result received an independent adversarial
  review on 2026-07-24; all actionable binding, cleanup, publication,
  boundedness, and failure-evidence findings were addressed before the passing
  M1 run. This review does not itself make the experiment protocol evidence.

### EXP-0007 — Complete controlled M1 DAO generation and readback

- Recorded: 2026-07-25, OpenAI Codex
- Kind: controlled black-box DAO evidence
- Question: Does the exact reviewed executor deterministically create, reopen,
  and semantically read back the complete protocol-1.1 controlled inventory
  through the recorded x86 DAO provider?
- Origin: seven project-controlled `dao_scenario` documents and two
  project-controlled `dao_pair` documents from
  `oracle/windows-dao/examples/m1-inventory.json`; no donated database,
  external MDB, or third-party MDB implementation was used
- Environment: the ready protocol-1.1 environment from `EXP-0006`, SHA-256
  `870ec9ceaaa6a5b9af0ebf16fbf0ef793b943718b49d9f003ed48cfd65af679f`;
  x86 Windows PowerShell 5.1.22621.6133; `DAO.DBEngine.36` 3.6;
  `dao360.dll` 03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  exact clean pushed commit
  `c2e5df29bcd5a779d6aa82582513e28b53f76598`
- Protocol: require the exact clean Git commit and Git-bound executor,
  validator, schemas, inventory, and examples; lock and recheck environment
  and provider identities before output mutation, COM, and publication;
  create only disposable project-generated `dbVersion30` databases; execute
  all seven scenarios; close and reopen through DAO; record typed readback,
  hashes, normalized failures, and both deep pair comparisons; validate the
  complete private stage; publish with one collision-refusing same-volume
  directory move; independently rerun the checked bundle validator
- Artifacts: immutable bundle retained outside the repository at
  `%TEMP%\jet3-rs-dao-m1-executor\evidence\c2e5df29bcd5a779d6aa82582513e28b53f76598\20260725T010957Z-dao-m1`;
  `bundle-manifest.json` SHA-256
  `9bc59d5db419e7283d8013d34e4fea16c3a9add8830c392294b8a8b6b1c32685`;
  `report.json` SHA-256
  `628f01ab5d6b238c4a4c1b0cdebd4339a71d6f313b44665970446a65c3356b25`;
  33 manifest payloads totaling 976,399 bytes
- Observation: all seven scenarios and both pairs passed. DAO exactly read
  back the fixed `dbBinary` marker, `dbLongBinary` byte ladders and `dbMemo`
  text ladders at lengths 1, 2047, 2048, 2049, 32767, 32768, and 32769,
  the controlled `dbText(8)` rows, the repeated empty-database equivalence
  pair, and the single nonunique text-index difference pair.
- Interpretation: this is commit-bound Microsoft DAO evidence for only the
  controlled DAO-generation/readback inventory and its exact environment. It
  establishes no Rust reader, writer, update behavior, MDB physical-layout
  conclusion, donated-fixture compatibility, or general Access 97 support.
  Consequently no product capability advances to `dao_opened` or
  `dao_differential`.
- Usage: `oracle/windows-dao/scripts/run-m1-controlled.ps1`;
  `oracle/windows-dao/scripts/validate_m1_protocol.py`;
  `docs/validation/M1_DAO_EVIDENCE.md`
- Rights: generated locally through the licensed provider; retained outside
  the repository and not redistributed
- Review: executor received the independent adversarial review described
  above; the published bundle passed a separate invocation of the checked
  protocol-1.1 validator

## Fixtures and black-box results

### FIX-0001 — January 2026 controller backup

- Recorded: 2026-07-23, OpenAI Codex
- Kind: donated fixture
- Question: What is the identity and permitted use of the January 2026 donated
  MDB candidate?
- Origin: user-offered controller reference bundle; its top-level README states
  that this candidate was copied from the named January controller backup
  directory. The donor states that it is an actual Access 97 database; this is
  donor metadata, not DAO proof.
- Environment: source controller, Windows/Access/DAO/Jet versions, operating
  system, architecture, locale, code pages, and time zone are unknown
- Protocol: opt in by setting `JET3_EXTERNAL_FIXTURE_ROOT` to the local
  inspection-authorized corpus root, then resolve the exact relative locator
  below and verify size and SHA-256 before use; see
  `docs/validation/EXTERNAL_CORPUS.md`
- Artifacts:
  `controller-backups/full-2026-01-26/SN_7213_UnArchived/ETS3000.mdb`,
  1,593,344 bytes, SHA-256
  `5c18e9d85c2c91a1afdd6d2ddc64c990fd1442c01c753a5d76d4b6d15259537b`
- Observation: donated external candidate; `OBS-0001` records its independently
  checked local identity and header bytes
- Interpretation: useful only as an external, opt-in inspection candidate. Its
  origin statement and filename do not establish format generation,
  correctness, or compatibility.
- Usage: `OBS-0001`; `EXP-0001`; `EXP-0002`; `EXP-0003`; `EXP-0004`;
  `docs/validation/EXTERNAL_CORPUS.md`
- Rights: inspection authorized locally; not redistributable; no redistribution
  grant; do not commit the file or derived content
- Review: pending independent review

### FIX-0002 — July 2026 controller backup

- Recorded: 2026-07-23, OpenAI Codex
- Kind: donated fixture
- Question: What is the identity and permitted use of the July 2026 donated MDB
  candidate?
- Origin: user-offered controller reference bundle; its top-level README states
  that this candidate was copied from the named July controller backup
  directory. The donor states that it is an actual Access 97 database; this is
  donor metadata, not DAO proof.
- Environment: source controller, Windows/Access/DAO/Jet versions, operating
  system, architecture, locale, code pages, and time zone are unknown
- Protocol: opt in by setting `JET3_EXTERNAL_FIXTURE_ROOT` to the local
  inspection-authorized corpus root, then resolve the exact relative locator
  below and verify size and SHA-256 before use; see
  `docs/validation/EXTERNAL_CORPUS.md`
- Artifacts:
  `controller-backups/full-2026-07-23/SN_7213_UnArchived/ETS3000.mdb`,
  1,593,344 bytes, SHA-256
  `0a68f70d901d4b519b765323c141c794b427f3d4ee25ef2bd390ce2a493378d9`
- Observation: donated external candidate; `OBS-0001` records its independently
  checked local identity and header bytes
- Interpretation: useful only as an external, opt-in inspection candidate. Its
  origin statement and filename do not establish format generation,
  correctness, or compatibility.
- Usage: `OBS-0001`; `EXP-0001`; `EXP-0002`; `EXP-0003`; `EXP-0004`;
  `docs/validation/EXTERNAL_CORPUS.md`
- Rights: inspection authorized locally; not redistributable; no redistribution
  grant; do not commit the file or derived content
- Review: pending independent review

### FIX-0003 — Historical 2019 controller backup

- Recorded: 2026-07-23, OpenAI Codex
- Kind: donated fixture
- Question: What is the identity and permitted use of the historical 2019
  donated MDB candidate?
- Origin: user-offered controller reference bundle; its top-level README states
  that this candidate came from `E:\Accurpress Backup - 2019`. The donor states
  that it is an actual Access 97 database; this is donor metadata, not DAO
  proof.
- Environment: source controller, Windows/Access/DAO/Jet versions, operating
  system, architecture, locale, code pages, and time zone are unknown
- Protocol: opt in by setting `JET3_EXTERNAL_FIXTURE_ROOT` to the local
  inspection-authorized corpus root, then resolve the exact relative locator
  below and verify size and SHA-256 before use; see
  `docs/validation/EXTERNAL_CORPUS.md`
- Artifacts: `controller-backups/historical-2019/ETS3000.mdb`, 1,220,608
  bytes, SHA-256
  `d8dba78c0ce51614f0099e9db7b2cd10790935ffb5db989db5fc766b7c5881fa`
- Observation: donated external candidate; `OBS-0001` records its independently
  checked local identity and header bytes
- Interpretation: useful only as an external, opt-in inspection candidate. Its
  origin statement and filename do not establish format generation,
  correctness, or compatibility.
- Usage: `OBS-0001`; `EXP-0001`; `EXP-0002`; `EXP-0004`;
  `docs/validation/EXTERNAL_CORPUS.md`
- Rights: inspection authorized locally; not redistributable; no redistribution
  grant; do not commit the file or derived content
- Review: pending independent review

### FIX-0004 — July 2026 jobs-only controller transfer

- Recorded: 2026-07-23, OpenAI Codex
- Kind: donated fixture
- Question: What is the identity and permitted use of the July 2026 jobs-only
  donated MDB candidate?
- Origin: user-offered controller reference bundle; its top-level README states
  that this file was exported by the controller and then extracted. The donor
  states that it is an actual Access 97 database; this is donor metadata, not
  DAO proof.
- Environment: source controller, export/extraction tools, Windows/Access/DAO/
  Jet versions, operating system, architecture, locale, code pages, and time
  zone are unknown
- Protocol: opt in by setting `JET3_EXTERNAL_FIXTURE_ROOT` to the local
  inspection-authorized corpus root, then resolve the exact relative locator
  below and verify size and SHA-256 before use; see
  `docs/validation/EXTERNAL_CORPUS.md`
- Artifacts:
  `controller-transfers/jobs-only-2026-07-23-three-jobs/extracted/ETS3000.MDB`,
  2,129,920 bytes, SHA-256
  `42aa474ee656d3f1249af08424ed92c91be1388b308906cafb54b4e7ff812d61`
- Observation: donated external candidate; `OBS-0001` records its independently
  checked local identity and header bytes
- Interpretation: useful only as an external, opt-in inspection candidate. Its
  origin statement and filename do not establish format generation,
  correctness, or compatibility.
- Usage: `OBS-0001`; `EXP-0001`; `EXP-0002`; `EXP-0004`;
  `docs/validation/EXTERNAL_CORPUS.md`
- Rights: inspection authorized locally; not redistributable; no redistribution
  grant; do not commit the file or derived content
- Review: pending independent review

## Quarantined bundle paths

Paths matching `project-source/**` or `project-context/**` in the donated
bundle are excluded and quarantined. They must never be inspected, used, or
cited as format sources because they risk prohibited implementation
contamination. Their contents are intentionally not described in this ledger.

## Entry template

Copy this block under the appropriate section and remove this instruction:

```text
### ID — short title

- Recorded:
- Kind:
- Question:
- Origin:
- Environment:
- Protocol:
- Artifacts:
- Observation:
- Interpretation:
- Usage:
- Rights:
- Review:
```
