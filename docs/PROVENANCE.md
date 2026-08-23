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
  `file:crates/jet3/src/allocation_traverse.rs`;
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
  Additional tracked Usage: `file:tools/inspect_external_corpus.py`.
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
  Additional tracked Usage: `file:tools/inspect_external_corpus.py`.
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
  Additional tracked Usage: `file:oracle/windows-dao/README.md`;
  `file:oracle/windows-dao/protocol/v1_1/README.md`;
  `file:oracle/windows-dao/scripts/m1/M1.Provider.ps1`;
  `file:oracle/windows-dao/tests/test_m1_executor_preflight.py`.
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
  Additional tracked Usage: `file:docs/validation/DAO_PROVIDER_BLOCKER.md`;
  `file:docs/validation/M2_PAGE_OBSERVATION.md`;
  `file:docs/validation/support-matrix.json`;
  `file:oracle/windows-dao/README.md`.
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
  Additional tracked Usage: `file:docs/validation/DAO_PROVIDER_BLOCKER.md`.
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
  Additional tracked Usage: `file:docs/validation/DAO_PROVIDER_BLOCKER.md`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m4r1/`;
  `dir:oracle/windows-dao/experiments/m4r2/`;
  `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m4r2/`;
  `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `file:docs/architecture/SEMANTIC_READER.md`;
  `dir:oracle/windows-dao/experiments/m5/`;
  `dir:oracle/windows-dao/experiments/m5s1/`;
  `file:oracle/windows-dao/scripts/m5s1_spec.py`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5/`.
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
  Additional tracked Usage: `dir:oracle/windows-dao/experiments/m5s1/`;
  `file:oracle/windows-dao/scripts/m5s1_spec.py`.
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

### EXP-0038 — Preregistered A1 report-interpretation amendment

- Recorded: 2026-08-20, OpenAI Codex
- Kind: additive pre-acquisition experiment-plan revision; no retained DAO
  scientific acquisition, physical-format result, or independent scientific
  validation
- Question: How must the A1 campaign encode preregistered no-outcome reasons
  and retain a decisive analysis while independent recomputation remains
  unavailable?
- Origin: project-authored revision
  `DAO-A1-ALLOCATION-MAPS-001-R2`, derived only from the immutable `EXP-0037`
  plan and analysis-report schema plus the fail-closed decisive-report rule in
  the checked A1 contract; no MDB input, campaign output, donated fixture, or
  third-party implementation was inspected
- Environment: hosted A1 workflow dispatches `32434371779`, `32437968174`,
  `32439806983`, and `32441192546` stopped in preflight or controller setup
  before any worker ran. Dispatch `32442251143` bound the proven stock x86 DAO
  image and ran one worker for approximately 30 minutes before the 1,800-second
  worker ceiling terminated it. The original Windows, PowerShell 5.1, Python
  3.13.x, exact clean pushed source, and `DAO.DBEngine.36` requirements remain
  unchanged.
- Protocol: preserve the complete `EXP-0037` scientific design and record the
  canonical mapping from each plan prose reason to the snake_case identifiers
  emitted by analysis reports; map the zero/multiple-survivor and record/inline
  ambiguity conditions to their two schema identifiers, forbid emission of the
  schema-only `incomplete_transition_evidence` identifier, retain any decisive
  analysis report, record bundle status
  `decisive_pending_independent_validation`, and cap capability verification at
  `not_independently_validated` until a separately provenanced independent
  recomputing validator exists and accepts the retained report and bundle
- Artifacts: revision plan
  `oracle/windows-dao/experiments/a1/a1-allocation-maps-r2.plan.json`, SHA-256
  `6967e72c0ea6c6aa68f102d76c48764a6300caebb4b6f7bbb2e0b931822b5b0c`;
  immutable original plan SHA-256
  `a7fa44cdb24b6f6e0d3884d478d7eef74685aa90ea12eacfff4b459b1da6ab80`
- Observation: none of the five dispatches produced or retained a checkpoint
  observation, page capture, replica observation, candidate set, bundle, or
  scientific report. Run `32442251143` retained only diagnostics artifact
  `windows-dao-a1-diagnostics-32442251143-1`. The immutable original plan does
  not define worker launch as acquisition start. For
  `preregistration.acquisition_started` and its `amendment_rule`, this revision
  treats acquisition as started when the first schema-valid replica observation
  is retained for inspection, because that is the first preregistered
  scientific artifact capable of informing a later amendment. A dispatch,
  preflight or controller setup, or worker launch without a retained replica
  observation does not meet that criterion; the flag therefore remains
  `false`. The original plan, schemas, and `a1_spec.py` validation remain
  byte-for-byte unchanged.
- Interpretation: this amendment resolves representation and artifact-status
  conflicts before acquisition without changing a scientific condition or
  authorizing execution. A decisive analyzer result is not independently
  validated merely because the existing controller invokes `a1_contract.py`,
  and this entry establishes no physical meaning, Rust correctness, DAO
  compatibility, or support-matrix advancement.
- Usage:
  `file:oracle/windows-dao/experiments/a1/a1-allocation-maps-r2.plan.json`;
  `file:oracle/windows-dao/tests/test_a1_plan_contract.py`; future checked A1
  analyzer, controller, bundle-status, and independent-validator work
- Rights: project-authored plan revision and synthetic hash-pin test; no
  Microsoft provider binary or generated MDB is redistributed
- Review: pending independent review before any manual A1 dispatch

### EXP-0039 — First retained A1 acquisition: ambiguous record boundary

- Recorded: 2026-08-21, OpenAI Codex
- Kind: controlled hosted DAO acquisition with an independently validated
  complete bundle and a preregistered `no_scientific_outcome` analysis result;
  no physical-format, Rust, or compatibility result
- Question: Can one preregistered joint model explain and predict the observed
  global and per-table allocation-map transitions in three fresh Jet 3
  databases?
- Origin: project-authored `DAO-A1-ALLOCATION-MAPS-001` campaign executed by
  GitHub Actions run `32486063559` from exact clean pushed producer commit
  `947038265f6898c55b39da99340220e548836594`, under the immutable `EXP-0037`
  plan and the report-interpretation rules recorded by `EXP-0038`; no donated
  MDB or third-party implementation was used
- Environment: `windows-2022` image `20260818.277.1`, x86 Windows PowerShell
  5.1, Python 3.13.7, and machine-registered `DAO.DBEngine.36` from
  `dao360.dll` file version `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  provider proof run `32439805418`; campaign status
  `independently_validated`; total elapsed time 5,725.92 seconds
- Protocol: execute all 71 closed-file checkpoints for each of three fresh
  replicas, with diagnostic progress durations 1,536.912 seconds for replica
  1, 1,475.135 seconds for replica 2, and 1,467.223 seconds for replica 3;
  retain the complete content-addressed bundle, run the preregistered bounded
  analysis, independently validate bundle structure and cross-artifact
  bindings, and upload the bundle, attestation, and bounded diagnostics
- Artifacts: Actions bundle artifact
  `windows-dao-a1-bundle-947038265f6898c55b39da99340220e548836594-20260821T132025Z-a1-gh32486063559-1`,
  175,556,608 bytes; attestation artifact
  `windows-dao-a1-attestation-947038265f6898c55b39da99340220e548836594-32486063559`;
  diagnostics artifact `windows-dao-a1-diagnostics-32486063559-1`;
  `bundle-manifest.json` SHA-256
  `97c1286624a5e02fc7bcfc7b1047986e8a15e3ac8aec22488a1a5b4bfa444381`;
  `analysis/analysis-report.json` SHA-256
  `5fae61fd7394a8847faef0a88c24a11962409daeffe408d0fb7d241ffd491a74`
- Observation: the passing manifest closes 21,102 payload entries, including
  20,883 unique page blobs, three replica observations, and 213 checkpoint
  indexes. The preregistered report records
  `scientific_outcome = no_scientific_outcome`, sole reason
  `ambiguous_record_boundary`, zero candidate models examined, zero derivation
  survivors, no holdout evaluation, 213 input checkpoints, 142,115 analysis
  work units, and plan SHA-256
  `a7fa44cdb24b6f6e0d3884d478d7eef74685aa90ea12eacfff4b459b1da6ab80`.
  This is the first retained schema-valid A1 replica observation, so acquisition
  has now started under the `EXP-0038` criterion. Any analyzer change requires
  a new experiment ID, plan file, and provenance entry before another run.
- Independent validation: on a separate local copy of the three downloaded
  artifacts, using Python 3.13.15 and the checked producer-commit validators,
  `PYTHONPATH=oracle/windows-dao/scripts python3.13 -c 'import sys; from pathlib
  import Path; from a1_bundle import validate_bundle;
  result=validate_bundle(Path(sys.argv[1]));
  print("PASS: a1_bundle.validate_bundle",
  result["manifest"]["run_id"])' "$BUNDLE"` returned `PASS` for run
  `20260821T132025Z-a1-gh32486063559-1`;
  `python3.13 oracle/windows-dao/scripts/a1_contract.py validate-bundle
  "$BUNDLE"` returned `PASS: checked DAO A1 bundle structure and bindings ...;
  scientific outcome not validated`;
  `python3.13 oracle/windows-dao/scripts/a1_spec.py validate-plan
  "$BUNDLE/plan/a1-allocation-maps.plan.json"`, `validate-observation` for
  `"$BUNDLE/observations/replica-01.json"` through `replica-03.json`, and
  `validate-report "$BUNDLE/analysis/analysis-report.json"` each returned
  `passed`. The local validation was read-only and did not alter the retained
  artifacts.
- Interpretation: the hosted A1 lane and complete-bundle validator worked, but
  the campaign produced no scientific model and assigned no byte, record,
  pointer, bitmap, page, or allocation-map meaning. The self-produced DAO-only
  result advances no product capability, establishes no Rust correctness or
  DAO compatibility, changes no support-matrix entry, and does not satisfy the
  release differential gate.
- Usage: `file:docs/validation/DAO_PROVIDER_BLOCKER.md`;
  `file:oracle/windows-dao/README.md`; future separately preregistered A1
  experiment design only
- Rights: project-generated through the licensed Microsoft DAO provider and
  retained as GitHub Actions artifacts; no provider binary or generated MDB is
  committed or redistributed by this repository
- Review: hosted status and attestation passed; manifest and analysis identities
  were independently recomputed and the complete local bundle passed all
  checked validators; scientific interpretation remains no outcome

### EXP-0040 — Preregistered A2 record-level allocation-map campaign

- Recorded: 2026-08-21, OpenAI Codex
- Kind: pre-acquisition experimental design and fail-closed analyzer dry-run
  contract; no A2 DAO acquisition, physical-format result, analyzer result, or
  independent scientific validation
- Question: Can separately delimited record-level models derived from two fresh
  Jet 3 databases predict the observed global and per-table allocation-map
  transitions in a third holdout database without refitting?
- Origin: project-authored plan `DAO-A2-ALLOCATION-MAPS-001`, informed by the
  immutable A1 preregistrations and result in `EXP-0037`, `EXP-0038`, and
  `EXP-0039`; the five committed, hash-pinned design inputs listed below; the
  retained A1 run-12 bundle; and the post-PR-33 run-11/run-12 progress traces.
  These were used only as exploratory design and dry-run inputs. No external
  MDB implementation, donated MDB, or other format implementation was
  inspected.
- Committed design-input identities:
  `oracle/windows-dao/experiments/a2/design-inputs/a1-run12-ambiguity-diagnosis.md`,
  SHA-256 `17d5ee28983ffc126feec63e7a7d8c7ffbc369e5f025193c9cd0d8404edf430d`;
  `oracle/windows-dao/experiments/a2/design-inputs/fable-review-findings.md`,
  SHA-256 `ef77b917e2c7da6c8fc7a7c262352cf9ec208783bb4b71c63c2f3bb058a2950a`;
  `oracle/windows-dao/experiments/a2/design-inputs/fable-analyzer-schedule-audit.md`,
  SHA-256 `c9f10f07b8b4b21da524de90819149d68fa387736dda4cb0cbcccfcb4f8ab603`;
  `oracle/windows-dao/experiments/a2/design-inputs/fable-a2-plan-review.md`,
  SHA-256 `342e6cd56963de476639768368b5d187ecc95fb4eccd7b390ec4df5091c8e876`;
  and
  `oracle/windows-dao/experiments/a2/design-inputs/fable-a2-plan-review-2.md`,
  SHA-256 `620aad56198446be88ceeab3b0185e0e24eef1df6b94f365c230ae7305cb764d`.
- Exploratory input identity: retained bundle
  `windows-dao-a1-bundle-947038265f6898c55b39da99340220e548836594-20260821T132025Z-a1-gh32486063559-1`,
  manifest SHA-256
  `97c1286624a5e02fc7bcfc7b1047986e8a15e3ac8aec22488a1a5b4bfa444381`.
  This A1 input is not A2 evidence, cannot satisfy an A2 derivation, holdout,
  or decision predicate, and cannot authorize a physical-format or capability
  claim.
- A1 run-12 diagnosis disclosure: the retained A1 report reason
  `ambiguous_record_boundary` was emitted by the zero-surviving-pages branch,
  not by multiple surviving record boundaries. As the cited read-only diagnosis
  establishes, A1 required whole-page D equality between `D_GROW_0128` and
  `D_REGROW_0128` even though its acquisition arithmetic grew from 23 to 151
  pages and then from 151 to 279 pages. The no-outcome label is therefore an
  analyzer/acquisition-contract mismatch, not an observed ambiguity property of
  Jet data, and A2 does not reinterpret the A1 holdout or report as evidence.
- A1 run-12 calibration disclosure: descriptive run-12 observations that the
  conversion occurred at source ordinal 40 (`P_ABS_16480`), both slots were
  active, growth cleared the relevant bits, and deletion added one file page
  are preregistered only as free-parameter values for a synthetic generator
  calibration case. The named checkpoint maps to A2 ordinal 20; source ordinal
  40 is not copied as an A2 ordinal. These values are exploratory,
  non-evidential, cannot satisfy any A2 predicate, and do not constrain A2 data.
- A1 run-12 remeasurement disclosure: a separate read-only remeasurement of
  derivation replicas 1 and 2 used the retained page indexes and SHA-256-checked
  page-1 blobs, never opened replica 3, and produced identical results in both
  replicas: 13 pages satisfy the global hash qualification; the final D-flipped
  page-1 byte is offset 1954; the 93 following bytes through 2047 are raw
  `0xFF` and decode to not-in-use under `set_means_not_in_use`; and the legacy
  delete/reinsert transitions change respectively one and zero of those suffix
  bytes. These measurements calibrate only the mandatory dry-run assertions and
  are not A2 evidence or format claims.
- Protocol: create three fresh replicas with rotated D/L/P/H table bindings.
  D grows from the initial post-create baseline to `baseline + 128`, is dropped
  and recreated, then grows from the new post-create baseline to its own
  `baseline + 128` target in fixed 32-row batches. Regrowth must be strictly
  larger than first growth. The global predicate is a record-level set relation:
  a nonempty set allocated by first growth is released after drop and
  reallocated by regrowth, which also allocates at least one additional page.
  D drop need not shrink the file and no page, byte, or record equality is
  assumed. The candidate page space is every page observed at any checkpoint.
  Hash-only global and TDEF page qualifications precede interval enumeration
  and are each capped at 16 pages. Every half-open interval over 2,049 fixed byte
  boundaries is tested using 2,049-entry prefix sums and O(1) interval queries,
  with 2,098,176 candidates per page and 67,141,632 combined. The global-record
  end is page-terminal only when a uniform suffix of at least 16 bytes decodes
  entirely to not-in-use under the D-selected polarity at every D checkpoint;
  shorter equivalent ends are rejected. A
  transition-structural exclusion rule applies identically on every page; no
  page or offset is blacklisted and no change envelope supplies a boundary.
  The global-map field signature includes the declared delete/reinsert
  transitions as well as D and growth transitions, while pointer signatures
  remain transition-selective.
  D alone delimits the global-map record and selects bit polarity. L/P/H growth
  checks polarity and evaluates conversion, slot activation, inline boundary,
  and extended-base candidates on that frozen global record. A separate TDEF
  record carries only the growth-only and full-delete churn-only pointer pair.
  Four report layers preserve independently decisive global-record,
  global-conversion/inline, extended-base, and TDEF-pointer results.
- Checkpoint schedule: 25 fixed, nonadaptive, closed-file checkpoints per
  replica. The schedule retains relative D A/B/A/C; L relative targets 64, 512,
  768, 896, 904, 1024, 1088, and 1280 plus full deletion to zero rows,
  same-ID/payload reinsert, and idle reopen; the complete absolute P window at
  4,096, 8,192, 12,288, and 16,480; and H relative targets 64, 896, and 904 plus
  idle reopen. H 64 forces slot-relative and referenced-page-relative base
  candidates to predict different slot-0 flips. The conversion checkpoint is
  derived as the earliest valid
  monotone inline-to-indirect transition across the entire preregistered
  L/P/H growth window and is not assumed to occur by `L_IDLE_REOPEN`.
  One or two global-map slots may be active at conversion, while exactly two
  must be active by `H_REL_0904`. Inline-boundary candidates are every fixed
  byte boundary inside the frozen global record and are independent of anchor
  fill. Failure of H 64 to discriminate a base is a no-outcome only for the
  extended-base layer, not for any earlier layer.
  Exploratory A1 run-12 page-1 transitions justify these probes. The omitted
  fine L points and later H points do not contribute an additional A2 decision
  predicate.
- Parallel acquisition and bounds: each replica must run as an independent
  matrix job with no shared database, page store, output directory, or mutable
  state, and emits its own environment document. Provider prog id, CLSID,
  binary hash, x86 architecture, and PowerShell major must match; runner image
  and Python patch may differ within the recorded bindings. Fan-in first
  validates replicas 1/2, persists and hashes every layered candidate set, and
  closes those inputs. Only then may a separate process structurally validate
  replica 3 and emit a bounded pass/fail receipt without exposing page bytes to
  the analyzer; holdout analysis follows the freeze. Post-PR-33 run-11 progress
  reached `H_REL_0904` in 400.440–462.513 seconds with 26.317–26.654 seconds of
  final idle work; run 12 took 543.107–619.412 plus 35.394–35.901 seconds. The
  slow observed A2-equivalent prefix plus idle is 654.865 seconds. Adding
  70.135 seconds for `D_RECREATE_EMPTY`, full-delete differences, and rounding
  freezes the projection at 725 seconds. The 1,700-second worker ceiling gives
  2.34x per-replica headroom. Concurrent acquisition plus the full 900-second
  fan-in bound gives a 1,625-second complete-campaign estimate, below the
  1,800-second hosted performance target. That target is not a safety bound.
  The 2,700-second campaign timeout exactly covers the worker ceiling, fan-in
  ceiling, and 100 seconds of setup and dispatch allowance, and gives 1.66x
  headroom over the estimate. The six timing-trace SHA-256 values, ordered run 11
  replicas 1–3 then run 12 replicas 1–3, are
  `b005ab46ae10144fd27aeef51ebd24d308a0460c0275a1bd7cd004a4df024a8a`,
  `f786a9e3616dca89a369130a028333f4a97d597735f6cfc9ae5c9aade46013f7`,
  `76fa961bb00d1b3c056dec659eaee6b89046dedb3ac46ba35d1ba01995d0706b`,
  `fbc70ccc78490abbb1c8e3cdfd43323242143e7762e374f958d0c5d3a332674a`,
  `1ae3013c0a38928e5b7800bef79fad34ea993769c834f7329607941f62d2072d`,
  and `632d4d86e9e83c80fca395faa8589ba0289c595803b0d700a5eeb93573e726ed`.
  Bounds remain conservative against run 12: 20,701 changed hashes versus
  65,536, about 254 MiB logical reads versus 2 GiB, and about 150,000 inserted
  rows versus 524,288; dry-run cases accept each ceiling and reject one over.
- Analyzer dry-run contract: before acquisition, the future A2 analyzer must
  run in explicit exploratory legacy mode against the identified retained A1
  run-12 bundle and must run against the future A2 synthetic generator. The
  legacy run may open at most 55 page blobs from derivation replicas 1 and 2,
  must never open the holdout, and uses the plan's explicit 25-row projection.
  `D_RECREATE_EMPTY` is missing and not applicable; A1's
  `L_DELETE_ALTERNATING` maps to A2's full-delete row only for chronology, with
  both A2 churn predicates not applicable. The dry run must resolve one global
  record with polarity-relative uniform suffix slack and independently derive
  and assert 13 D-qualified pages in each derivation replica, final D-flipped
  offset 1954, 93 following `0xFF` not-in-use bytes, and legacy suffix changes
  of one byte on delete and zero on reinsert. It also asserts at most 16 pages
  per submodel, at most 67,141,632 interval candidates, fewer than 600,000,000
  work units, and no more than 55 opened blobs. The synthetic
  generator parses the checkpoint design, row algorithm, candidate procedure,
  and bounds from this exact plan and derives every count and equality; no A1
  hand-typed count is imported. Conversion ordinal (all A1/A2 ordinals and
  never), slot activation (0/1/2), both bit polarities, anchor fill, and
  record-end slack are free parameters. Cases prove the D record-set relation,
  D-only polarity selection, later growth agreement, a unique global endpoint
  with slack, growth-only and full-delete churn-only pointer transitions, one-
  or two-slot conversion paths, anchor-independent inline boundaries, and both
  decisive and locally inconclusive layered outcomes. Every equality must be
  generator-producible; every Abort has one pinned reason/predicate/layer map
  and a single-perturbation case. A decisive layered report must pass the A2
  document validator before dispatch. Both schema-valid dry-run reports,
  inputs, hashes, commands, commits, predicate ids, layered results, and the
  decisive-validator result must be disclosed in a later additive A2
  provenance entry before dispatch and are explicitly non-evidential.
- Dry-run result disclosure: `not_run_preregistration_only`. This change does
  not implement the A2 worker, analyzer, generator, validator, or workflow, so
  neither required dry run exists. The execution gate remains `BLOCKED`; this
  status is non-evidential and authorizes no hosted acquisition.
- Terminal reporting: zero qualifying global pages, multiple qualifying global
  pages, zero surviving records on one qualifying page, and multiple surviving
  records on one qualifying page have four distinct identifiers. The equivalent
  TDEF page/record conditions, polarity, slot activation, conversion, pointer,
  inline boundary, base discrimination, and churn-precondition failures also
  have named outcomes. The plan pins a bijective 34-entry
  reason/predicate-id/layer mapping for every Abort or early terminal. None may
  be collapsed into A1's `ambiguous_record_boundary` identifier.
- Decisive-report retention: a schema-valid decisive report completes rather
  than fails the campaign, remains inventoried in the complete bundle, and
  requires bundle status `decisive_pending_independent_validation` with
  `analysis_report_retained = true`, `campaign_failed = false`, and independent
  validation status `not_independently_validated`. Structural validation may
  not delete staging merely because the scientific result is decisive; a
  capability remains capped until separately provenanced independent
  recomputation accepts the retained report and bundle.
- Artifacts: checked plan
  `oracle/windows-dao/experiments/a2/a2-allocation-maps.plan.json`, SHA-256
  `804e84dace5c423938f32dd350ebc778d43084d41db1da93f26f1777984480c2`;
  strict plan, environment, page-index, replica-observation, independent
  replica-artifact, layered analysis-report, holdout-structure-receipt,
  bundle-manifest, and dry-run-report schemas; hash-pin contract test
- Observation: `preregistration.acquisition_started` is `false`. No A2
  database, checkpoint, replica observation, candidate set, evidence bundle,
  dry-run report, or scientific report exists.
- Interpretation: this entry preregisters only a bounded experiment and its
  pre-acquisition checks. It assigns no byte, record, page, pointer, bitmap, or
  allocation-map meaning; establishes no Rust correctness or DAO compatibility;
  changes no support-matrix entry; and cannot satisfy a release differential
  gate.
- Usage: `file:oracle/windows-dao/experiments/a2/README.md`;
  `file:oracle/windows-dao/experiments/a2/a2-allocation-maps.plan.json`;
  future separately reviewed A2 implementation and pre-acquisition dry runs
- Rights: project-authored plan, schemas, tests, and five committed design
  documents; no Microsoft provider binary, generated MDB, or retained A1 bundle
  is committed or redistributed by this repository
- Review: focused JSON/hash/schedule contracts and the repository's Python
  oracle tests must pass; acquisition remains blocked on the plan's seven
  execution requirements

### EXP-0041 — A2 pre-acquisition analyzer dry runs and reachability revision

- Recorded: 2026-08-21, OpenAI Codex
- Kind: additive pre-acquisition reachability reconciliation and non-evidential
  analyzer dry-run result; no A2 DAO acquisition, physical-format observation,
  scientific evidence, independent recomputation, or capability validation
- Origin: project-authored A2 analyzer, schedule-derived synthetic generator,
  and dry-run harness at analyzer commit
  `370636d334666739b2df1da1ba0f88f6d5693f39`, applied to the immutable
  `DAO-A2-ALLOCATION-MAPS-001` plan and the retained A1 run-12 exploratory
  input identified in `EXP-0040`. No external MDB implementation, Microsoft
  implementation source, donated MDB, or A2 holdout artifact was inspected.
- Additive R2 revision: before acquisition, independent review established
  that `A2-INLINE-BOUNDARY-MULTIPLE` is structurally unreachable under the
  frozen `inline_boundary_procedure`. If a boundary survives, its entire
  all-checkpoint suffix must be quiet. A later boundary cannot also explain
  its additional represented interval under either registered polarity;
  attempts to manufacture a second survivor are preempted by
  `A2-INLINE-SUFFIX`. The suffix rule was not weakened. The original plan,
  schemas, 34-entry predicate registry, mappings, and scientific design remain
  immutable. Revision `DAO-A2-ALLOCATION-MAPS-001-R2` removes only this one
  site from the reachability requirement and requires its registry id to remain
  in the transcript with status `unreachable_by_construction` and the real
  preempting analyzer result.
- Retained-A1 dry run: result `pass`; the explicit legacy projection opened no
  holdout, qualified 13 global pages in each derivation replica, resolved one
  global record, selected polarity `set_means_not_in_use`, and measured the
  final D-flipped byte at offset 1954 followed by 93 raw `0xFF` bytes through
  byte 2047, all decoding to not-in-use. It opened 43 of the permitted 55
  distinct physical page blobs and emitted terminal state
  `legacy_projection_complete_with_tdef_churn_not_applicable`. The two retained
  terminal predicate ids are the explicitly not-applicable
  `A2-CHURN-PRECONDITION` and `A2-CHURN-POINTER-NONE`; this is not an A2
  scientific outcome.
- Synthetic dry run: result `pass`; 109 analyzer parameter/layer cases comprise
  one all-layers-decisive case, 25 A2 conversion cases (ordinals 1–24 plus
  never), 11 slot/polarity/anchor/slack axis cases, 71 A1 legacy conversion
  cases (source ordinals 1–70 plus never, projected by checkpoint identity),
  and one partial-layer case. A separate 34-case reachability sweep executed
  every registered perturbation. All 33 predicates required by R2 were reached
  by real analyzer execution (`33/33`), no required predicate was unreachable,
  and `A2-INLINE-BOUNDARY-MULTIPLE` was retained as the one
  `unreachable_by_construction` registry id; its attempted perturbation emitted
  the real preempting predicate `A2-INLINE-SUFFIX`. The corrected pointer
  perturbations retained a non-pointer qualifying transition while removing
  the target witness, and emitted exactly `A2-GROWTH-POINTER-NONE` and
  `A2-CHURN-POINTER-NONE`. All ten source-contract checks passed, all 36
  effective required terminal states were recorded, and the decisive report
  and bundle validators accepted status
  `decisive_pending_independent_validation`.
- Synthetic calibration: the explicitly non-evidential run-12 case retained
  source conversion ordinal 40 mapped by checkpoint identity to
  `P_ABS_16480` and A2 ordinal 20, two active slots, polarity
  `set_means_not_in_use`, delete page delta `+1`, and
  `scientific_evidence = false`. Parameter coverage also exercised both bit
  polarities, slot counts 0/1/2, anchor fills empty/partial/full, record-end
  slack 16/32/64 bytes, every required conversion ordinal, and never.
- Commands: `python3.13 oracle/windows-dao/scripts/a2_dryrun.py generate
  --replace-existing`; then `python3.13
  oracle/windows-dao/scripts/a2_dryrun.py verify`. Generation wrote the four
  retained artifacts, and verification independently recomputed them from the
  pinned inputs and reported exact byte equality.
- Plan and revision identities:
  `oracle/windows-dao/experiments/a2/a2-allocation-maps.plan.json`, SHA-256
  `804e84dace5c423938f32dd350ebc778d43084d41db1da93f26f1777984480c2`;
  `oracle/windows-dao/experiments/a2/a2-allocation-maps-r2.plan.json`, SHA-256
  `977d352b6b7c042cf4d0f0cab793086842b3ad2b7da13b9c217020f00c5193c4`.
- Dry-run report and input identities:
  `oracle/windows-dao/experiments/a2/dry-run/a1-run12-report.json`, SHA-256
  `075410ead688caedfc2a517b715574fb023c60b613f23b740a68a08e5a88edd1`;
  retained A1 run-12 input manifest, SHA-256
  `97c1286624a5e02fc7bcfc7b1047986e8a15e3ac8aec22488a1a5b4bfa444381`;
  `oracle/windows-dao/experiments/a2/dry-run/a2-synthetic-report.json`, SHA-256
  `253dbbb8b5a37f22b30942f193cfff03aa6ee0ab7e304cc4232e86dd1abdbd74`;
  synthetic case transcript/input
  `oracle/windows-dao/experiments/a2/dry-run/a2-synthetic-cases.json`, SHA-256
  `9e56c6fe4207f1112cce1197736b0c45b9bb7364760b9d8aa1233b3e2c059afc`;
  and `oracle/windows-dao/experiments/a2/dry-run/checksums.sha256`, SHA-256
  `76dd47feecc59aad658dbded12f28f038b3f60d1d5f8e1743244ac374c004d27`.
- Generator identities: `a2_generator.py`, SHA-256
  `1da5a1e0ee5fdc8469c84f5bc2300541af8b660ac1d428af69c7685c47245966`;
  `a2_generator_pages.py`, SHA-256
  `bc94d94a2ea996e7e1175bef31cf57afcaf0be72c0750c1384383310a7a73d86`;
  `a2_generator_schedule.py`, SHA-256
  `6fc96711bd1dc23e6d950378c34f5b1a82dfcbb6f159a836443fa15cdf56a5fb`;
  canonical combined generator SHA-256
  `19ebc201abc8006fd4e0e73c9b50121d07a2fc2d5103e56b0bebb07bc140dd80`.
- Observation: both schema-valid dry-run reports say `result = pass`,
  `holdout_opened = false`, `scientific_evidence = false`,
  `acquisition_authorized = false`, and
  `capability_advancement_authorized = false`. Their common recorded time is
  `2026-08-21T22:07:27.232325Z`.
- Interpretation and execution gate: every artifact, case, calibration value,
  and self-check in this entry is synthetic or exploratory and non-evidential.
  Nothing establishes a Jet physical-format fact, Rust correctness, DAO
  compatibility, independent validation, or a support-matrix advancement.
  Acquisition remains `BLOCKED`: the A2 worker and independent three-replica
  matrix/fan-in workflow do not exist, and the complete dispatch gate must be
  re-evaluated after they exist and all other blocking requirements are
  satisfied. These passing dry runs do not authorize hosted dispatch.
- Usage: pre-acquisition A2 analyzer/generator contract verification only
- Rights: project-authored revision, analyzer, generator, tests, and reports;
  the retained A1 bundle remains external and is not redistributed
- Review: the Python 3.13 oracle tests and `tools/reconcile_tests.py` must pass;
  an independent review must confirm the additive revision, real analyzer-site
  reachability, hashes, non-evidential classification, and blocked dispatch

### EXP-0042 — First retained A2 acquisition result

- Recorded: 2026-08-22, OpenAI Codex
- Kind: controlled hosted DAO acquisition with a complete retained bundle and a
  checked-workflow validation pass; descriptive provider observation only, not
  an independently recomputed physical-format, Rust, or compatibility result
- Question: Can the preregistered layered A2 models identify allocation-map
  records, polarity, conversion boundaries, extended bases, and table-definition
  pointer pairs from two derivation replicas, then predict the separately opened
  third-replica holdout?
- Origin: project-authored `DAO-A2-ALLOCATION-MAPS-001` campaign executed by
  GitHub Actions run `32587946283` from exact clean pushed producer commit
  `1a0585446ac8b0d232ee4c0391cce9d635e7c43a`, under the immutable `EXP-0040`
  plan, SHA-256
  `804e84dace5c423938f32dd350ebc778d43084d41db1da93f26f1777984480c2`,
  and its additive R2 reachability revision recorded by `EXP-0041`, SHA-256
  `977d352b6b7c042cf4d0f0cab793086842b3ad2b7da13b9c217020f00c5193c4`;
  no donated MDB or third-party implementation was used
- Environment: `windows-2022` hosted image, recorded runner image `win22` and
  Windows version `10.0.20348`, x86 Windows PowerShell 5.1.20348.5499, Python
  3.13.7, and machine-registered `DAO.DBEngine.36` from `dao360.dll` file
  version `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  all three environment records say `status = ready`
- Protocol and timing: execute 25 closed-file checkpoints in each of three
  independently hosted replicas, bind replicas 1 and 2 for derivation, freeze
  the candidate set, structurally validate and then open replica 3 as holdout,
  analyze the frozen candidates, close the complete content-addressed bundle,
  and run the checked in-workflow bundle validator. Hosted replica elapsed
  times were 472.585 seconds for replica 1, 319.722 seconds for replica 2, and
  346.909 seconds for replica 3. Fan-in recorded `assemble = success`,
  `analyze = success`, `finalize = success`, `validate = success`, complete
  timing records, and total campaign elapsed time 803.711 seconds, within the
  immutable 2,700-second campaign timeout.
- Artifacts: bundle artifact
  `windows-dao-a2-bundle-1a0585446ac8b0d232ee4c0391cce9d635e7c43a-32587946283-1`,
  111,379,714 bytes, SHA-256
  `7e58dc5e3c8424110897053cdfeab703b0e1d15fde2dfd4d8235efd62da43dc7`;
  fan-in diagnostics artifact
  `windows-dao-a2-fanin-diagnostics-32587946283-1`, 666 bytes, SHA-256
  `a52e82571f9ef1c83499266f3fcd9ad9ae51f90077c1735d55a3b75df9d22813`;
  replica artifacts `windows-dao-a2-replica-1`, 56,605,180 bytes, SHA-256
  `37dd9d96f6fa99f3701eb33b2c60924f446ed511471644f20c15bb263d90aefa`,
  `windows-dao-a2-replica-2`, 57,055,645 bytes, SHA-256
  `ad474669d34dfcf75789628eb45e74d40350c9b367eee9dc5bc60f0d81a5ce67`,
  and `windows-dao-a2-replica-3`, 56,592,455 bytes, SHA-256
  `7fd3171d9bf3b16e90617ae01d828b62a8d90e80057f35ab7a44eddf958a8b2a`;
  replica diagnostics artifacts 1, 2, and 3 were respectively 7,220, 7,218,
  and 7,216 bytes with SHA-256 values
  `ffb964712334d28884a1461cd64026dab876f077b4bde18243153eb5b56e2c6c`,
  `9b5698baf518fd6235b2f3f8eaa7317ec7cfbd1f7278a22b0eaa52f13d04a144`,
  and `6b775536cb8faa954141eba635e54210a288f7c2018db8402051c1ef0fe8fa53`
- Bundle identities and inventory: `bundle-manifest.json` SHA-256
  `9e1dac53e13f0bf765fc41b242b85beb26c8a518f7a15777aa37641af575dd46`;
  `analysis/analysis-report.json` SHA-256
  `946ab5d4e41bf95ed427110b2b0a737b6b24652de072aa543f6ebf0788cf1640`;
  `analysis/derivation-candidates.json` SHA-256
  `32680a47c33f873b7c281f44ca0610b80be2de1926bbb01c6228b3fb25d4f2e4`;
  `analysis/holdout-structure-receipt.json` SHA-256
  `97ee3c265e910425447a1242416b38a481b2213b55b005455a17c869b09b6adc`.
  The manifest closes 28,021 payload entries and 96,620,952 bytes excluding
  the manifest: three observations, three environment records, three replica
  manifests, 75 checkpoint page indexes, and 27,933 unique page blobs. Each
  replica observation contains 25 checkpoints and 124,103 indexed page
  references, ends at 17,388 pages, records 20,098 changed-page entries and
  150,016 inserted rows, and reports 254,162,944 logical checkpoint-read bytes.
- Analysis result: the retained report records
  `scientific_outcome = one_or_more_submodels_predict_holdout`, 75 input
  checkpoints, 134,594,025 analysis work units, 16,785,408 record candidates
  examined, and 37,648 candidate models examined. Derivation survivor counts
  were one for `global_map_record` and zero for
  `global_map_conversion_inline`, `global_map_extended_base`, and
  `tdef_pointer_pair`. The global-map record layer has status
  `decisive_predicts_holdout` with page 1, record interval `[1915, 2048)`,
  polarity `set_means_not_in_use`, and 92 zero-suffix slack bytes. The inline
  conversion layer has status `no_outcome` for
  `growth_polarity_disagreement`; the extended-base layer is
  `not_applicable`; and the table-definition pointer-pair layer has status
  `no_outcome` for `no_tdef_record_candidate`. The report-level no-outcome
  reasons are exactly `growth_polarity_disagreement` and
  `no_tdef_record_candidate`.
- Predicate results: the 13 passing predicate ids are `A2-IDLE-EQUALITY`,
  `A2-D-SET-RELATION`, `A2-GLOBAL-PAGE-NONE`, `A2-GLOBAL-PAGE-MULTIPLE`,
  `A2-GLOBAL-RECORD-NONE`, `A2-GLOBAL-RECORD-MULTIPLE`,
  `A2-GLOBAL-RECORD-END`, `A2-STRUCTURAL-EXCLUSION`, `A2-POLARITY-NONE`,
  `A2-POLARITY-MULTIPLE`, `A2-POINTER-VALIDITY`,
  `A2-REPLICA-DISAGREEMENT`, and `A2-HOLDOUT-PREDICTION`. The two failing,
  terminal predicate ids are `A2-POLARITY-CROSSCHECK` and
  `A2-TDEF-RECORD-NONE`. The 19 `not_applicable` ids are
  `A2-TDEF-PAGE-NONE`, `A2-TDEF-PAGE-MULTIPLE`,
  `A2-TDEF-RECORD-MULTIPLE`, `A2-GROWTH-POINTER-NONE`,
  `A2-CHURN-PRECONDITION`, `A2-CHURN-POINTER-NONE`, `A2-POINTER-MULTIPLE`,
  `A2-CONVERSION-NONE`, `A2-CONVERSION-MULTIPLE`, `A2-SLOT-ACTIVATION`,
  `A2-SLOT-FINAL`, `A2-INLINE-BOUNDARY-NONE`,
  `A2-INLINE-BOUNDARY-MULTIPLE`, `A2-INLINE-SUFFIX`,
  `A2-BASE-DISCRIMINATION`, `A2-BASE-NONE`, `A2-BASE-MULTIPLE`,
  `A2-SNAPSHOT-RECONSTRUCTION`, and `A2-RESOURCE-BOUND`.
- Prior failed dispatches and workflow-only repairs: three earlier dispatches
  on 2026-08-22 produced no analyzed A2 result. Run `32582790187` timed out in
  the contract job before any acquisition; PR 46 limited that workflow job to
  its intended contract tests. Run `32584974169` reached all three workers,
  whose preflight refused the Server 2025 `dao360.dll`, before any checkpoint;
  PR 47 pinned the hosted jobs to `windows-2022`. Run `32586156614` acquired
  and retained all three replicas, but fan-in stopped on a false artifact-ZIP
  digest mismatch before assembly or analysis, leaving those replicas
  retained but unanalysed; PR 48 made the transport ZIP digests advisory while
  preserving content-level validation. PRs 46, 47, and 48 were workflow-only
  fixes and did not edit, revise, or otherwise touch the immutable plan or its
  R2 revision.
- Independent validation: the fan-in diagnostic field
  `independent_validation_passed = true` records success of the checked
  producer-commit validator inside the same hosted workflow. The retained
  manifest remains authoritative about evidential state:
  `bundle_status = decisive_pending_independent_validation` and
  `independent_validation_status = not_independently_validated`. No separate
  party has recomputed the candidate set, layered analysis, or holdout result
  from the retained bundle.
- Interpretation: this first retained A2 result is a descriptive provider
  observation only. It does not establish a Jet physical-format fact, Rust
  correctness, DAO compatibility, or product support. It changes no capability
  or support-matrix entry. No capability may change unless and until a
  separately provenanced independent recomputing validator accepts this exact
  retained bundle and analysis result.
- Usage: future separately provenanced independent A2 recomputation only;
  `file:docs/validation/DAO_PROVIDER_BLOCKER.md`;
  `file:oracle/windows-dao/README.md`
- Rights: project-generated through the licensed Microsoft DAO provider and
  retained as GitHub Actions artifacts; no provider binary or generated MDB is
  committed or redistributed by this repository
- Review: complete bundle retained and checked-workflow validation passed;
  independent recomputation and scientific interpretation remain pending

### EXP-0043 — A2 record-layer downgrade and closure

- Recorded: 2026-08-22, OpenAI Codex
- Kind: additive adversarial review record and plan-literal recomputation;
  corrective interpretation only, not a new acquisition, physical-format
  result, Rust result, compatibility result, or independent validation
- Question: Does the independent recomputing validator proposed by PR 50
  establish that the decisive global-map record layer reported by `EXP-0042`
  follows from the immutable `EXP-0040` plan as written?
- Origin: independent validator PR 50, branch `codex/a2-independent-validator`,
  closed unmerged at commit `24b6d8c`; its validator accepted the exact retained
  `EXP-0042` report, after which an adversarial review compared the validator
  and analyzer derivations with the immutable `EXP-0040` plan and exercised
  contradictory but hash-relinked bundle reports
- Environment: document and source review of the closed, unmerged validator
  commit and the checked-in A2 analyzer, plus a local Python 3.13 recomputation
  over replica 1 page 1 from the retained GitHub Actions run `32587946283`
  bundle recorded by `EXP-0042`; this review did not run DAO, acquire new
  databases, or alter the retained bundle
- Protocol: read the replica 1 `page-indexes` for the plan-declared D
  checkpoints `E0`, `D_GROW_0128`, `D_DROP`, `D_RECREATE_EMPTY`, and
  `D_REGROW_0128`; resolve page 1 through each ordered page SHA-256, read and
  hash-check the corresponding `page-store` blobs, enumerate every candidate
  start using the review's five-byte-skipped represented-set reading, and apply
  the D-only allocation, release, reallocation, additional-regrowth, unique
  polarity, and page-terminal uniform-suffix predicates without applying tag,
  base, or page-count-highwater content constraints. Separately inspect the
  validator's frozen-candidate, conversion-cross-check, predicate-reporting,
  TDEF-reason, and pointer-validity contracts and the adversarial tamper results.
- Artifacts: verbatim independent review
  `oracle/windows-dao/experiments/a2/design-inputs/fable-a2-independent-validator-review.md`,
  13,328 bytes, SHA-256
  `f17799d60f343aed91e51244ec9211c10596b7f4554ea0c812488fc08fbb58c3`;
  retained run `32587946283` bundle identity and hashes remain those recorded by
  `EXP-0042`
- Observation: the independent recomputing validator accepted the `EXP-0042`
  report, but both it and `oracle/windows-dao/scripts/a2_model.py` resolve the
  unique global record start only through the same unpreregistered structural
  predicate. The analyzer's `polarity_direction` requires tag byte 0 at
  `start`, a little-endian u32 base at `start+1..start+5`, a bitmap beginning at
  `start+5`, and inclusion through the `E0`, `D_GROW_0128`, and
  `D_REGROW_0128` page-count highwaters. Under the `EXP-0040` plan text as
  written—`record_candidate_procedure`,
  `hypotheses.global_map_record_predicate`, and
  `global_record_end_resolution`, including its statement that no byte,
  record, or page equality between checkpoints is a predicate—the independent
  local recomputation measured exactly 1,935 starts, offsets 0 through 1934,
  surviving on replica 1 page 1, all with polarity
  `set_means_not_in_use`. The plan-literal record-layer outcome is therefore
  `multiple_global_record_boundaries_survive`, not
  `decisive_predicts_holdout`.
- Contract findings to carry into A3: H2, the frozen derivation candidate set is
  hash-linked but never parsed or compared; H3, the polarity cross-check fires
  on the inline-to-indirect conversion leg by reinterpreting its tag and base as
  bitmap bits; M1, non-terminal predicate results are not checked; M2, distinct
  preregistered TDEF no-outcome reasons are collapsed; and M3, pointer validity
  is applied at every checkpoint rather than only within the preregistered
  validity window. These are validator/analyzer contract defects, not accepted
  evidence.
- Interpretation: the retained `EXP-0042` report remains retained and
  unaltered, but its decisive global-map record layer is not supported by the
  plan as written. Its manifest's `independent_validation_status` remains
  `not_independently_validated`; the validator's acceptance changes no
  capability or support-matrix entry. The run `32587946283` bundle and this
  review are exploratory design inputs only for a successor A3 experiment with
  a new experiment id. Before acquisition, that plan must explicitly
  preregister the tag/base/bitmap layout and the E0-extent start-resolution
  rule, including the applicable `E0`, `D_GROW_0128`, and `D_REGROW_0128`
  page-count highwater predicate. A2 is closed.
- Usage: exploratory design input for a successor A3 preregistration only;
  `file:oracle/windows-dao/experiments/a2/design-inputs/fable-a2-independent-validator-review.md`;
  `EXP-0040`; `EXP-0041`; `EXP-0042`
- Rights: project-authored review text and aggregate recomputation over the
  retained licensed-provider bundle; no provider binary, MDB, page blob, or
  generated database content is committed or redistributed
- Review: adversarial review complete; A2 closed with no independently
  validated scientific outcome and no capability movement

### EXP-0044 — Preregistered A3 allocation-map prediction campaign

- Recorded: 2026-08-22, OpenAI Codex
- Kind: additive pre-acquisition experimental design; no A3 worker, workflow,
  analyzer, generator, independent validator, dry-run result, DAO acquisition,
  physical-format result, Rust result, or compatibility result
- Question: Can the disclosed tag/base/bitmap allocation-map representation
  and separately delimited record-level models derived from two fresh Jet 3
  databases predict the transitions in a third fresh holdout without refitting?
- Origin: project-authored `DAO-A3-ALLOCATION-MAPS-001`, informed by
  `EXP-0040` through `EXP-0043`, the immutable A2 plan and R2 revision, the
  retained `EXP-0042` bundle, and the adversarial independent-validator review
  recorded by `EXP-0043`. No external MDB implementation, donated MDB, or
  Microsoft implementation source was inspected.
- Design-input pointer identities:
  `oracle/windows-dao/experiments/a3/design-inputs/a2-preregistration-pointer.md`,
  SHA-256 `8f16e79686620e254b0ba98de4d7cb21611f84a3e9b5c84d9fd6428987f51632`;
  `oracle/windows-dao/experiments/a3/design-inputs/a2-independent-review-pointer.md`,
  SHA-256 `2e89bb60aa5ac99d8f384836c75ce54c078817564d579d5411acd3bba8daae3b`;
  and
  `oracle/windows-dao/experiments/a3/design-inputs/exp-0042-bundle-pointer.md`,
  SHA-256 `9bcb4b3c7ca2b43abd44a38200042312156d14552908c6d00ec9a25b24178349`.
  Each pointer records the underlying immutable path or artifact and SHA-256.
- Exploratory bundle identity: A2 artifact
  `windows-dao-a2-bundle-1a0585446ac8b0d232ee4c0391cce9d635e7c43a-32587946283-1`,
  artifact SHA-256
  `7e58dc5e3c8424110897053cdfeab703b0e1d15fde2dfd4d8235efd62da43dc7`,
  manifest SHA-256
  `9e1dac53e13f0bf765fc41b242b85beb26c8a518f7a15777aa37641af575dd46`.
  This bundle is an A3 design input only and cannot satisfy an A3 derivation,
  holdout, predicate, independent validation, or capability result.
- Prior-observation disclosure: the one-byte inline tag, little-endian u32
  base, following bitmap, page-count-highwater record-start rule, and tag-1
  indirect layout were observed in the `EXP-0042` bundle before A3 was frozen.
  The observed indirect prefix `01 | 00 3A 00 00 | E0 3F 00 00` decodes to tag
  1, slot-0 u32 14848, and slot-1 u32 16352. A3's decisive claim is therefore a
  prediction on three new replicas, not rediscovery of either representation.
- Inherited protocol: A3 keeps A2's 25 closed-file checkpoints, three rotated
  role bindings, row algorithm, content-addressed page capture, bounds,
  independent matrix jobs, freeze-before-holdout fan-in, four layered outcomes,
  and decisive-report retention. The future A2 worker/workflow rebinding may
  change only the experiment id, plan path, and A3 document/artifact names and
  must reject any plan whose `experiment_id` is not exactly
  `DAO-A3-ALLOCATION-MAPS-001`.
- Record-start rule: decode each interval as tag, u32 base, and LSB-first
  bitmap. At E0, `D_GROW_0128`, and `D_REGROW_0128`, tag must be zero, every
  page in `[base,page_count)` must decode in-use, and `page_count` must decode
  not-in-use within capacity. Apply this within-snapshot representation anchor
  together with the D set relation, unique polarity, and page-terminal suffix.
  It is not a cross-checkpoint byte, record, page, or page-count equality rule.
- Conversion cross-check: on each listed inline leg, only newly appended pages
  representable by both snapshots must flip not-in-use to in-use. Stop at the
  first violation or before the first tag change, and retain the first violating
  leg/page or nulls in both the frozen set and report. In the exploratory bundle,
  `[1915,2048)` has base
  zero and capacity 1,024; D highwaters are 29, 157, and 285 pages. The first
  two cross-check legs pass, but leg 3, `L_REL_0512` to `L_REL_0768`, violates
  at pages 1021, 1022, and 1023 in both derivation replicas. The walk stops with
  first violating page 1021, three evaluated legs, and no representation-change
  stop; the later `P_ABS_12288` to `P_ABS_16480` tag change is never reached.
  On EXP-0042-like data, the `global_map_conversion_inline` and
  `global_map_extended_base` layers are terminal at leg 3 by
  construction; only `global_map_record` and `tdef_pointer_pair` can reach
  holdout. These numbers are non-evidential A3 design examples.
- Freeze and reporting: `derivation-candidates.schema.json` fixes qualified
  page arrays, four layer objects, and the polarity transcript. The validator
  must parse and compare them with independent recomputation and the report;
  hash linkage alone does not suffice. All 34 registered predicate ids appear
  exactly once. Applicable-layer predicate results carry the literal
  `applicable_layer`; report terminals are deduplicated across layer terminals;
  and `A3-HOLDOUT-PREDICTION` passes whenever any layer is decisive, even if a
  different layer records that predicate as its holdout terminal. TDEF terminal
  stages are ordered precondition, growth windows, churn windows, record, then
  multiplicity. Pointer validity applies only at named checkpoints at/after
  activation and only to the tag-1 u32 global slots or TDEF u24 page fields.
- Independent-validation contract: a future independent validator is written
  from the plan and schemas without analyzer imports or reads, recomputes every
  layer and holdout result, parses the frozen set, and rejects T1–T5: polarity,
  conversion outcome, relinked frozen-set contradiction, TDEF reason ordering,
  and nonterminal predicate-status tampering. Only its separately provenanced
  acceptance moves `independent_validation_status`.
- Dry-run disclosure: `not_run_preregistration_only`. This lane implements no
  analyzer, independent validator, worker, or workflow, so no dry run or T1–T5
  suite exists and nothing authorizes hosted dispatch.
- Execution gate: `BLOCKED` on the checked A3 analyzer/synthetic generator,
  independent recomputing A3 validator, fail-closed worker/workflow rebinding,
  additive dry-run disclosure, decisive-report contract validation, exact clean
  pushed producer commit, and licensed x86 DAO host binding.
- Artifacts: immutable plan
  `oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json`, SHA-256
  `b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1`; A3-bound plan, analysis, bundle, dry-run,
  environment, holdout-receipt, page-index, replica-manifest, and observation
  schemas; fixed frozen-candidate and independent-validation-report schemas;
  focused plan contract test
- Observation: `preregistration.acquisition_started` is `false`; no A3 database,
  checkpoint, replica observation, candidate set, report, validation receipt,
  or evidence bundle exists.
- Interpretation: this entry freezes a prediction protocol only. It assigns no
  independently validated Jet meaning, proves no Rust behavior or DAO
  compatibility, changes no support-matrix entry, and satisfies no release gate.
- Usage: `file:oracle/windows-dao/experiments/a3/README.md`;
  `file:oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json`;
  future separately reviewed A3 implementation and pre-acquisition dry runs
- Rights: project-authored plan, schemas, tests, and pointer documents; no DAO
  binary, MDB, page blob, or retained bundle is committed or redistributed
- Review: focused A3 plan/schema/hash contracts and repository validation must
  pass; acquisition remains blocked until every listed requirement is met

### EXP-0045 — A3 pre-acquisition predicate evaluation sequence revision

- Recorded: 2026-08-22, OpenAI Codex
- Kind: additive pre-acquisition reporting-order reconciliation; no A3 worker,
  workflow, analyzer result, validator result, dry-run result, DAO acquisition,
  physical-format result, Rust result, or compatibility result
- Origin: project-authored review of the immutable
  `DAO-A3-ALLOCATION-MAPS-001` plan after two independent implementations,
  analyzer PR #54 and validator PR #53, disagreed on whether
  `A3-TDEF-PAGE-MULTIPLE` and `A3-POINTER-VALIDITY` were `pass` or
  `not_applicable` on an analyzer-produced synthetic report. No external MDB
  implementation, Microsoft implementation source, donated MDB, A3 DAO
  observation, or holdout artifact was inspected.
- Additive R2 revision: the base plan's
  `predicate_registry.reporting_rule` states total reporting and terminal
  projection but leaves the campaign and per-layer predicate evaluation
  sequence unstated. Revision `DAO-A3-ALLOCATION-MAPS-001-R2` pins the missing
  order without changing the immutable base plan, schemas, 34-entry registry,
  mappings, operational rules, scientific models, or blocked execution gate.
  Acquisition has not started, so this additive revision is permitted by the
  base plan's amendment rule.
- Campaign order: evaluate `A3-IDLE-EQUALITY`,
  `A3-SNAPSHOT-RECONSTRUCTION`, and `A3-RESOURCE-BOUND`, in that order, before
  any layer.
- Layer orders: `global_map.record` evaluates `A3-GLOBAL-PAGE-NONE`,
  `A3-GLOBAL-RECORD-NONE`, `A3-D-SET-RELATION`, `A3-GLOBAL-RECORD-END`,
  `A3-POLARITY-NONE`, `A3-POLARITY-MULTIPLE`, `A3-GLOBAL-PAGE-MULTIPLE`,
  `A3-GLOBAL-RECORD-MULTIPLE`, `A3-STRUCTURAL-EXCLUSION`, and
  `A3-REPLICA-DISAGREEMENT`; `global_map.conversion_inline` evaluates
  `A3-POLARITY-CROSSCHECK`, `A3-CONVERSION-NONE`, `A3-CONVERSION-MULTIPLE`,
  `A3-SLOT-ACTIVATION`, `A3-SLOT-FINAL`, `A3-POINTER-VALIDITY`,
  `A3-INLINE-BOUNDARY-NONE`, `A3-INLINE-BOUNDARY-MULTIPLE`,
  `A3-INLINE-SUFFIX`, `A3-STRUCTURAL-EXCLUSION`, and
  `A3-REPLICA-DISAGREEMENT`; `global_map.extended_base` evaluates
  `A3-BASE-DISCRIMINATION`, `A3-BASE-NONE`, `A3-BASE-MULTIPLE`,
  `A3-POINTER-VALIDITY`, and `A3-REPLICA-DISAGREEMENT`; and
  `tdef.pointer_pair` evaluates `A3-TDEF-PAGE-NONE`,
  `A3-CHURN-PRECONDITION`, `A3-GROWTH-POINTER-NONE`,
  `A3-CHURN-POINTER-NONE`, `A3-TDEF-RECORD-NONE`,
  `A3-TDEF-PAGE-MULTIPLE`, `A3-TDEF-RECORD-MULTIPLE`,
  `A3-POINTER-MULTIPLE`, `A3-POINTER-VALIDITY`,
  `A3-STRUCTURAL-EXCLUSION`, and `A3-REPLICA-DISAGREEMENT`, in those exact
  orders.
- Evaluation and status projection: an applicable layer stops at its first
  terminal. An `applicable_layer` predicate is `pass` iff reached in at least
  one applicable layer and terminal nowhere, `fail` iff terminal in any layer,
  and otherwise `not_applicable`. A layer-specific predicate is `pass` iff
  reached and nonterminal, and is `not_applicable` when unreached or its layer
  is inapplicable. A terminal layer-specific predicate is `fail`.
  `A3-HOLDOUT-PREDICTION` retains the base plan's exception unchanged.
- Base-text consistency review: the supplied sequence is pinned without silent
  reordering, but four positions appear to conflict with the immutable
  operational prose. `A3-GLOBAL-RECORD-END` precedes polarity although the
  record-end rule says D has already selected a unique polarity; the base
  start-resolution prose is itself internally inconsistent because it also
  says polarity uniqueness follows end resolution. `A3-POLARITY-CROSSCHECK`
  precedes conversion, slot, and inline checks although the global-map search
  and source-contract text list polarity agreement after them.
  `A3-INLINE-SUFFIX` follows boundary zero/multiplicity although the survival
  rule makes the quiet suffix a prerequisite to boundary survival. Extended-
  base `A3-POINTER-VALIDITY` follows every base terminal although the base rule
  evaluates allocation bitmaps on referenced `0x05` pages whose validity that
  predicate establishes. These positions remain exactly as supplied in R2.
- Plan identities:
  `oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json`, SHA-256
  `b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1`;
  `oracle/windows-dao/experiments/a3/a3-allocation-maps-r2.plan.json`, SHA-256
  `3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1`.
- Observation: `preregistration.acquisition_started` remains `false`; this
  revision records no database, checkpoint, replica observation, candidate
  set, report, validation receipt, evidence bundle, or scientific outcome.
- Interpretation and execution gate: this amendment resolves an implementation
  contract ambiguity only. It assigns no Jet meaning, proves no Rust behavior
  or DAO compatibility, changes no support-matrix entry, and authorizes no A3
  acquisition. The `EXP-0044` execution gate remains `BLOCKED`.
- Usage: `file:oracle/windows-dao/experiments/a3/README.md`;
  `file:oracle/windows-dao/experiments/a3/a3-allocation-maps-r2.plan.json`;
  future separately reviewed A3 analyzer and independent validator
- Rights: project-authored revision and tests; no DAO binary, MDB, page blob,
  or retained bundle is committed or redistributed
- Review: focused A3 plan hash, exact sequence, status-projection, amendment,
  and flagged-position contracts plus repository validation must pass

### EXP-0046 — A3 pre-acquisition layer-semantics revision

- Recorded: 2026-08-22, Claude Fable 5
- Kind: additive pre-acquisition operational-rule reconciliation; no A3 worker,
  workflow, analyzer result, validator result, dry-run result, DAO acquisition,
  physical-format result, Rust result, or compatibility result
- Origin: project-authored joint review of analyzer PR #54
  (`origin/codex/a3-analyzer`) and independent validator PR #53
  (`origin/codex/a3-validator`), which found ten plan gaps that the two lanes
  filled differently (extended-page bitmap offset, conversion attribution,
  replica agreement, inline boundary source and suffix order, polarity versus
  multiplicity order, TDEF validity/structural position, slot activation,
  report ordering fields, holdout slack/slot semantics, page absence). The
  review is committed as
  `oracle/windows-dao/experiments/a3/design-inputs/fable-a3-pair-review.md`,
  SHA-256 `70b9717d3b3387cbd2d4f1ceec3c8deff4f7706563af07eb2c5e77a6c05eab65`. Every
  rule was derived from the immutable base plan's intent and from direct
  inspection of the `EXP-0042` bundle derivation replicas 1 and 2; the two
  implementations were read only to understand each divergence and no rule
  adopts an implementation for convenience. `EXP-0042` replica 3 was not
  opened. No external MDB implementation, Microsoft implementation source,
  donated MDB, A3 DAO observation, or holdout artifact was inspected.
- Additive R3 revision: `DAO-A3-ALLOCATION-MAPS-001-R3` binds the base plan
  (SHA-256 `b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1`)
  and R2 (SHA-256
  `3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1`),
  inherits R2's sequences unchanged, and pins one operational rule with one
  implementation for each gap (`R3-G01` through `R3-G10`) plus six minor
  readings (`R3-M01` through `R3-M06`). Acquisition has not started, so the
  revision is permitted by the base plan's amendment rule. No schema changed.
- Extended-page layout re-derived from `EXP-0042` (both derivation replicas):
  the referenced `0x05` pages 14848 and 16352 carry bytes `05 01 00 00` at
  `[0,4)`; the bitmap is `[4,2048)`, 16352 LSB-first bits. Page 14848 has one
  nonzero bitmap byte at offset 1860 (`0xFE`, bits 14849–14855) at
  `P_ABS_16480`; those seven bits flip to in-use at `H_REL_0064` and the
  page-index hashes of exactly pages 14849–14855 (with 0, 27, 28, 14848,
  16352, 16356–16358) change across that leg. Page 16352's tail run of
  not-in-use bits starts at bit 129, 196, 1028, and 1036 at `P_ABS_16480`,
  `H_REL_0064`, `H_REL_0896`, and `H_REL_0904`/`H_IDLE_REOPEN`, i.e. exactly
  `16352 + i = page_count` (16481, 16548, 17380, 17388). Under the R3 survival
  rule (flip-direction prediction on the slot-0 discriminator leg, map page
  self-in-use, `page_count` sentinel, beyond-EOF not-in-use) the unique
  survivor is `slot_relative_expected_0_16352`; `referenced_page_relative`
  maps the flips beyond `page_count`, the two off-by-minus-one variants decode
  the map page or sentinel wrongly, and the off-by-plus-one variants predict
  page 14856 (unchanged) or put the sentinel in-use. A 1-byte header was tested
  and refuted: byte 1 (`0x01`) would mark page 0 and page 16352 not-in-use and
  the tail run would land on page 16505, not 16481. On `EXP-0042`-like data
  the conversion layer is terminal at leg 3 and the extended-base layer is
  therefore inapplicable; these numbers are calibration only.
- Other re-derived calibrations: the global record on page 1 `[1915,2048)`
  passes bounds only under `set_means_not_in_use`; in-use set sizes 29/157/27/
  29/285, `Gp = [29,157)`, `|R \ G| = 128`; 34 D-flipped bytes 1922–1955 and
  92 uniform `0xFF` bytes through 2047 (slack 92). The conversion window
  classifies as inline ×2 (349, 797 pages), neither ×10 (capacity 1024
  exceeded from 1053 pages), indirect ×4, so the class-change count is 2
  (`A3-CONVERSION-MULTIPLE` if reached). The inline boundary stage is not
  reached; for calibration only, had the two valid inline checkpoints formed
  the inline phase, `b*` = 1920 + floor(797/8) + 1 = 2020 ≤ 2048. At `E0` the bytes `[1920,1924)` decode to u32 3758096384 with tag
  0, which is why slot activation is restricted to tag-1 checkpoints; both
  slots activate at `P_ABS_16480`. Replica disagreement, TDEF exclusion,
  holdout opening, and page absence are not exhibited by the bundle and carry
  no worked example.
- Reachability: `A3-POLARITY-NONE`, `A3-INLINE-BOUNDARY-NONE`, and
  `A3-INLINE-BOUNDARY-MULTIPLE` are unreachable by construction with recorded
  proofs and preempting predicates (R3-G02 admits into the inline phase only
  checkpoints that passed the frozen-end capacity, so `b* ≤ end` and the
  reduced-capacity decode passes);
  `A3-STRUCTURAL-EXCLUSION` is unreachable on both global layers and reachable
  only on `tdef.pointer_pair`; extended-base `A3-POINTER-VALIDITY` is reached
  but never terminal. 31 predicate ids must be reached by executed fixtures.
- Holdout semantics: frozen models are re-checked on the holdout without
  re-derivation; `zero_suffix_slack_bytes` is frozen as the derivation minimum
  and requires only structural agreement (≥ 16 uniform bytes) on the holdout;
  `slot_reference_pages` and `b*` are exact predictions although both depend
  on overshoot (a replica difference is `replica_disagreement`, a holdout
  difference is `holdout_prediction_failure`, and the synthetic generator may
  not tune replica-3 overshoot to preserve them); the conversion-layer holdout
  check also runs the polarity cross-check walk and the `[b*, end)` quiet
  suffix; holdout uniqueness is not re-established and that limitation is
  disclosed. R3-G04 discloses its departure from the base boundary
  enumeration (upward-closed survivor set), R3-G06 restores all-growth churn
  stability with enumerated L/P/H transitions, R3-G07/G01 narrow bitmap reads
  to the validated window, and R3-G03 freezes replica 1's cross-check
  transcript and the union of per-replica `qualified_pages`.
- Dry-run honesty clause: predicate reachability only by executed fixture
  transcripts (`dry-run/a3-reachability-transcript.json`); synthetic replica 3
  generated with independent overshoot in every phase; analyzer/validator
  acceptance gate is full-sweep agreement on every synthetic case (identical
  terminals, models, transcripts, and 34 statuses) retained as
  `dry-run/a3-pair-agreement.json`; T1–T5 reported only when executed.
- Plan identities:
  `oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json`, SHA-256
  `b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1`;
  `oracle/windows-dao/experiments/a3/a3-allocation-maps-r2.plan.json`, SHA-256
  `3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1`;
  `oracle/windows-dao/experiments/a3/a3-allocation-maps-r3.plan.json`, SHA-256
  `bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a`.
- Observation: `preregistration.acquisition_started` remains `false`; this
  revision records no database, checkpoint, replica observation, candidate
  set, report, validation receipt, evidence bundle, or scientific outcome.
- Interpretation and execution gate: this amendment resolves implementation
  contract ambiguities only. It assigns no independently validated Jet
  meaning, proves no Rust behavior or DAO compatibility, changes no
  support-matrix entry, and authorizes no A3 acquisition. The `EXP-0044`
  execution gate remains `BLOCKED`.
- Usage: `file:oracle/windows-dao/experiments/a3/README.md`;
  `file:oracle/windows-dao/experiments/a3/a3-allocation-maps-r3.plan.json`;
  `file:oracle/windows-dao/experiments/a3/design-inputs/fable-a3-pair-review.md`;
  future separately reviewed A3 analyzer and independent validator
- Rights: project-authored revision, review, and tests; no DAO binary, MDB,
  page blob, or retained bundle is committed or redistributed
- Review: focused A3 plan hash, R3 binding, gap-rule text, reachability,
  holdout-semantics, and dry-run-clause contracts plus repository validation
  must pass

### EXP-0047 — A3 pre-acquisition survivor-count and replay blob-bound revision

- Recorded: 2026-08-22, Claude Fable 5
- Kind: additive pre-acquisition operational-rule reconciliation; no A3 worker,
  workflow, analyzer result, validator result, dry-run result, DAO acquisition,
  physical-format result, Rust result, or compatibility result
- Origin: the executed full-sweep analyzer/validator pair gate of PR #58
  (`origin/fable/a3-dryrun`, `dry-run/a3-pair-agreement.json`, 100 fixtures,
  89 disagreeing) and its `dry-run/exp-0042-replay-report.json`, which was
  schema-rejected solely on `input_page_blob_count` 81 > 55. The two results
  were read only to locate the gaps; neither rule adopts an implementation for
  convenience. The ceiling and the 81 were re-measured directly from the
  retained `EXP-0042` bundle's replica 1 and 2 page indexes (manifest SHA-256
  `9e1dac53e13f0bf765fc41b242b85beb26c8a518f7a15777aa37641af575dd46`);
  `EXP-0042` replica 3 was not opened. No external MDB implementation,
  Microsoft implementation source, donated MDB, A3 DAO observation, or holdout
  artifact was inspected.
- Additive R4 revision: `DAO-A3-ALLOCATION-MAPS-001-R4` binds the base plan
  (SHA-256 `b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1`),
  R2 (`3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1`), and
  R3 (`bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a`),
  inherits their sequences and rules unchanged except the two sentences
  `R4-C01` supersedes, and pins `R4-S01`, `R4-B01`, `R4-B02`, and `R4-C01`. Acquisition has not started, so the revision is permitted by
  the base plan's amendment rule.
- `R4-S01` survivor count: `decision_rules.freeze_rule` says only that
  `derivation_survivor_counts equals layer counts`; on
  `second_global_page_opposite_polarity` the analyzer froze 2 at
  `A3-POLARITY-MULTIPLE` and the validator expected 0. R4 pins the count as
  the cardinality, in derivation replica 1, of the candidate set the layer's
  terminal predicate classified: every MULTIPLE terminal carries its
  multiplicity (at least 2), every NONE/precondition/discrimination/
  set-relation/record-end/cross-check terminal carries 0, a model or a
  single-survivor terminal (slot, inline-suffix, pointer-validity, structural
  exclusion, holdout prediction) carries 1, `A3-REPLICA-DISAGREEMENT` carries
  replica 1's count at its own stop, and an inapplicable layer carries 0. The
  table is written for every terminal of every layer. Rationale: a MULTIPLE
  terminal is the statement that more than one candidate survived, so
  freezing 0 there would contradict the terminal and collapse the
  field-for-field freeze comparison to a constant for every non-decisive layer.
- `R4-B01` replay blob bound: the base plan's 55 lives in
  `analyzer_dry_run_contract.historical_a1_input_not_required_by_a3`
  (`max_input_page_blobs`, and `candidate_bound_assertion`'s 13 qualified
  pages per replica) and is A1 run-12 calibration text, but
  `dry-run-report.schema.json` copied it as the maximum of
  `input_page_blob_count`, which binds every A3 dry-run report. R4 pins the
  ceiling 1800 = 2 derivation replicas x 25 planned checkpoints x (16 + 16
  `max_qualified_pages_per_submodel` + 4 referenced pages: two global slots and
  two TDEF pointers), distinct blobs counted once, synthetic runs reporting 0,
  replica 3 never opened. Re-measured `EXP-0042` values: global qualified pages
  `{0,1,20,21}` and TDEF qualified pages `{0,1,23,24}` in both replicas; over
  25 checkpoints x 2 replicas the global pages yield 50 distinct blobs and the
  TDEF pages 71, sharing pages 0 and 1, union exactly 81; no referenced page is
  opened because the conversion layer stops at cross-check leg 3 and TDEF at
  `A3-TDEF-RECORD-NONE`. The replacement `candidate_bound_assertion` asserts
  those pages, at most 16 per submodel, exactly 81 blobs, and no more than
  1800.
- `R4-B02` schema edit: `oracle/windows-dao/experiments/a3/dry-run-report.schema.json`
  `properties.input_page_blob_count.maximum` 55 -> 1800, SHA-256
  `f88c1f9bf131352311d3e77e70f95d84d015b60c3d50cce40ceed668b390a593` ->
  `e7b054543529f4b2ac38cda7ae15fac80cf20bd6745f4fcd43cec02eabc9f13d`, and the
  matching pin in `oracle/windows-dao/scripts/a3_spec.py`. R3 kept every
  schema immutable; R4 edits this one because the dry-run report is a
  pre-acquisition calibration artifact the base plan declares non-evidential
  (`retained_exp_0042_input.scientific_evidence = false`, `disclosure_rule`),
  never enters a retained evidence bundle, and validates no scientific
  outcome. The nine evidence schemas and `plan.schema.json` are byte-identical.
  The single sentence of R3's `pair_acceptance_gate` declaring the dry-run
  schema unchanged is superseded for this one field.
- `R4-C01` record candidate count: independent review of this revision ruled
  the `sixteen_qualified_pages` divergence a plan gap, not an analyzer defect.
  `R3-G08`/`R3-G03` count `record_candidates_examined` per (derivation
  replica, qualified page), which at 16 + 16 pages in both replicas gives
  134,283,264, while the immutable base plan's
  `combined_record_candidate_bound`, `bounds.max_record_candidates`, and the
  analysis-report schema maximum are all 67,141,632 = 32 x 2,098,176 with no
  replica factor, and `prefix_sum_work_model` (537,133,056 + 1,049,088 =
  538,182,144 < 600,000,000) likewise assumes none. R4 supersedes those two
  sentences: the field and the work units count each union qualified page
  once across derivation replicas (a TDEF page when the churn precondition
  passed in at least one replica), so the exact ceiling 67,141,632 is accepted
  and one page more (69,239,808) is rejected by schema, bound, and
  `max_qualified_pages_per_submodel`. The independent validator must
  recompute the field on that basis and must also enforce
  `bounds.max_record_candidates`, which the validator lane never does today.
  `EXP-0042` replay value under the rule: 8 union pages, 16,785,408. No bound,
  `plan.schema.json` const, or evidence schema changes.
- Not resolved here: the TDEF u24 pointer-layout disagreement (87 of 89
  cases: analyzer `tdef_pointer_pair` terminal `A3-POINTER-MULTIPLE` with a
  null model where the validator derives `u24le_page_then_u8_slot`, offsets
  0/2044, under `tdef_no_outcome_ordering` and `R3-G06`) is an analyzer
  defect against existing text, not a plan gap, and is left to the analyzer
  lane.
- Independent review: `/private/tmp/fable-59-60-review.md` (sections A.6 and
  C), not committed; it verified additivity, hash pins, the schema-edit
  justification, the survivor table, and the 81/1800 derivations, and
  required the `R4-C01` ruling before merge.
- Plan identities:
  `oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json`, SHA-256
  `b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1`;
  `oracle/windows-dao/experiments/a3/a3-allocation-maps-r2.plan.json`, SHA-256
  `3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1`;
  `oracle/windows-dao/experiments/a3/a3-allocation-maps-r3.plan.json`, SHA-256
  `bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a`;
  `oracle/windows-dao/experiments/a3/a3-allocation-maps-r4.plan.json`, SHA-256
  `939ce3ceef035b9da0e4527f1ffd9ddd6b21e23f088f867c56172f84650332ea`.
- Observation: `preregistration.acquisition_started` remains `false`; this
  revision records no database, checkpoint, replica observation, candidate
  set, report, validation receipt, evidence bundle, or scientific outcome.
- Interpretation and execution gate: this amendment resolves implementation
  contract ambiguities only. It assigns no independently validated Jet
  meaning, proves no Rust behavior or DAO compatibility, changes no
  support-matrix entry, and authorizes no A3 acquisition. The `EXP-0044`
  execution gate remains `BLOCKED`.
- Usage: `file:oracle/windows-dao/experiments/a3/README.md`;
  `file:oracle/windows-dao/experiments/a3/a3-allocation-maps-r4.plan.json`;
  `file:oracle/windows-dao/experiments/a3/dry-run-report.schema.json`;
  the A3 analyzer and independent validator lanes, which must rebind to R4
- Rights: project-authored revision and tests; no DAO binary, MDB, page blob,
  or retained bundle is committed or redistributed
- Review: focused A3 plan hash, R4 binding, survivor-count table, blob-bound
  derivation, schema-hash, record-candidate-count, and analyzer-defect-exclusion
  contracts plus
  repository validation must pass

### EXP-0048 — A3 pre-acquisition dry-run disclosure

- Recorded: 2026-08-23, Claude Fable 5
- Kind: additive pre-acquisition dry-run disclosure under
  `analyzer_dry_run_contract.disclosure_rule` and R3
  `dry_run_honesty_clause`; non-evidential calibration only. No A3 DAO
  acquisition, worker/workflow run, physical-format result, Rust result,
  support-matrix change, or compatibility result
- Question: Did the schedule-derived synthetic analyzer dry run, the
  derivation-only EXP-0042 replay, the executed predicate-reachability
  transcript, and the full-sweep analyzer/independent-validator pair gate all
  execute and pass under the R4-bound implementations?
- Execution: `python3 -B oracle/windows-dao/scripts/a3_dryrun.py --jobs 10
  --workspace /private/tmp/access97-a3-dryrun-pOEsTz --retained-root
  /private/tmp/claude-501/-Users-oglass-Development-Misc-access97-rs/77df2993-62f0-4041-97d5-19885072a109/scratchpad/a2run4/windows-dao-a2-bundle-1a0585446ac8b0d232ee4c0391cce9d635e7c43a-32587946283-1/jet3-a2-bundle
  --output oracle/windows-dao/experiments/a3/dry-run` at code-only commit
  `6d1a8ff370f8a8abf8fee6f14f61131638faedc2` (branch
  `fable/a3-dryrun-disclosure`). The analyzer sources are those merged by PR
  #62 at `65359a0d1f73e3f7369e37de162ea498bbe5214d`; the validator sources are
  those merged by PR #63 at `5d49e6bcc8a61c9b2c375f28388ed7facece3795`
  plus the independent plan-derived pair-projection fix in the stamped commit.
  Every
  fixture was materialised as a real on-disk bundle in the plan artifact
  layout, analyzed through `a3_analysis.build_analysis` with the spawned
  holdout-structure receipt, then independently validated by
  `a3_independent_validator.py` (full verdict and `--pair-projection`) as a
  separate process on the identical bytes. The artefacts stamp
  `analyzer_commit = 6d1a8ff3…`, the commit whose code produced them.
- Synthetic sweep: 100 fixtures (baseline; every free-parameter value
  including conversion ordinals 1–24 and never, slot activation 0/1/2, both
  polarities, empty/partial/full anchor fill, slack 16/32/64, starts
  0/1/1915/2042, bases 0/1/16/1024, anchor tag 0/1, every representation-change
  leg and never; the EXP-0042 calibration fixture; 36 named perturbations).
  Every expected per-layer outcome, transcript stop/violation, and model field
  was asserted against the produced report; 0 failing. Twelve sweep checks all
  true: all-layers-decisive model recovered, partial outcome retained, anchor
  fill leaves the inline boundary invariant, calibration prefix
  `01003a0000e03f0000` produced from generated bytes, 16+16 qualified pages
  accepted with `record_candidates_examined` 67,141,632 and 17 rejected
  `A3-RESOURCE-BOUND`, every axis complete, replica 3 differs from both
  derivation replicas in each of D/L/P/H, pair agreement, every reachable
  predicate reached, unreachable predicates nonterminal, report schema valid.
  `a3-synthetic-report.json` result `pass`.
- Reachability (`a3-reachability-transcript.json`): 31 of 31 reachable
  predicate ids executed as the terminal of their designated fixture, derived
  from analyzer transcripts, never from the registry; `A3-POLARITY-NONE`,
  `A3-INLINE-BOUNDARY-NONE`, and `A3-INLINE-BOUNDARY-MULTIPLE` were never
  terminal in any fixture (asserted nonterminal).
- Pair gate (`a3-pair-agreement.json`): 100 of 100 fixtures carry and agree on
  400 independent layer views (status, terminal predicate id, model, and
  `derivation_survivor_count` under R4-S01), 100 polarity-cross-check
  transcripts, 100 campaign terminals, and all 3,400 ordered predicate
  statuses (34 per fixture). The
  validator emitted `accepted=true` with T1–T5 rejected on every fixture that
  holds a frozen global-record model; on fixtures without one it stops with
  `tamper_suite_not_executable` after every untampered recomputation,
  predicate status, and bound check passed, which the gate records as the
  tamper suite being not applicable (`tamper_cases` T1–T4 mutate a decisive
  model). `missing_page_blob` and `seventeen_qualified_pages` are rejected by
  the bundle contract as designed (`snapshot_page_blob_missing`,
  `resource_bound_breach`) with nonzero validator exits, while each still
  exports the bounded plan-derived four-layer view, empty cross-check,
  campaign terminal, and 34-status vector; the pair gate compares every field
  before accepting the expected rejection. A focused one-status mutation of
  either rejection fixture fails the pair gate.
- EXP-0042 replay (`exp-0042-replay-report.json`, result `pass`): replicas 1
  and 2 only (the source type refuses to name replica 3); every observation
  and page index hash-checked against manifest
  `9e1dac53e13f0bf765fc41b242b85beb26c8a518f7a15777aa37641af575dd46`; record
  page 1 `[1915,2048)` `set_means_not_in_use` slack 92; legacy relation leaves
  1,935 starts; highwaters 29/157/285; cross-check stops at leg 3
  `[L_REL_0512, L_REL_0768]` page 1021 with null representation stop;
  conversion would be `A3-CONVERSION-MULTIPLE` and is disclosed as unreached;
  TDEF `no_tdef_record_candidate`; qualified pages `{0,1,20,21}` /
  `{0,1,23,24}`; exactly 81 unique page blobs opened, below 1800 (R4-B01);
  T3 hash-relinked contradictory frozen set and T5 nonterminal fail both
  rejected on parsed values; every predicate id exactly once.
- Artefacts (`oracle/windows-dao/experiments/a3/dry-run/`, SHA-256 from
  `checksums.json`):
  `a3-synthetic-report.json`
  `ff91e8637778900b83690ae9ab58bd129f16543048cf7778002d328e9381d378`;
  `a3-synthetic-cases.json`
  `2383cfbc59ddb0f34d4321dcfbc119727644190ae47b5fc0e73b2f70fb874b9e`;
  `a3-reachability-transcript.json`
  `a69ff749e3972a0e0dcf1a09ae28aeab28a13599d3068ab403a86a3cdd1af32a`;
  `a3-pair-agreement.json`
  `c05907b53505732e21cc0381e88e4ac52151dcfc2b031895d3d96a8833164084`;
  `a3-sweep-checks.json`
  `a92b181d08955a42796fcc7241969cddca87db769789b37df02ba5c7fe72007d`;
  `exp-0042-replay-report.json`
  `7738f65ae0b4a45979a8ed4328244cfdd4d79f4f7066dc6c626491af368d7b75`;
  `exp-0042-replay-transcript.json`
  `92f1687e65161dbb6ebe21ee7844e478ec0b58462092cd5824a44749be51507a`.
  The two reports embed `recorded_utc`; a re-run reproduces every other
  artefact byte-for-byte and the reports up to that field.
- Independent review: Pass 6 of the reviewer's working file, committed
  verbatim as
  `oracle/windows-dao/experiments/a3/design-inputs/fable-pass6-58-dryrun-review.md`
  (SHA-256
  `42d75d6688704c68839b24880b3f82d967a663216e220e3150256326a2c71b34`). It
  re-ran the harness against the retained bundle and reproduced 100/100,
  31/31, and 81 blobs; verified that reachability, sweep checks, and the
  tamper-suite reading are derived from execution and plan text; and
  recommended two non-blocking fixes, both applied here: the stamped
  `analyzer_commit` is the code-only commit that generated the artefacts, and
  `replica_3_overshoot_independent` records every target-bearing checkpoint
  and requires per-phase D/L/P/H inequality against both derivation replicas.
- History: PR #58's first execution (R3) reached 30/31 ids with 11/100
  agreement and surfaced the TDEF u24 pointer-layout analyzer defect (fixed
  by PR #59), the survivor-count, blob-bound, and record-candidate plan gaps
  (`EXP-0047`, R4), and the validator's T5 choosing `A3-HOLDOUT-PREDICTION`
  on a non-decisive report (fixed by PR #63). None of those results is
  restated as evidence; only the R4-bound execution above is disclosed.
- Observation: `preregistration.acquisition_started` remains `false`; no DAO
  database, checkpoint, replica observation, or A3 scientific outcome exists.
  Every fixture is synthetic and every replay reading is EXP-0042 calibration
  declared non-evidential by the base plan.
- Interpretation and execution gate: the `dispatch_gate` items "schedule-derived
  synthetic analyzer dry run", "EXP-0042 derivation-only exploratory replay",
  "decisive-report contract-validator case" (`validate_analysis_report`
  accepted the all-layers-decisive report), and "independent-validator T1–T5
  tamper suite" (accepted=true with all five variants rejected on the
  decisive fixture) are executed and pass, and this entry is their committed
  disclosure. The `EXP-0044` execution gate nevertheless remains `BLOCKED`:
  the worker/workflow lane (PR #56) is not merged and has open blocking
  findings, no exact clean pushed commit has been designated for
  acquisition, and no licensed x86 DAO host run has been authorized. This
  entry assigns no independently validated Jet meaning, proves no Rust
  behavior or DAO compatibility, changes no support-matrix entry, and
  authorizes no acquisition.
- Usage: `file:oracle/windows-dao/experiments/a3/README.md`;
  `file:oracle/windows-dao/experiments/a3/dry-run/`;
  `file:oracle/windows-dao/scripts/a3_dryrun.py` and `a3_dryrun_*.py`,
  `a3_generator.py`, `a3_generator_schedule.py`
- Rights: project-authored harness, fixtures, and reports; the retained
  EXP-0042 bundle is read locally and not committed or redistributed; no DAO
  binary, MDB, or page blob is committed
- Review: focused A3 dry-run, generator, analyzer, validator, and plan-contract
  tests plus repository validation must pass
### EXP-0050 — A3 pre-acquisition revision binding, baseline, and timeout revision

- Recorded: 2026-08-22, OpenAI Codex
- Kind: additive pre-acquisition evidence-binding and operational-rule
  reconciliation; no A3 worker run, DAO acquisition, replica observation,
  candidate set, holdout receipt, analysis result, retained evidence bundle,
  physical-format result, Rust result, or compatibility result
- Origin: findings 3, 4, and 5 of the independent adversarial re-review of
  hosted-lane PR #56 (`/private/tmp/sol-56-review.md`, not committed); the
  `Revision binding` and `Relative baselines` reasoning in the provisionally
  numbered hosted-lane provenance entry at
  `origin/fable/a3-rebind:docs/PROVENANCE.md`; and the L/H baseline capture in
  the checked A2 worker at `oracle/windows-dao/scripts/a2/A2.Worker.ps1`.
  These inputs were read to locate unresolved plan semantics. No external MDB
  implementation, Microsoft implementation source, donated MDB, DAO
  observation, A3 replica, holdout artifact, or page blob was inspected.
- Ledger ordering: PR #64 merged as `EXP-0048`; `EXP-0049` is reserved for the
  still-open PR #56 hosted lane. Renumber this entry before merge if PR #56
  lands under a different number. Reserving its number now avoids recreating
  the collision found between those lanes.
- Additive R5 revision: `DAO-A3-ALLOCATION-MAPS-001-R5` binds the base plan
  (SHA-256 `b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1`),
  R2 (`3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1`),
  R3 (`bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a`),
  and R4 (`939ce3ceef035b9da0e4527f1ffd9ddd6b21e23f088f867c56172f84650332ea`).
  Acquisition has not started, so the base plan's amendment rule permits this
  revision.
- `R5-V01` revision binding: every schema-valid A3 evidence document must carry
  required `revision_plan_sha256` equal to the governing R5 hash. A detached
  closed bundle must retain the complete revision chain at
  `plan/a3-allocation-maps-r2.plan.json` through
  `plan/a3-allocation-maps-r5.plan.json`, each inventoried with role
  `revision_plan`, media type `application/json`, and its pinned byte hash; the
  base plan remains role `plan`. The binding is pinned in checked code instead
  of a schema `const`, because placing the R5 hash in a schema would make the
  R5 hash depend on the schema hash and the schema hash depend on R5. Producer
  and independent validation must reject a missing, altered, or mismatched
  revision document, inventory entry, or document binding.
- `R5-V02` schema edit inventory: only the following nine evidence schemas
  change. Each gains required `revision_plan_sha256`; the manifest additionally
  gains required `campaign_started_utc` and `campaign_elapsed_seconds`, role
  `revision_plan`, and four slots in its file-count bounds.

  | Schema | SHA-256 before | SHA-256 after |
  | --- | --- | --- |
  | `analysis-report.schema.json` | `f15bf39ad703f77fb7749d93214fe43711a9b525376b128f93c898b531db6460` | `91c75502fcaf404d484db17c5521d8eb9915250b35a290862856387cfc181993` |
  | `bundle-manifest.schema.json` | `9d049c910b4a53da5d3cd3ee71f02c5671fdbb75b94e33587999cf40a91e9727` | `ebf80361941aeef1dbbb466e396cfb7c6caca463a5e92187b503a83a0e35699c` |
  | `derivation-candidates.schema.json` | `50a9f7a1208969475a89ac3782077cb2bc0e5d3f9635ec51d5a46e8afcacd5b2` | `071408f3d9e1b1ac5cd99cbd0c2c8a93eaece1adde2d2b97b226b7ebaaa29d7b` |
  | `environment.schema.json` | `6fb863f1c224698b466ba5fd5e10d9869a6b313b7480f02045e70c2e8eb49465` | `244946f4f7204865775d2329fe0172f6a5c9a4d7bc3ea9d1c9334660307fd565` |
  | `holdout-structure-receipt.schema.json` | `c2316f9bf84f7722c93160c354f671d7411c0089bf7f52124237b262f43c50fe` | `e79d6c140b9adb31c313090c9ccc02c2ae09a185849554509d25334a0d93fed6` |
  | `independent-validation-report.schema.json` | `2ad90d2b6ade15e815ad9819c09ca28d6b7e77ab6064e3a1139a9acf7e4c6d8c` | `fa956530661d0fa04844d8a507729a7e1cd5a97e4125b4a88c20a9e8eddf8766` |
  | `page-index.schema.json` | `5e78e1a4b8d95ca1313c5d7e1df78f033f3791c959cb22a5b464aef581ddbdfd` | `102fc5ad5770eda32603d4494af19218513df22af49a3c19ccffd4ecf08a5428` |
  | `replica-artifact-manifest.schema.json` | `a60cf012c2ceb8dee55ffd55e4fa21b14759d0d258b0203e14fd583b0b08d197` | `7eb03e03beac3b965473d355c48f0d51106426dceae43743443678caa735cc43` |
  | `replica-observation.schema.json` | `e0605f67cae502da3b0187c05f9c6ff83b1f7da42a1496af95310dc90d1a2bbf` | `9f0fce53213372258a5783872ccbfa78bcd5ecd8b6436d84513398d3c473a016` |

- Unchanged schemas: `plan.schema.json` remains SHA-256
  `177fdbdda54b0e0d90383578a9bbea4a398cbcbd74424d522997a8f304113f03`;
  `dry-run-report.schema.json` remains
  `e7b054543529f4b2ac38cda7ae15fac80cf20bd6745f4fcd43cec02eabc9f13d`.
- `R5-V03` supersession: exactly five earlier statements are superseded for
  the R5-V02 additions and no other purpose: the word `schemas` in R3's
  `inherited_contract`; R3's `original_schemas_remain_immutable`; the R4-B02
  sentence that makes the nine evidence schemas byte-identical; the clause
  `or evidence schema changes` in R4's `revision_scope`; and R4's
  `original_evidence_schemas_remain_immutable`. The statements continue to
  hold for `plan.schema.json`; every other R3/R4 rule stands, and the nine
  evidence schemas are re-frozen at the after-hashes above.
- `R5-L01` relative baselines: L's baseline is the total closed-file page count
  recorded as `actual_file_pages` for `D_REGROW_0128`, the immediately
  preceding state with no intervening operation. H's baseline is
  `actual_file_pages` for `P_ABS_16480`. For each `L_REL_nnnn` or
  `H_REL_nnnn`, threshold = baseline + nnnn and overshoot = actual pages -
  threshold; the three existing disclosure fields must record those values,
  while P absolute checkpoints keep a null relative baseline. Both validators
  must reject a wrong capture point or arithmetic identity.
- Baseline decision rationale: a literal database post-create baseline would
  put the first L batch at least 256 pages beyond that baseline after the two D
  growth phases, so `L_REL_0064` would be satisfied by one batch and the first
  cross-check leg would degenerate. The EXP-0042 design inputs and their
  calibrated leg-3 violation were produced with the checked A2 worker's lazy,
  first-relative-batch capture. Fixing the reading to the explicit D-regrow and
  P-absolute states preserves that preregistered schedule and keeps A3 a
  prediction rather than silently retuning it.
- `R5-T01` campaign timeout: 2700 seconds is a hard retained-evidence bound.
  `campaign_started_utc` is the hosted run attempt's start observable (GitHub
  `run_started_at` for its run id and attempt), which includes the base plan's
  setup/dispatch allowance. `campaign_elapsed_seconds` is the floor of
  `created_utc - campaign_started_utc`. Exactly 2700 is accepted; 2701,
  missing timing fields, or inconsistent arithmetic is rejected. An attempt
  that cannot establish the start or exceeds the ceiling may retain diagnostics
  only; it must not write a schema-valid manifest or upload under the retained
  successful-bundle identity.
- Timeout decision rationale: the base plan derives 2700 as the exact sum of
  ceilings and requires accept-at-ceiling/reject-one-over tests. Advisory
  semantics would make that the only plan bound no retained evidence can
  witness. A delayed run can be re-dispatched without losing evidence, whereas
  an over-time bundle already retained as successful cannot be repaired.
- Contract implementation and re-pins in this entry: `a3_spec.py` binds R5,
  the full R2-R5 chain, and all new schema hashes; `a3_analysis.py` emits the
  governing hash; `a3_dryrun_bundle.py` emits the hash, retains the revision
  chain, and supplies schema-valid synthetic timing; `a3_independent_bundle.py`
  verifies the revision inventory, per-document binding, and manifest timing;
  `a3_independent_validator.py` makes R5 governing and emits its hash; focused
  analyzer, independent-validator, dry-run, and plan-contract tests are
  re-pinned to R5 and demonstrate binding/timing rejection.
- Deferred implementation: the hosted worker/producer-side emission,
  inventory, and hard pre-finalization timeout enforcement belong to PR #56's
  lane. R5-L01's per-checkpoint identity enforcement in both validators belongs
  to the hosted/validator lanes. This preregistration and its local checked
  consumers do not claim those deferred gates are complete.
- Plan identities:
  `oracle/windows-dao/experiments/a3/a3-allocation-maps.plan.json`, SHA-256
  `b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1`;
  `oracle/windows-dao/experiments/a3/a3-allocation-maps-r2.plan.json`, SHA-256
  `3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1`;
  `oracle/windows-dao/experiments/a3/a3-allocation-maps-r3.plan.json`, SHA-256
  `bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a`;
  `oracle/windows-dao/experiments/a3/a3-allocation-maps-r4.plan.json`, SHA-256
  `939ce3ceef035b9da0e4527f1ffd9ddd6b21e23f088f867c56172f84650332ea`;
  `oracle/windows-dao/experiments/a3/a3-allocation-maps-r5.plan.json`, SHA-256
  `03cdfe0dde1563d386c646d844e9383637547ca0e5321ef29bac264dfcc6bf3b`.
- Observation: `preregistration.acquisition_started` remains `false`; this
  entry records no database, checkpoint, replica observation, candidate set,
  report, validation receipt, evidence bundle, or scientific outcome.
- Interpretation and execution gate: this amendment pins evidence and
  operational semantics only. It assigns no independently validated Jet
  meaning, proves no Rust behavior or DAO compatibility, changes no
  support-matrix entry, and authorizes no A3 acquisition. The `EXP-0044`
  execution gate remains `BLOCKED`.
- Usage: `file:oracle/windows-dao/experiments/a3/README.md`;
  `file:oracle/windows-dao/experiments/a3/a3-allocation-maps-r5.plan.json`;
  the A3 analyzer, dry-run producer, hosted-lane producer, and independent
  validator contracts that must bind to R5
- Rights: project-authored revision, schema edits, contract code, and tests;
  no DAO binary, MDB, page blob, or retained bundle is committed or
  redistributed
- Review: focused A3 plan-chain, schema old-to-new hash, revision inventory,
  document binding, lazy-baseline, campaign-timeout, and unchanged-schema
  contracts plus repository validation must pass
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
  Additional tracked Usage: `file:docs/validation/external-corpus.json`.
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
  Additional tracked Usage: `file:docs/validation/external-corpus.json`.
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
  Additional tracked Usage: `file:docs/validation/external-corpus.json`.
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
  Additional tracked Usage: `file:docs/validation/external-corpus.json`.
- Rights: inspection authorized locally; not redistributable; no redistribution
  grant; do not commit the file or derived content
- Review: pending independent review

### OBS-0002 — Retained Stage 1 classifier run

- Recorded: 2026-08-20, OpenAI Codex
- Kind: observation
- Question: What lossless `PageKind` tallies does the Stage 1 classifier report
  when it visits every complete page of `FIX-0001` through `FIX-0004`?
- Origin: direct read-only classification of the four exact external fixtures
  beneath the opt-in `JET3_EXTERNAL_FIXTURE_ROOT`; no other bundle path was
  inspected
- Environment: macOS 26.3.1 build 25D771280a on arm64; Rust 1.96.0
  (`ac68faa20c58cbccd01ee7208bf3b6e93a7d7f96`); locale `C.UTF-8`; time zone
  `America/New_York`
- Protocol: at clean producer commit
  `0a48b190ffb3211e3e1fd1f0483327b507d15136`, first run the checked
  `tools/inspect_external_corpus.py` verifier over the manifest-bound
  `FIX-0001` through `FIX-0004` identities, then invoke the
  `jet3-classifier-snapshot` binary with those four exact paths, verified
  SHA-256 values, and verified byte sizes. The binary opens each fixture only
  through public `DatabaseReader`, visits every page once through
  `ClassifiedPage`, and emits canonical JSON keyed by fixture SHA-256 and the
  producer commit.
- Artifacts: producer commit
  `0a48b190ffb3211e3e1fd1f0483327b507d15136`; binary
  `jet3-classifier-snapshot`; snapshot
  `docs/validation/stage1-classifier-snapshot.json`, 1,774 bytes, SHA-256
  `b45e8c240cd386583398c96478345aa74127dc7a249b04decb50173c6b92d370`
- Observation: the snapshot records only per-fixture `PageKind` tallies,
  including numeric `Unknown(u8)` buckets, and no fixture file content.
- Interpretation: this is a bounded Rust self-observation of the narrow
  `SRC-0020` classifier. Unknown tags remain uninterpreted. The result does not
  establish structural validity, semantic correctness, Jet generation, or DAO
  compatibility and is not DAO verification.
- Usage: `file:docs/validation/stage1-classifier-snapshot.json`
- Rights: aggregate classifier tallies only; the external fixture files and
  their content remain outside the repository and are not redistributable
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
