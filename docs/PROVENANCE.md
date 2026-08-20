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
- **Usage:** source constants, tests, and documents that cite this ID. Declare
  exact tracked files as `` `file:path/from/repository/root` `` and tracked
  directories as `` `dir:path/from/repository/root/` ``. Directory declarations
  must end in `/`. Untagged code spans are narrative, not path declarations.
  Every declared path must contain or cover at least one citation, and every
  tracked citation must be covered by a declaration.
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
- Usage: `file:oracle/windows-dao/scripts/run-dao-gen-probe.ps1`
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
- Usage: `file:oracle/windows-dao/scripts/probe-provider.ps1`;
  `file:oracle/windows-dao/scripts/run-dao-gen-probe.ps1`;
  `file:oracle/windows-dao/scripts/m1/M1.Dao.ps1`
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
- Usage: `file:oracle/windows-dao/scripts/run-dao-gen-probe.ps1`;
  `file:oracle/windows-dao/scripts/m1/M1.Dao.ps1`
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
- Usage: `file:crates/jet3/src/header.rs`;
  `file:crates/jet3/src/database_header.rs`;
  `file:crates/jet3/src/candidate.rs`; `file:crates/jet3/src/database.rs`;
  `file:crates/jet3-cli/src/main.rs`; `OBS-0001`;
  `file:docs/architecture/SEMANTIC_READER.md`;
  `file:docs/validation/repository-contract.json`;
  `file:docs/validation/EXTERNAL_CORPUS.md`;
  `file:fuzz/corpus/manifest.json`; `file:tests/manifest.json`
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
- Usage: `file:crates/jet3/src/header.rs`;
  `file:crates/jet3/src/database_header.rs`;
  `file:crates/jet3/src/jet3_page.rs`;
  `file:crates/jet3/src/candidate.rs`;
  `file:crates/jet3/src/raw_page_stream.rs`;
  `file:crates/jet3/src/database.rs`; `file:crates/jet3-cli/src/main.rs`;
  `EXP-0001`; `file:docs/architecture/SEMANTIC_READER.md`;
  `file:docs/validation/repository-contract.json`;
  `file:docs/validation/EXTERNAL_CORPUS.md`;
  `file:oracle/windows-dao/scripts/observe_m1_pages.py`
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
- Usage: `file:docs/architecture/SEMANTIC_READER.md`; clean-room experiment
  planning and future DAO scenario design; not currently cited by production
  code
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
- Usage: `file:oracle/windows-dao/scripts/m1/M1.Dao.ps1`
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
- Usage: contextual provenance for controlled DAO schema design; not currently
  cited outside this ledger
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
- Usage: contextual provenance for controlled DAO scenario design; not currently
  cited outside this ledger
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
- Usage: `file:oracle/windows-dao/scripts/preflight-m1-controlled.ps1`;
  `file:oracle/windows-dao/tests/test_m1_preflight_contract.py`
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
- Usage: `file:crates/jet3/src/commit_state.rs`;
  `file:crates/jet3/src/database_header.rs`;
  `file:crates/jet3/src/database.rs`;
  `file:docs/architecture/SEMANTIC_READER.md`;
  `file:docs/validation/repository-contract.json`;
  `file:fuzz/corpus/manifest.json`; `dir:oracle/windows-dao/experiments/m4/`;
  `dir:oracle/windows-dao/experiments/m4r1/`;
  `dir:oracle/windows-dao/experiments/m4r2/`;
  `dir:oracle/windows-dao/experiments/m5/`;
  `dir:oracle/windows-dao/experiments/m5s1/`;
  `file:oracle/windows-dao/scripts/m5s1_spec.py`;
  `file:oracle/windows-dao/scripts/m4_spec.py`;
  `file:oracle/windows-dao/scripts/m4r1_spec.py`; future Windows `.ldb`
  correlation experiments
- Rights: citation to Microsoft-authored public material; no white-paper
  content is redistributed
- Review: pending independent review

### SRC-0014 — DAO creation version and encryption controls

- Recorded: 2026-07-25, OpenAI Codex
- Kind: public source
- Question: Which documented DAO inputs select Jet 2.0, Jet 3.0, or Jet 4.0
  data formats during database creation, and which input selects or omits
  encryption?
- Origin: Microsoft Learn, “DBEngine.CreateDatabase method (DAO)” and
  “DatabaseTypeEnum enumeration (DAO),” accessed 2026-07-25,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/dbengine-createdatabase-method-dao
  and
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/databasetypeenum-enumeration-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve both cited Microsoft Learn pages; inspect the
  `CreateDatabase` `Option` parameter and option table, then inspect only the
  `dbVersion20`, `dbVersion30`, `dbVersion40`, and `dbEncrypt` enumeration
  rows
- Artifacts: not applicable; the project stores citations, not redistributed
  copies
- Observation: Microsoft documents `dbVersion20`, `dbVersion30`, and
  `dbVersion40` as requesting Jet 2.0, Jet 3.0 (compatible with 3.5), and Jet
  4.0 file formats respectively. The enumeration page assigns them API values
  16, 32, and 64. Microsoft assigns `dbEncrypt` API value 2, documents it as
  creating an encrypted database, and states that omitting the encryption
  constant creates an unencrypted database.
- Interpretation: the M4 DAO-only experiment may use those named options and
  late-bound numeric API values as a controlled 3-by-2 creation factorial.
  These values are COM/DAO inputs only. They do not identify MDB bytes,
  offsets, bit masks, encryption algorithms, keys, page classes, or any other
  physical encoding, and a successful call does not establish Rust
  compatibility.
- Usage: `dir:oracle/windows-dao/experiments/m4/`;
  `dir:oracle/windows-dao/experiments/m4r1/`;
  `dir:oracle/windows-dao/experiments/m4r2/`;
  `dir:oracle/windows-dao/experiments/m5/`;
  `dir:oracle/windows-dao/experiments/m5s1/`;
  `file:oracle/windows-dao/scripts/m5s1_spec.py`
- Rights: citations to public Microsoft documentation; no documentation
  content is redistributed
- Review: pending independent review

### SRC-0015 — DAO database-version result labels

- Recorded: 2026-07-25, OpenAI Codex
- Kind: public source
- Question: What does DAO `Database.Version` report, and how is its result
  formatted for the three M4 creation-version conditions?
- Origin: Microsoft Learn, “Database.Version property (DAO),” accessed
  2026-07-25,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/database-version-property-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited Microsoft Learn page; inspect the property
  description, the documented `major.minor` result form, and the rows for
  Microsoft Jet 2.0, 3.0, 3.5, and 4.0
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft documents the read-only string as the version of the
  database engine that created the database and gives its result form as
  `major.minor`, with `3.0` as the example. The product table separately names
  Jet releases 2.0, 3.0, 3.5, and 4.0.
- Interpretation: combining this API-result contract with the creation-format
  options in `SRC-0014`, the checked M4 oracle expects exact labels `2.0`,
  `3.0`, and `4.0` for its respective `dbVersion20`, `dbVersion30`, and
  `dbVersion40` files. A different result must fail the experiment rather than
  be recast as a physical-format fact. The label is application-level oracle
  evidence only; it is not an MDB version field, byte string, encoding, or
  Rust compatibility result.
- Usage: `dir:oracle/windows-dao/experiments/m4/`;
  `dir:oracle/windows-dao/experiments/m4r1/`;
  `dir:oracle/windows-dao/experiments/m4r2/`;
  `dir:oracle/windows-dao/experiments/m5/`;
  `dir:oracle/windows-dao/experiments/m5s1/`;
  `file:oracle/windows-dao/scripts/m5s1_spec.py`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0016 — DAO compact-copy version and encryption controls

- Recorded: 2026-07-25, OpenAI Codex
- Kind: public source
- Question: Which version and encryption changes does Microsoft document for
  DAO `CompactDatabase`, and should that second creation path participate in
  the primary M4 factorial?
- Origin: Microsoft Learn, “DBEngine.CompactDatabase method (DAO),” accessed
  2026-07-25,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/dbengine-compactdatabase-method-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited Microsoft Learn page; inspect the `Options`
  parameter, encryption-option table, version-option table, and restrictions
  immediately following those tables
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft documents `dbEncrypt` and `dbDecrypt` as compact-copy
  encryption controls, states that omitting an encryption constant preserves
  source encryption, permits only one version constant, and documents
  `dbVersion20`, `dbVersion30`, and `dbVersion40` as compact-copy data-format
  selections. It also states that the destination version may only be the same
  as or later than the source version.
- Interpretation: `CompactDatabase` provides a documented future independent
  conversion control, but the primary M4 creation factorial excludes it so
  that every sample has one generation path: `CreateDatabase`. None of these
  API controls specifies how version or encryption is represented on disk,
  and no compacted file may be treated as compatibility or physical-layout
  evidence without a separately checked experiment.
- Usage: explicit exclusion and future-control rationale in
  `dir:oracle/windows-dao/experiments/m4/`,
  `dir:oracle/windows-dao/experiments/m4r1/`, and
  `dir:oracle/windows-dao/experiments/m4r2/`; the separately checked experiment
  anticipated here is preregistered as `EXP-0012` and `SRC-0018` in
  `dir:oracle/windows-dao/experiments/m5/`; successor experimental controls are
  preregistered under `dir:oracle/windows-dao/experiments/m5s1/` and checked by
  `file:oracle/windows-dao/scripts/m5s1_spec.py`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0017 — Rust primitive little-endian byte conversion

- Recorded: 2026-07-25, OpenAI Codex
- Kind: public source
- Question: Which standard Rust operations provide explicit little-endian byte
  representations for the format-neutral checked writer?
- Origin: official Rust 1.96 standard-library documentation for integer and
  floating-point `to_le_bytes`, accessed 2026-07-25,
  https://doc.rust-lang.org/1.96.0/std/primitive.u32.html#method.to_le_bytes and
  https://doc.rust-lang.org/1.96.0/std/primitive.f32.html#method.to_le_bytes
- Environment: Rust 1.96.0 standard-library documentation; operating system,
  architecture, locale, code pages, and time zone are not applicable
- Protocol: inspect the documented return values for `to_le_bytes` on the
  signed, unsigned, and floating primitive widths used by
  `crates/jet3/src/binary_writer.rs`; independently round-trip generated bit
  patterns through `BinaryCursor`
- Artifacts: no external artifact is retained; property scenarios
  `PROP-BINARY-WRITER-001` and `PROP-BINARY-WRITER-002` are tracked in
  `tests/manifest.json`
- Observation: Rust documents `to_le_bytes` as returning a primitive's memory
  representation in little-endian byte order.
- Interpretation: the format-neutral writer may use the standard primitive
  conversions rather than implement byte shifts. This establishes language
  operation semantics only; it supplies no Jet layout fact and does not show
  that any encoded buffer is an MDB.
- Usage: format-neutral language-operation rationale; not currently cited
  outside this ledger
- Rights: citation to official Rust documentation; no documentation content is
  redistributed
- Review: pending independent review

### SRC-0018 — DAO compact-copy call contract and documented restrictions

- Recorded: 2026-08-10, Claude (Anthropic)
- Kind: public source
- Question: Which parameters and documented restrictions govern a
  `DBEngine.CompactDatabase` call, so that a future M5 controller can bind an
  exact documented invocation and fail closed before any COM call?
- Origin: Microsoft Learn, “DBEngine.CompactDatabase method (DAO),” accessed
  2026-08-10, the same page cited by `SRC-0016`,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/dbengine-compactdatabase-method-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited Microsoft Learn page; inspect the applicability
  line, the syntax line, every row of the parameter table, and the remarks
  covering the collating-order constants, the encryption constants, the
  version constants, and the source/destination restrictions
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft describes the method as copying and compacting a
  closed database with the option of changing its version, collating order, and
  encryption, and marks the page as applying to Access 2013 and Access 2016.
  The documented call takes five positional arguments: required string
  `SrcName` and `DstName`, then optional variant `DstLocale`, `Options`, and
  `password`. `SrcName` identifies an existing, closed database as a file name
  or full path; the remarks state that the source must be closed and available
  for exclusive use and that an error occurs otherwise. `DstName` is the file
  name and path of the compacted database being created and may not name the
  same database file as `SrcName`. `DstLocale` selects a collating order,
  defaults to the source locale when omitted, and may also carry a `";pwd="`
  password string. `Options` takes one constant or a sum of constants.
  `password` supplies an encryption key preceded by `";pwd="` and is ignored
  when `DstLocale` already carries one; Microsoft marks `password`, `dbEncrypt`,
  and `dbDecrypt` as deprecated and unsupported for `.ACCDB`. For encryption,
  `dbEncrypt` encrypts and `dbDecrypt` decrypts while compacting, and either
  omitting both constants or supplying both gives the destination the same
  encryption as the source. For version, the page lists `dbVersion10`,
  `dbVersion11`, `dbVersion20`, `dbVersion30`, `dbVersion40`, and
  `dbVersion120`; states that only one version constant may be specified, that
  omitting one keeps the source version, and that the destination may be
  compacted only to a version the same as or later than the source; and states
  that the constant affects only the data format of the destination. The page
  further states that the method copies all data and security-permission
  settings, that disk space for both databases is required, and that it should
  not be used to convert Microsoft Access objects. No numeric API values appear
  on this page.
- Interpretation: this entry supplies the API-call provenance for the
  preregistered M5 experiment `EXP-0012`. It fixes which arguments an M5
  compact worker may pass, which option combinations the documentation permits,
  and which preconditions must hold before the call. It refines rather than
  replaces `SRC-0016`, which recorded the version and encryption controls and
  the rule that no compacted file is compatibility or physical-layout evidence
  without a separately checked experiment. Numeric API values remain governed
  by `SRC-0014`, which records `dbVersion20` 16, `dbVersion30` 32,
  `dbVersion40` 64, and `dbEncrypt` 2; no ledger entry records a numeric value
  for `dbDecrypt`, so an M5 controller may not compute or pass an option sum
  containing it until a new entry does. The page does not document what happens
  when the destination file already exists, so a nonexistent destination
  remains a controller-side precondition rather than a documented behavior.
  This entry assigns no on-disk meaning: it identifies no byte, offset, field,
  flag, encryption algorithm, key, page class, or layout; it does not establish
  that a compacted file is readable by this project; and it does not by itself
  authorize execution.
- Usage: `EXP-0012`; `dir:oracle/windows-dao/experiments/m5/`;
  `dir:oracle/windows-dao/experiments/m5s1/`;
  `file:oracle/windows-dao/scripts/m5s1_spec.py`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0019 — DAO `dbDecrypt` numeric value

- Recorded: 2026-08-10, OpenAI Codex
- Kind: public source
- Question: What numeric value must a late-bound DAO caller pass for the
  `dbDecrypt` enumeration member used by `DBEngine.CompactDatabase`?
- Origin: Microsoft Learn, “DatabaseTypeEnum enumeration (DAO),” accessed
  2026-08-10,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/databasetypeenum-enumeration-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the cited Microsoft Learn page and inspect only the
  enumeration row named `dbDecrypt`
- Artifacts: not applicable; the project stores a citation, not a redistributed
  copy
- Observation: Microsoft documents `dbDecrypt` with numeric value 4 and
  describes it as decrypting a database while compacting.
- Interpretation: a late-bound DAO M5 worker may pass integer 4 as the
  `dbDecrypt` API option after all other checked execution gates pass. This is
  only an API enumeration value; it establishes no MDB byte, encryption
  representation, key, offset, layout, or successful provider behavior.
- Usage: `EXP-0015`; revised M5 preregistrations under
  `dir:oracle/windows-dao/experiments/m5/`; successor preregistration under
  `dir:oracle/windows-dao/experiments/m5s1/`;
  `file:oracle/windows-dao/scripts/m5/M5.Dao.ps1`;
  `file:oracle/windows-dao/scripts/m5s1_spec.py`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0020 — Secondary documentation of Jet page, row-slot, and usage-map primitives

- Recorded: 2026-08-19, OpenAI Codex
- Kind: public source; reverse-engineered secondary documentation with a
  lineage likely derivative of MDB Tools `HACKING`, not independent
  corroboration
- Question: Which meanings may the experimental Stage 1 classifier assign
  using only a Jet page's number and byte at offset zero, and which detached
  data-page row-slot and usage-map primitives are sufficiently described for
  bounded internal-only experiments without claiming database traversal or a
  general row layout?
- Origin: `mkopa/ms-mdb` README pinned at commit
  `0be52d6b972ff38a8ee28c8a702010b1dff3a59f`, accessed 2026-08-19,
  https://github.com/mkopa/ms-mdb/blob/0be52d6b972ff38a8ee28c8a702010b1dff3a59f/README.md;
  contextual source: Library of Congress, “Microsoft Access MDB File Format
  Family,” accessed 2026-08-19,
  https://www.loc.gov/preservation/digital/formats/fdd/fdd000462.shtml
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: pin the repository commit, hash the raw README and MIT license,
  and inspect only the README's `General Notes`, `Pages`, `Data Pages`, and
  `Page Usage Maps` documentation sections. No implementation file or code
  from this or any other MDB implementation was consulted. Read the Library of
  Congress page only for source-lineage context: Microsoft has not published a
  public MDB specification, and public descriptions derive from unofficial
  documentation including the MDB Tools `HACKING` lineage.
- Artifacts: raw README SHA-256
  `67d2ba4eb5046eb01ded9ba2c156a01bc868299dbbd5af716049ee9e631c1090`;
  MIT `LICENSE` SHA-256
  `b1ad69508322d20f39dfb374965d9d9bafbde5aa9053d5154d560eced0fc91f1`;
  neither upstream file is redistributed in this repository
- Observation: the table labels byte offset zero as the page type and lists
  `00` as the database definition page at page zero, `01` as a data page, `02`
  as a table-definition page, `03` as an intermediate index page, `04` as a
  leaf index page, and `05` as an extended usage-map bitmap page. It explicitly
  leaves `08` unknown. It provides no supported meaning for any other tag in
  the scope inspected. The source generally describes multibyte pointers and
  integers as little-endian, outside an index-specific exception. Its usage-map
  section describes a type-0 record as byte `00`, a four-byte starting page,
  and following bitmap bytes whose low-order bit is first and whose set bits
  mean allocated to the table. It describes a type-1 record as byte `01`
  followed by four-byte pointers to type-`05` map pages. For Jet 3, a complete
  type-`05` page has the four-byte header `05 01 00 00` followed by 2,044
  bitmap bytes in the same low-bit-first, one-bit-per-page scheme. The source
  states 16,352 mapped bits per such Jet 3 page. Its data-page section describes
  a ten-byte Jet 3 data-page header, a little-endian 16-bit row count in bytes
  eight and nine, and two-byte row-offset entries beginning at byte ten. It
  describes row zero as ending at byte 2,048 and each later row as ending at
  the preceding row's offset. In the high byte of an offset, bit `0x40` marks
  an overflow row and bit `0x80` marks a deleted row. A long-value example
  describes its row identifier as zero-based and projects a raw row offset with
  `raw_offset & 0x1fff`.
- Interpretation: an experimental classifier may inspect exactly byte zero.
  Page zero with tag `00` may be named database definition; a nonzero page with
  tag `00`, or page zero with any nonzero tag, remains `Unknown` while retaining
  the byte. On nonzero pages, tags `01` through `05` may receive the listed
  names. Tag `08` and every other byte remain `Unknown`. Unknown is a successful
  classification, not proof of malformed input. The source establishes no
  other page-header field, payload rule, generation discriminator, structural
  validity, encryption state, catalog location, or DAO compatibility. A
  detached decoder may recognize exactly type `00` and type `01` records,
  decode their documented little-endian fields, enumerate low-bit-first bitmap
  positions, and decode a caller-supplied complete Jet 3 type-`05` page. This
  does not authorize locating the global map on page 1, locating or
  dereferencing a table-definition usage-map record, interpreting null or
  unused type-1 pointers, deriving the absolute page base represented by an
  extended map, or following any pointer. The source's fixed-size wording and
  arithmetic for the Jet 3 table map are not sufficiently consistent to impose
  a 128-byte record-length rule. Those operations remain blocked on physical
  evidence. The data-page description may define a closed A1 candidate search
  over row-count and row-offset slots, including the documented flag bits and
  zero-based long-value example. It does not establish that either candidate
  offset-mask projection applies generally; both remain preregistered nuisance
  candidates to be tested without refitting. It also establishes no official
  row format, field encoding, record payload interpretation, general DAO
  behavior, or compatibility. Because this is a reverse-engineered secondary
  lineage, all classifier, detached-map, and row-slot results are internal-only
  and are not independent or DAO verification.
- Usage: `file:crates/jet3/src/page_kind.rs`;
  `file:crates/jet3/src/allocation.rs`;
  `file:crates/jet3/src/database.rs`;
  `file:docs/architecture/SEMANTIC_READER.md`;
  `file:docs/validation/repository-contract.json`;
  `file:docs/validation/support-matrix.json`; `file:fuzz/README.md`;
  `file:fuzz/corpus/manifest.json`;
  `file:fuzz/fuzz_targets/allocation.rs`;
  `file:fuzz/fuzz_targets/page_classification.rs`; `file:tests/manifest.json`
- Rights: citation to public documentation distributed upstream under the MIT
  license; no upstream documentation content is redistributed
- Review: pending independent review

### SRC-0021 — DAO field type and fixed-width attribute values for A1

- Recorded: 2026-08-19, OpenAI Codex
- Kind: public source
- Question: Which numeric DAO values must the late-bound A1 acquisition worker
  use for a Long Integer field and the fixed-width field attribute?
- Origin: Microsoft Learn, “DataTypeEnum enumeration (DAO)” and
  “Field.Attributes property (DAO),” accessed 2026-08-19; documentation source
  pinned at MicrosoftDocs `office-developer-client-docs` commit
  `eedbd61ca40689e7cfed5e1cfd9440a9dc3ab7a5`,
  https://github.com/MicrosoftDocs/office-developer-client-docs/blob/eedbd61ca40689e7cfed5e1cfd9440a9dc3ab7a5/docs/access/desktop-database-reference/datatypeenum-enumeration-dao.md
  and
  https://github.com/MicrosoftDocs/office-developer-client-docs/blob/eedbd61ca40689e7cfed5e1cfd9440a9dc3ab7a5/docs/access/desktop-database-reference/field-attributes-property-dao.md
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: retrieve the two pinned Microsoft documentation files and inspect
  only the `dbLong` row in `DataTypeEnum` and the `dbFixedField` row in the
  `Field.Attributes` constants table
- Artifacts: pinned `datatypeenum-enumeration-dao.md` SHA-256
  `51147cb927489b36583de4729355fccc78cc0781032453775f2a011f58535d7b`;
  pinned `field-attributes-property-dao.md` SHA-256
  `08c0417611d7f71d786d6fff035c2718046a529c1e25ff07c29bc8c3633f036a`;
  neither documentation file is redistributed by this repository
- Observation: Microsoft documents `dbLong` as Long Integer data with numeric
  value 4. Microsoft documents `dbFixedField` as the field attribute indicating
  a fixed field size, with numeric value 1.
- Interpretation: the late-bound DAO A1 acquisition worker may use integer 4
  when creating its `dbLong` identifier field and integer 1 when setting the
  `dbFixedField` attribute on its fixed-width text field. These are DAO API
  values only; they establish no MDB type byte, field-width encoding, row
  layout, allocation behavior, or Rust compatibility.
- Usage: `file:oracle/windows-dao/scripts/a1/A1.Worker.ps1`
- Rights: citations to public Microsoft documentation; no documentation
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

### EXP-0008 — Controlled M1 physical page-difference observation

- Recorded: 2026-07-25, OpenAI Codex
- Kind: bounded descriptive experiment over controlled DAO output
- Question: Which complete 2-KiB pages and common-length byte positions differ
  between the two controlled M1 scenario pairs, without assigning physical
  format semantics?
- Origin: only the seven project-generated databases in the passing
  `EXP-0007` bundle; no donated database, external MDB, or third-party MDB
  implementation was read
- Environment: Microsoft Windows 10.0.22631 x64; PowerShell 7.4.17; culture
  `en-US`; UTF-8 default and output code page 65001; Eastern Standard Time;
  exact clean pushed observer commit
  `550ddc266eddf7e6765cf929ef50fd5aac19c542`
- Protocol: require the exact clean observer commit, exact `EXP-0007` manifest
  SHA-256, complete passing seven-scenario/two-pair protocol-1.1 validation,
  the 2,048-byte Jet 3 page size established by `SRC-0005`, and page-aligned
  databases within the 16-MiB per-file ceiling; hash each complete page in
  physical order; compare only the two declared pair sides; record differing
  page indices, common-length byte difference counts and first/last offsets,
  and length differences; publish one collision-refusing, fsynced JSON file
  outside the repository; independently rerun the M1 validator and recompute
  all page-hash sequences and pair byte bounds with PowerShell/.NET SHA-256
- Artifacts: source bundle from `EXP-0007`, manifest SHA-256
  `9bc59d5db419e7283d8013d34e4fea16c3a9add8830c392294b8a8b6b1c32685`;
  observation retained at
  `%TEMP%\jet3-rs-m2-observation\550ddc266eddf7e6765cf929ef50fd5aac19c542\20260725T012548Z-m1-pages.json`,
  21,302 bytes, SHA-256
  `59d38601f5c8214a3eaa85b140461de0b54d83bf8664d314c35cba8e5be6f445`
- Observation: the two equal-length repeated empty databases each contain 20
  pages; 151 common-length bytes differ across page indices 2, 3, 4, 5, 18,
  and 19, from absolute offset 4,206 through 40,691. The text baseline contains
  24 pages and the indexed variant 25; 740 bytes differ within their
  49,152-byte common length across page indices 1, 3, 4, 5, and 18 through 23,
  while page 24 is present only in the indexed file. The first and last
  common-length differences are at offsets 3,971 and 49,151.
- Interpretation: these exact-file observations do not identify a page class,
  header, field, row, index node, allocation record, nondeterministic field, or
  any other MDB structure. They establish no Rust compatibility and do not
  advance the support matrix. The repeated-empty differences also show why
  byte identity must not be assumed from semantic DAO equality.
- Usage: `oracle/windows-dao/scripts/observe_m1_pages.py`;
  `docs/validation/M2_PAGE_OBSERVATION.md`
- Rights: generated locally through the licensed provider in `EXP-0007`;
  retained outside the repository and not redistributed
- Review: the source M1 bundle independently revalidated, and an independent
  PowerShell/.NET pass exactly reproduced all seven page-hash sequences and
  both pair byte bounds

### EXP-0009 — Corrected M0 DAO generation and empty readback

- Recorded: 2026-07-25, OpenAI Codex
- Kind: controlled black-box DAO evidence
- Question: After correcting the system-table attribute test identified by
  `EXP-0005`, does the checked M0 executor create, reopen, and read back the
  empty `dbVersion30` scenario through DAO?
- Origin: project scenario `DAO-GEN-PROBE-001` executed through Microsoft DAO
  3.6; no donated database, external MDB, or third-party MDB implementation
  was used
- Environment: Windows 11 Pro 10.0.22631 on x64; x86 Windows PowerShell
  5.1.22621.6133; culture `en-US`; ANSI code page 1252; Eastern Standard Time;
  `DAO.DBEngine.36` from `dao360.dll` file version 03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  exact clean commit `416b834b0d786fdf68efa066ab0e38409e443edf`
- Protocol: require the checked protocol-1.0 ready environment and exact clean
  commit; create an unencrypted disposable database with
  `CreateDatabase(..., dbVersion30)`; close and reopen it; exclude DAO system
  tables using a nonzero `dbSystemObject` bitmask result; retain the empty
  canonical DAO snapshot, operation log, database, report, environment, and
  manifest; independently rerun the checked protocol-1.0 bundle validator
- Artifacts: immutable bundle retained outside the repository at
  `%TEMP%\jet3-rs-dao-m0\evidence\416b834b0d786fdf68efa066ab0e38409e443edf\20260724T234905Z-dao-m0`;
  56,078 total retained bytes; `bundle-manifest.json` SHA-256
  `4651e07957e1740c07c735ac74f2c1e6e7c9038ae9d9bb362b78860453c4c326`;
  `report.json` SHA-256
  `cd38157c1fdeb2b527a9ccad345a001ffe0033106bbc9baaadf0d7e65b84b0b7`;
  environment SHA-256
  `23bf2271297a948752c52da2d178e6263e319c646536084bc9c769467566eeaf`;
  database SHA-256
  `210a726bec325a991722f6a5b9833cc4736c1c33771c1e39a78bd64199f207ee`
- Observation: the retained manifest reports `pass` for the single
  `DAO-GEN-PROBE-001` scenario, and the checked protocol-1.0 validator accepted
  the complete immutable bundle again on 2026-07-25.
- Interpretation: the correction establishes only this exact empty
  DAO-generation/readback scenario and environment. It proves no Rust
  behavior, general schema support, physical MDB layout, or compatibility on
  a later release commit.
- Usage: `oracle/windows-dao/scripts/run-dao-gen-probe.ps1`;
  `docs/validation/DAO_PROVIDER_BLOCKER.md`
- Rights: generated locally through a licensed Microsoft DAO provider;
  retained outside the repository and not redistributed
- Review: retained identity and bundle structure independently revalidated on
  2026-07-25 before this ledger entry was added

### EXP-0010 — Replicated DAO physical-delta isolation

- Recorded: 2026-07-25, OpenAI Codex
- Kind: repeated controlled black-box DAO generation and bounded descriptive
  physical observation
- Question: Across fresh independent DAO processes, which absolute byte/page
  differences are run-specific within repeated empty, text-baseline, and
  text-plus-index cohorts, and which positions remain stable across the
  one-variable baseline/index contrast?
- Origin: three replicas each of the checked project-controlled
  `DAO-GEN-EMPTY-REPEAT-A`, `DAO-GEN-TEXT8-BASELINE-001`, and
  `DAO-GEN-TEXT8-INDEXED-001` scenarios; no donated database, external MDB, or
  third-party MDB implementation was used
- Environment: ready protocol-1.1 environment SHA-256
  `870ec9ceaaa6a5b9af0ebf16fbf0ef793b943718b49d9f003ed48cfd65af679f`;
  x86 Windows PowerShell 5.1.22621.6133; `DAO.DBEngine.36` 3.6;
  `dao360.dll` 03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  exact clean pushed producer commit
  `9977745e6515363cbb179d8d949d34604554b2cd`
- Protocol: execute the checked cyclic schedule `E-B-I`, `B-I-E`, `I-E-B`
  through nine fresh x86 processes; require exact private origin/ref/commit,
  plan/scenario/environment/provider bindings before COM; create one fresh
  `dbVersion30` database per process; reopen and semantically read it through
  DAO; retain all nine databases, page hashes, 18 exact comparison bitmaps,
  three within-cohort variance bitmaps, paired/full-cross
  intersections/unions/histograms, and the stable-cohort delta bitmap;
  validate the complete private stage and publish by one collision-refusing
  same-volume directory rename
- Artifacts: immutable bundle retained outside the repository at
  `%TEMP%\jet3-rs-dao-m3\evidence\9977745e6515363cbb179d8d949d34604554b2cd\20260725T024333Z-dao-m3`;
  75 manifest payloads totaling 673,887 bytes and 689,640 bytes including the
  manifest; `bundle-manifest.json` SHA-256
  `15a7abb3b768ea94233dc3d525a069fb25e595b0ed649f063d117697a6e3c55e`;
  `report.json` SHA-256
  `5fb2feebe9480e78ea1cda56077fc15ae4bfcd9a43ca1ff95ab321fed990419d`;
  `analysis/summary.json` SHA-256
  `d6d66afbe0500b5daa8d8cd22704c6208d1730d293bf3fa313ef9702b0fff0a8`;
  plan SHA-256
  `5943e1a64a0b84916b814c76d87cb81192fb341da800769b1d7dbcb13378d9de`
- Observation: all nine DAO scenarios and all 18 comparisons passed. Every
  empty replica was 40,960 bytes, every text-baseline replica was 49,152
  bytes, and every indexed replica was 51,200 bytes. Within-cohort variance
  covered 122, 220, and 274 absolute byte positions for empty, baseline, and
  indexed cohorts respectively. Across the 49,152-byte baseline/index common
  length, 485 positions were stable within each cohort and different between
  cohorts; the indexed cohort also had one page present in every indexed
  replica and no baseline replica. The paired comparison intersection
  contained 717 positions and union 796; the full nine-way cross intersection
  contained 717 positions and union 798.
- Interpretation: these are descriptive absolute-position candidates for this
  exact nine-sample run only. They do not identify page classes, headers,
  catalog fields, rows, index nodes, allocation structures, or stable format
  offsets. They establish no Rust behavior or compatibility, do not satisfy
  exact-release G3, and do not change the support matrix.
- Usage: `oracle/windows-dao/experiments/m3/`;
  `oracle/windows-dao/scripts/run-m3-controlled.ps1`;
  `oracle/windows-dao/scripts/m3_contract.py`;
  `docs/validation/M3_REPLICATED_DELTA_EVIDENCE.md`
- Rights: generated locally through the licensed Microsoft DAO provider;
  retained outside the repository and not redistributed
- Review: the executor/publisher received independent experimental-design,
  adversarial-safety, and thermo-nuclear maintainability reviews before COM;
  all actionable findings were fixed. The published bundle passed the checked
  validator and a separate PowerShell/.NET recomputation of every manifest
  file hash, database/page hash, cohort/comparison/stable bitmap, aggregate
  intersection/union, and occurrence histogram.

### EXP-0011 — Ready DAO version/encryption file-prefix campaign

- Recorded: 2026-07-25, OpenAI Codex
- Kind: declarative experiment plan with checked controller, workers, analysis,
  and complete-bundle validation; not yet executed
- Question: Across six fresh-process replicas of each documented
  `dbVersion20`/`dbVersion30`/`dbVersion40` and
  unencrypted/`dbEncrypt` creation combination, which absolute positions in a
  bounded file prefix covary descriptively with an API factor after
  accounting for within-condition and no-op reopen variation?
- Origin: project-authored factorial plan using only the DAO controls and
  labels in `SRC-0014` and `SRC-0015`; the documented `CompactDatabase`
  controls in `SRC-0016` are deliberately excluded from the primary campaign
- Environment: not yet executed; the plan requires distinct fresh x86 creator
  and reopen workers per sample and leaves the exact Windows, PowerShell, DAO
  provider, locale, code-page, time-zone, repository, and commit identities to
  a future commit-bound environment record
- Protocol: the checked controller binds
  `oracle/windows-dao/experiments/m4/m4-header-discriminator.plan.json`, the
  exact clean pushed producer commit, its transitive executed sources, and the
  Windows/DAO provider environment before any output mutation. It executes the
  3-by-2 factorial in the complete
  six-block cyclic schedule;
  clone each immutable closed creator database through a controller-owned,
  re-hashed, same-volume, non-hard-linked handoff before launching the separate
  reopen worker; retain paired closed-file creator/reopen observations; analyze
  only `[0x000,0x600)` from each 2,048-byte prefix; exclude `[0x600,0x800)`
  from every comparison; apply only the three preregistered version,
  `dbVersion30` encryption, and equal-nonzero-XOR encryption candidate
  predicates plus their preregistered outcome transition; and validate the
  complete immutable bundle
- Artifacts:
  `oracle/windows-dao/experiments/m4/m4-header-discriminator.plan.json`
  SHA-256
  `05112b48eed37163921763b126b673f2d3ef575af7396d5e18af5cab22424bed`;
  `plan.schema.json` SHA-256
  `feaaf431950407d34c670403b8b4f39ec0df738b55690512335ad2b250ad3d8c`;
  `sample-record.schema.json` SHA-256
  `02205d3903fb428468d2894159e0ec0bcbdbad5e799e656f4ae78ab4775f2136`;
  `analysis-report.schema.json` SHA-256
  `f589e3c20bbd066c4b66bd314cb5712c4f66c245cc876ee8214b247408e5de25`;
  `invocation.schema.json` SHA-256
  `1d4ce392e5b3b405892d22e520d2a9bfe8cf25504b65d59763b5693f8109e053`;
  `worker-result.schema.json` SHA-256
  `29c6cd004955572aa91c149bcda6b1b90686d38ad38e4dbe3597aa1a802e39d5`;
  `operation-log.schema.json` SHA-256
  `c0fc6aa43fdca469b695b0f06ef0fc082b01fb4f1af5e7b3cb953bd268d2a481`;
  `snapshot.schema.json` SHA-256
  `9660165a89d3a9cb3640df013e1da711eb4c0dc723475f779c54e66061af0256`;
  `clone-log.schema.json` SHA-256
  `38af5fcf2cc1034970fe80239f7bbf7af5f0ccda57a3be3c57a0d8fdfbf66c8a`;
  `bundle-manifest.schema.json` SHA-256
  `6309fefdf95f9f725d11a190f4fef8fc8f132543bdc2a2cdd3fbe9f450a28982`
- Observation: no M4 sample has been generated and no M4 byte result exists.
  The declarative plan contains 36 samples, gives every condition every
  within-block launch position exactly once, requires 72 independently bound
  workers, and separately bounds retained bytes, six acquisition reads per
  pair, each validator pass, prefixes, comparison work, worker count, and time.
- Interpretation: the checked plan authorizes execution only from its exact
  clean pushed producer commit after all controller preflight checks pass. A
  complete passing run may yield absolute candidate positions only. It may not
  assign physical meaning, change production code or the support matrix, or
  establish any Rust or MDB compatibility claim.
- Usage: `oracle/windows-dao/experiments/m4/`
- Rights: the plan and schemas are original project material; future generated
  databases and evidence must receive their own provenance and redistribution
  review
- Review: schema lint, checked plan identity/schedule/path/arithmetic tests,
  valid contract shapes, negative unknown/traversal/phase-drift checks,
  controller and isolated-worker contract tests, complete synthetic-bundle
  validation, adversarial corruption tests, and provenance-hash tests passed
  locally on 2026-07-25. Windows-only process and provider checks still require
  execution on the exact producer commit before any M4 bundle can exist.

### EXP-0012 — Preregistered DAO compact-copy confirmation campaign

- Recorded: 2026-08-10, Claude (Anthropic)
- Kind: declarative experiment plan without a checked controller, workers,
  analysis, or complete-bundle validator; not yet executed
- Question: Do databases produced by `CompactDatabase` from `CreateDatabase`
  sources carry the same bounded-prefix byte values that `EXP-0011` observes
  for the matched documented destination version and encryption state, and are
  there positions inside `[0x000,0x600)` that instead covary with the
  generation method?
- Origin: project-authored plan `DAO-M5-COMPACT-CONFIRM-001`, using only the
  DAO creation controls and API values in `SRC-0014`, the `Database.Version`
  result contract in `SRC-0015`, the compact-copy version/encryption controls
  in `SRC-0016`, the call contract and restrictions in `SRC-0018`, and the
  excluded commit region in `SRC-0013`. Recorded on 2026-08-10, before any M4
  execution, so that no M4 byte result can have influenced the M5 conditions,
  schedule, analysis window, comparison topology, predicates, or outcome rules;
  any post-M4 change requires a new plan file and a new provenance entry rather
  than an edit to this one.
- Environment: not yet executed; the plan requires three separate fresh x86
  workers per sample and leaves the exact Windows, PowerShell, DAO provider,
  locale, code-page, time-zone, repository, and commit identities to a future
  commit-bound environment record
- Protocol: the future checked controller must bind
  `oracle/windows-dao/experiments/m5/m5-compact-confirm.plan.json`, the exact
  clean pushed producer commit, its transitive executed sources, the
  Windows/DAO provider environment, and one complete passing M4 bundle by
  bundle-manifest SHA-256 before any output mutation. It executes the 36
  documented-legal source-version, source-encryption, destination-version, and
  compact-encryption conditions in three rotated blocks of the complete
  factorial; per sample it creates the source through `CreateDatabase`, clones
  the closed source through a controller-owned, re-hashed, same-volume,
  non-hard-linked handoff, calls `CompactDatabase` on that closed clone into a
  destination path that does not yet exist and is never the input path with the
  locale and password arguments omitted, clones the closed compacted
  destination through a second identical handoff, and reopens only that second
  clone; it retains closed-file source, destination, and verify observations;
  analyzes only `[0x000,0x600)` of each 2,048-byte prefix; excludes
  `[0x600,0x800)` from every comparison; applies only the three preregistered
  confirmation predicates and their preregistered outcome rules; and validates
  the complete immutable bundle. The gate stays `BLOCKED` until a checked
  controller, workers, analysis, and bundle validator exist, an M4 bundle is
  bound, a ledger entry records the numeric `dbDecrypt` API value, and the
  Windows DAO host is bound to the exact producer commit.
- Artifacts:
  `oracle/windows-dao/experiments/m5/m5-compact-confirm.plan.json` SHA-256
  `beeb6277af6b7224038e5a70ee20238dce907a35f7778b2f2f21f13f1f04d0a4`;
  `oracle/windows-dao/experiments/m5/README.md` SHA-256
  `7b0287d9c83716dcefbbfb21adf5b4bd49389ef6fed5668fcd3804d1559a4474`
- Observation: no M5 sample has been generated, no `CompactDatabase` call has
  been made by this project, and no M5 byte result exists. The declarative plan
  contains 36 conditions and 108 samples, gives every condition three distinct
  within-block positions across three complete blocks, requires 324
  independently bound workers and three per-sample phases, and separately
  bounds retained databases and bytes, eleven acquisition reads per sample,
  each validator pass, prefixes, comparison count and byte visits, worker
  count, and time. The plan records one open provenance requirement: no ledger
  entry records the numeric API value of `dbDecrypt`.
- Interpretation: the plan is a preregistration only. It authorizes no
  execution in its current `BLOCKED` state, and even a complete passing future
  run yields absolute offset agreement or divergence descriptions inside
  `[0x000,0x600)` and nothing more. Results are format observations; per
  `SRC-0016` no compacted file may be treated as compatibility or
  physical-layout evidence, so M5 may not assign physical meaning, change
  production code or the support matrix, or establish any Rust or MDB
  compatibility claim. Agreement with M4 would not confirm that any offset is a
  version or encryption field; divergence would only show that a position
  covaries with the generation method.
- Usage: `oracle/windows-dao/experiments/m5/`
- Rights: the plan and its README are original project material; future
  generated databases and evidence must receive their own provenance and
  redistribution review
- Review: pending independent review. No checked M5 controller, worker,
  analysis, or bundle validator exists yet, and every Windows and provider
  check still requires execution on the exact producer commit before any M5
  bundle can exist.

### EXP-0013 — M4 companion-file execution blocker observation

- Recorded: 2026-08-10, OpenAI Codex
- Kind: controlled black-box DAO failure observation and bounded diagnostic
- Question: Why did the preregistered `EXP-0011` campaign stop during its first
  `dbVersion20` creator phase after the checked controller reached DAO?
- Origin: the checked project M4 controller and two disposable follow-up
  diagnostics using only the licensed Microsoft DAO provider; no donated MDB,
  external MDB, third-party MDB implementation, or Rust format interpretation
  was used
- Environment: Windows 11 Pro 10.0.22631 x64; x86 Windows PowerShell
  5.1.22621.6133; culture `en-US`; ANSI code page 1252; Eastern Standard Time;
  `DAO.DBEngine.36` 3.6 from `dao360.dll` 03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  exact clean pushed producer commit
  `bed346737b7f01fd53f2f6ef5505347c9484da31`
- Protocol: execute `run-m4-controlled.ps1` with run ID
  `20260810T201948Z-m4-main`; retain its structured first-worker failure;
  separately create disposable unencrypted databases with documented
  `dbVersion20` and `dbVersion30` options through the same checked DAO helper;
  close and final-release the database, workspace collection, workspace, and
  engine; force managed finalization; observe only whether the canonical
  sibling `.ldb` path exists; in a second `dbVersion20` diagnostic also close
  the workspace explicitly; delete only the disposable MDB diagnostic files
  and retain any provider-left companion files
- Artifacts: no M4 bundle was published; protocol-1.1 environment record at
  `%TEMP%\jet3-rs-m4-bootstrap\environment.json`, SHA-256
  `8664fddb92483831cc300d4e16a8cb755b7fe4100c3f7b14e5c1220bb86c03d5`;
  retained 64-byte diagnostic companions
  `%TEMP%\JET3-M4-V20-LOCK-879E69A5D1C44111AE65CE18FFE6AE13.ldb`,
  SHA-256
  `6690b84326f011828cbf325c12bf9143c5774603aa9b5525d14e10c8f0d2f7d3`,
  and
  `%TEMP%\JET3-M4-V20-WORKSPACE-CLOSE-F6ADD35F5BEB43FD9F96A098338D7B13.ldb`,
  SHA-256
  `36b7b15d9d4f8178c037e56831738c2777c39672822978a2a371ea6626e38aba`
- Observation: the checked M4 worker stopped at the first `V20-U` creator with
  the structured message “The DAO lock file remains present after database
  close.” Both bounded `dbVersion20` diagnostics left a 64-byte `.ldb` at the
  canonical sibling path after close, final COM release, managed finalization,
  and process exit. The analogous `dbVersion30` helper diagnostic observed no
  `.ldb` immediately after the same helper returned. No prefix candidate set or
  scientific M4 output was built or published.
- Interpretation: for this exact provider, path existence alone is not a valid
  universal post-close acceptance predicate across the M4 version factorial.
  `EXP-0011` remains failed and immutable. A later preregistered revision may
  replace absence with bounded companion-state retention plus exclusive-open
  and stable-identity quiescence proofs, while keeping companion bytes wholly
  outside MDB-prefix analysis. This observation assigns no physical meaning to
  either MDB or `.ldb` bytes and establishes no compatibility.
- Usage: blocker record for a future M4/M5 revision; no production Rust usage
- Rights: generated locally through the licensed provider; retained outside
  the repository and not redistributed
- Review: pending independent review

### EXP-0014 — Preregistered companion-aware M4 revision

- Recorded: 2026-08-10, OpenAI Codex
- Kind: declarative experiment-plan revision with checked controller, isolated
  workers, analysis, and complete-bundle validator; not yet executed
- Question: Can the original M4 version/encryption prefix campaign execute
  without treating canonical `.ldb` path absence as a universal post-close
  predicate, while leaving every scientific input, comparison, predicate, and
  outcome rule unchanged?
- Origin: project-authored experiment
  `DAO-M4-HEADER-DISCRIMINATOR-002`, revised only in response to the bounded
  operational observation in `EXP-0013`; no failed-run MDB prefix or candidate
  result was retained or analyzed when designing this revision
- Environment: not yet executed; the plan requires the same licensed x86 DAO
  provider class and leaves the exact host, runtime, provider binary, locale,
  code pages, time zone, repository, and producer commit to a future bound
  environment record
- Protocol: preserve the original six-condition factorial, six replicas,
  cyclic schedule, 2,048-byte prefixes, `[0x000,0x600)` analysis window, 324
  comparisons, three candidate predicates, and outcome state machine; keep
  pre-COM companion absence; after each checked worker exits, have the
  controller exclusively reread and re-identify the MDB and require exact
  agreement with the worker's size, SHA-256, and prefix hash; derive only the
  canonical sibling `.ldb` path; record it as absent or, if present, require an
  ordinary non-reparse single-link file, exclusively read and hash it under a
  65,536-byte protocol work ceiling, retain it unchanged, and close the bundle
  over it; never delete, move, truncate, synthesize, or copy a companion; keep
  every companion byte outside all scientific analysis
- Artifacts:
  `oracle/windows-dao/experiments/m4r1/m4-header-discriminator-r1.plan.json`
  SHA-256
  `3f0603e88da25a9f8a1cb5cf6860cdd7cba06ef2c21416724109f90f6776b90d`;
  `oracle/windows-dao/experiments/m4r1/post-worker-quiescence.schema.json`
  SHA-256
  `1a4ab0a2ff67873b8b1116fa26f4955630e7970e50952a2e78cef1971a8cc3d2`;
  `oracle/windows-dao/experiments/m4r1/README.md` SHA-256
  `c04cb240929230c4c533e2307dcee14a133ff35a6fa3b4df1347c73aa8db3824`
- Observation: no revised-M4 DAO sample or byte result exists. Schema, exact
  plan, PowerShell source-contract, synthetic absent/present bundle, and
  companion-corruption tests pass locally. The normalized scientific
  projection equals `EXP-0011`; only controller-owned post-worker quiescence,
  companion retention, associated resource bounds, and bundle closure differ.
- Interpretation: this is an additive preregistration, not evidence. A future
  complete bundle may report only the original bounded descriptive M4 outcome.
  Companion state is an orchestration observation and may not enter candidate
  sets, acquire physical meaning, or establish compatibility.
- Usage: `oracle/windows-dao/experiments/m4r1/`;
  `oracle/windows-dao/scripts/run-m4r1-controlled.ps1`; revised M5 input
- Rights: plan, schemas, controller, validators, and tests are original project
  material; future generated evidence requires its own retention and rights
  record
- Review: pending independent review and exact-commit execution

### EXP-0015 — Preregistered companion-aware M5 revision

- Recorded: 2026-08-10, OpenAI Codex
- Kind: declarative experiment-plan revision without a checked M5 controller,
  workers, analysis, or complete-bundle validator; not yet executed
- Question: Can the original compact-copy confirmation campaign consume one
  immutable passing `EXP-0014` bundle and execute with the same post-worker
  quiescence contract, without changing its scientific design?
- Origin: project-authored experiment `DAO-M5-COMPACT-CONFIRM-002`, recorded
  after `EXP-0013` and before any revised-M4 byte result; based on `SRC-0019`
  for the numeric `dbDecrypt` API value and `EXP-0014` only for the revised M4
  identity and operational companion contract
- Environment: not yet executed; the plan requires 324 fresh x86 workers and
  leaves exact host, runtime, provider, locale, code pages, time zone,
  repository, producer commit, and M4 bundle-manifest hash to future checked
  bindings
- Protocol: preserve the original 36 conditions, 108 samples, three rotated
  blocks, 648 comparisons, `[0x000,0x600)` analysis range, excluded range,
  three predicates, and outcome rules; fill only the previously open
  `dbDecrypt` API value and derived option sums from `SRC-0019`; bind one
  passing `DAO-M4-HEADER-DISCRIMINATOR-002` bundle read-only; replace universal
  post-close `.ldb` absence with four fixed controller-owned quiescence records
  per sample, each requiring worker exit, exclusive stable MDB reread and
  bounded absent/present companion retention; never analyze companion bytes
- Artifacts:
  `oracle/windows-dao/experiments/m5/m5-compact-confirm-r2.plan.json` SHA-256
  `7fee21985173b1c5fb9758fd98cdf60dd671eae4b98d723a400be8cf8d3ce59b`;
  `oracle/windows-dao/experiments/m5/README-r2.md` SHA-256
  `4b0d68a23b2c123e7e9517deb876f5326b8729b952f58cd44a12b247a1cfcd4c`
- Observation: no revised-M5 sample exists and no `CompactDatabase` call has
  been made by this project. The execution gate remains `BLOCKED` until the
  complete checked implementation exists and a passing `EXP-0014` bundle is
  bound by manifest SHA-256.
- Interpretation: this revision authorizes no execution in its present state
  and changes no product support claim. Any future result remains a bounded
  DAO-only generation-method observation and cannot identify a physical field
  or establish Rust compatibility.
- Usage: `oracle/windows-dao/experiments/m5/m5-compact-confirm-r2.plan.json`;
  future checked M5R1 implementation
- Rights: plan and README are original project material; future generated
  evidence requires its own retention and redistribution review
- Review: pending independent review

### EXP-0016 — M4R1 exact-path bundle-closure blocker

- Recorded: 2026-08-10, OpenAI Codex
- Kind: controlled black-box DAO execution and exact bundle-validation failure
  observation
- Question: Why did the complete M4R1 worker campaign fail to publish after
  its isolated DAO phases finished?
- Origin: the checked project M4R1 controller, licensed Microsoft DAO provider,
  and project-authored independent bundle validator only; no third-party MDB
  implementation, donated MDB, or Rust format interpretation was used
- Environment: the same Windows, x86 Windows PowerShell, locale, code-page,
  time-zone, `DAO.DBEngine.36`, and `dao360.dll` identities recorded in
  `EXP-0013`; exact clean pushed producer commit
  `79077b9300b741a6f83b1196a963a6a203215ef7`
- Protocol: execute `run-m4r1-controlled.ps1` with run ID
  `20260810T212252Z-m4-r1`; allow the checked controller to run all isolated
  creator and reopen workers, controller quiescence observations, cloning, and
  analysis generation; before publication, run exact case-sensitive manifest
  and bundle-tree closure; inspect only the validator's path-mismatch error and
  do not open any staged MDB, prefix, sample record, or analysis report
- Artifacts: no M4R1 bundle was published or retained; the controller removed
  its failed staging bundle and left only its empty commit parent under
  `%TEMP%\jet3-rs-dao-m4r1\evidence-79077b9`; the provider environment record
  remains `%TEMP%\jet3-rs-m4-bootstrap\environment.json`, SHA-256
  `8664fddb92483831cc300d4e16a8cb755b7fe4100c3f7b14e5c1220bb86c03d5`
- Observation: staged bundle validation reported lowercase declared
  `creator.mdb` and `creator.ldb` paths missing and uppercase `CREATOR.MDB` and
  `CREATOR.ldb` paths extra for all twelve `dbVersion20` creator phases. The
  error reported no prefix byte, candidate set, comparison, or scientific
  outcome. Exact path closure prevented publication.
- Interpretation: M4R1 remains failed and immutable. A later blinded
  operational revision may use uppercase database basenames uniformly across
  every condition while preserving exact case-sensitive tree closure. The
  validator must not become case-insensitive, and the controller must not move,
  copy, delete, or synthesize database or companion evidence to make a path
  pass. This observation assigns no MDB or companion format meaning and
  establishes no compatibility.
- Usage: blocker record for `EXP-0017`; no production Rust usage
- Rights: generated locally through the licensed provider; no bundle was
  retained or redistributed
- Review: pending independent review

### EXP-0017 — Preregistered canonical-path M4 revision

- Recorded: 2026-08-10, OpenAI Codex
- Kind: declarative experiment-plan revision with checked controller, isolated
  workers, analysis, and complete-bundle validator; not yet executed
- Question: Can the unchanged companion-aware M4 campaign close an exact
  case-sensitive bundle when every condition uses preregistered uppercase
  database basenames?
- Origin: project-authored experiment
  `DAO-M4-HEADER-DISCRIMINATOR-003`, revised only from the filename-casing
  validation error in `EXP-0016`; no M4R1 prefix, candidate set, comparison, or
  analysis report was published or inspected before this plan was recorded
- Environment: not yet executed; the plan requires the licensed x86 DAO
  provider and leaves the exact host, runtime, provider binary, locale, code
  pages, time zone, repository, and producer commit to a future bound
  environment record
- Protocol: preserve all `EXP-0014` conditions, replicas, cyclic schedule, DAO
  calls, quiescence, companion retention, resource bounds, 2,048-byte prefixes,
  `[0x000,0x600)` analysis window, 324 comparisons, three predicates, and
  scientific outcome rules; change only every sample's database basenames from
  `creator.mdb` and `reopen.mdb` to `CREATOR.MDB` and `REOPEN.MDB`; derive the
  lowercase `.ldb` extension from those exact basenames; require exact
  case-sensitive manifest/tree closure and retain the no-mutation rules
- Artifacts:
  `oracle/windows-dao/experiments/m4r2/m4-header-discriminator-r2.plan.json`
  SHA-256
  `37c66244ee0021e4e63096ac7b5e0ac27615fa1e82d7afc9fab56fbc7f07ce46`;
  `oracle/windows-dao/experiments/m4r2/post-worker-quiescence.schema.json`
  SHA-256
  `00beb1ba275812cf3dce0da4d85f3f1189e44b0a27ded82e055cd96f2e499438`;
  `oracle/windows-dao/experiments/m4r2/README.md` SHA-256
  `67fd685cd582df21b53d55d6c953368ea91e634cf4a886e181b54466a5012dc8`
- Observation: no M4R2 DAO call, sample, prefix, or result exists. The normalized
  scientific projection is unchanged from `EXP-0014`; only the experiment
  identity, evidence ref, provenance binding, and uniformly uppercase database
  locators differ.
- Interpretation: this is a blinded operational preregistration, not evidence.
  It cannot assign physical meaning, change Rust support, or establish MDB
  compatibility. A result exists only after complete exact-commit execution and
  independent bundle validation.
- Usage: `oracle/windows-dao/experiments/m4r2/`; companion-aware M4 executor;
  future M5 input revision
- Rights: plan, schemas, controller, validators, and tests are original project
  material; future generated evidence requires its own retention and rights
  record
- Review: pending independent review and exact-commit execution

### EXP-0018 — Validated canonical-path M4 execution

- Recorded: 2026-08-10, OpenAI Codex
- Kind: controlled black-box DAO experiment with independently validated
  complete evidence bundle
- Question: Under the preregistered `EXP-0017` design, which absolute offsets
  in the bounded Jet database prefix satisfy the declared version/encryption
  predicates?
- Origin: project-authored `DAO-M4-HEADER-DISCRIMINATOR-003` controller and
  validators using only the licensed Microsoft DAO provider as the independent
  generator and semantic oracle; no third-party MDB implementation, donated
  MDB, or Rust self-read was used
- Environment: Windows 11 Pro 10.0.22631 x64; x86 Windows PowerShell
  5.1.22621.6133; culture `en-US`; ANSI code page 1252; Eastern Standard Time;
  `DAO.DBEngine.36` 3.6 from `dao360.dll` 03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  exact clean pushed producer commit
  `35f5f55f0b7277fc07831db540eab7fa69a41a20`
- Protocol: execute the checked x86 controller with run ID
  `20260810T220332Z-m4-r2` against the exact pushed
  `refs/heads/codex/m4r2-canonical-paths` ref; run 36 samples and 72 isolated
  creator/reopen workers in the preregistered cyclic order; retain all
  controller quiescence records and present companions; publish only after the
  controller's exact bundle validator passes; then invoke
  `m4r1_contract.py validate-bundle` again in a separate read-only Python
  process against the published directory
- Artifacts: retained bundle at
  `%TEMP%\jet3-rs-dao-m4r2\evidence-35f5f55\35f5f55f0b7277fc07831db540eab7fa69a41a20\20260810T220332Z-m4-r2`;
  `bundle-manifest.json` SHA-256
  `0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d`;
  591 manifest payloads; `analysis/report.json` SHA-256
  `994d918ffff3cadb6b193c01d868bc7f275d8d1e2b56dbc162b3e53d923fb6ee`;
  checked plan SHA-256
  `37c66244ee0021e4e63096ac7b5e0ac27615fa1e82d7afc9fab56fbc7f07ce46`
- Observation: execution and both complete-bundle validations passed. All 36
  sample records, 72 database artifacts, 72 prefixes, 72 post-worker
  quiescence records, and 324 comparisons closed exactly. Twelve companion
  artifacts were present, all in the `dbVersion20` creator phases; the other 60
  companion states were absent. Only
  `M4-CANDIDATE-V30-ENCRYPTION` was nonempty: absolute offset 65 (`0x041`)
  appeared in 12 declared comparison occurrences. The scientific outcome is
  `inconclusive`; the other candidate predicates were empty.
- Interpretation: this is a bounded descriptive DAO generation-method result.
  Offset 65 has no assigned physical meaning and is not a new format fact.
  The result does not establish Rust behavior, semantic-reader correctness, or
  MDB compatibility. It may be consumed only as the immutable, manifest-bound
  M4 input to a later preregistered M5 revision.
- Usage: immutable input to the future M5 follow-on revision; no production
  Rust format constant or support claim
- Rights: generated locally through the licensed provider; retained outside
  the repository and not redistributed
- Review: complete project validator passed twice; independent human review
  remains pending

### EXP-0019 — Preregistered exact-M4-bound M5 revision

- Recorded: 2026-08-10, OpenAI Codex
- Kind: additive declarative experiment-plan revision without a checked M5
  controller, workers, analysis, or complete-bundle validator; not yet executed
- Question: Can the unchanged M5 compact-copy campaign consume the exact
  validated `EXP-0018` bundle while preserving case-sensitive evidence closure?
- Origin: project-authored experiment `DAO-M5-COMPACT-CONFIRM-003`; recorded
  after the M4R2 result with that timing disclosed, and derived from the
  immutable M5R2 scientific design plus the operational path correction in
  `EXP-0016`
- Environment: not yet executed; future execution requires the licensed x86
  DAO provider, an exact clean pushed producer commit, and read-only validation
  of the M4R2 manifest before any M5 COM call
- Protocol: preserve all 36 M5 conditions, 108 samples, three rotated blocks,
  648 comparisons, `[0x000,0x600)` analysis range, excluded range, three
  confirmation predicates, and scientific outcome rules from `EXP-0015`;
  bind the exact `EXP-0018` manifest SHA-256 as immutable input; change every
  sample's four database basenames uniformly to `SOURCE.MDB`,
  `COMPACT-INPUT.MDB`, `COMPACTED.MDB`, and `VERIFY.MDB`; keep controller-owned
  quiescence, companion retention, bounds, and exact case-sensitive tree
  closure unchanged
- Artifacts:
  `oracle/windows-dao/experiments/m5/m5-compact-confirm-r3.plan.json` SHA-256
  `92779d51660569635872f36f3c97769b0cb4043b775751569ecd38978dc06f8a`;
  `oracle/windows-dao/experiments/m5/README-r3.md` SHA-256
  `3736ff8a795b8dc6c651578401efeac77b0e2c907f44ce3a2f78f30eb902ecb2`;
  required M4R2 bundle-manifest SHA-256
  `0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d`
- Observation: no M5R3 DAO call, sample, compacted database, or result exists.
  The normalized scientific projection is identical to M5R2. The known M4R2
  candidate result did not change a factor, condition, sample, schedule,
  comparison, predicate, or outcome; it supplies only the previously declared
  immutable M4 input.
- Interpretation: this is a transparent post-M4 operational revision, not M5
  evidence. The execution gate remains `BLOCKED` until the complete checked M5
  implementation and focused corruption/resource tests exist. No compacted
  file may be called compatibility evidence.
- Usage: `oracle/windows-dao/experiments/m5/m5-compact-confirm-r3.plan.json`;
  future checked M5R2 implementation
- Rights: plan and README are original project material; future generated
  evidence requires its own retention and redistribution review
- Review: pending independent review and implementation

### EXP-0020 — Checked M5R3 execution activation

- Recorded: 2026-08-10, OpenAI Codex
- Kind: exact-source implementation and execution-readiness record; no DAO
  experiment result
- Question: Are the blockers preserved in the immutable `EXP-0019`
  preregistration satisfied without rewriting its historical `BLOCKED` field?
- Origin: project-authored M5R3 controller, isolated workers, schemas,
  analysis, complete-bundle validator, and focused contract/corruption tests;
  the independently validated `EXP-0018` bundle is the only M4 input
- Environment: Windows 11 Pro 10.0.22631 x64; x86 Windows PowerShell
  5.1.22621.6133; `DAO.DBEngine.36` 3.6 from `dao360.dll`
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  ready environment record SHA-256
  `8664fddb92483831cc300d4e16a8cb755b7fe4100c3f7b14e5c1220bb86c03d5`;
  checked implementation commit
  `294f7cf06b8a6666b068530e58eb4ca8b62d4181`
- Protocol: validate the immutable M5R3 plan at SHA-256
  `92779d51660569635872f36f3c97769b0cb4043b775751569ecd38978dc06f8a`;
  lint all ten additive schemas; execute the complete Windows DAO contract
  suite and focused M5 plan, analysis, manifest-closure, provider-identity,
  clone-handoff, path-alias, resource-bound, and corruption tests; parse every
  PowerShell source; require the exact clean implementation commit to be
  pushed; independently revalidate the complete M4R2 bundle and its root
  `bundle-manifest.json` SHA-256 before activation; retain the preregistration's
  `execution_gate.status = BLOCKED` as immutable history and use this later
  provenance record as the activation decision
- Artifacts: M5R3 implementation commit
  `294f7cf06b8a6666b068530e58eb4ca8b62d4181`; 31 focused M5 tests passed with
  three platform-dependent symlink skips; the complete Windows DAO contract
  suite passed 298 tests with twelve platform-dependent skips; M4R2 bundle
  root `bundle-manifest.json` independently revalidated at SHA-256
  `0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d`
- Observation: the checked implementation requires 108 samples, 324 fresh x86
  workers, 432 bounded controller quiescence observations, exact uppercase MDB
  locators, optional bounded companion retention, 648 preregistered
  comparisons, canonical analysis recomputation, exact case-sensitive tree
  closure, and a second complete validation before publication. It validates
  the immutable M4 bundle read-only before any M5 COM call and again before
  publication. The implementation and its transitive validators/schemas are
  held against their exact Git blobs.
- Interpretation: all three implementation/host requirements named by
  `EXP-0019` are satisfied for a later exact clean pushed execution commit that
  contains this activation record. This does not change the preregistration,
  report an M5 observation, assign physical meaning, or establish MDB
  compatibility. Any source, plan, provider, M4 binding, or evidence-ref drift
  blocks execution.
- Usage: authorize one exact-commit execution of
  `DAO-M5-COMPACT-CONFIRM-003` through
  `refs/heads/codex/m5r2-m4r2-bound`; future M5 execution record only
- Rights: implementation, schemas, and synthetic test bundles are original
  project material; future licensed-provider output requires its own retention
  and redistribution record
- Review: implementation contracts verified; experimental result pending

### EXP-0021 — M5R3 pre-COM timeout-contract blocker

- Recorded: 2026-08-10, OpenAI Codex
- Kind: controlled execution-gate failure before any DAO activation or
  scientific observation
- Question: Why did the first exact-commit M5R3 controller attempt stop before
  launching an isolated worker?
- Origin: project-authored `DAO-M5-COMPACT-CONFIRM-003` controller and shared
  bounded-process contract only; no MDB, companion, prefix, or DAO result was
  inspected
- Environment: the exact licensed x86 environment recorded in `EXP-0020`;
  exact clean pushed commit
  `56af22e83cbb4a3cfef7c80fbfaf9b21083a7043`; exact remote ref
  `refs/heads/codex/m5r2-m4r2-bound`
- Protocol: invoke the checked x86 controller with run ID
  `20260810T225657Z-m5-r3` and a fresh output root; allow bootstrap, exact Git
  and remote binding, provider preflight, and immutable-plan validation to run;
  stop on the first checked error without changing the shared bounded-process
  policy or starting a worker
- Artifacts: no staging tree, published bundle, database, companion, prefix,
  sample record, or analysis report exists; the fresh output root remained
  empty
- Observation: the controller requested a 180-second timeout for the checked
  immutable-plan validation child. `BoundedProcess.ps1` rejected that request
  because its existing reviewed ceiling is 120 seconds, reporting `M5 checked
  immutable plan validation child timeout is outside the reviewed ceiling.`
  The failure occurred before M4 validation completed, before worker launch,
  and before any COM activation.
- Interpretation: M5R3 is a failed operational attempt and remains immutable.
  The safety ceiling must not be raised to make it pass. A later additive plan
  may reduce both validation-child and worker timeouts uniformly to the
  existing 120-second ceiling while preserving every scientific condition,
  sample, schedule, DAO call, locator, comparison, predicate, outcome,
  artifact, and validation rule.
- Usage: blocker source for the next additive M5 preregistration; no production
  Rust or MDB format usage
- Rights: no licensed-provider output was created or retained
- Review: pending additive preregistration and exact-commit re-execution

### EXP-0022 — Preregistered bounded-timeout M5 revision

- Recorded: 2026-08-10, OpenAI Codex
- Kind: additive declarative experiment-plan and checked-executor revision; not
  yet executed
- Question: Can the unchanged M5 compact-copy campaign execute while every
  controller and worker child timeout stays within the existing reviewed
  120-second bounded-process ceiling?
- Origin: project-authored `DAO-M5-COMPACT-CONFIRM-004`, revised only from the
  pre-COM operational failure in `EXP-0021`; no M5 DAO call, MDB, companion,
  retained prefix, comparison, or candidate result existed before this plan
  was recorded
- Environment: not yet executed; the plan requires the licensed x86
  `DAO.DBEngine.36` host bound to a future exact clean pushed producer commit
  at `refs/heads/codex/m5r3-timeout-bounded`
- Protocol: preserve the complete M5R3 factorial, 36 conditions, 108 samples,
  three-replica rotated schedule, 324 isolated workers, DAO calls, uppercase
  database locators, clone and four-role quiescence topology, companion rules,
  retained prefixes, `[0x000,0x600)` analysis range, 648 comparisons,
  confirmation predicates, scientific outcome rules, immutable M4 binding,
  schemas, and independent publication validation; change only
  `worker_timeout_seconds` from 180 to 120, bind all validator child calls to
  at most 120 seconds, and use the additive experiment/ref/schema identities;
  do not raise or modify the shared hard ceiling
- Artifacts:
  `oracle/windows-dao/experiments/m5/m5-compact-confirm-r4.plan.json` SHA-256
  `7f9b49b18d75824843eb6269fafa25d1b21e4cd82c1bfe289af915ee0783aaed`;
  `oracle/windows-dao/experiments/m5/README-r4.md` SHA-256
  `e3db46890fbc0eb3848045746466bca42eae8c56aac52cde9d60ad8c9d47d107`;
  ten additive schemas under `oracle/windows-dao/experiments/m5r3/`
- Observation: no M5R4 DAO call, worker, database, companion, prefix, sample,
  comparison, analysis, or result exists. Checked normalization found the R3
  and R4 design, validity rules, analysis, all conditions, all samples,
  locators, source projections, and every other bound identical. Schema
  normalization found only additive schema IDs, experiment/ref identities,
  accurate execution-gate metadata, and the timeout reduction. Thirty-five
  focused M5 tests passed with three platform-dependent symlink skips.
- Interpretation: this is a preregistered operational safety correction, not
  M5 evidence. Its execution gate remains `BLOCKED` only until the exact clean
  pushed R4 producer commit and licensed host are bound. It assigns no MDB
  byte meaning, changes no scientific hypothesis, and establishes no
  compatibility.
- Usage: checked M5 controller, isolated workers, analysis, and independent
  bundle validator; future exact-commit M5R4 execution only
- Rights: plan, README, schemas, controller changes, validators, and synthetic
  tests are original project material; future provider output requires its own
  retention and redistribution record
- Review: preregistration and normalized-design checks complete; exact-commit
  activation and execution pending

### EXP-0023 — Checked M5R4 execution activation

- Recorded: 2026-08-10, OpenAI Codex
- Kind: exact-source implementation and execution-readiness record; no DAO
  experiment result
- Question: Are the sole remaining exact-commit/host requirements in the
  immutable `EXP-0022` plan satisfied without changing its historical
  `BLOCKED` gate?
- Origin: project-authored M5R4 plan, additive schemas, checked controller,
  isolated workers, analysis, complete-bundle validator, and focused
  normalization/corruption tests; the independently validated `EXP-0018`
  bundle is the only M4 input
- Environment: Windows 11 Pro 10.0.22631 x64; x86 Windows PowerShell
  5.1.22621.6133; `DAO.DBEngine.36` 3.6 from `dao360.dll`
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  ready environment-record SHA-256
  `8664fddb92483831cc300d4e16a8cb755b7fe4100c3f7b14e5c1220bb86c03d5`;
  checked implementation commit
  `88d119bdcdbebcea77166f1cfcd661acf4e0753f`
- Protocol: validate plan SHA-256
  `7f9b49b18d75824843eb6269fafa25d1b21e4cd82c1bfe289af915ee0783aaed`;
  prove normalized R3/R4 scientific and schema equality with only the declared
  revision identities, accurate one-blocker execution gate, and timeout
  reduction; run the focused M5 contract/corruption suite and complete Windows
  DAO contract suite; parse every PowerShell source; require every bounded
  child timeout to be at most 120 seconds; require the exact clean
  implementation commit to be pushed; independently revalidate the complete
  M4R2 bundle before activation; retain `execution_gate.status = BLOCKED` as
  preregistration history and use this later provenance record as the
  activation decision
- Artifacts: M5R4 implementation commit
  `88d119bdcdbebcea77166f1cfcd661acf4e0753f`; 35 focused M5 tests passed with
  three platform-dependent symlink skips; the complete Windows DAO contract
  suite passed 305 tests with twelve platform-dependent skips; M4R2 bundle
  root `bundle-manifest.json` independently revalidated at SHA-256
  `0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d`
- Observation: the checked implementation binds experiment
  `DAO-M5-COMPACT-CONFIRM-004`, the exact R4 plan, its ten schemas, all
  transitive M5/M4 validators and schemas, the immutable M4 manifest, the
  licensed provider, and the revised evidence ref. The bootstrap and
  controller exact-source sets match. Every static or plan-derived child
  timeout is rejected above 120 seconds before launch.
- Interpretation: the R4 implementation and host requirements are satisfied
  for a later exact clean pushed execution commit containing this activation
  record. This does not rewrite the preregistration, report an M5 observation,
  assign physical meaning, or establish MDB compatibility. Any plan, source,
  provider, M4, timeout, or evidence-ref drift blocks execution.
- Usage: authorize one exact-commit execution of
  `DAO-M5-COMPACT-CONFIRM-004` through
  `refs/heads/codex/m5r3-timeout-bounded`; future M5R4 execution record only
- Rights: implementation, schemas, and synthetic tests are original project
  material; future licensed-provider output requires its own retention and
  redistribution record
- Review: implementation and activation contracts verified; experimental
  result pending

### EXP-0024 — M5R4 isolated-worker preflight blocker

- Recorded: 2026-08-10, OpenAI Codex
- Kind: controlled isolated-worker failure before any DAO activation or
  scientific observation
- Question: Why did the first exact-commit M5R4 worker stop before reading its
  checked invocation and activating DAO?
- Origin: project-authored `DAO-M5-COMPACT-CONFIRM-004` controller, isolated
  worker, and checked PowerShell helper modules only; no MDB, companion,
  prefix, or DAO result was inspected
- Environment: the exact licensed x86 environment recorded in `EXP-0023`;
  exact clean pushed commit
  `1fff6ba3ccf2307405c582fe9e551438a761fde5`; exact remote ref
  `refs/heads/codex/m5r3-timeout-bounded`
- Protocol: invoke the checked x86 controller with run ID
  `20260810T231145Z-m5-r4` and a fresh output root; allow bootstrap, exact Git
  and remote binding, provider preflight, immutable-plan validation, read-only
  M4 validation, staging creation, and the first isolated worker launch; stop
  on the first checked worker error without retrying or changing a validator
- Artifacts: no published or staged bundle, database, companion, prefix,
  sample record, comparison, or analysis exists; failure cleanup left only the
  empty exact-commit parent under the fresh output root; the structured worker
  error reported process ID 17156 and null sample/phase identities because
  invocation parsing had not begun
- Observation: the worker loaded `M1.Preflight.ps1`, but then a checked helper
  called `Assert-M1NoReparseComponents`, whose definition is in the separately
  exact-source-bound `M1.PublicationPaths.ps1`. The worker had not dot-sourced
  that module and returned `System.Management.Automation.CommandNotFoundException`.
  The failure occurred before invocation JSON was read, before a sample or
  phase was accepted, and before any COM activation.
- Interpretation: M5R4 is a failed operational attempt and remains immutable.
  A later additive revision may load the already bound publication-path helper
  in each isolated worker before any path check, while preserving the
  120-second ceiling and every scientific condition, sample, schedule, DAO
  call, locator, artifact, comparison, predicate, outcome, and validation rule.
- Usage: blocker source for the next additive M5 preregistration; no production
  Rust or MDB format usage
- Rights: no licensed-provider output was created or retained
- Review: pending additive preregistration and exact-commit re-execution

### EXP-0025 — Preregistered worker-preflight M5 revision

- Recorded: 2026-08-10, OpenAI Codex
- Kind: additive declarative experiment-plan and checked-worker revision; not
  yet executed
- Question: Can the unchanged M5 compact-copy campaign execute when every
  isolated worker loads the already exact-source-bound publication-path helper
  before invoking its path-safety checks?
- Origin: project-authored `DAO-M5-COMPACT-CONFIRM-005`, revised only from the
  pre-COM operational failure in `EXP-0024`; that failure occurred before the
  invocation was read or COM was activated, and produced no MDB, companion,
  retained prefix, comparison, candidate, or scientific result
- Environment: not yet executed; the plan requires the licensed x86
  `DAO.DBEngine.36` host bound to a future exact clean pushed producer commit
  at `refs/heads/codex/m5r4-worker-preflight-bound`
- Protocol: preserve the complete M5R4 factorial, 36 conditions, 108 samples,
  three-replica rotated schedule, 324 isolated workers, DAO calls, uppercase
  database locators, clone and four-role quiescence topology, companion rules,
  120-second worker timeout and shared hard ceiling, retained prefixes,
  `[0x000,0x600)` analysis range, 648 comparisons, confirmation predicates,
  scientific outcome rules, immutable M4 binding, and independent publication
  validation; change only worker preflight load order so
  `M1.PublicationPaths.ps1` is dot-sourced immediately after
  `M1.Preflight.ps1` and before any helper that calls
  `Assert-M1NoReparseComponents`, then bind the additive experiment, ref, and
  schema identities
- Artifacts:
  `oracle/windows-dao/experiments/m5/m5-compact-confirm-r5.plan.json` SHA-256
  `ca1c46d037edfb7f4df977ba069825c89be5ff66f8aadd5e7f514bb42278315c`;
  `oracle/windows-dao/experiments/m5/README-r5.md` SHA-256
  `25daf72b40929f8a13dbad99b34e0ae7ab34fe30fb99ffd4d241800d2bdf088e`;
  ten additive schemas under `oracle/windows-dao/experiments/m5r4/`
- Observation: no M5R5 DAO call, worker, database, companion, prefix, sample,
  comparison, analysis, or result exists. Checked normalization found the R4
  and R5 scientific design, validity rules, analysis, conditions, samples,
  uppercase locators, resource bounds, and worker timeout identical. Schema
  normalization found only additive schema IDs and experiment/ref identities.
  Thirty-seven focused M5 tests passed with three platform-dependent symlink
  skips, including a fresh-PowerShell no-COM test proving the path-check
  function is defined before worker use.
- Interpretation: this is a preregistered dependency-load correction, not M5
  evidence. Its execution gate remains `BLOCKED` only until the exact clean
  pushed R5 producer commit and licensed host are bound. It weakens no path,
  resource, bundle, provider, or evidence validator, assigns no MDB byte
  meaning, changes no scientific hypothesis, and establishes no compatibility.
- Usage: checked M5 controller, isolated workers, analysis, and independent
  bundle validator; future exact-commit M5R5 execution only
- Rights: plan, README, schemas, worker changes, validators, and synthetic tests
  are original project material; future licensed-provider output requires its
  own retention and redistribution record
- Review: preregistration and normalized-design checks complete; exact-commit
  activation and execution pending

### EXP-0026 — Checked M5R5 execution activation

- Recorded: 2026-08-10, OpenAI Codex
- Kind: exact-source implementation and execution-readiness record; no DAO
  experiment result
- Question: Are the sole remaining exact-commit/host requirements in the
  immutable `EXP-0025` plan satisfied without changing its historical
  `BLOCKED` gate?
- Origin: project-authored M5R5 plan, additive schemas, checked controller,
  isolated workers, analysis, complete-bundle validator, and focused
  normalization/corruption tests; the independently validated `EXP-0018`
  bundle is the only M4 input
- Environment: Windows 11 Pro 10.0.22631 x64; x86 Windows PowerShell
  5.1.22621.6133; `DAO.DBEngine.36` 3.6, CLSID
  `{00000100-0000-0010-8000-00AA006D2EA4}`, from `dao360.dll`
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  ready environment-record SHA-256
  `8664fddb92483831cc300d4e16a8cb755b7fe4100c3f7b14e5c1220bb86c03d5`;
  checked implementation commit
  `7321dfe26a8c2aa02448fe714ed74ffd98070937`
- Protocol: validate plan SHA-256
  `ca1c46d037edfb7f4df977ba069825c89be5ff66f8aadd5e7f514bb42278315c`;
  prove normalized R4/R5 scientific, resource-bound, and schema equality with
  only the declared revision identities and accurate one-blocker execution
  gate; run the focused M5 contract/corruption suite and complete Windows DAO
  contract suite; parse every PowerShell source; prove in a fresh no-COM x86
  PowerShell process that the path guard is defined before worker use; require
  the exact clean implementation and activation commits to be pushed; and
  independently revalidate the complete M4R2 bundle before activation; retain
  `execution_gate.status = BLOCKED` as preregistration history and use this
  later provenance record as the activation decision
- Artifacts: M5R5 implementation commit
  `7321dfe26a8c2aa02448fe714ed74ffd98070937`; 37 focused M5 tests passed with
  three platform-dependent symlink skips; the complete Windows DAO contract
  suite passed 307 tests with twelve platform-dependent skips; M4R2 bundle
  root `bundle-manifest.json` independently revalidated at SHA-256
  `0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d`
- Observation: the checked implementation binds experiment
  `DAO-M5-COMPACT-CONFIRM-005`, the exact R5 plan, its ten schemas, all 68
  transitive M5/M4 sources and schemas, the immutable M4 manifest, the
  licensed provider, and the revised evidence ref. Bootstrap and controller
  exact-source sets match. The isolated worker loads
  `M1.PublicationPaths.ps1` before any M5 helper can invoke
  `Assert-M1NoReparseComponents`; all 120-second bounds remain unchanged.
- Interpretation: the R5 implementation and host requirements are satisfied
  for a later exact clean pushed execution commit containing this activation
  record. This does not rewrite the preregistration, report an M5 observation,
  assign physical meaning, or establish MDB compatibility. Any plan, source,
  provider, M4, timeout, helper order, or evidence-ref drift blocks execution.
- Usage: authorize one exact-commit execution of
  `DAO-M5-COMPACT-CONFIRM-005` through
  `refs/heads/codex/m5r4-worker-preflight-bound`; future M5R5 execution record
  only
- Rights: implementation, schemas, and synthetic tests are original project
  material; future licensed-provider output requires its own retention and
  redistribution record
- Review: implementation and activation contracts verified; experimental
  result pending

### EXP-0027 — M5R5 post-DAO worker return blocker

- Recorded: 2026-08-10, OpenAI Codex
- Kind: controlled isolated-worker failure after bounded DAO activity but
  before worker-result retention or scientific analysis
- Question: Why did the first exact-commit M5R5 source worker fail after the
  checked invocation and DAO phase completed?
- Origin: project-authored `DAO-M5-COMPACT-CONFIRM-005` controller, isolated
  worker, and checked PowerShell helper modules; no retained MDB, prefix,
  comparison, candidate, or analysis result was used
- Environment: the exact licensed x86 environment recorded in `EXP-0026`;
  exact clean pushed commit
  `815f9f08fd129d03a8c37d5ed3fed1b8d1d8c59f`; exact remote ref
  `refs/heads/codex/m5r4-worker-preflight-bound`
- Protocol: invoke the checked x86 controller with run ID
  `20260810T232448Z-m5-r5` and fresh output root
  `%TEMP%\jet3-rs-dao-m5r5\evidence-815f9f0`; allow bootstrap, exact Git and
  remote binding, provider preflight, immutable-plan validation, read-only M4
  validation, staging creation, and the first isolated source worker; stop on
  the first checked worker error without retrying or changing a validator
- Artifacts: no published or staged bundle, database, companion, prefix,
  snapshot, operation log, worker result, sample record, comparison, or
  analysis remains; failure cleanup left only the empty exact-commit parent
  under the fresh output root; the structured worker error reported sample
  `M5-S20U-D20-OMIT-01`, phase `source`, process ID 16196, HRESULT
  `0x80131501`, and `System.Management.Automation.CommandNotFoundException`
- Observation: the worker passed invocation and path preflight, activated the
  bound provider, created the source database, read its documented DAO version
  and empty user-schema observation, closed and released DAO, and then reached
  `return if ($null -eq $snapshot) ...` in `Invoke-M5DaoPhase`. Windows
  PowerShell treated `if` in that return-argument position as a command and
  failed with “The term 'if' is not recognized”. Completion of the preceding
  steps is inferred from the checked control-flow location; no MDB bytes or
  scientific observation survived publication cleanup.
- Interpretation: M5R5 is a failed operational attempt and remains immutable.
  A later additive revision may replace only the invalid return-expression
  form with explicit conditional returns and test both snapshot and null
  branches in fresh x86 PowerShell, while preserving every scientific and
  evidence rule. The observed command-parsing failure assigns no MDB byte
  meaning and establishes no compatibility.
- Usage: blocker source for the next additive M5 preregistration; no production
  Rust or MDB format usage
- Rights: no licensed-provider output was retained or published
- Review: pending additive preregistration and exact-commit re-execution

### EXP-0028 — Preregistered explicit-return M5 revision

- Recorded: 2026-08-10, OpenAI Codex
- Kind: additive declarative experiment-plan and checked-worker revision; not
  yet executed
- Question: Can the unchanged M5 compact-copy campaign complete each isolated
  DAO helper when its source/verify snapshot and compact null result are
  returned through valid explicit Windows PowerShell branches?
- Origin: project-authored `DAO-M5-COMPACT-CONFIRM-006`, revised only from the
  post-DAO operational failure in `EXP-0027`; no worker result, MDB, companion,
  prefix, comparison, candidate, or analysis survived that failed attempt
- Environment: not yet executed; the plan requires the licensed x86
  `DAO.DBEngine.36` host bound to a future exact clean pushed producer commit
  at `refs/heads/codex/m5r5-worker-return-bound`
- Protocol: preserve the complete M5R5 factorial, 36 conditions, 108 samples,
  three-replica rotated schedule, 324 isolated workers, DAO calls, uppercase
  database locators, clone and four-role quiescence topology, companion rules,
  120-second worker timeout and every resource ceiling, retained prefixes,
  `[0x000,0x600)` analysis range, 648 comparisons, confirmation predicates,
  scientific outcome rules, immutable M4 binding, helper load order, and
  independent publication validation; replace only
  `return if ($null -eq $snapshot) ...` after DAO cleanup with an explicit null
  branch followed by an explicit snapshot return, then bind the additive
  experiment, ref, and schema identities
- Artifacts:
  `oracle/windows-dao/experiments/m5/m5-compact-confirm-r6.plan.json` SHA-256
  `f2d69fb1f5c8ebf421c0e48d383614f427ec15a819bd91fabefd5adc572f4de9`;
  `oracle/windows-dao/experiments/m5/README-r6.md` SHA-256
  `8ae475182d962e9065544426966107bbec0795268fbfd1cce42813a2b2659122`;
  ten additive schemas under `oracle/windows-dao/experiments/m5r5/`
- Observation: no M5R6 DAO call, worker, database, companion, prefix, sample,
  comparison, analysis, or result exists. Checked normalization found the R5
  and R6 scientific design, validity rules, analysis, conditions, samples,
  uppercase locators, resource bounds, helper load requirement, and timeout
  identical. Schema normalization found only additive schema IDs and
  experiment/ref identities.
- Interpretation: this is a preregistered language-level return correction,
  not M5 evidence. Its execution gate remains `BLOCKED` only until the exact
  clean pushed R6 producer commit and licensed host are bound. It weakens no
  path, resource, bundle, provider, or evidence validator, assigns no MDB byte
  meaning, changes no scientific hypothesis, and establishes no compatibility.
- Usage: checked M5 controller, isolated workers, analysis, and independent
  bundle validator; future exact-commit M5R6 execution only
- Rights: plan, README, schemas, worker changes, validators, and synthetic tests
  are original project material; future licensed-provider output requires its
  own retention and redistribution record
- Review: preregistration and normalized-design checks complete; exact-commit
  implementation, activation, and execution pending

### EXP-0029 — Checked M5R6 execution activation

- Recorded: 2026-08-10, OpenAI Codex
- Kind: exact-source implementation and execution-readiness record; no DAO
  experiment result
- Question: Are the sole remaining exact-commit/host requirements in the
  immutable `EXP-0028` plan satisfied without changing its historical
  `BLOCKED` gate?
- Origin: project-authored M5R6 plan, additive schemas, checked controller,
  isolated workers, analysis, complete-bundle validator, and focused
  normalization/corruption tests; the independently validated `EXP-0018`
  bundle is the only M4 input
- Environment: Windows 11 Pro 10.0.22631 x64; x86 Windows PowerShell
  5.1.22621.6133; `DAO.DBEngine.36` 3.6, CLSID
  `{00000100-0000-0010-8000-00AA006D2EA4}`, from `dao360.dll`
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  ready environment-record SHA-256
  `8664fddb92483831cc300d4e16a8cb755b7fe4100c3f7b14e5c1220bb86c03d5`;
  checked implementation commit
  `4a2f857a78eaaaab3f59c73dcc00c7e9807a8549`
- Protocol: validate plan SHA-256
  `f2d69fb1f5c8ebf421c0e48d383614f427ec15a819bd91fabefd5adc572f4de9`;
  prove normalized R5/R6 scientific, resource-bound, and schema equality; run
  the focused M5 contract/corruption suite and complete Windows DAO contract
  suite; parse every PowerShell source; execute the production DAO-result
  helper in a fresh no-COM Windows PowerShell process and require zero output
  for compact null plus exactly one typed snapshot for source/verify; require
  the exact clean implementation and activation commits to be pushed; and
  independently revalidate the complete M4R2 bundle before activation; retain
  `execution_gate.status = BLOCKED` as preregistration history and use this
  later provenance record as the activation decision
- Artifacts: M5R6 implementation commit
  `4a2f857a78eaaaab3f59c73dcc00c7e9807a8549`; 39 focused M5 tests passed with
  three platform-dependent symlink skips; the complete Windows DAO contract
  suite passed 309 tests with twelve platform-dependent skips; M4R2 bundle
  root `bundle-manifest.json` independently revalidated at SHA-256
  `0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d`
- Observation: the checked implementation binds experiment
  `DAO-M5-COMPACT-CONFIRM-006`, the exact R6 plan and ten schemas, all 68
  transitive M5/M4 sources and schemas, the immutable M4 manifest, licensed
  provider, and revised evidence ref. Bootstrap and controller exact-source
  sets match. `return if` is absent; the checked result helper uses explicit
  branches after DAO cleanup; all 120-second and resource bounds are unchanged.
- Interpretation: the R6 implementation and host requirements are satisfied
  for a later exact clean pushed execution commit containing this activation
  record. This does not rewrite the preregistration, report an M5 observation,
  assign physical meaning, or establish MDB compatibility. Any plan, source,
  provider, M4, timeout, return behavior, or evidence-ref drift blocks
  execution.
- Usage: authorize one exact-commit execution of
  `DAO-M5-COMPACT-CONFIRM-006` through
  `refs/heads/codex/m5r5-worker-return-bound`; future M5R6 execution record only
- Rights: implementation, schemas, and synthetic tests are original project
  material; future licensed-provider output requires its own retention and
  redistribution record
- Review: implementation and activation contracts verified; experimental
  result pending

### EXP-0030 — M5R6 compact-result null-coercion blocker

- Recorded: 2026-08-10, OpenAI Codex
- Kind: controlled isolated-worker/result-validation failure after bounded DAO
  activity but before sample retention or scientific analysis
- Question: Why did the first exact-commit M5R6 compact worker result fail its
  unchanged checked schema?
- Origin: project-authored `DAO-M5-COMPACT-CONFIRM-006` controller, isolated
  workers, artifact builder, and checked validator; no retained MDB, prefix,
  comparison, candidate, or analysis result was used
- Environment: the exact licensed x86 environment recorded in `EXP-0029`;
  exact clean pushed commit
  `1b2d9178ab4a555d3eefd2fadd23840d6fc02763`; exact remote ref
  `refs/heads/codex/m5r5-worker-return-bound`
- Protocol: invoke the checked x86 controller with run ID
  `20260810T233958Z-m5-r6` and fresh output root
  `%TEMP%\jet3-rs-dao-m5r6\evidence-1b2d917`; allow bootstrap, exact Git and
  remote binding, provider preflight, immutable-plan validation, read-only M4
  validation, staging, the first source phase and handoff clone, and the first
  compact worker; stop when unchanged compact-result validation rejects the
  malformed optional prefix without retry or validator modification
- Artifacts: no published or staged bundle, database, companion, prefix,
  snapshot, operation log, worker result, sample record, comparison, or
  analysis remains; failure cleanup left only the empty exact-commit parent;
  the validator reported that `$.database_observations[0].prefix` was neither
  JSON null nor a valid artifact reference because its path was an empty string
- Observation: the compact-input observation intentionally has no retained
  prefix. The worker passed PowerShell `$null` to a parameter declared
  `[AllowNull()][string]`; Windows PowerShell coerced that bound null to an
  empty string. The artifact builder's null test therefore constructed a
  prefix reference with an empty path, which the checked schema correctly
  rejected. Reaching compact-result validation implies bounded source and
  compact DAO phases completed, but all temporary provider output was removed
  by publication cleanup and no scientific bytes were retained.
- Interpretation: M5R6 is a failed operational attempt and remains immutable.
  A later additive revision may preserve the optional prefix sentinel as a
  true null through a checked object-typed parameter and add fresh-PowerShell
  null/non-null artifact-shape tests, while leaving the schema and all evidence
  gates unchanged. This assigns no MDB byte meaning and establishes no
  compatibility.
- Usage: blocker source for the next additive M5 preregistration; no production
  Rust or MDB format usage
- Rights: no licensed-provider output was retained or published
- Review: pending additive preregistration and exact-commit re-execution

### EXP-0031 — Preregistered null-preserving M5 revision

- Recorded: 2026-08-10, OpenAI Codex
- Kind: additive declarative experiment-plan and checked-artifact revision;
  not yet executed
- Question: Can the unchanged M5 compact-copy campaign represent its
  intentionally absent compact-input retained prefix as JSON null without
  PowerShell string coercion?
- Origin: project-authored `DAO-M5-COMPACT-CONFIRM-007`, revised only from the
  result-validation failure in `EXP-0030`; no sample record, MDB, companion,
  prefix, comparison, candidate, or analysis survived that failed attempt
- Environment: not yet executed; the plan requires the licensed x86
  `DAO.DBEngine.36` host bound to a future exact clean pushed producer commit
  at `refs/heads/codex/m5r6-null-prefix-bound`
- Protocol: preserve the complete M5R6 factorial, 36 conditions, 108 samples,
  three-replica rotated schedule, 324 isolated workers, DAO calls, uppercase
  database locators, clone and four-role quiescence topology, companion rules,
  120-second worker timeout and every resource ceiling, retained prefixes,
  `[0x000,0x600)` analysis range, 648 comparisons, confirmation predicates,
  scientific outcome rules, immutable M4 binding, worker helper/return fixes,
  schemas, and independent publication validation; change only the internal
  optional prefix-locator parameter so bound null is preserved, while any
  non-null value must be a nonempty string, then bind the additive experiment,
  ref, and schema identities
- Artifacts:
  `oracle/windows-dao/experiments/m5/m5-compact-confirm-r7.plan.json` SHA-256
  `b0e5cb2de39fac78be7519f93328a6e7c85e6fcfd6058d15a834065dd880c0e6`;
  `oracle/windows-dao/experiments/m5/README-r7.md` SHA-256
  `d8bb3ec0b3ff5ffc7ba7a97447ad9e55f0fc4453e52382a97f8fc5428b1006ad`;
  ten additive schemas under `oracle/windows-dao/experiments/m5r6/`
- Observation: no M5R7 DAO call, worker, database, companion, prefix, sample,
  comparison, analysis, or result exists. Checked normalization found the R6
  and R7 scientific design, validity rules, analysis, conditions, samples,
  uppercase locators, resource bounds, and timeout identical. Schema
  normalization found only additive schema IDs and experiment/ref identities.
- Interpretation: this is a preregistered PowerShell null-preservation
  correction, not M5 evidence. Its execution gate remains `BLOCKED` only until
  the exact clean pushed R7 producer commit and licensed host are bound. It
  weakens no schema or validator, assigns no MDB byte meaning, changes no
  scientific hypothesis, and establishes no compatibility.
- Usage: checked M5 controller, isolated workers, analysis, and independent
  bundle validator; future exact-commit M5R7 execution only
- Rights: plan, README, schemas, artifact-builder changes, validators, and
  synthetic tests are original project material; future licensed-provider
  output requires its own retention and redistribution record
- Review: preregistration and normalized-design checks complete; exact-commit
  implementation, activation, and execution pending

### EXP-0032 — Checked M5R7 execution activation

- Recorded: 2026-08-10, OpenAI Codex
- Kind: exact-source implementation and execution-readiness record; no DAO
  experiment result
- Question: Are the sole remaining exact-commit/host requirements in the
  immutable `EXP-0031` plan satisfied without rewriting its `BLOCKED` history?
- Origin: project-authored M5R7 plan, schemas, checked controller/workers,
  analysis, complete-bundle validator, and normalization/corruption tests; the
  independently validated `EXP-0018` bundle is the only M4 input
- Environment: Windows 11 Pro 10.0.22631 x64; x86 Windows PowerShell
  5.1.22621.6133; `DAO.DBEngine.36` 3.6, CLSID
  `{00000100-0000-0010-8000-00AA006D2EA4}`, `dao360.dll` 03.60.9765.0,
  SHA-256 `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  environment-record SHA-256
  `8664fddb92483831cc300d4e16a8cb755b7fe4100c3f7b14e5c1220bb86c03d5`;
  checked implementation commit
  `06ef6480daedc9835e2747c19cd32e2931773ec4`
- Protocol: validate plan SHA-256
  `b0e5cb2de39fac78be7519f93328a6e7c85e6fcfd6058d15a834065dd880c0e6`;
  prove normalized R6/R7 design/resource/schema equality; run focused and full
  oracle suites; parse PowerShell; execute the production artifact builder in
  fresh no-COM PowerShell and require null preservation, valid retained-prefix
  projection, and empty/non-string rejection; require exact clean pushed
  implementation/activation commits; independently revalidate M4R2
- Artifacts: implementation commit
  `06ef6480daedc9835e2747c19cd32e2931773ec4`; 41 focused M5 tests passed with
  three platform-dependent skips; full suite passed 311 tests with twelve
  platform-dependent skips; M4R2 manifest independently revalidated at
  SHA-256 `0e6dbba7d5f6bd6933dcc932636b4462487a754f40f2a2f17b48f3c4124baa8d`
- Observation: experiment `DAO-M5-COMPACT-CONFIRM-007`, its exact plan and ten
  schemas, all 68 transitive sources, immutable M4 input, provider, and revised
  ref are bound. The optional locator preserves null and rejects invalid
  non-null shapes; prior worker helper/return corrections and all bounds remain.
- Interpretation: R7 is ready for one later exact clean pushed execution. This
  records no M5 result, assigns no physical meaning, weakens no validator, and
  establishes no MDB compatibility.
- Usage: authorize one exact-commit execution through
  `refs/heads/codex/m5r6-null-prefix-bound`; future M5R7 execution record only
- Rights: implementation, schemas, and tests are original project material;
  future licensed-provider output requires its own retention record
- Review: implementation and activation contracts verified; result pending

### EXP-0033 — M5R7 immutable-M4 stability blocker

- Recorded: 2026-08-10, OpenAI Codex
- Kind: controlled complete-acquisition analysis failure; no published M5
  bundle or scientific result
- Question: Can the preregistered M5 comparison topology be constructed when
  its immutable M4 input has no single stable byte value for a matched
  condition at an analyzed offset?
- Origin: project-authored `DAO-M5-COMPACT-CONFIRM-007` checked controller and
  analysis against the independently validated, immutable `EXP-0018` M4R2
  bundle; no external implementation or format source was consulted
- Environment: the exact licensed x86 environment recorded in `EXP-0032`;
  exact clean pushed commit
  `3761e165d99d8566bfec189f66afa1c364ae1ccd`; exact remote ref
  `refs/heads/codex/m5r6-null-prefix-bound`
- Protocol: execute run `20260810T235119Z-m5-r7` under all checked bounds;
  complete all 108 samples and 324 isolated DAO workers; then build the exact
  648 preregistered comparisons using only per-condition M4 values that are
  stable across all twelve creator/reopen observations; fail before report or
  publication if such a value does not exist
- Artifacts: no published or staged M5 bundle remains; publication cleanup
  left only the empty exact-commit parent under
  `%TEMP%\jet3-rs-dao-m5r7\evidence-3761e16`; no M5 database, companion,
  prefix, sample record, comparison, candidate set, or report is retained
- Observation: acquisition completed all 108 samples without a worker, clone,
  quiescence, sample, provider, or resource-bound error. Checked analysis then
  found the immutable M4 condition `V20-E` unstable at absolute offset 1264
  (`0x4F0`) within `[0x000,0x600)` and stopped. M4R2 itself already reports an
  inconclusive scientific outcome; this is not a newly inferred format fact.
- Interpretation: M5R7 is a failed experiment and remains immutable. The
  `compact_versus_created_matched` comparison requires a single right-hand M4
  byte vector, which does not exist for every analyzed offset. Selecting a
  representative M4 observation, deleting the offset, reducing comparisons,
  or converting the analysis error to a passing outcome after complete M5
  acquisition would materially redesign the preregistered experiment and is
  forbidden here. A future M5 successor needs a new, independently reviewed
  scientific protocol that defines unstable-M4 reference semantics before any
  new M5 acquisition; this blocker must not be made green by weakening the
  validator.
- Usage: terminal blocker for the current M5 campaign family; input to a future
  separately preregistered experimental design only; no production Rust or MDB
  format usage
- Rights: no licensed-provider output was retained or published
- Review: execution trace and immutable-M4 cause verified; current M5 campaign
  remains blocked with no scientific or compatibility claim

### EXP-0034 — Preregistered M5 set-reference successor

- Recorded: 2026-08-13, OpenAI Codex
- Kind: separately preregistered declarative successor with a checked plan
  projection; no DAO acquisition, bundle, analysis result, or format claim
- Question: For every fresh compacted-database byte in `[0x000,0x600)`, is the
  value a member of the complete set observed at the same absolute offset in
  all twelve validated M4 creator/reopen prefixes for the matched documented
  destination condition?
- Origin: project-authored `DAO-M5-SET-REFERENCE-001` plan recorded after the
  terminal `EXP-0033` failure. The design uses only the immutable `EXP-0018`
  M4 bundle, the DAO API facts in `SRC-0014` through `SRC-0016`, `SRC-0018`,
  and `SRC-0019`, and the excluded commit region in `SRC-0013`. No retained
  M5R7 file or observation exists, and no external MDB implementation or new
  format source was consulted.
- Environment: not executed; a future run requires a licensed x86 DAO host,
  exact provider and Windows identities, and an exact clean pushed successor
  implementation commit recorded before COM activation
- Protocol: bind the exact immutable M4R2 manifest; construct each reference
  set from the sorted distinct unsigned bytes in all six creator and all six
  reopen prefixes for one M4 condition and offset; require all twelve inputs;
  run a complete new 36-condition, three-replica, three-phase successor
  acquisition; perform exactly 165,888 primary membership evaluations over
  `[0x000,0x600)`; never select a representative M4 value, delete an unstable
  offset, special-case `0x4F0`, use an M4 candidate set as a prerequisite, or
  reuse the discarded M5R7 acquisition
- Artifacts:
  `oracle/windows-dao/experiments/m5s1/m5-set-reference.plan.json`, SHA-256
  `3f2863fb51338aa2d6ef54553fcbe5b4826c8d98cce85151958b04d500611261`;
  `oracle/windows-dao/experiments/m5s1/README.md`, SHA-256
  `0c1fd6cd8ca8133482c8bdc1ced3f86ff378f319209c61219abaf2e14b7b5851`;
  `oracle/windows-dao/scripts/m5s1_spec.py`, SHA-256
  `2e7201d2fb6fe756e155f21976566b3141ab8d2b7acacd77d7b9112d8bec1ca7`;
  `oracle/windows-dao/tests/test_m5s1_plan_contract.py`, SHA-256
  `6725f9b679d40129e4aee78bf04463360651bf78de9b26781d274d260e8ba86a`
- Observation: the exact plan derives 36 unique conditions, 108 samples in
  three fixed 12-position rotations, 324 future isolated workers, 55,296
  condition-offset reference units, and 165,888 primary memberships. Its
  execution gate is `BLOCKED` on independent design review, successor-specific
  controller/worker/bundle contracts, checked set-reference analysis and an
  independent validator, and exact clean pushed host binding.
- Interpretation: this is a new experiment family, not M5R8 and not a repair
  to M5R7. Set-valued references preserve observed M4 instability without
  choosing a byte or suppressing an offset. A complete valid future run may
  report only whether all compact observations belong to their matched sets or
  whether compact observations extend those sets. Either result remains a
  bounded provider observation and assigns no physical meaning or compatibility.
- Usage: scientific and implementation contract for future work under
  `oracle/windows-dao/experiments/m5s1/` and
  `oracle/windows-dao/scripts/m5s1_spec.py`; no production Rust usage
- Rights: plan, documentation, checked projection, and tests are original
  project material; future licensed-provider output requires a separate
  retention and rights record
- Review: focused contract tests pass; independent scientific review and all
  execution implementation remain explicitly blocked

### EXP-0035 — Checked M5 successor set-reference analysis core

- Recorded: 2026-08-13, OpenAI Codex
- Kind: project-authored bounded analysis implementation and synthetic contract
  tests; no DAO acquisition, bundle validation, scientific result, or format
  claim
- Question: Does the preregistered `EXP-0034` membership algorithm preserve
  every M4-observed value, evaluate every successor condition/replica/offset,
  exclude the commit region, and fail closed on incomplete or ambiguous input?
- Origin: exact `DAO-M5-SET-REFERENCE-001` plan and checked projection from
  `EXP-0034`; no MDB implementation, new public format source, retained M5R7
  output, or new DAO observation was consulted
- Environment: macOS 26.3.1 arm64; Python 3.14.3; no COM activation, Windows
  provider, external database, or network input
- Protocol: accept only 72 uniquely identified exact-size M4 prefixes covering
  six conditions, six replicas, and creator/reopen phases; accept only 108
  uniquely identified exact-size compact prefixes covering all successor
  conditions and three replicas; cap iterable consumption before indexing;
  build all M4 sets for `[0x000,0x600)`; perform the plan's 165,888 primary
  memberships in canonical order; bound the canonical report bytes; test
  singleton and unstable references, novel values, excluded bytes, missing,
  duplicate, short, and permuted inputs
- Artifacts: `oracle/windows-dao/scripts/m5s1_analysis.py`, SHA-256
  `d6814ad2de07e506dabb56a4109c2940d6bbd9bb323de9bde51619bc91421c75`;
  `oracle/windows-dao/tests/test_m5s1_analysis.py`, SHA-256
  `58f29f705f9479c267f905184b39ddc263b53d0b1c6ff65393e166042503af19`
- Observation: twelve focused M5S1 plan/analysis tests pass. Synthetic M4
  variation at `V20-E` offset `0x4F0` admits both observed values without
  selecting one; an unobserved value produces the exact preregistered extension
  outcome; changes in `[0x600,0x800)` do not enter the report; reversed complete
  inputs produce byte-equivalent logical reports.
- Interpretation: the pure analysis algorithm required by `EXP-0034` now
  exists and is bounded against its typed complete inputs. This is not an
  independent bundle validator and does not make successor execution ready.
  Real input adapters, schemas, retained-tree validation, independent
  recomputation, Windows acquisition, and exact host/commit bindings remain
  blocked. Synthetic success establishes robustness only, not an MDB fact.
- Usage: future M5 successor analysis behind the still-blocked execution gate;
  no production Rust usage
- Rights: implementation and tests are original project material; future
  licensed-provider output requires a separate retention and rights record
- Review: focused deterministic and corruption-path tests pass; independent
  analysis and scientific review remain pending

### EXP-0036 — Stock GitHub-hosted x86 DAO provider observation for A1

- Recorded: 2026-08-20, OpenAI Codex
- Kind: controlled black-box environment observation; no A1 acquisition,
  format result, Rust validation, or compatibility claim
- Question: Do untouched GitHub-hosted `windows-2022` and `windows-2025`
  images expose an in-process x86 DAO provider that passes disposable
  `dbVersion30` creation, and which lane matches the reviewed A1 provider?
- Origin: GitHub Actions run `32327232241`, attempt 1, at exact pushed commit
  `8300196ae8c72b45b8d0af87567ab549fea29567`,
  https://github.com/oglassdev/jet3-rs/actions/runs/32327232241
- Environment: GitHub-hosted `windows-2022` image `20260802.262.1`, Windows
  Server 2022 build 20348, x86 Windows PowerShell 5.1.20348.5386; and
  `windows-2025` image `20260810.198.2`, Windows Server 2025 build 26100, x86
  Windows PowerShell 5.1.26100.33158; both `en-US`, ANSI code page 1252, OEM
  code page 437, and UTC
- Protocol: on each untouched image, record runner identity; invoke the checked
  protocol-1.1 provider probe through SysWOW64 Windows PowerShell with a
  120-second and 1-MiB output bound; accept only exit 0 or the documented
  blocked exit 3; conditionally install the pinned Access Runtime only after a
  blocked stock probe; retain structured environment and runner records; and
  fail the job unless the selected record passed disposable `dbVersion30`
  creation. Both stock probes passed, so installation and post-install probing
  were skipped.
- Artifacts: `windows-2022` artifact ID `9391786179`, uploaded archive SHA-256
  `d4b1aa4d0012078eb0678ed3219717acbb0f4877767e07af6254e9b924dc0d20`,
  `environment.json` SHA-256
  `8b720b84d85eec0279eb3ad3415ef7189a27a6f0afbcde281827ff51ec117e22`,
  and `runner.json` SHA-256
  `434e00fbc7d00f5f530f410f746b4a7f912e8b737fedf98e86c200f195e2db82`;
  `windows-2025` artifact ID `9391786643`, uploaded archive SHA-256
  `e54a954b8ac59342a3ed00bdf0b585b8fd758eccc5cace07ad15a63aa67c6673`,
  `environment.json` SHA-256
  `f60271b2a99546487b458eb48e0bca35d32ae09d02f43c68fc014a5936e93ecf`,
  and `runner.json` SHA-256
  `5a5a38250a87a340f34e741f2a8446b09f5c665d0c73e22679f6fa261643f4e2`;
  Actions retained the artifacts for 14 days, and this repository does not
  redistribute them
- Observation: both stock images returned protocol status `ready` and passed
  disposable `dbVersion30` creation through machine-registered x86
  `DAO.DBEngine.36`. On `windows-2022`, `dao360.dll` reported provider version
  3.6, file version `03.60.9765.0`, and SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`,
  exactly matching the historically reviewed local binary. On `windows-2025`,
  the provider reported 3.6 but `dao360.dll` was patched to file version
  `10.0.26100.5074` with SHA-256
  `c2da31acb8836c976c22843862eec36114d4fd3c42e8642190f4c4629273ad3e`.
- Interpretation: `windows-2022` is the pinned A1 campaign lane because this
  exact observation matches the reviewed provider identity. Every future run
  must still re-probe and bind the exact image, runtime, registration, binary,
  and producer commit; provider drift blocks acquisition. `windows-2025` is
  not interchangeable without separate review. This observation authorizes no
  A1 scientific conclusion, MDB physical fact, Rust support level, or DAO
  compatibility claim.
- Usage: `file:oracle/windows-dao/README.md`;
  `file:docs/validation/DAO_PROVIDER_BLOCKER.md`
- Rights: project-generated diagnostic metadata from GitHub-hosted runners;
  no Microsoft provider binary or MDB output is redistributed
- Review: run conclusion and both jobs passed; independent A1 evidence review
  remains required

### EXP-0037 — Preregistered A1 allocation-map campaign

- Recorded: 2026-08-19, OpenAI Codex
- Kind: pre-acquisition experimental design and fail-closed validation contract;
  no DAO acquisition or physical-format result
- Question: Can one bounded joint model derived from two fresh Jet 3 databases
  predict the observed global and per-table allocation-map transitions in a
  third holdout database without refitting?
- Origin: project-authored plan `DAO-A1-ALLOCATION-MAPS-001`; no MDB input,
  donated fixture, third-party implementation, or campaign output was
  inspected to define the checked plan
- Environment: acquisition is restricted to x86 Windows PowerShell 5.1,
  Python 3.13.x, exact clean pushed source, and a freshly probed
  `DAO.DBEngine.36` provider whose binary identity matches the reviewed
  `windows-2022` observation in `EXP-0036`
- Protocol: the checked plan fixes three replicas, replicas 1 and 2 as the
  derivation set, replica 3 as the holdout, 71 nonadaptive closed-file
  checkpoints per replica, deterministic table-role rotation and row recipes,
  content-addressed 2-KiB page capture, explicit work/file/byte/time ceilings,
  and a rule that any post-acquisition amendment requires a new experiment ID
- Artifacts: checked plan
  `oracle/windows-dao/experiments/a1/a1-allocation-maps.plan.json`, SHA-256
  `a7fa44cdb24b6f6e0d3884d478d7eef74685aa90ea12eacfff4b459b1da6ab80`;
  schemas, validators, bounded acquisition scripts, and a manual-only hosted
  workflow are checked alongside it
- Observation: `preregistration.acquisition_started` is `false`. No A1
  database, page capture, candidate set, bundle, or scientific report exists.
  The current analyzer can produce only fail-closed no-outcome reports and the
  independent bundle validator rejects decisive reports until scientific
  recomputation is implemented.
- Interpretation: this entry records only the pre-acquisition contract. It
  authorizes no global-map location, TDEF pointer layout, row-slot layout,
  inline or indirect allocation formula, Rust behavior, DAO compatibility, or
  support-matrix advancement. Acquisition remains blocked pending the planned
  three-layer model amendment, bounded row-slot dereference, derivation-set
  freeze before holdout access, independent candidate recomputation, and
  jointly feasible capacity reservations.
- Usage: `file:oracle/windows-dao/experiments/a1/README.md`;
  `file:oracle/windows-dao/experiments/a1/a1-allocation-maps.plan.json`;
  `file:oracle/windows-dao/experiments/a1/plan.schema.json`;
  `file:oracle/windows-dao/scripts/a1_spec.py`;
  `file:oracle/windows-dao/tests/test_a1_plan_contract.py`
- Rights: project-authored plan, schemas, scripts, and synthetic contract-test
  data; no Microsoft provider binary or generated MDB is redistributed
- Review: focused plan, analysis, bundle, PowerShell-source, and hosted-workflow
  contract tests pass; scientific design and capacity audits require the
  pre-acquisition amendments listed above before any manual dispatch

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
