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
- Review: two independent review/fix passes covered evidence attribution,
  producer reachability, analyzer/result-state boundaries, capture and
  publication bounds, artifact identity, exact pins, and operational-doc
  agreement. They corrected stale-pin test expectations and superseded run
  instructions; final focused verification found no remaining findings.

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
- Usage: `file:oracle/windows-dao/scripts/m1/M1.Dao.ps1`;
  `file:oracle/windows-dao/scripts/dev/Value.DevJob.ps1`
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
- Usage: `file:oracle/windows-dao/scripts/dev/Value.DevJob.ps1`
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
  `file:oracle/windows-dao/tests/test_m1_preflight_contract.py`;
  `file:oracle/windows-dao/scripts/dev/Value.DevJob.ps1`
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
  Additional tracked Usage:
  `file:crates/jet3/src/usage_map.rs`;
  `file:crates/jet3/src/catalog_record.rs`;
  `file:fuzz/fuzz_targets/table_definition_parsing.rs`;
  `file:fuzz/fuzz_targets/catalog_parsing.rs`;
  `file:fuzz/fuzz_targets/usage_map_traverse.rs`;
  `file:oracle/windows-dao/experiments/a4/README.md`;
  `file:oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json`;
  `file:oracle/windows-dao/experiments/a4/design-inputs/a4-scope-approved.md`;
  `file:oracle/windows-dao/scripts/a4_independent_h3.py`.
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

### SRC-0022 — DAO database-password mutation contract

- Recorded: 2026-08-26, OpenAI Codex
- Kind: public source
- Question: How can a development-only DAO control add a database password,
  and what input bound applies?
- Origin: Microsoft Learn, “Database.NewPassword method (DAO),” accessed
  2026-08-26,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/database-newpassword-method-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: inspect the documented method signature, argument descriptions,
  and remarks only
- Artifacts: the cited public documentation page; no documentation content is
  redistributed by this repository
- Observation: Microsoft documents `NewPassword(old, new)` as changing a
  database password; each password string is limited to 20 characters, an
  empty old password adds a password to a database that has none, and password
  matching is case-sensitive.
- Interpretation: the development-only DAO opening matrix may use
  `NewPassword` with a bounded nonempty password to create a passworded control.
  This API contract assigns no meaning to an MDB byte or field and establishes
  no Rust correctness or compatibility.
- Usage:
  `file:oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1`
- Rights: citation to public Microsoft documentation; no documentation content
  is redistributed
- Review: pending independent review

### SRC-0023 — DAO type, field, index, and relation constants for local discovery

- Recorded: 2026-08-27, OpenAI Codex
- Kind: public source
- Question: Which complete documented DAO type candidates and field/relation
  attributes may the local table-definition discovery job pass to DAO without
  reproducing an inventory from memory?
- Origin: Microsoft Learn documentation source pinned at MicrosoftDocs
  `office-developer-client-docs` commit
  `eedbd61ca40689e7cfed5e1cfd9440a9dc3ab7a5`: “DataTypeEnum enumeration
  (DAO),” “Field.Attributes property (DAO),” “Database.CreateRelation method
  (DAO),” and “RelationAttributeEnum enumeration (DAO),” accessed 2026-08-26
  and 2026-08-27:
  https://github.com/MicrosoftDocs/office-developer-client-docs/blob/eedbd61ca40689e7cfed5e1cfd9440a9dc3ab7a5/docs/access/desktop-database-reference/datatypeenum-enumeration-dao.md,
  https://github.com/MicrosoftDocs/office-developer-client-docs/blob/eedbd61ca40689e7cfed5e1cfd9440a9dc3ab7a5/docs/access/desktop-database-reference/field-attributes-property-dao.md,
  https://github.com/MicrosoftDocs/office-developer-client-docs/blob/eedbd61ca40689e7cfed5e1cfd9440a9dc3ab7a5/docs/access/desktop-database-reference/database-createrelation-method-dao.md,
  and
  https://github.com/MicrosoftDocs/office-developer-client-docs/blob/eedbd61ca40689e7cfed5e1cfd9440a9dc3ab7a5/docs/access/desktop-database-reference/relationattributeenum-enumeration-dao.md
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: inspect the complete DataTypeEnum table, the fixed/variable and
  auto-increment field-attribute rows, the CreateRelation signature, and the
  update/delete cascade relation-attribute rows. Preserve the complete type
  table as checked JSON input and let the pinned DAO provider accept or reject
  every candidate in an isolated fresh Jet 3 database.
- Artifacts: pinned `datatypeenum-enumeration-dao.md` SHA-256
  `51147cb927489b36583de4729355fccc78cc0781032453775f2a011f58535d7b`;
  pinned `field-attributes-property-dao.md` SHA-256
  `08c0417611d7f71d786d6fff035c2718046a529c1e25ff07c29bc8c3633f036a`;
  pinned `database-createrelation-method-dao.md` SHA-256
  `91d8314d5a8f734f879bb79145e46df84be65f947d303b6aa97439ec057d0bfa`;
  pinned `relationattributeenum-enumeration-dao.md` SHA-256
  `cb41bbd96eb4122b30056772427b81e514e27d6876507a6c51f42af4e9f754c0`;
  none of the documentation files is redistributed by this repository
- Observation: the documentation supplies 31 distinct DataTypeEnum values,
  field attributes 1 (fixed), 2 (variable), and 16 (auto-increment), and
  relation attributes 256 (cascade update) and 4096 (cascade delete).
- Interpretation: these are bounded DAO API inputs only. They assign no
  meaning to MDB bytes and establish no physical layout, Rust correctness, or
  compatibility. Physical meanings require a separately recorded repeated
  observation.
- Usage:
  `file:oracle/windows-dao/scripts/dev/TableDefinition.TypeInputs.json`;
  `file:oracle/windows-dao/scripts/dev/TableDefinition.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Value.DevJob.ps1`;
  `file:crates/jet3/src/column_definition.rs`;
  `file:docs/validation/repository-contract.json`
- Rights: citations to public Microsoft documentation; no documentation
  content is redistributed
- Review: pending independent review

### SRC-0024 — DAO record mutation methods for local row discovery

- Recorded: 2026-08-27, OpenAI Codex
- Kind: public source
- Question: Which documented DAO operations may the local row-discovery job
  use to create null rows, update existing rows, locate controlled records,
  and delete a record without assigning meaning to an MDB byte?
- Origin: Microsoft Learn, “Recordset.AddNew method (DAO),” “Recordset.Edit
  method (DAO),” “Recordset.Update method (DAO),” “Recordset.FindFirst method
  (DAO),” “Recordset.NoMatch property (DAO),” and “Recordset.Delete method
  (DAO),” accessed 2026-08-27:
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/recordset-addnew-method-dao,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/recordset-edit-method-dao,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/recordset-update-method-dao,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/recordset-findfirst-method-dao,
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/recordset-nomatch-property-dao,
  and
  https://learn.microsoft.com/en-us/office/client-developer/access/desktop-database-reference/recordset-delete-method-dao
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, code pages, and time zone are not applicable
- Protocol: inspect only the method/property contracts needed to construct the
  bounded local scenarios. Use AddNew/Update for inserted rows, the documented
  default Null state for unassigned fields, FindFirst plus NoMatch before
  Edit/Update or Delete, and close DAO before retaining a checkpoint.
- Artifacts: the cited public documentation pages; no documentation content is
  redistributed by this repository
- Observation: AddNew prepares a new record and defaults fields without an
  explicit default to Null; Update persists AddNew or Edit changes. Edit
  prepares the current record for changes. FindFirst locates the first record
  matching controlled criteria and NoMatch reports failure. Delete removes the
  current record.
- Interpretation: these are bounded DAO API operations only. They assign no
  physical meaning to MDB bytes and establish no Rust correctness or
  compatibility. Physical row facts require the separately repeated
  `EXP-0060` observation.
- Usage: `file:oracle/windows-dao/scripts/dev/Row.DevJob.ps1`
- Rights: citations to public Microsoft documentation; no documentation
  content is redistributed
- Review: pending independent review

### SRC-0025 — Unicode mappings for Windows code pages 1251 and 1252

- Recorded: 2026-08-27, OpenAI Codex
- Kind: public primary mapping tables
- Question: Which exact single-byte values map to Unicode scalars in Windows
  code pages 1251 and 1252, including undefined byte positions?
- Origin: Unicode Consortium, Microsoft-vendor mapping directory, “cp1251 to
  Unicode table” and “cp1252 to Unicode table,” table version 2.01 dated
  1998-04-15, accessed 2026-08-27:
  https://www.unicode.org/Public/MAPPINGS/VENDORS/MICSFT/WINDOWS/CP1251.TXT
  and
  https://www.unicode.org/Public/MAPPINGS/VENDORS/MICSFT/WINDOWS/CP1252.TXT
- Environment: documentation retrieval; operating system, architecture,
  provider version, locale, system code page, and time zone are not applicable
- Protocol: transcribe the complete 128-entry upper-half mapping for each
  table, preserve undefined positions as errors, and verify every lower-half
  byte maps to the same Unicode scalar value
- Artifacts: the cited public text tables; repository tests use selected exact
  byte/scalar pairs and do not redistribute the source files
- Observation: both tables define bytes `00` through `7f` identically. CP1251
  maps `80` to U+0402, `88` to U+20AC, `c0` to U+0410, and `ff` to U+044F,
  with `98` undefined. CP1252 maps `80` to U+20AC, `8c` to U+0152, `9f` to
  U+0178, and `ff` to U+00FF, with `81`, `8d`, `8f`, `90`, and `9d`
  undefined.
- Interpretation: an explicitly selected text decoder may map these two code
  pages byte for byte and must reject an undefined input while retaining its
  raw bytes. These tables do not establish which code page an MDB uses or how
  a database declares it.
- Usage: `file:crates/jet3/src/text.rs`;
  `file:crates/jet3/src/text_tests.rs`;
  `file:docs/architecture/SEMANTIC_READER.md`;
  `file:fuzz/README.md`;
  `file:docs/validation/repository-contract.json`
- Rights: citations to Unicode Consortium mapping data; no table file is
  redistributed
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
### EXP-0049 — Hosted A3 acquisition lane rebinding (no acquisition)

- Recorded: 2026-08-23, OpenAI Codex
- Kind: implementation rebinding of the hosted acquisition lane to the A3 base
  plan and governing R5 revision; no worker or workflow run, DAO acquisition,
  replica observation, candidate set, holdout receipt, analysis result,
  independent-validation result, retained evidence bundle, physical-format
  result, Rust result, support claim, or compatibility result
- Origin: the checked A2 lane as permitted by the base plan's
  `implementation_rebinding.source_rule`; the EXP-0042 operating lessons
  already recorded in this ledger; the additive R2 through R5 plan chain; and
  the independent adversarial re-review of PR #56 at
  `/private/tmp/sol-56-review.md` (not committed). The review and checked
  project-authored sources were used to locate contract defects. No external
  MDB implementation, Microsoft implementation source, donated MDB, DAO
  observation, A3 replica, holdout artifact, or page blob was inspected.
- Rebound lane: `run-a3-replica.ps1`, `a3/A3.Worker.ps1`, the A3 page-store and
  progress glue, `a3_bundle.py`, the phase-split analyzer and holdout process,
  and `.github/workflows/windows-dao-a3.yml` implement one dispatch-only,
  three-replica `windows-2022` campaign with a single fan-in job. Production
  documents use the `dao_a3_*` evidence types and the checked A3 experiment,
  plan, producer, campaign, replica, environment, and provider bindings.
- Freeze order implemented by checked control flow: fan-in downloads and
  assembles replicas 1 and 2, derives and canonically retains
  `analysis/derivation-candidates.json`, and records its SHA-256 and completed
  freeze marker before the workflow downloads replica 3. A separately spawned
  holdout process reads the retained freeze state and frozen bytes before it
  inventories or copies the separately downloaded holdout tree; only after its
  schema-valid receipt is accepted does the analyzer resume from the retained
  frozen state and open replica 3. Resume does not rederive or refit the frozen
  models.
- Receipt observables implemented fail closed: the freeze state records
  `replica_3_artifact_existed_before_freeze_phase_completed` and
  `analyzer_replica_3_opens_before_receipt`; the holdout process derives
  `validated_after_candidate_freeze` from holdout absence, the retained
  candidate bytes and digest, the bound completed marker, and zero pre-receipt
  analyzer opens. It derives `page_bytes_exposed_to_analyzer` from that open
  count, rejects either exposure or a failed phase binding, and emits the
  bounded receipt. The analysis report's
  `holdout_structurally_validated_after_freeze` is accepted only from that
  receipt and the same zero-open observable. These statements describe checked
  code paths and tests, not an observed hosted execution.
- R5 revision binding implemented on the producer side: the governing
  `revision_plan_sha256` is
  `03cdfe0dde1563d386c646d844e9383637547ca0e5321ef29bac264dfcc6bf3b`
  in every environment, replica observation, page index, replica artifact
  manifest, frozen candidate set, holdout receipt, analysis report, and bundle
  manifest. The worker verifies the base plan and R2
  (`3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1`),
  R3 (`bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a`),
  R4 (`939ce3ceef035b9da0e4527f1ffd9ddd6b21e23f088f867c56172f84650332ea`),
  and R5 before DAO database creation, and the bootstrap source inventory pins
  all four revision files. The closed bundle retains the complete R2-R5 chain
  under `plan/`, each with role `revision_plan`, media type
  `application/json`, and its pinned byte hash; the base plan remains role
  `plan`. Producer validation recomputes the plan bytes, inventory tuple,
  per-document governing hash, and cross-document bindings. The independent
  validator is invoked with R5 and independently recomputes the same retained
  chain and binding contract.
- R5-L01 baseline implementation: immediately after retaining
  `D_REGROW_0128`, the worker captures that checkpoint's
  `actual_file_pages` as the L baseline; immediately after retaining
  `P_ABS_16480`, it captures that checkpoint's `actual_file_pages` as the H
  baseline. The worker refuses a relative target before its named capture and
  discloses `target_baseline_pages`, `target_threshold_pages`, and
  `target_overshoot_pages` at every L/H checkpoint. P absolute checkpoints
  disclose a null relative baseline. Producer and independent validation check
  both named capture identities, threshold arithmetic, overshoot arithmetic,
  and achieved-page lower bounds against the R5 rule.
- R5-T01 timeout implementation: before evidence finalization, fan-in reads
  the hosting service's run-attempt `run_started_at` through the GitHub Actions
  API for the bound run id and attempt. The producer writes `created_utc` only
  after the complete payload validates, computes
  `campaign_elapsed_seconds = floor(created_utc - campaign_started_utc)`, and
  refuses to write `bundle-manifest.json` unless the elapsed value is within
  the hard 2700-second retained-evidence bound. Producer and independent
  validators recompute the identity; focused tests accept exactly 2700 and
  reject 2701 without a manifest. The successful retained-bundle artifact step
  runs only on job success, while bounded diagnostics remain `if: always()`.
  Fan-in `timeout-minutes: 15` equals the base plan's
  `bounds.fan_in_timeout_seconds = 900` and is asserted by the workflow
  contract test.
- Observation: `preregistration.acquisition_started` remains `false`; this
  entry records source and contract-test behavior only. No schema-valid A3
  evidence document from DAO, database, checkpoint, replica, candidate set,
  report, receipt, or retained hosted bundle was acquired or inspected.
- Interpretation and execution gate: the rebinding assigns no independently
  validated Jet meaning, proves no Rust behavior or DAO compatibility, changes
  no support-matrix entry, and does not authorize acquisition. The EXP-0044
  execution gate remains `BLOCKED` until its separate dispatch requirements
  are explicitly satisfied.
- Usage: `file:.github/workflows/windows-dao-a3.yml`;
  `file:oracle/windows-dao/scripts/run-a3-replica.ps1`;
  `file:oracle/windows-dao/scripts/a3/A3.Worker.ps1`;
  `file:oracle/windows-dao/scripts/a3_bundle.py`;
  `file:oracle/windows-dao/scripts/a3_analysis.py`;
  `file:oracle/windows-dao/scripts/a3_holdout.py`
- Rights: project-authored scripts, workflow, tests, and provenance record; no
  DAO binary, MDB, page blob, replica artifact, or retained bundle is committed
  or redistributed
- Review: all `test_a3_*` modules, `test_windows_dao_a3_workflow`,
  `validate_repository_contract`, and diff hygiene must pass

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

### EXP-0051 — First hosted A3 result: global-map record predicts holdout

- Recorded: 2026-08-23, OpenAI Codex
- Kind: controlled hosted DAO acquisition with a complete retained bundle, a
  preregistered decisive scientific result, and acceptance by the
  independently recomputing A3 validator; descriptive provider observation
  only, not a Rust or DAO-compatibility result
- Question: Can the disclosed tag/base/bitmap allocation-map representation
  and separately delimited record-level models derived from two fresh Jet 3
  databases predict allocation-map transitions in a third fresh holdout
  database without refitting?
- Origin and binding: project-authored `DAO-A3-ALLOCATION-MAPS-001` campaign
  `a3-run-32626186825`, executed end to end by GitHub Actions run
  `32626186825` using `.github/workflows/windows-dao-a3.yml` on `main` from
  exact clean pushed producer commit
  `146add25cd6443c3cdae7f3f02e20080014f3ba3`. The evidence binds the immutable
  base plan SHA-256
  `b16f78436bdfea701451880a9b761b3e3aaf1b3ea0b62fef32a6afde22e05cb1`
  and governing R5 SHA-256
  `03cdfe0dde1563d386c646d844e9383637547ca0e5321ef29bac264dfcc6bf3b`,
  which in turn binds R2
  `3feca409d07bd748954902c51c44f85d7c0708c1af9a99a53f96db2d87ea3bc1`,
  R3 `bac371167fa67e92e87649e3f28c338ccc6ca57a668da496dfa084c42ce1996a`,
  and R4 `939ce3ceef035b9da0e4527f1ffd9ddd6b21e23f088f867c56172f84650332ea`.
  No donated MDB or third-party MDB implementation was used.
- Environment and timing: all three replicas ran on `windows-2022` (`win22`,
  Windows `10.0.20348`) in x86 Windows PowerShell `5.1.20348.5499` with Python
  `3.13.7` and machine-registered `DAO.DBEngine.36` from `dao360.dll` file
  version `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  every environment record says `status = ready`. The hosted run began at
  `2026-08-23T07:40:26Z`; the manifest was created at
  `2026-08-23T08:01:36Z`; the retained evidence records 1,270 elapsed seconds,
  within R5's hard 2,700-second campaign bound. The workflow completed
  successfully at `2026-08-23T08:02:27Z`.
- Protocol and freeze order: execute 25 closed-file checkpoints for each of
  three fresh replicas, use replicas 1 and 2 for derivation, canonically freeze
  the candidate set, only then download and structurally validate replica 3,
  resume analysis without refitting, close and producer-validate the bundle,
  and invoke the independent recomputing validator in a separate process. The
  report records 75 input checkpoints and all three holdout flags as true:
  `holdout_opened_after_freeze`,
  `holdout_structurally_validated_after_freeze`, and `holdout_evaluated`. The
  holdout receipt records `validated_after_candidate_freeze = true`,
  `page_bytes_exposed_to_analyzer = false`, and `result = pass`.
- Retained artifact: GitHub artifact id `9490002226`,
  `windows-dao-a3-bundle-146add25cd6443c3cdae7f3f02e20080014f3ba3-32626186825-1`,
  106,941,677 bytes, transport SHA-256
  `2336bfacc05b4e1cdfeed61afa7a07dcad346a70e192d9c5b6152c6f98e8eacd`.
  A read-only local copy was inspected at
  `/private/tmp/claude-501/-Users-oglass-Development-Misc-access97-rs/77df2993-62f0-4041-97d5-19885072a109/scratchpad/a3run/bundle`.
  The manifest closes 26,259 payload entries and 93,109,888 bytes excluding
  the manifest, including 26,167 unique page blobs, three observations, three
  environments, three replica manifests, and 75 checkpoint indexes.
- Evidence identities: `bundle-manifest.json` is 6,431,127 bytes, SHA-256
  `f1a644abae1585d8ed0531f45a0544d3264d2449f6d5973ef2ef0bb3d5fefaab`;
  `analysis/derivation-candidates.json` is 1,612 bytes, SHA-256
  `ec7c8d27cc46ef9dfdc8214d025cd2d6493ab089f00fc35dbf0ccb9899cdcc0a`;
  `analysis/analysis-report.json` is 5,914 bytes, SHA-256
  `7587389e4323171aff9b9efcd46bcd5fc8e2ec8273116e8a0360965e4e11faeb`;
  and `analysis/holdout-structure-receipt.json` is SHA-256
  `f51e4874065f5f31974b1a92e68062345db0a0f516ee7d79515b9462b7aff277`.
  The separately retained 1,197-byte
  `validation/independent-validation-report.json` is SHA-256
  `28297781b8476176f4d7e23675b257029711f7a7c8a856b15fcdfef8973dfe18`;
  `fan-in-status.json` is SHA-256
  `1a59f4f642f92dd4c0dc2c24ea354f2c509d85806a24a9ed814b9867fb212d85`.
- Scientific outcome: the report records
  `scientific_outcome = one_or_more_submodels_predict_holdout`, 16,785,408
  record candidates examined, 22,652 candidate models examined, and
  134,291,460 analysis work units. Qualified global pages are exactly
  `[0, 1, 20, 21]` and qualified TDEF pages exactly `[0, 1, 23, 24]`.
  Derivation survivor counts are one for `global_map.record` and zero for
  `global_map.conversion_inline`, `global_map.extended_base`, and
  `tdef.pointer_pair`.
- Layer results: `global_map.record` is `decisive_predicts_holdout` with the
  sole model `bit_polarity = set_means_not_in_use`, page 1, record interval
  `[1915, 2048)`, and 92 zero-suffix slack bytes.
  `global_map.conversion_inline` is `no_outcome` at
  `A3-POLARITY-CROSSCHECK` for `growth_polarity_disagreement`: the first two
  growth legs pass and the third, `L_REL_0512` to `L_REL_0768`, first violates
  at page 1021. `global_map.extended_base` is `not_applicable` because the
  conversion layer retained no model. `tdef.pointer_pair` is `no_outcome` at
  `A3-TDEF-RECORD-NONE` for `no_tdef_record_candidate`. The two report
  terminals are exactly `A3-POLARITY-CROSSCHECK` and
  `A3-TDEF-RECORD-NONE`; the report-level no-outcome reasons are exactly
  `growth_polarity_disagreement` and `no_tdef_record_candidate`.
- Predicate results: 18 ids pass: `A3-IDLE-EQUALITY`,
  `A3-D-SET-RELATION`, `A3-GLOBAL-PAGE-NONE`,
  `A3-GLOBAL-PAGE-MULTIPLE`, `A3-GLOBAL-RECORD-NONE`,
  `A3-GLOBAL-RECORD-MULTIPLE`, `A3-GLOBAL-RECORD-END`,
  `A3-TDEF-PAGE-NONE`, `A3-STRUCTURAL-EXCLUSION`, `A3-POLARITY-NONE`,
  `A3-POLARITY-MULTIPLE`, `A3-GROWTH-POINTER-NONE`,
  `A3-CHURN-PRECONDITION`, `A3-CHURN-POINTER-NONE`,
  `A3-REPLICA-DISAGREEMENT`, `A3-SNAPSHOT-RECONSTRUCTION`,
  `A3-RESOURCE-BOUND`, and `A3-HOLDOUT-PREDICTION`. The two terminal ids
  above fail. Fourteen ids are `not_applicable`: `A3-TDEF-PAGE-MULTIPLE`,
  `A3-TDEF-RECORD-MULTIPLE`, `A3-POINTER-MULTIPLE`,
  `A3-POINTER-VALIDITY`, `A3-CONVERSION-NONE`,
  `A3-CONVERSION-MULTIPLE`, `A3-SLOT-ACTIVATION`, `A3-SLOT-FINAL`,
  `A3-INLINE-BOUNDARY-NONE`, `A3-INLINE-BOUNDARY-MULTIPLE`,
  `A3-INLINE-SUFFIX`, `A3-BASE-DISCRIMINATION`, `A3-BASE-NONE`, and
  `A3-BASE-MULTIPLE`; this accounts for all 34 registered ids in R2 order.
- Predicted versus observed: the non-evidential `EXP-0048` EXP-0042 replay
  predicted the same qualified page sets, the same page-1 `[1915, 2048)`
  `set_means_not_in_use` global record with 92 slack bytes, the same conversion
  terminal and leg-3/page-1021 disagreement, the same inapplicable
  extended-base layer, and the same TDEF terminal. That replay did not open a
  holdout and therefore predicted only the derivation shape. This fresh A3 run
  reproduced that preregistered shape and, after the freeze, independently
  observed that the frozen global-record model predicted replica 3 without
  refitting. Conversion, extended-base, and TDEF made no successful holdout
  prediction.
- Independent recomputation: the checked independent validator ran from the
  same exact commit in a separate process and emitted `accepted = true`,
  `independent_validation_status = independently_validated`, an empty
  `discrepancy_codes` array, and true values for frozen-set parsing, agreement
  with recomputation, agreement with the analysis report, predicate-registry
  recomputation, and holdout recomputation. It independently rejected T1
  through T5 with their required discrepancy codes. A read-only local rerun of
  both producer validation and the independent validator against the retained
  copy reproduced manifest SHA-256
  `f1a644abae1585d8ed0531f45a0544d3264d2449f6d5973ef2ef0bb3d5fefaab`
  and byte-identical independent-report SHA-256
  `28297781b8476176f4d7e23675b257029711f7a7c8a856b15fcdfef8973dfe18`.
  The immutable manifest remains
  `bundle_status = decisive_pending_independent_validation` and
  `independent_validation_status = not_independently_validated`; the separate
  report and this additive entry supply the later provenance required by the
  base plan to establish the independently validated scientific result without
  rewriting retained evidence.
- Prior infrastructure-only attempts: runs `32619652843`, `32620335712`, and
  `32621021140` failed while `actions/download-artifact` extracted replica
  artifacts, before fan-in; no scientific analysis ran and no bundle was
  retained. Runs `32621896162` and `32623259568` exceeded the 900-second fan-in
  bound and were cancelled before or at independent recomputation; neither
  retained a bundle. These five attempts are infrastructure failures, not
  scientific no-outcomes and not contrary observations. PRs #66 and #67 fixed
  artifact download/extraction before the successful run, and PR #68 made
  fan-in fit its existing bound; all three merged before run `32626186825`
  without changing the preregistered scientific rules.
- Claims and capability decision: the report's claims block has only
  `descriptive_provider_observation_only = true`; its
  `general_tdef_catalog_row_index_or_lval_layout`,
  `unobserved_slot_or_base_behavior`,
  `compaction_encryption_or_version_behavior`, `rust_correctness`, and
  `dao_compatibility_or_support` claims are all false. Accordingly this result
  independently validates only the preregistered DAO-observation prediction.
  It does not establish general allocation usage, conversion, extended-base,
  TDEF/catalog, row, index, long-value, compaction, encryption, version, Rust
  correctness, DAO compatibility, or product support.
- Governing support-matrix decision: no capability advances.
  `format.pages_allocation_usage` remains `implementation = partial` and
  `verification = internal_only`; every other entry remains unchanged. The
  base plan's `claims.dao_compatibility_or_support = false` and
  `decisive_report_handling.independent_validation_rule` authorize the
  separately provenanced `independent_validation_status` movement, not a DAO
  compatibility claim. `docs/validation/EVIDENCE.md` defines
  `dao_differential` as agreement between DAO and Rust canonical semantic
  results for the required scenarios and operation, while this campaign
  produced no Rust semantic result; it also forbids the “DAO verified” label
  without such a bundle. The required G3 differential scenario set and G8
  exact-release evidence are absent, so both gates remain `BLOCKED`. The
  `docs/PROVENANCE.md` entry is the clean-room evidence-ledger record required
  by G0; no separate gate-status ledger or support-matrix evidence pointer is
  permitted for this result.
- Usage: narrow independently validated experimental input for a future,
  separately provenanced implementation only;
  `file:oracle/windows-dao/experiments/a3/README.md`;
  `file:oracle/windows-dao/experiments/a3/analysis-report.schema.json`;
  `file:oracle/windows-dao/experiments/a3/independent-validation-report.schema.json`
- Rights: project-generated through the licensed Microsoft DAO provider and
  retained as GitHub Actions artifacts; no provider binary, MDB, page blob, or
  retained bundle is committed or redistributed by this repository
- Review: hosted workflow success, artifact identity, manifest and frozen-set
  hashes, producer validation, and independent recomputation were checked;
  focused A3 plan-contract and repository-contract tests must pass

### EXP-0052 — Preregistered A4 row-anchored allocation and catalog campaign

- Recorded: 2026-08-23, OpenAI Codex
- Kind: preregistered base plan for a controlled DAO physical-observation
  campaign; no A4 acquisition, result, analyzer, validator, worker, or workflow
  is included
- Question: Can table-relative row-directory locators, complete allocation-map
  rows, indirect extended-map traversal, and a minimum catalog field model
  derived from two fresh Jet 3 databases predict a role-rotated third holdout
  without refitting?
- Origin: project-authored experiment `DAO-A4-ROW-ANCHORED-MAPS-001`, grounded
  in the bounded row-directory and allocation-map primitives of `SRC-0020` and
  the user-approved scope brief copied byte-for-byte at
  `oracle/windows-dao/experiments/a4/design-inputs/a4-scope-approved.md`,
  SHA-256
  `ead09d9cec961d018ed4845f14d825d2ae8da2d3329f12d6ae9ea2233e4eeeb7`.
  The retained A3 result `EXP-0051` and its local read-only bundle were used
  only as calibration/design input, never as A4 evidence.
- Environment: planned Windows x86 PowerShell 5 with `DAO.DBEngine.36`; exact
  provider identity and binary SHA-256 must match across three replicas.
  Windows ANSI code page 1252 is mandatory and recorded; `A4TAB_É4`, bytes
  `41 34 54 41 42 5f c9 34`, is the sole non-ASCII identifier.
- Protocol: freeze immutable base plan
  `oracle/windows-dao/experiments/a4/a4-row-anchored-maps.plan.json`, SHA-256
  `3e74e67a213611596aaa0f5a4c3e433b2528a438bfa74708f4937e0233ed9aa1`,
  before acquisition. Execute exactly 25 closed, quiescent checkpoints with
  one listed logical schema mutation per schema transition and canonical DAO
  user-schema snapshots at every checkpoint. Derive H1--H4 on independent
  replicas 1 and 2, canonically freeze before replica 3 is downloaded/opened,
  then predict the role-rotated holdout without refit. Retain R5-V01-style
  plan/revision binding, phase-specific R4-S01-style measured survivor counts,
  R4-C01-style union-once work charging with terminal-path maxima, including
  a two-replica 774,929,266-unit H4 path below the 800,000,000-unit ceiling
  of `A4-SCOPE-AMENDMENT-001` (classified
  `conservative_upper`; only its checked comparator is unit-tested at
  800,000,000/800,000,001 outside the 40 byte fixtures, and the byte-level
  resource terminal is a 67,200-entry changed-hash one-over campaign),
  complete frozen candidate arrays, distinct H1 model/physical candidate ids,
  exact lifecycle ranges, and independent H4 root/structural/encoding
  results, a complete manifest,
  independent recomputation and tamper suite, and the R5-T01-style hard
  2,700-second campaign bound derived from hosted `run_started_at`: accept
  2,700, reject 2,701 before manifest creation.
- Artifacts: immutable base plan and README under
  `oracle/windows-dao/experiments/a4/`; A4-specific evidence schema family;
  new `dao-schema-snapshot.schema.json`; hash-pinned calibration receipt
  `oracle/windows-dao/experiments/a4/design-inputs/a3-calibration-receipt.json`,
  SHA-256
  `788605e1aeca015d88319ef78b3ae34adbec04527efaa11b79f5663474169d3e`;
  focused structural/arithmetic contract
  `oracle/windows-dao/tests/test_a4_plan_contract.py`; and plan-derived work
  recomputation script
  `oracle/windows-dao/experiments/a4/design-inputs/recompute_a4_work_terms.py`.
  No A4 reachability
  evaluator ships with the plan; the pre-dispatch byte-level harness is an
  explicit blocked execution gate. The A3 calibration
  identities recorded in the plan are manifest
  `f1a644abae1585d8ed0531f45a0544d3264d2449f6d5973ef2ef0bb3d5fefaab`,
  analysis report
  `7587389e4323171aff9b9efcd46bcd5fc8e2ec8273116e8a0360965e4e11faeb`,
  and frozen derivation set
  `ec7c8d27cc46ef9dfdc8214d025cd2d6493ab089f00fc35dbf0ccb9899cdcc0a`.
- Observation: no A4 provider observation exists. The plan preregisters four
  layered hypotheses: H1 locates exactly two row-directory-anchored map rows
  from a lifecycle-selected TDEF; H2 assigns complete type-0/type-1 rows to
  owned/in-use and available roles while permitting row motion; H3 tests zero
  slots, exact tag-05 references, bitmap bytes `[4,2048)`, and
  `slot_ordinal * 16352 + bit_index`; H4 tests one allocation-admitted catalog
  root and a minimal kind/id/name field model. All 40 predicate ids have an
  explicit evaluation rule, order, status/terminal/count behavior, and a
  claimed byte fixture whose reachability must be executed before dispatch.
  Derivation evaluates every non-holdout H1--H4 predicate and freezes all four
  layers before the ordered holdout phase begins. Closed A4 grammars cover
  TDEF lifecycle, an exact masked table-record signature, syntactic locators,
  target validity, complete H2 map rows and transition roles, references/bitmaps,
  system catalog-root selection, and bounded kind/id/name fields. Retained A3
  page 23 recomputes 4,090 raw windows, 3,491,392 raw nonoverlapping pairs, and
  a 3,495,482-unit raw interval/pair charge before one structural pair survives
  under each layout; target validation leaves zero page-then-row pairs and one
  row-then-page pair.
  H4 locates records only from operation deltas and non-name structural fields;
  every structural candidate carries a replica-invariant model and one
  replica-qualified binding per bound replica, each with seven ordered
  per-operation compatible-occurrence bitmaps referencing by SHA-256 a
  separate bounded two-replica-group
  `analysis/h4-occurrence-evidence.json` (at most 1,048,576 bytes) so frozen
  candidates stay within 4,096 bytes, without an encoding result. After one
  structural model survives it compares strict Windows-1252 (`c9`
  for U+00C9) with UTF-8 (`c389`) and reports the observationally equivalent
  CP1252 byte/scalar length hypotheses as one class because no within-CP1252
  identifier can discriminate them. The schedule
  includes one nonunique `A4IX_ID` index solely as an
  object-kind perturbation.
- Interpretation: A4 is a new base experiment, not an A3 revision or
  reinterpretation. `EXP-0051` concrete row motion (page-24 row 0 starts
  1915, 1911, 1895, 1847, then 1843), the pages-1021--1023 A3 boundary
  violation, type-1 prefixes, and tag-05 references are disclosed calibration
  inputs only. A future A4 result must arise from three fresh replicas and
  independent validation. Acquisition remains `BLOCKED` until every execution
  gate and dry-run honesty requirement is implemented and disclosed.
- Usage: physical-provenance plan for future Stage 2 map-location,
  pointer-following, extended-base work and minimum Stage 3 catalog bootstrap;
  no production implementation or capability movement is authorized
- Rights: project-authored plan and schemas; future MDBs and page blobs remain
  uncommitted DAO-generated evidence. No donated MDB, provider binary, or
  third-party MDB implementation is used or redistributed.
- Review: user approved combined H1--H4, one code-page discriminator, one
  nonunique index perturbation, three replicas with freeze/holdout and
  independent validation, and the 2,700-second hard bound. Adversarial review
  pass 1 findings B1--B8, S1--S3, and N1--N2 and pass 2 findings P2-B1--B6
  and P2-S1--S2, plus pass 3 findings P3-B1--P3-B6 and P3-S1--P3-S2 and pass
  4 findings P4-B1--P4-B3 and P4-S1--P4-S2, and pass 5 findings
  P5-B1--P5-B4 and P5-S1, were applied to this unmerged
  base plan in place. Pass 4 also canonically binds the required, not-yet-
  executed reachability transcript and recomputes the 2,036-byte row, 1,850
  occurrence, 18,324 scan, 165,888 inner-grammar, and 306,892,800 tuple terms.
  Pass 5 separates H4 structural and encoding results, makes every terminal
  serialize only its declared payload (candidate set, grouped operation set,
  per-replica pair, or invalid observation), fixes the transcript schema
  positionally for all 40 registry entries with an exact ten-case adversarial
  set, and records an executed reference reachability harness (draft PR #74,
  branch `fable/a4-dryrun`, transcript SHA-256
  `06179c9fdada8cf7a8c3a6ce47919f4f19b4065ebf603b27720250fe9768af21`,
  built by a different agent against the pass-4 plan) as a design input
  only; its 17 recorded ambiguities AMB-01--AMB-17 are each resolved by a
  stated decision in the plan's `harness_ambiguity_resolutions`; pass 8
  supersedes the earlier AMB-02/AMB-16 exception as described below while
  retaining the page-bound decodability rule that reproduces the 1,872-window
  count.
  Pass 6 finding P6-B1
  splits H4 identity into a replica-invariant `canonical_model_id` and a
  binding-qualified `canonical_candidate_id` (replica agreement compares
  model ids), freezes both derivation replicas' physical evidence as two
  replica groups with fourteen bindings on a decisive candidate, recomputes
  every terminal-path maximum over both replicas, and
  records delegate-approved `A4-SCOPE-AMENDMENT-001`
  (`oracle/windows-dao/experiments/a4/design-inputs/a4-scope-amendment-001.md`,
  SHA-256
  `770215c2472d8dee823db6c8fc3af75fc44cfd0769802e7f9f486a25131f3b25`)
  raising `max_analysis_work_units` from the approved brief's 600,000,000 to
  800,000,000. Pass 7 finding P7-B1 restores all seven approved operations as
  required record, structural, and encoding inputs, including explicit
  table/field/index kind mappings, and removes the non-failing contrast design.
  The two-replica work equation is
  `2 * 1,850 * (16*3*3*2*16*3*6*2) = 613,785,600`; the latest H4 terminal is
  694,378,226, below the approved ceiling by 105,621,774 units. P7-S1 is
  recorded additively in
  `oracle/windows-dao/experiments/a4/design-inputs/a4-scope-amendment-001-timing-correction.md`,
  SHA-256
  `49139e945641bf09dfd9969634c8af2e584559707ab89bf02384eef07eab2a8d`.
  GitHub Actions run `32626186825`, fan-in job `97163239067`, records about
  3.84 seconds for derivation freeze, 0.67 seconds for analyzer resume, and
  16.81 seconds for the independent recomputing validator. Using the slower
  independent-recomputation observation gives
  `800,000,000 / 134,291,460 * 16.81 ~= 100.1 seconds`; this is coarse hosted
  planning evidence, not an A4 runtime proof. Normative controls remain the
  checked 800,000,000-unit counter, 900-second fan-in timeout, and 2,700-second
  hard campaign timeout;
  pass 8 finding B1 makes all 40 predicate fixtures claimed-reachable with the
  exact literal `claimed_reachable; execution_required_before_dispatch` and
  no exception. `A4-H1-LOCATOR-PAIR-MULTIPLE` now has fixture
  `A4-R10-H1-PAIR-MULTIPLE`: a closed, mutually exclusive three-hole TDEF
  signature duplicates the second locator at the third offset, producing two
  identity-preserved target-valid pairs under only the row-then-page layout.
  The two pairs reuse the same two decoded targets, so independently
  recomputed candidate bounds, the 1,600 target-validity checks, all resource
  bounds, and the 694,378,226-unit total are unchanged. Before/after schema
  hashes for this pre-acquisition P0 repair are:

  | Schema | SHA-256 before | SHA-256 after |
  | --- | --- | --- |
  | `analysis-report.schema.json` | `3a3903a49a05cedad0b6685ccf194ef598ba4c359aad18adcd1ba2113abcc0db` | `132239732f50872ae3e579b4857a498f2df1aff09ecffc389e08d1756c988104` |
  | `derivation-candidates.schema.json` | `c6dc3b11a73a6ba4dedb582e01b405aeb8d84cd801b261f41ed6884792a5c1e6` | `f0dec323bb1b1647b0bf093a9692b0a338859ed3b219567b3b4f0751294d69f7` |
  | `reachability-transcript.schema.json` | `2faec2aed55be8b9274631e15bbf3b58e72036c3ff3896f807a992f3a3c96c4f` | `beaa8179a9c0e5a3d26c1098494f6d0bf32c20ea87d162de65e0637aa3f95bb5` |

  Pass 9 findings P9-B1 and P9-B2 are applied additively to the same unmerged,
  pre-acquisition P0 base. H1 target-layout candidates now admit exactly the
  standard and duplicate-locator signatures. H1 locator-pair candidates are a
  closed signature-discriminated union: the standard signature admits only
  `[35,39]`, while the duplicate signature admits `[35,39]`, `[35,43]`, or
  `[39,43]`; semantic evaluation still rejects duplicate decoded targets and
  any layout whose decoded page/tag/row targets fail against the bound page
  bytes. The duplicate grammar now records exact structured mask derivation,
  `[39,43) == [43,47)` equality, and inequality against the base signature's
  fixed `[43,47)` value. The work script validates every four-byte hole,
  derives equality classes rather than trusting their recorded output, proves
  the standard/duplicate intersection empty, and rejects altered equality,
  partition, hole, inequality, or base mask/value before summing. The Pass-8
  plan hash
  `0a9ba13efe2c26cdde1f207189832af0869d7a52c23cba569a898a7454fbd597`
  is superseded by the current plan hash above. Pass-9 changed-file identities
  are:

  | File | SHA-256 before Pass 9 | SHA-256 after Pass 9 |
  | --- | --- | --- |
  | `analysis-report.schema.json` | `132239732f50872ae3e579b4857a498f2df1aff09ecffc389e08d1756c988104` | `d320894cfd9b9cb9ddd7ad0d05dcd84333003a83fb352a0d1001715045a495f0` |
  | `derivation-candidates.schema.json` | `f0dec323bb1b1647b0bf093a9692b0a338859ed3b219567b3b4f0751294d69f7` | `2276299d1aea1fe5796684d3236bf5889c806ebaf6e06c146f482a38561ae245` |
  | `a4-row-anchored-maps.plan.json` | `0a9ba13efe2c26cdde1f207189832af0869d7a52c23cba569a898a7454fbd597` | `a934586299edfcd53ac2f7d7fa0428c9b389dfb47bce98f28c9ca445a65fd314` |
  | `README.md` | `2af4f607607d1b26c16b68395c7ed7a9c5ebf375e6e6760d9ef5876932bdccf9` | `e15bb743ce4f3f550691210efb47b732d40028e67fe1dc76a69df8f25338735e` |
  | `design-inputs/recompute_a4_work_terms.py` | `c1772790010981f80f9eb44e280af970129d27f3a0d7863f7cafa5ae7f9ac6b9` | `d120aae59b2f8fd5aa46a2b0f09f5049cdb84f80199c7ff34febec2480aa4f36` |
  | `tests/test_a4_plan_contract.py` | `7488b5b615a11ba8a96b16fd3a0dcccebbf2d4f08905d45d67e00e6d35bbea05` | `8210eefc9411433f19716a212187d62c6bf2bf367089fd710aacbff28957f058` |

  Focused complete-report falsification accepts both registered signatures and
  rejects overlapping `[35,37]`, standard-plus-`[35,43]`, duplicate-plus-
  `[100,104]`, duplicate candidates, duplicate target tuples, and the
  page-then-row interpretation of the exact R10 bytes. CLI/module mutation
  cases cover registered units, the H4 dimension, removed/cross-hole/extra
  equality, conflicting derived grouping, a fourth hole, missing/changed
  mutual exclusion, and base mask/value. The derived target-validity term
  remains 1,600 and the complete work total remains 694,378,226; acquisition,
  capability movement, and final P0 closure remain blocked.

  Pass 10 independently reviewed exact head
  `3a738787d36e08d68b8134fd8965eddc29d1d198` and found two blockers. P10-B1
  showed that the focused byte-semantic checker compared decoded physical
  target tuples across all lifecycle and replica bindings, contradicting the
  registered invariant-model/rotating-binding split. P10-B2 showed that the
  work model identified qualified pages only by numeric page and checkpoint
  across two independently created MDBs, so H1--H3 and catalog-row work omitted
  the second replica's physical inspections. This pre-acquisition repair binds
  H1 page evidence by `(replica, checkpoint id, page number)`, validates every
  applicable checkpoint of every binding independently without requiring
  physical target equality, and charges the second derivation replica for each
  affected term. The invalid-directory alternative rises from 407,600 to
  815,200 units; the latest H4 path rises by 80,551,040 to 774,929,266 and
  remains 25,070,734 below the approved 800,000,000 ceiling.
  The repaired recomputation helper has SHA-256
  `49079d3bfb413bdcc98288adf3c2e7ae736577e0ef5002d8e00d816aba25a7ec`;
  the focused contract test has SHA-256
  `fbd712fd9bec62b1663336d7e3365cdb586ca333a277f121829539f6fc2ce7d2`.
  No approved scope,
  acquisition state, DAO evidence, support state, or compatibility claim is
  changed; final P0 closure remains blocked pending a fresh post-repair review.

  Pass 11 independently reviewed exact head
  `a83016b4a031f52cd987e734e792f325cf28fa93` and found two additional
  pre-acquisition blockers. P11-B1 showed that the frozen schema retained
  single-replica maxima for ten now-aggregate charges, omitted the 815,200-unit
  invalid-directory alternative, and represented qualified pages only as
  unqualified numeric page numbers; the analysis-report schema also could not
  reproduce the frozen charges. P11-B2 showed that AMB-02 and AMB-03 still
  prescribed the superseded 1,600-check and cross-replica-deduplicated rules.
  This repair keeps `max_locator_pairs` and
  `max_qualified_pages_per_submodel` as per-replica bounds, while both evidence
  schemas now admit the exact aggregate named-term maxima and the mutually
  exclusive invalid-directory charge. Frozen and report qualified-page
  inventories use canonical `(replica, checkpoint_id, page_number)` objects,
  bounded by 3,200 identities across two replicas, 25 checkpoints, four
  layers, and 16 pages. The analysis report must reproduce those identities
  and the complete work charges byte-for-byte. Changed schema identities are:

  | File | SHA-256 before Pass 11 | SHA-256 after Pass 11 |
  | --- | --- | --- |
  | `analysis-report.schema.json` | `d320894cfd9b9cb9ddd7ad0d05dcd84333003a83fb352a0d1001715045a495f0` | `bd1cdd62fdf6dae1ed756c092a90936be1318dc3a66e9d1d6309ecfa0d3d2010` |
  | `derivation-candidates.schema.json` | `2276299d1aea1fe5796684d3236bf5889c806ebaf6e06c146f482a38561ae245` | `1cf5829b14663a68c934ff4d16b1b95668291b21b645ad3ae0e93abe3c839a28` |

  The Pass-11 focused contract-test SHA-256 is
  `cdc341b4c6ef0e3e4b08e908e4f357096da9f635b42ff0110b12e9fbd761a91e`.
  Acquisition, support movement, and compatibility claims remain blocked
  pending another exact-head independent review.

  focused A4
  plan and repository contract checks must pass

### EXP-0053 — A4 deterministic pre-acquisition dry-run disclosure

- Recorded: 2026-08-25, OpenAI Codex
- Kind: additive, non-evidential pre-acquisition execution disclosure; no DAO
  acquisition, holdout result, format observation, or capability advancement
- Question: Does the implementation bound to the immutable A4 plan make all
  40 registered terminal predicates reachable, preserve the plan's ordered
  first-failure semantics and resource limits, and agree exactly with a
  separately invoked independent validator before any acquisition is allowed?
- Preregistration: immutable plan and revision SHA-256
  `3e74e67a213611596aaa0f5a4c3e433b2528a438bfa74708f4937e0233ed9aa1`.
  The analyzer, fixture generator, harness, and independent validator were
  executed from exact commit
  `668692bc4eba63b18fb841db4d0a05530db9c335`; no preregistered plan or prior
  provenance entry was rewritten.
- Calibration input: retained read-only A3 `EXP-0051` bundle at
  `/tmp/jet3-a3-retained-32626186825/jet3-a3-bundle`, manifest SHA-256
  `f1a644abae1585d8ed0531f45a0544d3264d2449f6d5973ef2ef0bb3d5fefaab`.
  The dry run opened 22 distinct retained calibration page blobs from replica 1 and
  never opened replica 3. It independently recomputed 1,872 syntactically
  preserved windows and 1,745,696 canonical nonoverlapping pairs for each
  locator layout, with 7 page-then-row and 25 row-then-page target-valid
  checkpoints. `holdout_opened` is false.
- Protocol: execute
  `python3 oracle/windows-dao/scripts/a4_dryrun.py generate
  --retained-root /tmp/jet3-a3-retained-32626186825/jet3-a3-bundle --output
  /tmp/jet3-a4-artifacts-final.Tpv5iv/dry-run`, place the four resulting files
  under `oracle/windows-dao/experiments/a4/dry-run`, then execute the
  corresponding `verify` command against the retained root and tracked artifact
  directory.
  The generator serialized complete page trees for every fixture, ran the
  production analyzer in a child process, and ran an independently implemented
  validator in a separate child process. The verifier recomputed each result
  and required exact agreement on terminal predicate, terminal status,
  candidate count, candidate-set SHA-256, and registered-prefix evaluation.
  Serialized inputs, tree inventory, child output, and generator parameters
  were checked against the preregistered bounds before use.
- Process separation: the analyzer script SHA-256 was
  `12edbb97e7fab2aa0ab658f31bffc74ca0a044b215606d3a6e1018619444767a`
  and its successful marker log was 30 bytes with SHA-256
  `7c89671e61381898b9451372d83c264ffdd6272627306c8b9a032bfc9f17c482`.
  The independent-validator script SHA-256 was
  `20ff1f18cddd345db9952d6f2dfaae3b2bf814e5bef5b56530970ac8ce6610f6`
  and its successful marker log was 43 bytes with SHA-256
  `37bcf1654adcd1ab5cc5ae76af6afa73765e486c34643be820080838d6f09ebf`.
  The driver required distinct script paths, script hashes, process markers,
  and log hashes for every fixture before recording agreement.
- Observation: the retained-A3 calibration replay passed separately. One
  A4-schedule-synthetic sweep reached all 40 registered terminal fixtures, and
  its single transcript was bound into both disclosure reports. All ten
  required adversarial outcomes were derived from the two child results and
  agreed:
  multiplicities 2, 3, and 4, encoding counts 0 and 2, and exact work-counter
  equality were accepted; an unregistered candidate id, malformed page,
  invalidated earlier predicate, and one-over resource count were rejected.
  The analyzer and independent validator both report commit
  `668692bc4eba63b18fb841db4d0a05530db9c335`; generation and fresh
  byte-for-byte verification each report `PASS (40/40)`.
- Artifacts: `a3-calibration-report.json`, 3,889 bytes, SHA-256
  `d4478ca40b40bc60bc090aa30f06e07220ef6edac52678229a06cb5ae775624e`;
  `a4-reachability-transcript.json`, 125,211 bytes, SHA-256
  `36af100d8c457de48509b8610ad070411eb9787c8884b1f1812bb281077f499c`;
  `a4-synthetic-report.json`, 3,960 bytes, SHA-256
  `4800ef229e935e22923249690d065f95ca64bf16f9b47005c052ab3248279d9c`;
  and `checksums.sha256`, 282 bytes, SHA-256
  `e7509245432a94d7d964e316d39aebebb109039d6aa8e3562625b4c6a96b8b9d`,
  all under `oracle/windows-dao/experiments/a4/dry-run/`.
- Interpretation: this closes only the deterministic A4 P1 pre-acquisition
  reachability and analyzer/validator-agreement obligation. Both reports set
  `scientific_evidence`, `acquisition_authorized`, and
  `capability_advancement_authorized` to false. The synthetic report contains
  no page blobs and no fixture verdict fields. It is not DAO evidence and
  cannot support a compatibility claim or support-matrix movement.
- Review: the complete A4-focused suite passed 229 tests with one
  retained-bundle-path-dependent test skipped, including the generator, analyzer,
  dry-run driver, independent campaign checks, independent H3/H4 checks, and
  independent validator. Python bytecode compilation, whitespace validation,
  artifact checksum validation, deterministic generation, and fresh independent
  verification passed.

### EXP-0054 — First hosted A4 dispatch worker-plan-chain blocker

- Recorded: 2026-08-26, OpenAI Codex
- Kind: additive exact-commit hosted failure record and conservative
  `IMPLEMENTATION_PLAN.md` Section 6.4 scientific stop; not an A4 acquisition
  result, H-layer result, or `no_outcome`
- Question: Why did the first authorized hosted A4 dispatch stop in all three
  replica workers, and did it retain any preregistered A4 scientific artifact?
- Origin: project-authored `DAO-A4-ROW-ANCHORED-MAPS-001` workflow, controller,
  worker, immutable plan, and failure diagnostics only. No MDB or provider bytes
  were retained or inspected.
- Environment: GitHub Actions run `32917739205`, attempt 1, dispatched on
  `main` at 2026-08-26T01:06:53Z from exact clean pushed commit
  `905f6342fa4d6a46cdf533bbc226f753d0bb669a`. The checked contract job
  `98024880803` passed. The three `windows-2022` replica jobs were
  `98025089916`, `98025089885`, and `98025089888`. Fresh hosted provider proof
  run `32439805418`, attempt 1, had passed on 2026-08-21; each replica's
  preflight again selected x86 `DAO.DBEngine.36`, `dao360.dll` version
  `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`,
  with Windows ANSI code page 1252.
- Protocol: dispatch `.github/workflows/windows-dao-a4.yml` on `main` with
  `execute_a4_campaign=true`; require the exact pushed checkout and checked A4
  contract; launch the three bounded replicas; stop on the first checked worker
  error; upload only bounded diagnostics on failure; do not redispatch. The
  contract completed successfully before the three replica workers began.
- Retained artifacts: no replica tree or complete A4 bundle exists, and fan-in
  job `98025380301` was skipped. GitHub retained only diagnostics artifacts
  `windows-dao-a4-diagnostics-1-32917739205-1` (id `9588800826`),
  `windows-dao-a4-diagnostics-2-32917739205-1` (id `9588801059`), and
  `windows-dao-a4-diagnostics-3-32917739205-1` (id `9588801439`), each 6,723
  transport bytes and expiring 2026-09-09. Their extracted read-only tree
  inventory SHA-256 values were respectively
  `74dc902376657da9c1697bbb869e482afd7ad51d0a395554361588f509cfa058`,
  `5c38490619494f41653256a77877b3cfd922b17a75ff8a691b6db5d9f445b585`,
  and `ecba54d3737dacf8e86021b3e40459cf15a820b8f74d814d52ca79e9f6cf7ef3`.
  All three `failure.json` files were 561 bytes with identical SHA-256
  `ebdec1af1a69496308da0007c7816af59a28bab84428dffe5eeb89e9e42bbbe8`;
  all three stderr logs were 538 bytes with identical SHA-256
  `494fe5610bffce7b87f76d6a0835f087001f626b59a3fc3dae7b7532ed6f173b`.
- Observation: each worker stopped after approximately 22--24 seconds in
  `Assert-A4PlanChain` because strict-mode property access required
  `preregistration.status`, which does not exist in the immutable A4 plan. The
  failure occurred before environment binding, A4 provider activation, and
  `Invoke-A4Schedule`. Each progress log was zero bytes, no schema-valid replica
  observation or DAO schema snapshot was retained, and the plan's explicit A4
  acquisition-start criterion was not met.
- Boundary classification: the controller's preceding provider preflight did
  create and close a disposable `dbVersion30` MDB. Although no A4 acquisition
  artifact survived, that DAO mutation makes the Section 6.4 first-mutation
  boundary conservative or uncertain. This attempt is therefore recorded once
  as a scientific stop for escalation purposes, not reclassified as an H-layer
  scientific result, infrastructure `no_outcome`, or contrary format
  observation.
- Human decision: on 2026-08-26 the user directed this separate additive record,
  authorized treating the worker plan-chain correction as non-scientific
  execution-glue repair under the unchanged A4 experiment, and authorized
  continued P2 work including a replacement dispatch without another approval
  unless a genuine blocker or major plan deviation occurs. The repair may change
  only pre-acquisition binding code and focused tests; it may not change the
  immutable plan, evidence schemas, scientific worker schedule, analyzer,
  validator, predicates, bounds, or holdout controls.
- Interpretation and claims: this failed attempt assigns no MDB byte meaning,
  establishes no Rust correctness, DAO compatibility, product support, or
  capability movement, and supplies no H1--H4 value. No report claims block
  exists; every compatibility or format claim remains false.
- Usage: exact failure input for the authorized focused A4 execution-glue repair
  and any later result entry's prior-attempt disclosure; no production Rust or
  MDB format usage
- Rights: project-authored diagnostics and descriptive licensed-provider
  metadata only; no MDB or provider binary is committed or redistributed
- Review: pending exact-head independent review before repair or redispatch

### EXP-0055 — Replacement hosted A4 post-mutation schema-snapshot stop

- Recorded: 2026-08-26, OpenAI Codex
- Kind: additive exact-commit hosted scientific-event record under
  `IMPLEMENTATION_PLAN.md` Section 6.4; not a complete A4 acquisition result,
  H-layer result, infrastructure failure, or `no_outcome`
- Question: Why did the authorized replacement A4 dispatch stop in all three
  replica workers, and had the preregistered campaign crossed its first DAO
  mutation boundary?
- Origin: project-authored `DAO-A4-ROW-ANCHORED-MAPS-001` workflow, controller,
  worker, immutable plan, and bounded failure diagnostics only. The diagnostics
  contain three project-generated failed MDBs; they were retained read-only and
  identity-hashed but their contents were not semantically inspected. No donated
  MDB, provider binary, or third-party MDB implementation was used or inspected.
- Environment: GitHub Actions run `32929243031`, attempt 1, dispatched on
  `main` at 2026-08-26T04:11:01Z from exact clean pushed commit
  `e6e209612f0b3a98f84d8750e422efd353f9a03b`. The checked contract job
  `98058137895` passed. The three `windows-2022` replica jobs were
  `98058316327`, `98058316345`, and `98058316377`. Fresh hosted provider proof
  run `32439805418`, attempt 1, had passed on 2026-08-21. Each replica again
  recorded Windows Server 2022 build 20348, x86 process architecture, and ready
  `DAO.DBEngine.36` 3.6 from `dao360.dll` version `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`,
  with `dbVersion30` creation capability.
- Protocol: after the user-authorized execution-glue repair merged as PR 83,
  reconfirm an exact clean pushed `main`, no open pull request, merged A4
  disclosure, and provider proof younger than seven days; dispatch
  `.github/workflows/windows-dao-a4.yml` once with
  `execute_a4_campaign=true`; require the checked contract and exact producer
  binding; launch three bounded independent replicas; on any failure retain
  bounded diagnostics, skip fan-in, do not redispatch, and classify the event
  using Section 6.4.
- Retained artifacts: no retained replica tree or complete A4 bundle exists,
  and fan-in job `98058602728` was skipped. GitHub retained diagnostics artifacts
  `windows-dao-a4-diagnostics-1-32929243031-1` (id `9592651485`, 47,981
  transport bytes), `windows-dao-a4-diagnostics-2-32929243031-1` (id
  `9592650889`, 47,985 transport bytes), and
  `windows-dao-a4-diagnostics-3-32929243031-1` (id `9592653671`, 47,985
  transport bytes), all expiring 2026-09-09. Canonical relative extracted-tree
  inventory SHA-256 values were respectively
  `3ffffb850ce7f04555bc613d8145ea5510215f85d9f63128d1c4255efbce1d6b`,
  `ec9f84b8cb384dc704c7b9e26e375741d65ac8312ee1fe576df5eed3e7b79443`,
  and `7139ee52a25fd710bcf65fbfa3b6fd7dda80a85d52f604d068a1fd486fd9dc8e`.
  All three `failure.json` files were 645 bytes with identical SHA-256
  `9b1408a483ba409bcca840f56df6394a128c66544168ce0e7410450b45866f38`;
  all three stderr logs were 622 bytes with identical SHA-256
  `f46c3545c03ad40d38048de4f5205d61fedfe09d76247ec88adf01bb2791dd30`.
  Each stdout and progress log was empty with SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
  The three failed MDBs were each 40,960 bytes, with respective SHA-256 values
  `d6ac51d58e7fe45a3cb40c6cc59d4071333ee595d486920498dbc7bd6d52398c`,
  `af4b2aebe79419da8eac70825c833fe4ad4f601bc701faffaf52ef95e4291a63`,
  and `ebcc1cbef8cfd2749a1ac73ee170406fa04f66eba74b4d4ef0f73de7891aa687`.
- Observation: all three replicas created and closed their fresh Jet 3 campaign
  database, then failed identically at the first `EMPTY` checkpoint. The worker
  called `Read-A4SchemaSnapshot`; the plan-derived expected descriptor list was
  empty, and PowerShell rejected that empty array when binding the mandatory
  `Descriptors` parameter of `Get-A4ScheduledTableNames`. The normalized error
  was `ParameterArgumentValidationErrorEmptyArrayNotAllowed` at
  `A4.Worker.ps1:436`. Hosted elapsed times were 23.000 seconds for replica 1,
  23.418 seconds for replica 2, and 25.689 seconds for replica 3. No schema
  snapshot, checkpoint progress event, analyzed report, or H1--H4 value was
  retained.
- Boundary classification: `Invoke-A4Schedule` calls DAO `CreateDatabase`
  before entering the checkpoint loop, and each diagnostics artifact contains a
  distinct failed campaign MDB. The failure therefore occurred after the first
  DAO mutation and is unambiguously a scientific event under Section 6.4 item
  4. It is recorded once. No further dispatch, worker change, or scientific
  input change is authorized without a new human decision.
- Interpretation and claims: this event is neither an H1--H4 finding nor a
  valid analyzed `no_outcome`. It assigns no MDB byte meaning, establishes no
  Rust correctness, DAO compatibility, product support, or capability movement,
  and does not unlock P3 or P4. No report claims block exists; every
  compatibility or format claim remains false.
- Usage: exact scientific-stop input for the required human decision and any
  later additive revision or prior-attempt disclosure; no production Rust or
  MDB format usage
- Rights: project-generated failed MDBs and project-authored diagnostics were
  retained as access-controlled GitHub artifacts and in a local read-only
  scratch tree; no MDB or provider binary is committed or redistributed
- Review: pending exact-head review; further A4 acquisition is stopped pending
  human direction

### EXP-0056 — Local DAO opening discriminator matrix

- Recorded: 2026-08-26, OpenAI Codex
- Kind: repeatable local exploratory observation with
  `development_only = true`; diagnostic format discovery, not an official
  evidence campaign, release result, or compatibility result
- Question: Which minimal page-zero states distinguish DAO-created Jet 3 from
  Jet 4 controls and the supported Jet 3 unencrypted, no-password state from
  encrypted or passworded controls?
- Origin: the project-authored local Windows development runner using the DAO
  creation, open, and password APIs documented in `SRC-0014`, `SRC-0015`, and
  `SRC-0022`; no third-party MDB implementation was inspected
- Environment: local Windows Server 2022 build 20348 VM; x86 Windows PowerShell
  5.1.20348.558; `DAO.DBEngine.36` 3.6 from `dao360.dll` version
  `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  host and guest time zone `America/New_York`
- Protocol: run three independent `opening-matrix` jobs. In each run, create,
  close, and reopen eight controls spanning DAO versions 3.0 and 4.0, encrypted
  and unencrypted creation, and passworded and no-password states. Require DAO
  reopen success and the expected `Database.Version`, then compare only the
  bounded page-zero prefix `[0, 0x600)` across all 24 private outputs.
- Artifacts: development run ids `20260826T143505Z-dev-dao`,
  `20260826T143554Z-dev-dao`, and `20260826T143604Z-dev-dao`; respective
  `result.json` SHA-256 values
  `0f48cd6d509b7729047c4049ebec3cae0941ba9d7dd65c328b062c2c48f96b17`,
  `4cde98eb6e87af4749a41995bc49ed398bc6d91b35c79d5a484a2e7741996c96`,
  and `b7972ab6cf9791391364f5003c2f3bfa006ea12b31227a1e8d575e1f2a331690`.
  Raw MDBs and complete outputs remain outside the repository.
- Observation: DAO reported version `3.0` and 40,960 bytes for every Jet 3
  control and version `4.0` and 65,536 bytes for every Jet 4 control. Across
  every run, offset `0x014` was `00` for Jet 3 and `01` for Jet 4; offset
  `0x041` was `4e` for unencrypted controls and `ee` for encrypted controls.
  Every Jet 3 no-password control, encrypted or unencrypted, had bytes
  `86 fb ec 37 5d 44 9c fa c6 5e 28 e6 13 b6` at `[0x042, 0x050)`;
  passworded Jet 3 controls differed there. Other encrypted bytes varied across
  runs. The Rust CLI admitted every unencrypted, no-password Jet 3 control and
  rejected every Jet 4, encrypted, or passworded control by the corresponding
  discriminator.
- Interpretation: the v1 opening boundary may require the exact Jet 3 marker
  at `0x014`, unencrypted marker at `0x041`, and observed Jet 3 no-password
  state at `[0x042, 0x050)`, failing closed for every other value. This
  establishes only an internal opening discriminator. It does not validate the
  rest of page zero or the database structure, advance the support matrix to
  DAO-verified, or establish compatibility. `EXP-0018` remains unchanged: its
  official campaign was inconclusive and assigned no physical meaning.
- Usage: `file:crates/jet3/src/database_header.rs`;
  `file:crates/jet3/src/database.rs`
- Rights: project-generated licensed-provider outputs are private local
  development material and are neither committed nor redistributed
- Review: pending independent review

### EXP-0057 — Local DAO allocation-map traversal observations

- Recorded: 2026-08-26, OpenAI Codex
- Kind: repeatable local exploratory observation with
  `development_only = true`; diagnostic format discovery, not an official
  evidence campaign, release result, or compatibility result
- Question: In controlled Jet 3 table creation, row growth, deletion, and
  reinsertion, where are table allocation-map rows located, how do type-1 raw
  references select type-`05` pages, and how does a bitmap slot determine its
  absolute page base?
- Origin: the project-authored local Windows development runner using the DAO
  creation, field, row, and long-value APIs documented in `SRC-0001`,
  `SRC-0009`, `SRC-0010`, and `SRC-0012`; bounded page, row-directory, and
  usage-map interpretations were restricted to the grammar in `SRC-0020`; no
  third-party MDB implementation was inspected
- Environment: the same private Windows Server 2022/x86 Windows PowerShell
  development VM as `EXP-0056`; all three runs accepted `DAO.DBEngine.36`
  version 3.6 from `dao360.dll` version `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`
- Protocol: run three independent `allocation-map` jobs. Each created one
  fresh Jet 3 database and one table with an integer key and deterministic
  1,800-byte long-binary payload, captured the closed empty, table-created,
  and two-row states, then added rows in fixed 256-row batches. The loop made
  no threshold or locator assumption: it captured the state immediately
  before and after the first new type-`05` page, then continued until one
  structurally delimited type-1 row contained at least two nonzero references
  that each named an extant type-`05` page, failing closed after 32,768 rows.
  It finally deleted and reinserted the last 256 rows and captured both closed
  states.
- Artifacts: development run ids `20260826T210000Z-allocation3`,
  `20260826T211000Z-allocation4`, and `20260826T212000Z-allocation5`;
  respective `result.json` SHA-256 values
  `3d7defcb2d5d0b3cbf5976a15ec04f0ea2fd93e2b262f653feb1471a725e42e8`,
  `a4f52aa27c81da77503ef196b977b0fb53f2a6c0c5b81c975a15dd0940a3478a`,
  and `e16eac23bc1d95b8bbe7c4b28982a5ac524a2cb96391abfea9fe444ff99de6e3`.
  Raw MDBs and complete outputs remain outside the repository.
- Observation: every run added exactly one new tag-`02` page at physical page
  20 when the table was created. Exhaustive four-byte-window decoding under
  both candidate locator orders found the unique adjacent, non-overlapping
  table-map pair at page offsets 35 and 39: each value is one row byte followed
  by a three-byte little-endian page number. The values were respectively row
  0/page 21 and row 1/page 21 in all checkpoints. The row at the first locator
  tracked every table-owned data page; the second was a subset tracking the
  currently available data page. The locators and roles were stable across
  growth, deletion, and reinsertion.
- Observation: type-1 rows consisted of their tag followed by complete
  four-byte little-endian slots. Every nonzero slot value directly equaled the
  physical page number of an extant type-`05` page; zero slots followed the
  active prefix and selected no page. In every run the table's long-value
  owned-page row reached the identical two-reference state `[7041, 16354]`.
- Observation: a complete type-`05` bitmap has 16,352 bits as recorded by
  `SRC-0020`. For the two-reference table row, slot 0 bit indices through
  16,351 described physical pages with the same numbers, while slot 1 bit
  indices described physical pages `16,352 + bit_index`. Deleting 256 rows
  cleared slot-0 bits 16,340 through 16,351 and 244 slot-1 bits between 1 and
  249; the represented pages were the corresponding table-owned long-value
  pages, and reinsertion set the newly allocated positions again. A
  reference-relative base would map the slot-0 changes beyond the captured
  16,606-page input and is refuted. Thus the absolute page calculation is
  checked `slot_ordinal * 16_352 + bit_index`, independent of the physical
  page storing that bitmap.
- Interpretation: the bounded reader may decode the two table-map locators at
  offsets 35 and 39, use the first as the owned-page map, treat zero type-1
  slots as unused, follow each nonzero raw value as a direct checked physical
  page reference requiring tag `05`, and derive extended absolute pages with
  checked `slot_ordinal * 16_352 + bit_index`. It must still reject malformed
  row directories, flags, out-of-capture references, self-references,
  repeated references, arithmetic overflow, and resource exhaustion. These
  observations establish only internal traversal behavior for this project;
  they do not decode the catalog, provide release-eligible DAO differential
  evidence, or establish compatibility.
- Usage: `file:crates/jet3/src/map_location.rs`;
  `file:crates/jet3/src/usage_map.rs`;
  `file:crates/jet3/src/allocation_traverse.rs`;
  `file:crates/jet3/src/database.rs`;
  `file:fuzz/fuzz_targets/table_definition_parsing.rs`;
  `file:fuzz/fuzz_targets/usage_map_traverse.rs`
- Rights: project-generated licensed-provider outputs are private local
  development material and are neither committed nor redistributed
- Review: pending independent review

### EXP-0058 — Local DAO catalog bootstrap observations

- Recorded: 2026-08-26, OpenAI Codex
- Kind: repeatable local exploratory observation with
  `development_only = true`; diagnostic format discovery, not an official
  evidence campaign, release result, or compatibility result
- Question: Can a Jet 3 catalog root be found without an absolute page
  assumption, and which minimum row fields identify table objects, system
  classification, raw names, and table-definition roots?
- Origin: the project-authored, explicitly allowlisted local Windows
  development runner using DAO database, table-definition, field, append, and
  delete APIs documented in `SRC-0001`, `SRC-0009`, `SRC-0010`, `SRC-0012`,
  and the database locale from `SRC-0014`; page classification, row-directory
  bounds, and allocation traversal were restricted to `SRC-0020` and
  `EXP-0057`; no third-party MDB implementation was inspected
- Environment: the same private Windows Server 2022/x86 Windows PowerShell
  development VM as `EXP-0056`; every run accepted `DAO.DBEngine.36` version
  3.6 from `dao360.dll` version `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  each database was created with
  `;LANGID=0x0409;CP=1252;COUNTRY=0`
- Protocol: run three independent `catalog` jobs. Each job created one fresh
  Jet 3 database and captured seven checkpoints: empty; create, drop, and
  recreate `CatalogAscii`; then create, drop, and recreate
  `Café_Euro€`. The non-ASCII name was constructed from Unicode code points,
  not decoded from the PowerShell source file. Each checkpoint reopened DAO
  only to capture at most 128 table names and attributes, closed every DAO and
  COM object, forced finalization, and only then copied the MDB. The physical
  analysis enumerated every tag-`02` page rather than assuming a catalog page,
  traversed each candidate's owned map, admitted only tag-`01` pages, and used
  complete `SRC-0020` row-directory bounds. It required the same observations
  in all three runs before interpretation.
- Artifacts: development run ids `20260827T001825Z-catalog1`,
  `20260827T001844Z-catalog2`, and `20260827T001855Z-catalog3`; respective
  `result.json` SHA-256 values
  `0d8458f40920c32eef1b4552bb68c813a993579ef2e8b3afacdb8eee309bb3ac`,
  `dacb635ecec6c3a8ef81b90a786c9fc542cbcb6b9d67047e7321d8a5c68bc716`,
  and `98694b637fc6c1cba135236abaa9854744f41dc5e7d6a3dea6e4d08dfd831c4a`.
  The job-specific result was byte-identical in all three runs, SHA-256
  `b711efd74ff44cdf20cf6cc5b19a4bba8fb1e9cd0babc0860c0dc3355391d092`.
  Raw MDBs and complete outputs remain outside the repository.
- Observation: the empty databases had 20 pages and tag-`02` candidates 2, 3,
  4, and 5. At every checkpoint, traversing candidate 2's owned map admitted
  tag-`01` page 18, which contained exactly one active table record whose raw
  name was `MSysObjects`, whose identifier was 2, whose raw kind was 1, and
  whose flags were `0x80000000`. No other tag-`02` candidate had that
  self-identifying record. Candidate 3's owned page also changed during table
  operations, so change alone is not a unique catalog-root discriminator.
- Observation: every active catalog record began with column count 17. The
  little-endian identifier occupied row bytes `[1,5)`, the little-endian raw
  kind occupied `[9,11)`, and the little-endian object flags occupied
  `[27,31)`. In each row, the fifth byte from the end was name-start offset 31,
  the sixth byte from the end was the exclusive name-end offset, the fourth
  byte from the end was fixed boundary 11, and the third byte from the end was
  `0xff`; the name range was nonempty and ended before this six-byte trailer.
  Table records had raw kind 1. DAO's four system table definitions correlated
  with object flags `0x80000000`; both user tables correlated with flags 0.
  The identifier of every observed table record was also its in-range,
  tag-`02` table-definition page: 2 through 5 for the system tables, 20 for
  `CatalogAscii`, and 23 for `Café_Euro€`. Drop/recreate reused the same
  physical table-definition identifier in this no-compaction scenario, so the
  identifier is not interpreted as a lifetime-unique generation.
- Observation: the ASCII name was stored as its 12 ASCII bytes. Under the
  recorded CP1252 database locale, `Café_Euro€` was stored exactly as
  `43 61 66 e9 5f 45 75 72 6f 80`, not either UTF-8 byte sequence. Dropping a
  table changed its directory entry to flags `0xc000`; after a later append,
  the masked dropped-row start equaled the next active row's end and delimited
  an empty tombstone. Active catalog entries used no directory flags.
- Interpretation: catalog discovery may scan the captured page range under the
  operation budget, consider only correctly classified tag-`02` candidates,
  traverse candidate-owned pages using `EXP-0057`, require those pages to be
  tag `01`, and select the unique candidate containing the self-identifying
  active `MSysObjects` system-table record above. It must reject zero or
  multiple roots. On the selected owned pages it may decode the recorded
  minimum fields, preserve unknown kind values, require exact system/user flag
  values, retain raw name bytes with database-code-page context, and expose the
  identifier as a checked table-definition reference only for raw table kind
  1. Deleted `0xc000` tombstones are skipped; unknown directory flags, active
  overflow entries, malformed bounds/trailers, duplicate active identifiers,
  and invalid references fail closed. This does not establish a general row
  grammar, decode arbitrary text, traverse index trees, validate DAO
  compatibility, or revise any hosted-campaign result.
- Usage: `file:oracle/windows-dao/scripts/dev/Catalog.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1`;
  `file:scripts/windows-dao-dev.py`; `file:crates/jet3/src/catalog.rs`;
  `file:crates/jet3/src/catalog_record.rs`;
  `file:fuzz/fuzz_targets/catalog_parsing.rs`
- Rights: project-generated licensed-provider outputs are private local
  development material and are neither committed nor redistributed
- Review: pending independent review

### EXP-0059 — Local DAO table-definition observations

- Recorded: 2026-08-27, OpenAI Codex
- Kind: repeatable local exploratory observation with
  `development_only = true`; diagnostic format discovery, not an official
  evidence campaign, release result, or compatibility result
- Question: How does a catalog table reference lead to bounded Jet 3 column,
  physical-index, logical-index, and minimum relationship metadata without
  traversing any index tree?
- Origin: the project-authored, explicitly allowlisted local Windows
  development runner using only the DAO inputs documented in `SRC-0023`, the
  catalog reference established by `EXP-0058`, and page/allocation primitives
  from `SRC-0020` and `EXP-0057`; no third-party MDB implementation was
  inspected
- Environment: the same private Windows Server 2022/x86 Windows PowerShell
  development VM as `EXP-0056`; every run accepted `DAO.DBEngine.36` version
  3.6 from `dao360.dll` version `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  each database was created with
  `;LANGID=0x0409;CP=1252;COUNTRY=0`
- Protocol: run three independent `table-definition` jobs. Each tested all 31
  checked DataTypeEnum candidates in separate fresh databases, then captured
  a main database at empty, accepted-type, column-property, three incremental
  index, and combined-cascade parent/child relationship checkpoints. A
  separate fresh database held a 64-column long-name boundary table so its
  multi-page definition could not consume the main scenario's DAO resources.
  Every snapshot was bounded, DAO was closed and COM objects finalized before
  the MDB was copied, and no compaction or index traversal occurred. Physical
  analysis used catalog identifiers rather than absolute table-page
  assumptions and required the observations below in all three final runs.
- Artifacts: development run ids `20260827T034000Z-tdef1`,
  `20260827T034030Z-tdef2`, and `20260827T034100Z-tdef3`; respective
  `result.json` SHA-256 values
  `889faaa4d7810625220e4f6c732066bb49854373a11e487a2b921183d0953476`,
  `727e77b89afd0c2070728319106f458833255187069cdd0e4adffb15607ac85c`,
  and `91d49d9d6888c35d494e6f172e4a03183117a2ac55eac82446d371ccf7cd69bc`.
  The job-specific result was byte-identical in all three runs, SHA-256
  `63ad30b3a84283533d10d9658bc3afc446038086127968eb6cd9e53df829eca4`.
  Raw MDBs and complete outputs remain outside the repository.
- Observation: exactly 13 of the 31 checked type candidates were accepted in
  all runs: physical values 1 Boolean, 2 Byte, 3 Integer, 4 Long, 5 Currency,
  6 Single, 7 Double, 8 Date, 9 Binary, 10 Text, 11 LongBinary, 12 Memo, and 15
  GUID. Each other checked value was rejected by the provider with HRESULT
  `-2146825029`. The DAO snapshots reported sizes 1, 1, 2, 4, 8, 4, 8, 8,
  caller-selected 13, caller-selected 13, 0, 0, and 16 respectively.
- Observation: every catalog table identifier examined named its tag-`02`
  table-definition root. A definition page began `02 01 56 43`; bytes `[4,8)`
  held a little-endian next-definition-page reference, where zero terminated
  the chain, and root bytes `[8,12)` held the total logical definition length.
  The root contributed bytes `[0,2048)` and each continuation contributed
  `[8,2048)`. The 4,333-byte boundary definition followed pages 20, 172, 171,
  then zero in every run. Continuations repeated the four-byte prefix. The
  final page can contain bytes beyond the admitted logical length, so those
  bytes are not definition input.
- Observation: in the admitted logical bytes, byte 20 was `0x4e`; little-endian
  counts at `[21,23)`, `[23,25)`, and `[25,27)` were respectively total
  columns, variable columns, and repeated total columns. Counts at `[27,29)`
  and `[31,33)` were respectively logical and physical indexes; `[29,31)` was
  zero in the controls. The map locators at `[35,43)` retained the meanings
  established in `EXP-0057`. Starting at byte 43, each physical index had an
  eight-byte sourced prefix (zero in these controls), followed by one 18-byte
  record per column, then one byte-length-prefixed raw name per column, one
  39-byte record per physical index, one 20-byte record per logical index, and
  one byte-length-prefixed raw name per logical index. The logical definition
  ended in `ff ff`; bytes between the last known name and that terminator were
  present for some variable/long columns and remain uninterpreted raw suffix.
- Observation: each 18-byte column record held physical type at byte 0,
  little-endian ordinal at `[1,3)` and again at `[5,7)`, variable-column index
  at `[3,5)` for variable columns, sourced value 1 at `[7,9)`, four raw locale
  context bytes `09 04 e4 04` at `[9,13)`, class/flags at byte 13, a
  little-endian fixed offset at `[14,16)` for fixed columns, and declared size
  at `[16,18)`. Class 2 correlated with variable Binary, Text, LongBinary, and
  Memo; class 3 with fixed fields, including fixed Text; class 7 with the
  auto-increment Long. Fixed offsets advanced by fixed sizes except Boolean,
  which occupied a bit and did not advance the next byte offset. Bytes
  `[14,16)` of variable records and direction bytes belonging to unused index
  slots varied between repeat runs and have no assigned meaning. Required and
  nullable versions of otherwise identical Long and GUID fields had identical
  physical column records, so this slice does not infer requiredness there.
  Column names were raw database-code-page bytes.
- Observation: each 39-byte physical-index record began with ten three-byte
  key slots: a little-endian column ordinal followed by direction 0 descending
  or 1 ascending. Used slots formed a prefix; unused ordinals were `0xffff` and
  their direction byte was uninterpreted. Byte 30 plus the three-byte
  little-endian page number at `[31,34)` formed the index's data-page usage-map
  locator. `[34,38)` held an in-range tag-`04` index-root page in the controls;
  it was recorded but never traversed. Byte 38 flags were `0x09` for primary,
  `0x01` for unique, `0x08` for required non-unique, and zero for the foreign
  child index.
- Observation: ordinary 20-byte logical-index records mapped to their
  physical-index ordinal at both `[0,4)` and `[4,8)`, held byte 8 zero,
  `0xffffffff` at `[9,13)`, zero reference at `[13,17)`, raw context `04 04`
  at `[17,19)`, and class 0 ordinary or 1 primary at byte 19. Logical names
  were stored in DAO's reported sorted order while those ordinal fields mapped
  them back to physical creation order. The composite control mapped Code
  descending then Sequence ascending.
- Observation: creating the one-field relationship added a foreign physical
  index to the child and sourced logical records on both tables. The parent
  gained hidden raw name `.rB`, physical selectors 1 and 0 at `[0,4)` and
  `[4,8)`, byte 8 value 1, raw value 0 at `[9,13)`, child TDEF reference 33,
  context `01 01`, and class 2. The child's named `ParentChild` record had
  selectors 0 and 0, byte 8 value 2, raw value 1 at `[9,13)`, parent TDEF
  reference 30, context `01 01`, and class 2. These sourced fields and names
  are retained losslessly. The combined DAO cascade attributes were not
  isolated to a proven physical field, so individual cascade semantics and
  the roadmap relationship checkbox remain open for a focused follow-up.
- Interpretation: the reader may follow a catalog table reference through a
  checked, iterative tag-`02` definition chain, charge the total admitted
  bytes before allocation, decode the exact counts and records above, preserve
  all raw unknown/context/suffix bytes, and validate referenced pages by kind
  without traversing index roots. It must reject truncated chains, zero/too
  large lengths, cycles, out-of-range or wrong-kind references, inconsistent
  counts/ordinals, holes in used index slots, unsupported type/size/class and
  index flag combinations, malformed names/terminator, and resource
  exhaustion. Relationship records may be exposed as raw typed references but
  must not claim independently decoded cascade semantics. This is
  internal-only format discovery and establishes no DAO compatibility.
- Usage:
  `file:oracle/windows-dao/scripts/dev/TableDefinition.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/TableDefinition.TypeInputs.json`;
  `file:oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1`;
  `file:scripts/windows-dao-dev.py`;
  `file:crates/jet3/src/column_definition.rs`;
  `file:crates/jet3/src/table_definition.rs`;
  `file:crates/jet3/src/index_definition.rs`;
  `file:fuzz/fuzz_targets/table_definition_parsing.rs`;
  `file:docs/validation/repository-contract.json`
- Rights: project-generated licensed-provider outputs are private local
  development material and are neither committed nor redistributed
- Review: pending independent review

### EXP-0060 — Local DAO row-directory and raw-row observations

- Recorded: 2026-08-27, OpenAI Codex
- Kind: repeatable local exploratory observation with
  `development_only = true`; diagnostic format discovery, not an official
  evidence campaign, release result, or compatibility result
- Question: Which bounded physical directory, deletion, overflow, null, fixed,
  and variable boundaries are stable across controlled Jet 3 row mutations?
- Origin: one project-authored, explicitly allowlisted local Windows
  development job using the DAO operations in `SRC-0024`, types in `SRC-0023`,
  and the table/allocation primitives in `EXP-0057` through `EXP-0059`; no
  third-party MDB implementation was inspected
- Environment: the same private Windows Server 2022/x86 Windows PowerShell
  development VM as `EXP-0056`; the job accepted `DAO.DBEngine.36` version 3.6
  from `dao360.dll` version `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  databases used `;LANGID=0x0409;CP=1252;COUNTRY=0`
- Protocol: run one bounded staged `row` job. In three independent fresh
  databases per scenario, create fixed-only, variable-only, mixed, all-null,
  page-boundary, growing, shrinking, deleted, and overflowing rows. Bound each
  database to at most 64 DAO-visible rows; close all DAO objects before each
  retained MDB; never compact. Compare physical facts only when identical in
  all three replicas of a scenario. The two preceding failed local pilots were
  diagnostics and were excluded.
- Artifacts: development run id `20260826T192000Z-row-layout`; `result.json`
  SHA-256
  `f339bcea3da693da385ceab803ff7a0121b37a874810033b8e0f633a052e437c`;
  `row-job-result.json` SHA-256
  `94e8d32dd870a32b6d921873f1f6e72e8968ec1e9ca1d7df51eafcc22eca4f13`;
  SHA-256 of the sorted 27-line MDB SHA-256 manifest
  `f2bc3cd96b29c6def347db8508f3f00da532f8ff7157582d3e9bd5bf4a7433e8`.
  Raw MDBs, the manifest, and complete outputs remain outside the repository.
- Observation: every selected user data page stored its table-definition root
  as a little-endian `u32` at `[4,8)`, row count as little-endian `u16` at
  `[8,10)`, then two-byte reverse-packed directory entries from byte 10. The
  low 13 bits selected the row start; each row ended at the prior start or
  page end. Bit `0x2000` did not occur. Primary rows had `0x8000` clear. A
  deleted slot was zero-length with `0xc000`; an overflow-storage row had
  `0x8000` with its sourced row bytes retained but was not a second primary
  row.
- Observation: growing a packed primary row beyond its source page replaced
  its bytes with one four-byte `0x4000` directory row. Those four bytes were a
  target row slot followed by a three-byte little-endian target data-page
  number. The target page repeated the same table root, and the named target
  slot carried `0x8000` and the complete logical row. In every overflow
  replica, source page 23 slot 4 contained `08 18 00 00`, naming page 24 slot
  8. No general chain shape beyond this pointer representation was inferred.
- Observation: a logical row began with its one-byte column count. Fixed data
  began at byte 1 and followed the fixed offsets from `EXP-0059`; Boolean did
  not consume a fixed byte. The final `ceil(column_count/8)` bytes were a
  little-endian-by-ordinal presence map: set meant present and the all-null
  control was zero. Bytes reserved for null fixed fields were not interpreted.
  With no variable columns, fixed data met the presence map directly.
- Observation: for rows shorter than 256 bytes, the byte before the presence
  map was the variable-column count. Immediately before it were
  `variable_count + 1` one-byte boundaries in reverse order: logical variable
  end boundaries followed by the fixed/variable boundary. The variable-only
  control `02 41 42 43 44 45 06 02 01 02 03` and mixed control
  `03 40 30 20 10 2a 6d 69 78 65 64 0b 06 01 07` repeated exactly. The
  265-byte, one-variable overflow target stored low boundary bytes `04 05`,
  jump byte `01`, variable count `01`, and presence byte `03`, yielding sourced
  boundaries 5 and 260. Wider rows with multiple variable columns remain
  unsupported because this experiment did not isolate their jump encoding.
- Interpretation: the reader may stream table-owned primary rows, skip hidden
  deletion/overflow-storage entries, follow the observed slot-plus-u24
  overflow representation iteratively, and expose validated raw fixed and
  variable field slices while preserving the complete logical row. It must
  reject wrong owners/page kinds, count or trailer disagreement, nonzero
  unused presence bits, offsets outside or overlapping admitted data,
  malformed targets, self-links, cycles, and operation resource exhaustion.
  This does not decode scalar values, Boolean truth, text, long values, index
  trees, writes, or DAO compatibility.
- Usage: `file:oracle/windows-dao/scripts/dev/Row.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Dispatch.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Publish.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1`;
  `file:scripts/windows-dao-dev.py`;
  `file:crates/jet3/src/row_directory.rs`;
  `file:crates/jet3/src/row.rs`;
  `file:fuzz/fuzz_targets/row_parsing.rs`;
  `file:docs/validation/repository-contract.json`
- Rights: project-generated licensed-provider outputs are private local
  development material and are neither committed nor redistributed
- Review: pending independent review

### EXP-0061 — Local DAO scalar, text, and long-value observations

- Recorded: 2026-08-27, OpenAI Codex
- Kind: repeatable local exploratory observation with
  `development_only = true`; diagnostic format discovery, not an official
  evidence campaign, release result, or compatibility result
- Question: Which bounded physical scalar encodings, CP1252 text bytes, and
  Memo/OLE inline and chained representations are stable across controlled Jet
  3 values?
- Origin: one project-authored, explicitly allowlisted staged `value` job using
  the checked DAO types and operations in `SRC-0009`, `SRC-0010`, `SRC-0012`,
  `SRC-0023`, and `EXP-0006`; the row boundaries in `EXP-0060`; and no
  third-party MDB implementation
- Environment: the same private Windows Server 2022/x86 Windows PowerShell
  development VM and provider as `EXP-0060`; system ANSI code page 1252. The
  CP1252 cases used `;LANGID=0x0409;CP=1252;COUNTRY=0`; the diagnostic CP1251
  cases used `;LANGID=0x0419;CP=1251;COUNTRY=0`.
- Protocol: in three independent fresh databases per case, create four
  scalar-boundary rows (null, minimum, representative, maximum), three CP1252
  text rows (null, empty, discriminator), three diagnostic CP1251 text rows,
  and one Memo or OLE value at each length 32, 512, 2048, and 4096. Require DAO
  readback, close every object, retain at most 4 MiB per database, never compact,
  and compare physical facts only when identical in all three replicas. Five
  failed assignment/readback pilots and two earlier successful construction
  pilots without final readback/metadata were diagnostics and were excluded.
- Artifacts: development run id `20260827T045000Z-value-layout`; `result.json`
  SHA-256
  `2120d336cebec2626107ce7a12f81ab807e4e77913d95498be9d339d505601d9`;
  `value-job-result.json` SHA-256
  `513322679823b72d71fb5ad35fdb9148cf648822a546b5ebc0baa7162ad3a77d`;
  SHA-256 of the sorted 33-line MDB SHA-256 manifest
  `812512916d151ba70fdf630cc942ada63d67d4ca2d06ef105f1e3a6bd7e4973e`.
  Raw MDBs, the manifest, and complete output remain outside the repository.
- Observation: for present fixed values, Byte was one raw byte; Integer and
  Long were little-endian signed 16- and 32-bit integers; Currency was a
  little-endian signed 64-bit integer scaled by 10,000; Single and Double were
  their little-endian IEEE-754 bit patterns; and Date was the little-endian
  IEEE-754 bit pattern of DAO's OLE Automation day value. Binary retained its
  exact fixed bytes. Replication IDs stored the first 32-bit, 16-bit, and
  16-bit groups little-endian followed by the final eight bytes in display
  order. The four boundary rows and DAO readback agreed in all replicas.
- Observation: assigning Null to the Boolean control read back as false. Its
  row bit was clear for both that row and explicit false and set for true; it
  consumed no fixed byte. Other clear presence bits represented Null, while an
  empty Text value remained present with a zero-length slice.
- Observation: the CP1252 discriminator `Café € Œ Ÿ` stored exact bytes
  `43 61 66 e9 20 80 20 8c 20 9f` in all replicas. The diagnostic database
  declared with the CP1251 locale stored `Euro €` as `45 75 72 6f 20 80`, not
  the `88` mapping in `SRC-0025`, and DAO read it back as Euro on the CP1252
  host. Therefore this run does not establish CP1251 selection or physical
  encoding; production must not infer a code page from that diagnostic alone.
- Observation: every long field began with a 12-byte header. Its first
  little-endian `u32` held the 24-bit decoded length and exactly one observed
  storage flag: `0x80000000` inline, `0x40000000` single-page, or zero chained.
  Inline headers had eight zero bytes followed by the exact payload. External
  headers stored a row slot plus a three-byte little-endian page at `[4,8)` and
  four zero bytes at `[8,12)`. Length 32 was inline, 512 single-page, and 2048
  and 4096 chained for both Memo and OLE; these are controls, not inferred
  universal thresholds.
- Observation: external targets were tag-`01` data pages with ASCII owner
  `LVAL` at `[4,8)` and the `EXP-0060` row directory. A single-page row was
  exactly the declared payload. Each chained row began with another
  slot-plus-u24 pointer and then a payload fragment; an all-zero pointer ended
  the chain. The 2048 controls used fragments 2032 and 16; the 4096 controls
  used 2032, 2032, and 32. Memo raw payload bytes were the encoded text bytes;
  DAO `FieldSize` reported twice the ASCII character count, while OLE reported
  the byte count.
- Interpretation: scalar and explicitly selected single-byte text decoders may
  retain raw bytes beside typed output and charge the decoded output length
  before constructing it. A long-value cursor may stream inline, single-page,
  and chained payloads through one fixed page, but must validate flags, reserved
  bytes, exact total length, page kind/owner/directory/slot, termination,
  repeats, self-links, truncation, and operation-wide resource limits. No
  universal storage threshold, automatic database code-page selection, write
  support, DAO differential result, or compatibility claim follows.
- Usage: `file:oracle/windows-dao/scripts/dev/Value.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Dispatch.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Publish.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1`;
  `file:scripts/windows-dao-dev.py`;
  `file:crates/jet3/src/value.rs`;
  `file:crates/jet3/src/text.rs`;
  `file:crates/jet3/src/long_value.rs`;
  `file:fuzz/fuzz_targets/long_values.rs`;
  `file:docs/architecture/SEMANTIC_READER.md`;
  `file:docs/LOCAL_WINDOWS_VM.md`;
  `file:fuzz/README.md`;
  `file:docs/validation/repository-contract.json`
- Rights: project-generated licensed-provider outputs are private local
  development material and are neither committed nor redistributed
- Review: pending independent review

### EXP-0062 — Local DAO index-tree and relationship-option observations

- Recorded: 2026-08-27, OpenAI Codex
- Kind: repeatable local exploratory observation with
  `development_only = true`; diagnostic format discovery, not an official
  evidence campaign, release result, or compatibility result
- Question: Which bounded branch/leaf layout, links, key byte shapes, and
  relationship option bytes are stable across controlled Jet 3 indexes?
- Origin: one project-authored, explicitly allowlisted staged `index` job using
  the checked DAO types and operations in `SRC-0023` and `SRC-0024`, the table
  definitions in `EXP-0059`, and no third-party MDB implementation
- Environment: the same private Windows Server 2022/x86 Windows PowerShell
  development VM and provider as `EXP-0061`; every database used
  `;LANGID=0x0409;CP=1252;COUNTRY=0`
- Protocol: create bounded fresh databases holding 4,096 ascending,
  descending, or permuted Long keys; a 1,024-row mixed-direction composite
  key; isolated physical-type indexes; and parent/child relationship
  checkpoints for absent, no-cascade, update-only, delete-only, combined, and
  deleted states. Close DAO before every retained copy, never compact, and
  require a second final run after the relationship cases were isolated.
  Seven preceding local pilots failed while the job isolated provider
  behavior for descending fields, populated GUID indexes, and invalid
  Memo/LongBinary definitions; they are diagnostics and are excluded.
- Artifacts: development run id `20260827T183000Z-index-layout`;
  `result.json` SHA-256
  `b5755a74ba8461a405dd9aa8741d498f428bcbcfc89feb32ae7bffee7ddbc176`;
  `index-job-result.json` SHA-256
  `19e9fe8cd685d80c2e32971cff12126c5b93996d67f771f0edbf969bcc9feb97`;
  SHA-256 of the sorted 11-line MDB SHA-256 manifest
  `5e58c26c85dc4eca6524ac6b8a76d81b3f9bf554a1e76173b0786c4cd75f64f5`.
  Raw MDBs, the manifest, and complete output remain outside the repository.
- Observation: a branched physical-index root was tag `03`; leaves were tag
  `04`. Both used byte 1 value 1, little-endian free bytes at `[2,4)`, the
  owning TDEF page at `[4,8)`, previous and next sibling references at
  `[8,12)` and `[12,16)`, a branch tail child at `[16,20)` (zero for leaves),
  common-key prefix length at byte 20, and class byte 1 for branches or 0 for
  leaves at byte 21. Bytes `[22,248)` were an LSB-first bitmap of cumulative
  entry-end offsets in the 1,800-byte area `[248,2048)`; the free count equaled
  1,800 minus the highest boundary. Bytes after that boundary could remain
  stale and were not input.
- Observation: each leaf entry was the page common prefix plus its stored
  suffix and ended with a four-byte row locator: a three-byte big-endian page
  followed by one row slot. Each branch separator ended with the same
  key-and-row-locator form followed by a four-byte big-endian child page; the
  header supplied the rightmost child. Separators duplicated the maximum key
  in the child reached through them. Sibling references formed exact
  left-to-right chains at each populated depth in the ascending, descending,
  and permuted controls.
- Observation: a non-null single-field key began with `7f`. Boolean and Byte
  keys were two bytes; Integer three; Long and Single five; Currency, Double,
  and Date nine. Fixed Binary held the marker, exact declared bytes, and a
  trailing declared-length byte. Text held provider collation bytes after the
  marker and ended in zero; those bytes were not decoded as text. Null was the
  single byte `00`. DAO accepted index definitions for Boolean, Byte, Integer,
  Long, Currency, Single, Double, Date, Binary, Text, and GUID; inserting the
  first indexed GUID failed in this provider, so no GUID key encoding was
  observed. Memo and LongBinary index definitions were rejected. Composite
  component boundaries and all unsupported encodings remain lossless raw
  bytes rather than guessed values.
- Observation: the two relationship logical-record context bytes at `[17,19)`
  were respectively cascade-update and cascade-delete flags. The isolated
  controls produced `00 00`, `01 00`, `00 01`, and `01 01` on both relationship
  sides. Deleting the relation removed those relationship logical indexes.
- Interpretation: an index reader may traverse the child graph iteratively,
  validate owner/kind/header/free-space/boundaries/row and child references,
  require exact sibling chains and uniform leaf depth, reject repeated pages,
  cycles, self-links, and resource exhaustion, and retain every raw key. It
  may label only the exact single-field shapes above; composite, GUID, Memo,
  LongBinary, or any other shape stays explicitly unsupported and lossless.
  Relationship views may expose the isolated booleans and raw record. This is
  internal-only discovery and does not establish DAO compatibility.
- Usage: `file:oracle/windows-dao/scripts/dev/Index.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Dispatch.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Publish.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1`;
  `file:scripts/windows-dao-dev.py`;
  `file:crates/jet3/src/index_definition.rs`;
  `file:crates/jet3/src/index_tree.rs`;
  `file:crates/jet3/src/index_tree_page.rs`;
  `file:crates/jet3/src/index_tree_rows.rs`;
  `file:crates/jet3/src/relationships.rs`;
  `file:fuzz/fuzz_targets/index_traversal.rs`;
  `file:docs/architecture/SEMANTIC_READER.md`;
  `file:docs/LOCAL_WINDOWS_VM.md`;
  `file:fuzz/README.md`;
  `file:docs/validation/repository-contract.json`
- Rights: project-generated licensed-provider outputs are private local
  development material and are neither committed nor redistributed
- Review: pending independent review

### EXP-0063 — First hosted protocol-1.2 read acquisition producer stop

- Recorded: 2026-08-30, Claude Fable 5
- Kind: additive exact-commit hosted scientific-event record plus explicitly
  non-evidentiary post-event local diagnosis; a rejected acquisition under the
  `read-v1_2.plan.json` decision rule, not a DAO read-differential result,
  compatibility evidence, or `no_outcome`
- Question: Why did the first approved hosted protocol-1.2 read acquisition
  stop, and had it crossed its first DAO mutation boundary?
- Origin: project-authored `.github/workflows/windows-dao-hosted.yml`,
  `oracle/windows-dao/scripts/Invoke-DaoReadV12.ps1`, the pinned plan, and the
  retained hosted failure diagnostics establish the scientific-event facts.
  Subsequent diagnosis used only project-authored scripts and disposable
  outputs on the private local development VM described below. The one hosted
  project-generated MDB was identity-hashed and read with the project's own
  bounded reader; no donated MDB, provider binary, or third-party MDB
  implementation was used or inspected.
- Environment: GitHub Actions run `33323400200`, attempt 1, dispatched on
  `main` at 2026-08-30T16:46:36Z from exact clean pushed commit
  `cbb34df4bc84396f17bb45980cfa2a783981b67d` with
  `accept_microsoft_access_runtime_license=true`, `run_acquisition=true`,
  `approve_acquisition=true`, and the approved plan SHA-256
  `1323c3784f761f930a2ec5566056cbb3cd581b79a4634b2ab5626b9057c544a9`. Job
  `99289207309` ran on `windows-2022` image `20260824.284.2`, Windows Server
  2022 build 20348. The stock image probe was ready without installing the
  Access Runtime: x86 `DAO.DBEngine.36` version 3.6 from `dao360.dll` version
  `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`, with a
  passing `dbVersion30` creation test; system ANSI code page 1252, culture
  `en-US`, time zone UTC.
- Protocol: the plan verification, runner identity, provider probe, and
  `cargo build --locked --release -p jet3-cli` steps passed. The producer then
  ran the 98-scenario inventory once in inventory order. The evaluator step
  was skipped because the producer failed, and no redispatch was made.
- Retained artifacts: GitHub artifact
  `windows-dao-read-cbb34df4bc84396f17bb45980cfa2a783981b67d-1` (id
  `9735548453`, 5,245 transport bytes, expiring 2026-09-13) containing
  `dao-hosted-probe/environment.json` (SHA-256
  `15a31185497a4a80f7dd8356deb9fec82028838164a5463f23defae59df273c2`, identical
  to `environment-stock.json`), `dao-hosted-probe/runner.json` (SHA-256
  `c18f563c889f7e7d2d7797534323dcd90292b960a6275bf2da6950887716f94f`), and one
  47,104-byte scenario database
  `dao-read-v1_2/DAO-READ-ALLOC-DELETE-REINSERT/database.mdb` (SHA-256
  `364b3d2ddb50aa97f11a98e9079a836e46222938bb21e2abec5ece9ead693496`). No
  `dao-manifest.raw.json`, DAO snapshot, or `report.json` exists.
- Observation: the producer stopped 1.6 seconds into the first scenario,
  `DAO-READ-ALLOC-DELETE-REINSERT`, with `System.InvalidCastException:
  Specified cast is not valid` raised from `Invoke-DaoReadV12.ps1`. The
  retained database has 23 pages; reading it with `jet3-cli snapshot` at the
  same commit found the `Items` table with columns `Id` (`Long`) and `Name`
  (`Text`) and zero rows, so `CreateDatabase`, `CreateTableDef`,
  and `TableDefs.Append` had completed and the failure was inside the first
  `insert_rows` step before its first `Update`.
- Local-diagnostic status: every following `Diagnosis` item is a development
  preflight performed after the hosted stop. Those private VM outputs are
  external, disposable, non-preregistered, and not inputs to this hosted event
  record; they establish no format fact, compatibility result, or support
  state. Exact execution inputs for a replacement hosted acquisition are bound
  separately by the re-pinned plan.
- Diagnosis: reproduced on the private local Windows Server 2022/x86
  development VM of `EXP-0056` with the same provider identity. With the
  script stack retained, the exception came from `Set-RecordsetValue` line 145,
  the single statement `$field.Value = $value`, on the second field `Name`
  after `Id` had been assigned through the same statement; instrumentation
  showed the field-type map and value types were correct (`dbLong`/`Int32`,
  then `dbText`/`String`). Controlled variants on the same table showed that
  recordset type, holding or releasing the `Field` COM wrapper, and value
  provenance did not matter, while any single assignment statement invoked
  first with an `Int32` and then with a `String` (or the reverse) failed with
  the same exception, and one statement per value type succeeded. Windows
  PowerShell 5.1 caches one COM property-set binding rule per call site, and
  the rule bound for the first value type rejects the second. The local
  `Row.DevJob.ps1` and `Value.DevJob.ps1` had never hit this because they
  already use one assignment statement per DAO type.
- Diagnosis, second defect: running the repaired producer over the whole
  inventory on the same VM exposed a second defect that the first had masked.
  `insert_until_page_count` measured the closed-file page count from the MDB
  length after every `Update` while the database was still open, but DAO
  only extends the file when the database closes: 2,000 inserted rows left
  the open file at 40,960 bytes, `DBEngine.Idle(dbRefreshCache)` and closing
  the recordset changed nothing, and `Database.Close` grew it to 503,808
  bytes. The three `DAO-READ-ALLOC-EXTENDED-SLOT-1-*` scenarios would
  therefore have inserted until the ten-million-row ceiling. The step is
  repaired to insert adaptively sized batches with a close, measure, reopen
  cycle between them, still failing closed on overshoot.
- Diagnosis, inventory boundary: with that repair, the
  `DAO-READ-ALLOC-EXTENDED-SLOT-1-ABOVE` scenario still failed twice with
  "overshot" at 16,361 pages against its exact target of 16,353. Growing a
  fresh table of the same `Id`/`Name` rows one row per close cycle on the VM
  showed the closed page count rising by one page every 145 rows through
  16,351 and 16,352, then jumping from 16,352 to 16,361 on a single row, and
  continuing by one page thereafter. Closed page counts 16,353 through 16,360
  therefore do not occur for this recipe, and the protocol-1.2 `above`
  boundary as written was unattainable by any producer. The inventory
  generator now marks `above` as non-exact (`require_exact_page_count`
  false: stop at the first closed page count that reaches the target); the
  `below` and `at` scenarios keep exact targets. The nine-page jump itself is
  an observation for the writer-allocation experiment, not an interpreted
  format fact.
- Diagnosis, snapshot size: with the non-exact target the producer reached
  16,361 pages, then stopped with `System.OutOfMemoryException` while
  building the DAO snapshot: the protocol lists every row, and the
  two-column recipe needed about 2.3 million rows (145 per page) to reach
  the boundary, which the x86 Windows PowerShell process cannot hold. The
  three boundary scenarios now use a third 200-character `Payload` text
  column so the same page counts are reached with roughly 150,000 rows.
- Diagnosis, GUID values: the next full run passed 65 scenarios, including
  all three boundary scenarios, and stopped at `DAO-READ-VALUES-GUID-MAX`
  with DAO's "Insufficient memory to continue the execution of the program"
  error, which DAO also raised in the VM for an `Int64` variant. The producer
  assigned a .NET `Guid` variant to a `dbGUID` field; it now assigns the
  braced string form that the local `Value.DevJob.ps1` had used. Reading the
  value back, DAO returned the text `{guid {FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF}}`,
  which the snapshot now unwraps before parsing.
- Diagnosis, empty text: the following run passed 94 scenarios and stopped
  at `DAO-READ-VALUES-TEXT-EMPTY` with DAO error "Field 'Typed.Value' cannot
  be a zero-length string". The inventory stores empty text as a state
  distinct from Null, which DAO only accepts when the field's
  `AllowZeroLength` property is true, as the `EXP-0061` development job had
  set; the producer now sets it for every `dbText` and `dbMemo` field.
- Diagnosis, reader tombstone bound: the first complete 98-scenario producer
  run then failed evaluation in the Rust reader itself on
  `DAO-READ-ALLOC-DELETE-REINSERT`: DAO records a deleted first-packed row as
  directory entry `0xc800`, a zero-length `0xc000` tombstone whose masked
  start 2,048 equals the page end, a state `EXP-0060` records as valid. The
  reader rejected masked starts equal to the page length outright; it now
  admits them for hidden entries only, with a focused regression test.
- Diagnosis, unattainable overflow branch: evaluation next stopped on
  `DAO-READ-ALLOC-MULTIPLE-TABLES` with an unsatisfied coverage verdict for
  `rows.overflow_pointer`. Per `EXP-0060`, overflow pointers only arise when
  an existing packed row grows beyond its source page, and the recipe
  grammar had no growth action, so no DAO producer could satisfy the three
  scenarios requiring that branch. The protocol gains a bounded `grow_rows`
  step (grow one field of the first N rows in place), and those scenarios
  now grow rows after packing.
- Diagnosis, recipe and coverage contracts: local diagnostic runs exposed
  several requirements that the declared recipes could not reach. The
  multiple-table and page-span fixtures now use one variable field, page-span
  inserts 512 rows, the deleted-all open-grown case no longer claims deleted
  row coverage, and index fixtures use 256 maximum-width unique keys. The
  primary-key fixture remains leaf-only while the five other index fixtures
  require branch traversal. Longer wide-table field names also force the
  intended table-definition continuation while retaining single-page
  coverage.
- Diagnosis, semantic adapters: DAO exposes the two sides of a relationship
  under asymmetric names, so the reader pairs complementary mutual table
  references and exposes only the DAO-visible foreign-side index. Boolean
  column-definition offset words are stored but do not advance or validate
  the fixed-data cursor. Date values now mirror .NET Framework OLE-Date
  conversion to milliseconds, and Single values mirror its round-trip `G7`
  or `G9` spelling, including midpoint-away rounding and normalization of
  negative zero.
- Local diagnostic outcome: a final-binary evaluator accepted all 98 cases
  from the private disposable local-VM artifact (`all_matched=true`,
  `matched_count=98`); its uncommitted `report.json` SHA-256 was
  `16bc1c0b7ba9f8407e068495d29882fb55c92ec100b010a308e9252d77ae26df`.
  This developer-only run is non-evidentiary, does not replace a hosted
  preregistered acquisition, and establishes no compatibility, support, or
  capability claim.
- Boundary classification: `CreateDatabase` had run and a distinct scenario
  MDB was retained, so the stop occurred after the first DAO mutation and is a
  scientific result under the plan's `retry` rule. It is recorded once. The
  identified producer, protocol, inventory, and parser defects are repaired in
  the same change; because the plan pins the execution inputs, the same plan
  file is re-pinned in place and its new SHA-256 requires a new human approval
  before any second dispatch.
- Interpretation and claims: this event assigns no MDB byte meaning,
  establishes no Rust correctness, DAO compatibility, capability verification
  state, or support-matrix movement. Every read-compatibility claim remains
  unestablished pending an accepted acquisition.
- Usage: `file:oracle/windows-dao/scripts/Invoke-DaoReadV12.ps1`;
  `file:oracle/windows-dao/acquisition/read-v1_2.plan.json`
- Rights: the project-generated MDB and diagnostics are retained only as an
  access-controlled GitHub artifact and a local read-only scratch copy; no MDB
  or provider binary is committed or redistributed
- Review: pending independent review

### EXP-0064 — Accepted hosted protocol-1.2 read differential

- Recorded: 2026-08-30, OpenAI Codex
- Kind: accepted exact-commit hosted DAO-versus-Rust read differential
- Question: Does the bounded Rust reader produce the same canonical semantic
  results as DAO for every scenario in the preregistered protocol-1.2 read
  inventory?
- Origin: GitHub Actions run `33338088215`, attempt 1, job `99328555970`,
  dispatched from exact clean pushed commit
  `e6a7b2c24afa2ef386031a2e70cdedb120180a3e` under the approved
  `read-v1_2.plan.json` SHA-256
  `b4a05fc381efdaf56011205063c07232a77d23f99837e021242ee199cda48570`.
  The project-authored producer created the scenario databases and DAO
  snapshots; the project-authored evaluator generated the result report.
- Environment: `windows-2022` image `20260824.284.2`, Windows Server 2022
  build 20348, x86 Windows PowerShell 5.1, culture `en-US`, ANSI code page
  1252, and UTC. The untouched image supplied x86 `DAO.DBEngine.36` version
  3.6 from `dao360.dll` version `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  its `dbVersion30` creation probe passed, so no runtime was installed.
- Protocol: the approved plan permitted one attempt over exactly 98 scenarios.
  Plan verification, provider probing, the locked release build, acquisition,
  canonical validation, Rust coverage checks, and evaluation all completed.
  A fresh download of the retained artifact was evaluated again from the same
  source revision with `dao_read_diff.py`; the recomputed report was
  byte-for-byte identical to the hosted report.
- Artifacts: access-controlled GitHub artifact
  `windows-dao-read-e6a7b2c24afa2ef386031a2e70cdedb120180a3e-1` (id
  `9739871270`) contains the generated MDBs, DAO snapshots, manifest,
  comparisons, provider diagnostics, and report. `report.json` SHA-256 is
  `d5593d9a66962b478e68bf8e764cb606911db6d8b04e41390e81b0f46cc6eea4`;
  `dao-manifest.raw.json` SHA-256 is
  `c66b1a1d3d29da7a0735fc96dd2c15dddae17fdf880838f26727911048acd20a`.
- Observation: the canonical report records `all_matched=true`, 98 distinct
  scenario results, and inventory SHA-256
  `b1f4a2e7d4b657e35467a43196008916df86d5426a367b0aeb8d8423cda9b97f`.
  All 95 successful-open DAO/Rust comparison projections matched, and all
  three declared opening-error scenarios produced their expected matched
  outcomes.
- Interpretation: this accepted result establishes `dao_differential`
  verification at the exact source revision only for the capability IDs
  explicitly attached to its scenarios: `database.open`,
  `format.header_and_version`, `format.pages_allocation_usage`,
  `schema.catalog_and_table_definitions`, `rows.streaming_read`,
  `values.all_dao_jet3_table_types`, `values.null_fixed_variable`,
  `values.code_pages_lossless_raw`,
  `values.date_currency_binary_guid_replication`,
  `values.memo_ole_multi_page`, `indexes.primary_unique_non_unique`, and
  `indexes.composite_ascending_descending`. Partial implementation states
  remain partial. The result does not verify capabilities absent from the
  inventory and makes no writer, malformed-input, Jet 4, encryption, or
  password-support claim.
- Usage: `file:docs/validation/support-matrix.json`;
  `file:oracle/windows-dao/acquisition/read-v1_2.plan.json`
- Rights: the generated MDBs, snapshots, report, and provider diagnostics
  remain access-controlled and uncommitted; no MDB bytes or provider binaries
  are redistributed
- Review: report integrity independently reproduced; entry and matrix review
  pending

### EXP-0065 — Accepted hosted A9 writer-allocation observations

- Recorded: 2026-08-30, OpenAI Codex
- Kind: controlled hosted DAO format-discovery acquisition with three replicas
  and an accepted evaluator report; descriptive provider observation only, not
  a Rust-correctness, compatibility, or support result
- Question: For the five preregistered A9 questions, what empty-database,
  append, reuse, table-map-extension, and index/long-value ownership behavior
  does DAO 3.6 exhibit consistently across three fresh Jet 3 databases?
- Origin and binding: GitHub Actions run `33338088173`, attempt 1, executed
  `.github/workflows/windows-dao-allocation-a9.yml` from exact clean pushed
  source revision `e6a7b2c24afa2ef386031a2e70cdedb120180a3e`. The retained manifest binds
  immutable approved plan SHA-256
  `045f25cdeec93060776ab494e9a7c462ebee634ce533e96074c5e0070ab17ea8`,
  issue 99, and three replicas. No donated MDB or third-party MDB
  implementation was used.
- Environment: GitHub `windows-2022` image `win22`, version
  `20260824.284.2`, Windows Server 2022 build `10.0.20348`; x86 Windows
  PowerShell `5.1.20348.5499`; culture `en-US`, ANSI code page 1252, and UTC.
  The stock machine-registered `DAO.DBEngine.36` provider reported version
  3.6 from `dao360.dll` file version `03.60.9765.0`, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`,
  and passed the disposable `dbVersion30` creation probe. Runtime installation
  was not attempted.
- Protocol and retained artifact: execute Q1 through Q5 in order for each of
  three fresh replicas under the plan's row, page, tagged-page, payload, and
  wall-clock bounds; close DAO before every capture; record 20 checkpoints per
  replica; then validate the manifest, checkpoint digests, page digests, map
  decoding, and cross-replica agreement. GitHub artifact id `9739695284`,
  `windows-dao-allocation-a9-e6a7b2c24afa2ef386031a2e70cdedb120180a3e-1`,
  was 305,059 compressed bytes with GitHub transport digest
  `sha256:f5dd0523628be82c70bf58b771edb2c08f50c55393be8176a545081fed2e7f6c`.
- Validation result: `manifest.raw.json` records `status = complete`, detail
  `All replicas completed.`, three replicas, and 60 retained checkpoints. The
  canonical evaluator report records `status = accepted`,
  `generator_status = complete`, `checkpoint_count = 60`, all Q1-Q5 statuses
  `answered`, and `compatibility_claim = false`; its SHA-256 is
  `75d6b39351c1e13c18039e416464fe28224a7c75ce837136a6a6759371388151`.
  A separate read-only local invocation of the checked evaluator over the
  downloaded artifact accepted it and reproduced that report byte for byte.
- Q1 observation: every empty database has 20 pages, matching the page count
  recorded by `EXP-0058`. A9 establishes the full page-tag vector. Pages 0-2
  and 6-17 are constant across replicas; only delimited byte ranges on pages
  3-5, 18, and 19 vary.
- Q2 observation: creating `A9Rows` appends pages 20-22, with tags 02, 01, and
  01 respectively; the first subsequent data-page growth appends page 23 with
  tag 01. Those pages become in use in the global map. Both transitions change
  page 0 only at `[1538,1539)` and page 1 only at `[1922,1923)`, identically
  across replicas.
- Q3 observation: freeing rows and dropping the second table leaves page count
  32 and makes pages 23-25 and 29-31 free. The surviving table's owned map
  names pages 26-28 and its free map names page 28. Four reinsert steps append
  no pages and reuse freed pages 23, 24, and 25 in steps 1, 3, and 4; step 2
  needs no newly in-use page. Every replica's verdict is `reuse`.
- Q4 observation: the first observed global tag-05 growth adds page 7041 while
  the table owned map remains type-0 inline, so the evaluator classifies it as
  `other_type05_growth`. The second adds page 13020 and converts the primary
  owned map from type-0 inline to type-1 indirect with exact references
  `[13020]`, so it is `primary_owned_map_extension`. The free map remains
  type-0 inline. Exact decoded map sets, map kinds, tag-05 counts, and both
  transition classifications agree across all replicas.
- Q5 observation: each populated replica adds 193 classified pages: 65 data
  and 128 long-value pages. All are globally in use. Some data pages occur in
  the table owned and free maps, while no long-value page occurs in either
  table map; all three replicas have the same all/some/none summary.
- Workflow conclusion: the GitHub workflow is red only because Windows
  PowerShell exposed a null `Process.ExitCode` after the redirected generator
  had completed. The acquisition step consequently threw `The A9 generator
  failed with exit .`; generator stderr was empty, the complete manifest and
  all checkpoints were already retained, and the following evaluator step
  accepted them. This wrapper false negative does not alter the report-backed
  accepted scientific outcome. No retry or redispatch occurred or is
  authorized by this entry.
- Interpretation and claims: these observations are narrow implementation
  input for a future writer change. They assign no meaning beyond the five
  preregistered questions, establish no Rust correctness or DAO differential,
  and do not justify capability, compatibility, or support-matrix movement.
- Usage: future separately reviewed writer-allocation implementation;
  `file:crates/jet3/src/page_append_plan.rs`;
  `file:oracle/windows-dao/acquisition/a9-allocation.plan.json`;
  `file:oracle/windows-dao/scripts/dao_allocation_a9.py`
- Rights: project-generated through the licensed Microsoft DAO provider and
  retained as an access-controlled GitHub Actions artifact and a temporary
  read-only local copy; no provider binary, MDB, checkpoint page image, or
  report is committed or redistributed by this repository
- Review: retained artifact identity, manifest completeness, report identity,
  report status, question results, runner failure log, and independent
  byte-for-byte evaluator recomputation checked; focused A9 contract tests must
  pass

### EXP-0066 — Preregistered local writer-bootstrap layout experiment

- Recorded: 2026-08-30, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no provider
  acquisition or format observation has occurred
- Question: For one empty table containing one Long column, which candidate
  page-0 byte, catalog timestamp fields, structural LvProp reference, and
  bounded existing/appended page mutation groups can be identified, and which
  preregistered groups are necessary for DAO read-only open and enumeration?
- Origin: project-authored clean-room experiment design using only the DAO
  operations documented by the retained public-source inventory and the exact
  bounded facts recorded by `EXP-0058`, `EXP-0059`, `EXP-0061`, and
  `EXP-0065`; no third-party MDB implementation or donated MDB is an input
- Protocol: execute three independent fresh `dbVersion30` replicas once in the
  private local Windows development VM. Capture empty, created, and renamed
  checkpoints with DAO closed; use the one clock-separated rename only to
  distinguish candidate timestamps; run created-state page ablations and
  renamed-state timestamp ablations once through four read-only DAO endpoints;
  verify exact clone reconstruction and unchanged post-open hashes. LvProp is
  correlation-only because no valid empty replacement encoding is established.
  Failures and ambiguous correlations yield an honest `no_outcome` and are not
  retried.
- Preregistration artifact:
  `oracle/windows-dao/acquisition/bootstrap-layout.plan.json`, SHA-256
  `73e402a255795eb6bd08bffa5e3611ceef219f6e810e99f9715f0e69b4aef8fc`.
  The plan pins the host client, provider probe, guest runner, dispatcher,
  publisher, producer, and host analyzer. The host and guest both reject input
  digest mismatches before the first DAO mutation.
- Observation: `preregistration.acquisition_started` is `false`. No new MDB,
  provider output, canonical report, or scientific result exists.
- Interpretation: this entry fixes the bounded experiment and its
  necessity-only decision rules. It establishes no format fact, sufficiency,
  Rust correctness, compatibility, or support result. Post-hoc inspection of
  retained A9 or catalog MDBs remains design input only.
- Usage: `file:oracle/windows-dao/acquisition/bootstrap-layout.plan.json`;
  `file:oracle/windows-dao/scripts/dev/BootstrapLayout.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/bootstrap_layout.py`;
  `file:docs/LOCAL_WINDOWS_VM.md`
- Rights: future project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: pending independent plan, producer, analyzer, and negative-control
  review before local acquisition

### EXP-0067 — Validation-rejected local writer-bootstrap acquisition

- Recorded: 2026-08-30, OpenAI Codex
- Kind: development-only local DAO acquisition record; validation rejected;
  not an accepted or `no_outcome` scientific result
- Question: Did the one-shot acquisition preregistered by `EXP-0066` execute
  and produce a canonical analyzer report that can support its Q1--Q3 decision
  rules?
- Origin: project-authored clean-room experiment at merged commit
  `188541e73b3bab67a3589c69661881ecb132d70c`, using the exact plan and seven
  input-file digests pinned by `EXP-0066`; no third-party MDB implementation or
  donated MDB was an input
- Environment: private local Windows development VM; x86 provider probe
  reported `READY`; external `environment.json`, 4,277 bytes, SHA-256
  `cb67c16ed03b5d18295b9c4c98178741b81238bec8e1a0c0040cd345e597f754`
- Protocol: run ID `20260831T003722Z-bootstrap` executed the pinned
  `bootstrap-layout` job once. The guest attempted all three bounded replicas,
  published the exact closed inventory, and the host invoked the pinned
  analyzer. No redispatch occurred.
- Artifacts: external `bootstrap-layout-job-result.json`, 266,194 bytes,
  SHA-256
  `37bc5372b5445d25847d1e7b6e3e3eaedbc400d9b915ca23fc103dd79c6016f0`;
  external `result.json`, 323,792 bytes, SHA-256
  `22dc55f800df2757c05e1e0d035806bc95d9dfa4b55eb2e042fd04025c88ca89`;
  plan SHA-256
  `73e402a255795eb6bd08bffa5e3611ceef219f6e810e99f9715f0e69b4aef8fc`.
  The MDB checkpoints and variants remain external. No
  `bootstrap-layout-report.json` exists.
- Observation: the producer reported timestamp range `[38381,38389)` on page
  19 for every replica. That range is on zero-based page 18. The pinned
  analyzer therefore stopped at
  `$.replicas[0].variants[1].ranges[0] is outside page 19`. The project
  producer calculated the page with `[int]($offset / $PageSize)`; PowerShell
  rounded `38381 / 2048` to 19 instead of taking the required floor. Retained
  artifact hashes and exact mutation reconstruction otherwise matched, but
  the immutable producer JSON remains malformed under the pinned analyzer.
- Interpretation: the acquisition is validation-rejected. It establishes no
  Q1--Q3 format or necessity fact, writer implementation fact, sufficiency,
  Rust correctness, compatibility, support result, or support-matrix movement.
  Removing or correcting the bad metadata after acquisition would be
  exploratory only and cannot retroactively create preregistered evidence.
- Usage: `EXP-0066`; `file:oracle/windows-dao/acquisition/bootstrap-layout.plan.json`;
  issue `#100`
- Rights: project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: retained identities, page arithmetic, reconstruction, and the
  rejection path were independently rechecked; no scientific outcome exists
  to review

### EXP-0068 — Preregistered local writer-bootstrap page-floor experiment

- Recorded: 2026-08-30, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration for one new
  human-authorized acquisition; no acquisition or format observation has
  occurred under this plan
- Question: For three fresh replicas of the `EXP-0066` scenario, can the same
  bounded Q1--Q3 questions produce a canonical report after timestamp page
  metadata is derived with floor division?
- Origin: project-authored clean-room experiment design using only the exact
  basis admitted by `EXP-0066`. `EXP-0067` contributes only the identity of the
  rejected run and its project-producer rounding defect; none of that run's
  MDB bytes, endpoint patterns, corrected projections, or Q1--Q3 observations
  are inputs.
- Protocol: create three new independent `dbVersion30` replicas and execute
  the unchanged `EXP-0066` scenario, checkpoints, ablations, bounds, read-only
  endpoints, decision rules, and exclusions once. The only producer correction
  derives timestamp page metadata as `floor(offset / 2048)` and rejects a
  timestamp range that crosses that page. The exact rejected offset 38,381 is
  fixed as a regression mapping to zero-based page 18. This is a distinct next
  experiment, not a revision or retroactive repair of `EXP-0066`.
- Preregistration artifact:
  `oracle/windows-dao/acquisition/bootstrap-layout-floor.plan.json`, SHA-256
  `c0161be2ba1189249d743c9198bcd004dd9d927edcc7d753cffe79c421677773`.
  The plan pins the host client, provider probe, guest runner, dispatcher,
  publisher, corrected producer, and unchanged host analyzer. The host and
  guest reject digest mismatches before the first DAO mutation.
- Observation: `preregistration.acquisition_started` is `false`. The explicit
  post-`EXP-0067` authorization permits one new acquisition only. No new MDB,
  provider output, canonical report, or scientific result exists.
- Interpretation: this entry fixes only the timestamp page-metadata defect and
  preserves the original experiment's questions and lane boundaries. It
  establishes no format or necessity fact, sufficiency, writer implementation
  fact, Rust correctness, compatibility, support result, or support-matrix
  movement.
- Usage:
  `file:oracle/windows-dao/acquisition/bootstrap-layout-floor.plan.json`;
  `file:oracle/windows-dao/scripts/dev/BootstrapLayout.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/bootstrap_layout.py`;
  `file:docs/LOCAL_WINDOWS_VM.md`; issue `#100`
- Rights: future project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: pending independent correction-boundary, plan-pin, producer,
  analyzer, and negative-control review before local acquisition

### EXP-0069 — Validated no-outcome writer-bootstrap page-floor result

- Recorded: 2026-08-30, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO `no_outcome`
  result derived from the canonical analyzer report
- Question: For the three fresh replicas preregistered by `EXP-0068`, what
  answers do the bounded Q1--Q3 observations support after correcting the
  timestamp page-metadata calculation?
- Origin: project-authored clean-room experiment at merged preregistration
  commit `ead2a92d8b92cfde0437e90465561387c2eecd65`, using the exact basis and
  fresh-input boundary fixed by `EXP-0068`; no MDB, result, corrected
  projection, endpoint pattern, or Q1--Q3 answer from `EXP-0067` was an input
- Environment: private local Windows development VM; the x86 provider probe
  reported `ready`; external `environment.json`, 4,277 bytes, SHA-256
  `129a6dbb8b67935582a43b42bde7023063c0bceac026c7a553cdada6f26c8659`
- Protocol: run ID `20260831T005855Z-bootstrap-floor` executed the pinned
  `bootstrap-layout` job once under plan SHA-256
  `c0161be2ba1189249d743c9198bcd004dd9d927edcc7d753cffe79c421677773`.
  The guest result status was `pass`; all three fresh replicas completed once,
  and no redispatch occurred.
- Artifacts: external `bootstrap-layout-job-result.json`, 265,572 bytes,
  SHA-256
  `8f5f0ae133ef51def6d0b4372f76d7447554bdd1af25934f45fae00758946275`;
  external `result.json`, 323,040 bytes, SHA-256
  `f599f3ec0ad2303346940698514812b9e12e04b552c9c624f37c0bc67412e5ae`;
  external canonical `bootstrap-layout-report.json`, 18,643 bytes, SHA-256
  `9bb8b7b2cbe02f03dacbc81bbce3001b85ea5f9dfd36ad1ea531172ad3fd2040`.
  The report has `document_type` `bootstrap_layout_report`,
  `development_only: true`, the exact plan digest above, status `no_outcome`,
  and `compatibility_claim: false`, `sufficiency_claim: false`, and
  `support_movement: false`. MDB checkpoints and variants remain external.
- Q1 observation: `candidate_page0.status` is `answered` and its outcome is
  `not_observed_necessary`. In every replica the page-0 values were empty `0`,
  created `2`, and renamed `3`; both empty-to-created and created-to-renamed
  changed ranges were exactly `[1538,1539)`.
- Q2 observation: `candidate_catalog_fields.status` is `no_outcome`.
  `date_created.status` is `no_outcome` for the verbatim reason `at least one
  replica did not resolve the correlation`. `date_updated.status` is
  `answered` with outcome `not_observed_necessary`. `lvprop.status` is
  `no_outcome` for the verbatim reason `at least one replica did not resolve
  the structural correlation`. LvProp is structural correlation only; no
  LvProp necessity claim is made.
- Q3 observation: `required_mutation_groups.status` is `answered`. Every group
  below has status `answered`; ranges are absolute and half-open:
  - `appended-page-20`: kind `zero_appended_page`, page 20, range
    `[40960,43008)`, outcome `necessary`
  - `appended-page-21`: kind `zero_appended_page`, page 21, range
    `[43008,45056)`, outcome `not_observed_necessary`
  - `appended-page-22`: kind `zero_appended_page`, page 22, range
    `[45056,47104)`, outcome `necessary`
  - `existing-page-1`: kind `revert_existing_page`, page 1, range
    `[3970,3971)`, outcome `not_observed_necessary`
  - `existing-page-11`: kind `revert_existing_page`, page 11, ranges
    `[22530,22531)`, `[22560,22561)`, `[22813,22814)`, `[22816,22817)`,
    `[22820,22821)`, `[22825,22826)`, `[22829,22830)`, `[22834,22835)`,
    `[22838,22839)`, `[22840,22841)`, `[22843,22844)`, `[22847,22850)`,
    and `[22855,22857)`, outcome `not_observed_necessary`
  - `existing-page-13`: kind `revert_existing_page`, page 13, ranges
    `[26626,26627)`, `[26665,26667)`, `[26936,26937)`, `[26939,26940)`,
    `[26943,26944)`, `[26945,26946)`, `[26948,26949)`, `[26952,26953)`,
    `[26961,26962)`, `[26966,26967)`, `[26970,26971)`, `[26975,26976)`,
    `[26979,26980)`, `[26984,26985)`, `[26988,26989)`, `[26997,26998)`,
    `[26999,27000)`, `[27002,27003)`, `[27006,27007)`, `[27008,27009)`,
    `[27011,27012)`, `[27015,27018)`, `[27023,27027)`, and `[27032,27034)`,
    outcome `necessary`
  - `existing-page-18`: kind `revert_existing_page`, page 18, ranges
    `[36866,36868)`, `[36872,36873)`, `[36890,36892)`, `[38363,38365)`,
    `[38368,38369)`, `[38371,38373)`, `[38374,38390)`, `[38394,38412)`,
    `[38414,38415)`, `[38416,38417)`, and `[38423,38438)`, outcome
    `necessary`
  - `existing-page-19`: kind `revert_existing_page`, page 19, ranges
    `[38914,38915)`, `[38920,38921)`, `[38954,38958)`, `[40690,40698)`,
    and `[40699,40720)`, outcome `necessary`
  - `existing-page-2`: kind `revert_existing_page`, page 2, ranges
    `[4108,4109)`, `[4143,4144)`, and `[4151,4152)`, outcome
    `not_observed_necessary`
  - `existing-page-3`: kind `revert_existing_page`, page 3, ranges
    `[6156,6157)` and `[6191,6192)`, outcome `not_observed_necessary`
  - `existing-page-6`: kind `revert_existing_page`, page 6, ranges
    `[12747,12748)` and `[12880,12881)`, outcome `not_observed_necessary`
  - `existing-page-9`: kind `revert_existing_page`, page 9, ranges
    `[18434,18435)`, `[18464,18465)`, `[18466,18468)`, `[18469,18471)`,
    `[18472,18474)`, `[18475,18477)`, `[18478,18479)`, `[18747,18762)`,
    `[18764,18776)`, `[18777,18827)`, `[18828,18839)`, `[18840,18853)`,
    `[18856,18860)`, `[18862,18870)`, and `[18873,18875)`, outcome
    `necessary`
- Replica agreement: replicas 1, 2, and 3 each have status `pass`,
  `baseline_passed: true`, 12 mutation groups, identical page-0 values and
  changed ranges, identical correlation statuses (`date_created: no_outcome`,
  `date_updated: resolved`, `lvprop: no_outcome`), and identical outcomes for
  all 14 variants. Their empty/created/renamed checkpoint SHA-256 identities
  are respectively: replica 1
  `b10d1db864749a03638244bd85f31d7e0957369cd4acdc43228029b971095abb` /
  `5e81875bc3ab0fafdbd17915092e0b372765722b7c2cd90a624072a9565f84cc` /
  `eb91ca438c7a906d0506063074149ada0b353480c932fc6ce68d2389093b8006`;
  replica 2
  `4483a86b1f5e81db6239668efd0f47c9121c1bc91141143cdaa243942ec2d8bc` /
  `4be8d61df5b72f64197d826f4c51898ab2aee33e2be7bb826a750bbb7fc9f9a0` /
  `30f7e8157b4fcd541bdc1a1678b2519279b8bcb3adb2d63cfb5632ad64664e4a`;
  replica 3
  `92e4ba497e682cafa9eff3e489b4063e1ef1f2b54add765c95a951cd4c0e2899` /
  `70eeb39fdfbf8a09e36efc26b5c842e09b146d79184a39268bf30c357be9e305` /
  `1c60183a0a410990743f0b29c7b0ef8f7a5bb27bb75345dd8bfd2f5ca3aca9d6`.
- Interpretation and claims: the overall result is honestly `no_outcome`
  because DateCreated and LvProp were not resolved. The answered Q1, timestamp,
  and Q3 observations are necessity-only leave-one-out results. They establish
  neither sufficiency nor a minimal complete mutation set, and establish no
  Rust writer correctness, DAO compatibility, support result, or
  support-matrix movement.
- Usage: future separately reviewed issue `#100` writer-bootstrap work;
  `EXP-0068`;
  `file:oracle/windows-dao/acquisition/bootstrap-layout-floor.plan.json`;
  `file:oracle/windows-dao/scripts/bootstrap_layout.py`
- Rights: project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: retained JSON identities, plan binding, report status and false
  claim flags, all question fields, three-replica agreement, checkpoint
  identities, exact mutation reconstruction, and deterministic canonical
  report checked; focused bootstrap analyzer and pin-contract tests must pass

### EXP-0070 — Preregistered local bootstrap-correlation and sufficiency successor

- Recorded: 2026-08-31, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no provider
  acquisition or format observation has occurred under this plan
- Question: For three fresh replicas, can the unresolved `EXP-0069`
  DateCreated and LvProp correlations be resolved under bounded, independently
  checked rules, and is a candidate assembled from the complete observed
  empty-to-created mutation groups sufficient for the four bounded read-only
  DAO endpoints?
- Origin: project-authored clean-room successor using the exact basis admitted
  by `EXP-0068` plus only the validated, additive `EXP-0069` result recorded
  above. No prior MDB, producer JSON, unrecorded endpoint pattern, corrected
  projection, or post-hoc byte inspection is an experiment input, and
  `EXP-0069` is not treated as composition or sufficiency evidence.
- Protocol: create three independent fresh `dbVersion30` replicas once. For
  DateCreated, admit `unique_exact` for one exact whole-image OLE Date match or
  `last_updated_anchor` for exactly one match in the fixed plus-or-minus-64-byte
  window around one exact LastUpdated match. LastUpdated admits only
  `unique_exact`; DateCreated can never serve as its reverse anchor. After the
  property-free empty, created, and renamed checkpoints, add
  one deterministic 768-character custom DAO Memo property and capture a
  separate `property-set` checkpoint; read `MSysObjects` through one temporary
  `WITH OWNERACCESS OPTION` QueryDef and require one exact `EXP-0061`
  single-page LvProp row/header correlation. The downstream property checkpoint
  cannot contribute bytes to the created-state sufficiency candidate.
- Sufficiency protocol: allocate a new created-length byte array, copy only the
  empty checkpoint into its prefix, and apply every exact page-0 and existing-
  page empty-to-created changed range plus every complete appended page. The
  producer does not copy the created file as the candidate. The pinned analyzer
  independently repeats the application and uses byte equality with the
  created checkpoint solely as a completeness and integrity check, then
  validates the candidate's once-only DAO endpoint result. Baseline,
  sufficiency, and ablation artifacts all report the same bounded before/after
  size and SHA-256 snapshot contract. A bounded size or hash change is retained
  as a repair and produces `no_outcome`; malformed or out-of-bound snapshots
  reject.
  The `EXP-0068` leave-one-out variants are repeated only as execution-integrity
  controls and cannot broaden the necessity claims already recorded by
  `EXP-0069`.
- Preregistration artifact:
  `oracle/windows-dao/acquisition/bootstrap-layout-sufficiency.plan.json`,
  SHA-256
  `da56d399dd4608a6d938deac3dde4ce0d6125a15a56dc8204be1ebe9338b6994`.
  The plan pins the host client, provider probe, guest runner, dispatcher,
  publisher, producer, and host analyzer. The host and guest reject plan or
  staged-input digest mismatches before the first DAO mutation.
- Observation: `preregistration.acquisition_started` is `false`. Committing the
  plan does not authorize acquisition. After review and merge, one explicit
  human authorization is required for one local-VM run. No new MDB, provider
  output, canonical report, or scientific result exists.
- Decision rule: validated correlations require identical canonical methods
  and byte targets across replicas. Endpoint evidence must be one reachable
  sequential frontier: `FFFF`, `TFFF`, `TTFF`, `TTTF`, or `TTTT`; any
  true-after-false pattern rejects. A validated, identical, unchanged-size and
  unchanged-hash DAO endpoint map reports `observed_sufficient` or
  `not_observed_sufficient` only for this complete-group scenario. Unresolved
  correlation, baseline failure or bounded repair, composed-image bounded
  repair, correlation-target or endpoint-frontier disagreement, or an
  otherwise intact non-decisive result is an honest `no_outcome`; a failed or
  repaired created baseline also forces the sufficiency claim false. Integrity,
  bound, and shape defects reject validation. There is no automatic retry
  after the first DAO mutation.
- Interpretation: this entry fixes a bounded acquisition and analysis contract
  only. It establishes no format fact, minimal mutation set, general timestamp
  or LvProp encoding, Rust writer correctness, compatibility, support result,
  or support-matrix movement.
- Usage:
  `file:oracle/windows-dao/acquisition/bootstrap-layout-sufficiency.plan.json`;
  `file:oracle/windows-dao/scripts/dev/BootstrapLayout.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/bootstrap_layout.py`;
  `file:docs/LOCAL_WINDOWS_VM.md`; issue `#100`
- Rights: future project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: pending independent evidence-boundary, checkpoint-isolation,
  plan-pin, producer, analyzer, sufficiency-negative-control, and no-retry
  review before merge and local acquisition

### EXP-0071 — Validated no-outcome bootstrap-correlation and sufficiency result

- Recorded: 2026-09-01, Claude Fable 5.1
- Kind: validated SHA-256-pinned, development-only local DAO `no_outcome`
  result derived from the canonical analyzer report
- Question: For the three fresh replicas preregistered by `EXP-0070`, which
  DateCreated, LastUpdated, and LvProp correlations resolve under the
  preregistered rules, and is the complete-group composed image sufficient for
  the four bounded read-only DAO endpoints?
- Origin: project-authored clean-room experiment at merged preregistration
  commit `4896a72998925a9e17672ed26fdd153e8b63800c`, using the exact basis and
  evidence boundary fixed by `EXP-0070`; one explicit human authorization
  preceded the single acquisition
- Environment: private local Windows development VM; the x86 provider probe
  reported `ready`; external `environment.json`, 4,277 bytes, SHA-256
  `9ac5ff05613b8dd7f1502db2454d84795da5a7ce4d45ee58e16ff6c99ca40c08`
- Protocol: run ID `20260901T201319Z-bootstrap-sufficiency` executed the
  pinned `bootstrap-layout` job once under plan SHA-256
  `da56d399dd4608a6d938deac3dde4ce0d6125a15a56dc8204be1ebe9338b6994`. The host
  client verified every input pin before staging and after acquisition. The
  guest result status was `pass`; all three fresh replicas, controls, and
  composed candidates completed once, and no redispatch occurred.
- Artifacts: external `bootstrap-layout-job-result.json`, 286,684 bytes,
  SHA-256
  `f5972585496ee1315b5022a8025f3132835ef64f4abd7265040d331a43d5b71e`;
  external `result.json`, 348,544 bytes, SHA-256
  `b3178ef47de7057979f5fc012c2eda817d82d19df5f855fb29a4ebcb6b5050dc`;
  external canonical `bootstrap-layout-report.json`, 26,358 bytes, SHA-256
  `297840058d80578436f624bc3bed987af9e63f52b3acbeba23de6e53abb99972`,
  reproduced byte-identically by re-running the pinned analyzer on the host.
  The report has `document_type` `bootstrap_layout_report`,
  `development_only: true`, the exact plan digest above, status `no_outcome`,
  and `compatibility_claim: false`, `sufficiency_claim: false`, and
  `support_movement: false`. The four MDB checkpoints, the composed candidate,
  and every variant per replica remain external.
- Q1 timestamp observation: `date_created.status` is `answered` with evidence
  `status: resolved`, method `last_updated_anchor`, offsets `[38373]`, and
  outcome `not_observed_necessary`. `date_updated.status` is `answered` with
  evidence `status: resolved`, method `unique_exact`, offsets `[38381]`, and
  outcome `not_observed_necessary`. Both offsets are absolute file offsets on
  zero-based page 18, and the DateCreated eight-byte range `[38373,38381)`
  immediately precedes the LastUpdated range `[38381,38389)`. The
  valid-OLE-Date-zero ablations of each range left all four endpoints `true`
  in every replica.
- Q1 LvProp observation: `lvprop.status` is `no_outcome` for the verbatim
  reason `at least one replica did not resolve the structural correlation`.
  In every replica and at both the renamed and property-set checkpoints the
  producer recorded the verbatim DAO error `Record(s) cannot be read; no read
  permission on 'MSysObjects'.` for the temporary `WITH OWNERACCESS OPTION`
  QueryDef, so no bounded LvProp payload was observed and no header, page, or
  row target exists. `candidate_catalog_fields.status` is therefore
  `no_outcome`.
- Page-0 control observation: `candidate_page0.status` is `answered` with
  outcome `not_observed_necessary`; page-0 values were empty `0`, created
  `2`, and renamed `3`, and both changed ranges were exactly `[1538,1539)`.
- Q2 observation: `composed_image_sufficiency.status` is `answered` with
  outcome `observed_sufficient`. In every replica the created baseline passed,
  the independently reconstructed candidate equalled the created checkpoint,
  and the composed candidate returned `open_database`, `table_enumerated`,
  `field_enumerated`, and `table_opened` all `true` with `repaired: false`
  (unchanged bounded size and SHA-256 after DAO access).
- Necessity-repetition controls: `required_mutation_groups.status` is
  `answered` with 12 groups whose kinds, pages, absolute half-open ranges, and
  outcomes are identical to those recorded in `EXP-0069`: pages 9, 13, 18, 19,
  20, and 22 `necessary`; pages 1, 2, 3, 6, 11, and 21
  `not_observed_necessary`. These repeat `EXP-0069` as execution-integrity
  controls and do not broaden its necessity claims.
- Replica agreement: replicas 1, 2, and 3 each have status `pass`,
  `baseline_passed: true`, 12 mutation groups, 15 variants with identical
  outcomes, identical correlation statuses (`date_created: resolved`,
  `date_updated: resolved`, `lvprop: no_outcome`), identical timestamp methods
  and offsets, and identical sufficiency endpoint maps. Their
  empty/created/renamed/property-set checkpoint SHA-256 identities are
  respectively: replica 1
  `144059824f73e6f34f81110f9e40c84d346377322fe81b017d676103810fe893` /
  `ef088de81fc376b2425af8115d7b5ef6cb84869451ec22925f62748c613a6d71` /
  `3c1eba87eaec92b361ede96ff65f6bdbe0226eb41f8190993b697b088a48d757` /
  `fb074af5a21a9645fc5233210ec6d45bc6484bad24996235395d4ceb98145f78`;
  replica 2
  `ce95e6b140fbf19f2b7ca390f24f60c998ad313152bf1f9df0c5394a24a67f0b` /
  `f06241c91b71fbfcae9f680e530186a3c0c467ef5e5e97182bed3da04bdf5432` /
  `b5d1f83af5a5ecb2df2ad872583a4dd9a0473f7e09a1f02dc502adbf5e24d067` /
  `650e1984fada143ed9afc9d3f6fb04366e44b80b319c8229155efe9ab6638456`;
  replica 3
  `a6b330f13094ef58e7f3836e215d0c113f1e03ff9c2ebf2c36d9668b2eb4f224` /
  `ef7346052ec8cc6db1b7c4ce864351b71694e8e5a0e3bfd779c23e73d10c268c` /
  `b314acd9bc6a47463df8468a8eca2c3e69769531c7ab7a45b4a55a25febb0212` /
  `4eb9a5c252d8504d38350ce2d0ad9271bfecb2945dc14a43d45e0f1eea9e44c6`.
- Interpretation and claims: the overall result is honestly `no_outcome`
  because LvProp was not resolved, and `sufficiency_claim` stays `false` as the
  `EXP-0070` decision rule requires. The answered subresults are retained as
  recorded: the replica-consistent DateCreated and LastUpdated byte targets on
  page 18 for this scenario, and the observation that one complete-group
  reconstruction from the empty checkpoint satisfied all four bounded
  read-only endpoints in every replica. This establishes no minimal mutation
  set, no general timestamp or LvProp encoding, no Rust writer correctness,
  no DAO compatibility, no support result, and no support-matrix movement.
  The `MSysObjects` permission failure is a method defect in the LvProp leg;
  any retry requires a distinct preregistration and a new human decision.
- Usage: future separately reviewed issue `#100` writer-bootstrap work;
  `EXP-0070`;
  `file:oracle/windows-dao/acquisition/bootstrap-layout-sufficiency.plan.json`;
  `file:oracle/windows-dao/scripts/bootstrap_layout.py`
- Rights: project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: retained JSON identities, plan binding, host re-analysis
  determinism, report status and false claim flags, all question fields,
  three-replica agreement, checkpoint identities, exact mutation and
  composed-candidate reconstruction, and verbatim LvProp failure detail
  checked


### EXP-0072 — Preregistered local system-catalog semantics experiment

- Recorded: 2026-09-01, Claude Fable 5.1
- Kind: SHA-256-pinned, development-only local DAO preregistration; no provider
  acquisition or format observation has occurred under this plan
- Question: For three fresh replicas, do the four system table definitions,
  every page of the empty image, the system rows, and the byte ranges changed
  by adding one table, one indexed table, one saved query, and one
  relationship decode under bounded pinned hypotheses, identically across
  replicas and correlated to DAO-visible names, attributes, timestamps, query
  text, and relationship fields?
- Origin: project-authored clean-room experiment using only `EXP-0051`,
  `EXP-0057`, `EXP-0058`, `EXP-0059`, `EXP-0060`, `EXP-0061`, `EXP-0065`
  Q1/Q2, `EXP-0069`, and `EXP-0071` as recorded above. The local provider has
  no workgroup information file, so DAO refuses system-table definition and
  row reads; the plan therefore decodes DAO-created bytes under pinned
  hypotheses and correlates only what DAO exposes. Interactive discovery on
  retained `EXP-0071` checkpoints shaped the hypotheses but is not an
  experiment input.
- Protocol: create three independent fresh `dbVersion30` replicas once. Per
  replica capture five closed checkpoints: `empty`; `table1` after creating
  `Alpha` with one Long `Id`; `table2` after creating `Beta` with Long `Id`,
  Text(50) `Name`, and primary key `PrimaryKey`; `query` after saving
  QueryDef `QueryOne` (`SELECT Id FROM Alpha;`); and `relationship` after
  appending relation `BetaAlpha` from `Beta`.`Id` to `Alpha`.`Id`. At each
  checkpoint record bounded DAO metadata for TableDefs, Containers and
  Documents, QueryDefs, Relations, and database Properties, recording refused
  reads verbatim per item, and re-hash after the read-only metadata open.
- Hypotheses: H1, the `EXP-0059` grammar decodes system definitions with
  exactly three relaxations (header byte 20 `0x53`, column constant `[7,9)`
  zero, column ordinal repeat `[5,7)` zero), the class byte recorded raw,
  header `[12,16)` as row count, each physical-index prefix `[4,8)` as
  distinct-key count, and the definition suffix recorded raw. H2, every page takes exactly
  one role derived from decoded structures or is `unassigned`. H3, system
  rows decode under `EXP-0060` with the H1 schema and every DAO-visible name
  correlates to exactly one `MSysObjects` row. H4, every changed range between
  consecutive checkpoints lies inside one decoded structure or is
  `unattributed`.
- Preregistration artifact:
  `oracle/windows-dao/acquisition/system-catalog.plan.json`, SHA-256
  `92d89772633c859baac50664cdc920d6d96cea8c78c9512297993f74f2f9c532`. The plan pins the host client, provider probe, guest
  runner, dispatcher, publisher, producer, and host analyzer. The host and
  guest reject plan or staged-input digest mismatches before the first DAO
  mutation.
- Observation: `preregistration.acquisition_started` is `false`. Committing
  the plan does not authorize acquisition. After review and merge, one
  explicit human authorization is required for one local-VM run. No new MDB,
  provider output, canonical report, or scientific result exists.
- Decision rule: each of Q1--Q4 is `answered` only when its observation is
  identical across all three replicas modulo timestamp values and the bytes
  `EXP-0059` already marks as varying; replica disagreement, a decode failure
  under a pinned hypothesis, a zero-or-several DAO correlation, or an
  unassigned page or unattributed range that differs between replicas is an
  honest `no_outcome` for that question. Digest, bound, shape, or checkpoint
  defects reject validation. There is no automatic retry after the first DAO
  mutation.
- Interpretation: this entry fixes a bounded acquisition and analysis
  contract only. It establishes no format fact, Rust writer correctness,
  minimal bootstrap image, compatibility, support result, or support-matrix
  movement.
- Usage: future separately reviewed issue `#100` writer-bootstrap work;
  `file:oracle/windows-dao/acquisition/system-catalog.plan.json`;
  `file:oracle/windows-dao/scripts/dev/SystemCatalog.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/system_catalog.py`;
  `file:docs/LOCAL_WINDOWS_VM.md`; issue `#100`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: pending independent evidence-boundary, hypothesis, plan-pin,
  producer, analyzer, and no-retry review before merge and local acquisition


### EXP-0073 — Accepted local system-catalog semantics result

- Recorded: 2026-09-01, Claude Fable 5.1
- Kind: validated SHA-256-pinned, development-only local DAO `accepted`
  result derived from the canonical analyzer report
- Question: For the three fresh replicas preregistered by `EXP-0072`, what do
  the system table definitions, page roles, system rows, and transition
  attributions look like under hypotheses H1--H4?
- Origin: project-authored clean-room experiment at merged preregistration
  commit `b195014e1898fdc64566951d0ef07d2babb91344`, using the exact basis and evidence boundary fixed by
  `EXP-0072`; one explicit human authorization preceded the single
  acquisition
- Environment: private local Windows development VM; the x86 provider probe
  reported `ready`; external `environment.json`, 4,277 bytes, SHA-256
  `2d0dde2fb8484974f9437d613ea6fd539ab8ac01984b0caba9becb106df8ed77`
- Protocol: run ID `20260901T211001Z-system-catalog` executed the pinned
  `system-catalog` job once under plan SHA-256
  `92d89772633c859baac50664cdc920d6d96cea8c78c9512297993f74f2f9c532`. The
  host client verified every input pin before staging and after acquisition.
  The guest result status was `pass`; all three replicas completed all five
  checkpoints once, no DAO per-item read errored, every checkpoint's digest
  was unchanged after the read-only metadata open, and no redispatch
  occurred.
- Artifacts: external `system-catalog-job-result.json`, 279,086 bytes,
  SHA-256
  `c237ad4c43130b35294d2ebbed53ef43043cba2aeaeb8cf0f2cfc1ada8b6041b`;
  external `result.json`, 321,852 bytes, SHA-256
  `b38ac4f11c7f313dc1315265f401da12a04fb1bf36d74e97af5e9b494f620c88`;
  external canonical `system-catalog-report.json`, 64,955 bytes, SHA-256
  `4870565bb327f82924e7765ef793fd3c8427b39b36bd2333671f1363d5734b3a`,
  reproduced byte-identically by re-running the pinned analyzer on the host.
  The report has `document_type` `system_catalog_report`,
  `development_only: true`, the exact plan digest above, status `accepted`,
  `compatibility_claim: false`, and `support_movement: false`. Checkpoint
  sizes were identical across replicas: `empty` 40,960; `table1` 47,104;
  `table2` 53,248; `query` 55,296; `relationship` 59,392 bytes. The fifteen
  MDB checkpoints remain external.
- Q1 observation (`answered`): all four system definitions decoded
  identically across replicas and checkpoints under H1 except row counts and
  distinct-key counts. Every header byte 20 was `0x53`; every column
  constant `[7,9)` and ordinal repeat `[5,7)` was zero; header bytes
  `[16,20)` and `[33,35)` were zero; context bytes were `09 04 e4 04`;
  physical-index prefix bytes `[0,4)` were zero.
  - `MSysObjects`, root 2, logical length 708, owned map page 6 row 0,
    available map page 6 row 1, 17 columns: 0 `Id` Long class `0x13` fixed
    offset 0 size 4; 1 `ParentId` Long `0x13` offset 4 size 4; 2 `Name` Text
    `0x12` variable 0 size 255; 3 `Type` Integer `0x13` offset 8 size 2;
    4 `DateCreate` Date `0x13` offset 10 size 8; 5 `DateUpdate` Date `0x13`
    offset 18 size 8; 6 `Owner` Binary `0x32` variable 1 size 255; 7 `Flags`
    Long `0x13` offset 26 size 4; 8 `Database` Memo `0x12` variable 2;
    9 `Connect` Memo `0x12` variable 3; 10 `ForeignName` Text `0x12` variable
    4 size 255; 11 `RmtInfoShort` Binary `0x12` variable 5 size 255;
    12 `RmtInfoLong` LongBinary `0x12` variable 6; 13 `Lv` LongBinary `0x12`
    variable 7; 14 `LvProp` LongBinary `0x12` variable 8; 15 `LvModule`
    LongBinary `0x12` variable 9; 16 `LvExtra` LongBinary `0x12` variable 10.
    Physical index 0 keys (`ParentId` ascending, `Name` ascending), map page 8
    row 0, root page 9, flags `0x01`; physical index 1 key (`Id` ascending),
    map page 10 row 0, root page 11, flags `0x01`. Logical indexes `Id`
    (physical 1, class 1) and `ParentIdName` (physical 0, class 0). Raw
    suffix
    `09000406000005060000080002060000030600000d00080600000906000010000e060000000700000f000c0600000d0600000e000a0600000b0600000c000606000007060000`.
    Row counts 8, 9, 10, 11, 12 across the five checkpoints; both index
    counts equal.
  - `MSysACEs`, root 3, logical length 223, maps page 12 rows 0 and 1,
    columns 0 `ObjectId` Long `0x13` offset 0 size 4; 1 `SID` Binary `0x32`
    variable 0 size 255; 2 `ACM` Long `0x13` offset 4 size 4;
    3 `FInheritable` Boolean `0x13` offset 0 size 1. Physical index 0 key
    (`ObjectId` ascending), map page 12 row 2, root page 13, flags `0x08`;
    logical index `ObjectId` class 0; empty suffix. Row counts 16, 18, 20,
    22, 24; distinct-key counts 8, 9, 10, 11, 12.
  - `MSysQueries`, root 4, logical length 319, maps page 12 rows 3 and 4,
    columns 0 `ObjectId` Long `0x13` offset 0 size 4; 1 `Attribute` Byte
    `0x13` offset 4 size 1; 2 `Order` Binary `0x12` variable 0 size 255;
    3 `Name1` Text `0x12` variable 1 size 255; 4 `Name2` Text `0x12` variable
    2 size 255; 5 `Expression` Memo `0x12` variable 3; 6 `Flag` Integer
    `0x13` offset 5 size 2. Physical index 0 keys (`ObjectId`, `Attribute`,
    `Order`, all ascending), map page 12 row 7, root page 14, flags `0x01`;
    logical index `ObjectIdAttribute` class 1; suffix
    `0500050c0000060c0000`. Row counts 0, 0, 0, 4, 4.
  - `MSysRelationships`, root 5, logical length 526, maps page 12 rows 8 and
    9, columns 0 `szRelationship` Text `0x12` variable 0 size 255; 1 `grbit`
    Long `0x13` offset 0; 2 `ccolumn` Long `0x13` offset 4; 3 `icolumn` Long
    `0x13` offset 8; 4 `szObject` Text `0x12` variable 1; 5 `szColumn` Text
    `0x12` variable 2; 6 `szReferencedObject` Text `0x12` variable 3;
    7 `szReferencedColumn` Text `0x12` variable 4, all Text size 255.
    Physical indexes 0, 1, 2 on `szRelationship`, `szObject`, and
    `szReferencedObject` ascending, maps page 12 rows 10, 11, 12, roots
    pages 15, 16, 17, flags `0x02`; logical indexes `szObject` (physical 1),
    `szReferencedObject` (physical 2), `szRelationship` (physical 0), all
    class 0; empty suffix. Row counts 0, 0, 0, 0, 1.
- Q2 observation (`answered`): page roles were identical across replicas.
  At `empty`: 0 header; 1 global map; 2--5 definition roots; 6 map rows
  (`MSysObjects`); 7 `unassigned` (tag `0x01`, one 133-byte zero row); 8 map
  rows and 9 index root (`MSysObjects` index 0); 10 map rows and 11 index
  root (`MSysObjects` index 1); 12 map rows (`MSysACEs`, `MSysQueries`,
  `MSysRelationships` and their indexes); 13 index root (`MSysACEs`); 14
  index root (`MSysQueries`); 15--17 index roots (`MSysRelationships`); 18
  data (`MSysObjects`); 19 data (`MSysACEs`). Appended pages: `table1` 20
  definition root `Alpha`, 21 map rows, 22 long value; `table2` 23
  definition root `Beta`, 24 map rows (table and index), 25 index root;
  `query` 26 data (`MSysQueries`); `relationship` 27 data
  (`MSysRelationships`), 28 index root (`Alpha` index 0). Page 7 was the
  only unassigned page at every checkpoint.
- Q3 observation (`answered`): every DAO-visible Document, TableDef,
  QueryDef, and Relation name correlated to exactly one `MSysObjects` row in
  every replica, each Document's row `ParentId` equalled its container's
  row `Id`, and every DAO `DateCreated`/`LastUpdated` exposed for TableDefs
  and the QueryDef equalled the row's `DateCreate`/`DateUpdate` exactly.
  Observed per class (`Type`, `Flags`, `ParentId`): containers `Tables`,
  `Databases`, `Relationships` type 3, flags `0x80000000`, parent
  `0x0f000000`, ids `0x0f000001`--`0x0f000003`; `MSysDb` type 2, flags
  `0x80000000`, parent `Databases`, id `0x10000000`; system tables type 1,
  flags `0x80000000`, parent `Tables`, ids 2--5; user tables `Alpha` and
  `Beta` type 1, flags 0, parent `Tables`, ids 20 and 23 equal to their
  definition roots; `QueryOne` type 5, flags 0, parent `Tables`, id
  `0x80000000`; `BetaAlpha` type 8, flags 0, parent `Relationships`, id
  `0x80000001`. Every row had `Name` and `Owner` present; user tables and the
  query also had `LvProp` present. `MSysACEs` held two rows per object
  (`SID` `0301` and `0201`) plus inheritable container rows with `SID`
  `0204`; every `ObjectId` matched a catalog row. `MSysQueries` held four
  rows for `QueryOne` with `Attribute` 0, 255, 6 (inline Memo `Expression`,
  header `020000800000000000000000`, 14 bytes), and 5 (`Name1` `Alpha`).
  `MSysRelationships` held one row `BetaAlpha`, `grbit` 0, `ccolumn` 1,
  `icolumn` 0, `szObject` `Alpha`, `szColumn` `Id`, `szReferencedObject`
  `Beta`, `szReferencedColumn` `Id`.
- Q4 observation (`answered`): every changed range in all four transitions
  was attributed, with zero `unattributed` ranges, identically across
  replicas. Each transition changed page 0 `[1538,1539)`, the page-1 global
  map row, the affected definitions' row counts and index distinct-key
  counts, the `MSysObjects` and `MSysACEs` row directories, new rows, and
  index pages 9, 11, and 13. `empty`→`table1` additionally changed page 6
  rows 10 and 11 (map rows reachable only through the raw suffix, tracking
  the appended long-value page 22) and appended pages 20--22.
  `table1`→`table2` changed long-value page 22 and appended 23--25.
  `table2`→`query` changed the `MSysQueries` maps on page 12 and index page
  14 and appended data page 26. `query`→`relationship` changed the
  `MSysRelationships` maps and index pages 15--17, the `Alpha` and `Beta`
  definition bodies (recorded as `definition_other`), `Alpha`'s new index
  count and map row on page 21, and appended pages 27 and 28.
- Replica agreement: replicas 1, 2, and 3 each have status `pass` with five
  checkpoints and no DAO errors. Their empty/table1/table2/query/relationship
  checkpoint SHA-256 identities are respectively: replica 1
  `dd0ffd3a2cfbfc001fb44398c4d1390a8e13e47744aa6e3a6fae5ea8d9fb9419` /
  `9e02d1ca66dd5bfcabbc48b6d1310a69d4f45398be66b64ce5f1b330802db785` /
  `82a5ac820776e76d4702c0482a8c263c7a417dcf80367b15ef9558831a24f379` /
  `cac0467cf1f55c786629cf65249264fb600329e2f6707bc30deb16407dbee860` /
  `916a3b77057b2ec3a1c675796f372f20e30c124b5812fdf94d86233506046fed`;
  replica 2
  `af4d45242fc8144365b99892b1fe4e5849e13cf8d5bf88fb4a409e04685ff09f` /
  `3ee2f14952168baa0d4941ddf6dd723e219b21bb50d92a114fc34b48072c351b` /
  `dbcb63119d48855f38537f376f331aec1ddd4021edc38fc61d2d5e16844682e7` /
  `55e355c8b2610e3f96b68d71133ebf85cd5906f47a06a7af3ebec35fcb1b6f1e` /
  `6613ec1c454db6be0ec1ffc84672f28a0b2b7ce96fd362d3326e329dceb00e92`;
  replica 3
  `7827358a8c13460a3cfcbc408dfd273d47de3f31c087eabdf0bfc3c8b6c5aa8c` /
  `a28709db9b115f04e0b1e674bfefc117de465e68876a1b0deba8fd23d3c52353` /
  `6cca18c79dc7b35b65dc5f0709b985af190a9e712fc6a2e41b8a446dc7fb08e4` /
  `8d96f815e7b7001cb14861a1968c6ea09dc60a0563558d71e0fdb630966c2851` /
  `ffb1bd672fcc6a593b7e3459a3afef82cee6974b197fdb1065f523ed177de526`.
- Interpretation and claims: this establishes, for this scenario and
  provider, the system definition grammar relaxations of H1, a complete
  page-role assignment for the empty image except page 7, the system row
  encodings and per-class catalog values above, and full attribution of the
  bounded transitions. The raw definition suffix, page 7, index page
  contents, and long-value contents remain uninterpreted. It establishes no
  Rust writer correctness, no minimal bootstrap image, no DAO compatibility,
  no support result, and no support-matrix movement.
- Usage: future separately reviewed issue `#100` writer-bootstrap work;
  `EXP-0072`; `file:oracle/windows-dao/acquisition/system-catalog.plan.json`;
  `file:oracle/windows-dao/scripts/system_catalog.py`
- Rights: project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: retained JSON identities, plan binding, host re-analysis
  determinism, report status and false claim flags, all question fields,
  three-replica agreement, checkpoint identities, and zero unattributed
  ranges checked

### EXP-0074 — Preregistered local long-value column-map experiment

- Recorded: 2026-09-01, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no provider
  acquisition or format observation has occurred under this plan
- Question: Across three fresh replicas, does the table-definition suffix
  decode as one 10-byte group per Memo or LongBinary column, does that grammar
  assign every page of the empty image, and does a user Memo column's owned
  map add the page that receives one external long value?
- Origin: project-authored clean-room experiment using only `EXP-0051`,
  `EXP-0057`, `EXP-0059`, `EXP-0060`, `EXP-0061`, and `EXP-0073` as recorded
  above. Interactive decoding of the externally retained `EXP-0073`
  checkpoints suggested H5 but is design input only, not an experiment input
  or result.
- Protocol: create three independent fresh `dbVersion30` CP1252 replicas
  once. Per replica capture the closed checkpoints `empty`; `table` after
  creating `Gamma` with Long `Id` and Memo `Note`; and `row` after inserting
  exactly one row whose 4,101-character Memo forces external storage. Close
  and release DAO before every copy or hash; retain all nine MDBs externally.
- Hypothesis H5: the definition suffix consists of 10-byte groups containing
  a little-endian column ordinal followed by owned and available map locators,
  each in row-byte-plus-24-bit-little-endian-page form. In the empty image the
  decoded locators assign page 7 through `MSysObjects.LvExtra`; `Gamma.Note`
  has exactly one group of the same shape; and inserting the row adds its
  external long-value page to Note's owned map.
- Preregistration artifact:
  `oracle/windows-dao/acquisition/long-value-maps.plan.json`, SHA-256
  `5b2fc593ddfaaa303c37e885d68ab1964e99779593f307ea3bfb702a4c0621d9`.
  The plan pins the host client, provider probe, guest runner, dispatcher,
  publisher, producer, and analyzer. Host and guest reject any plan or staged
  input mismatch before the first DAO mutation.
- Observation: `preregistration.acquisition_started` is `false`. Committing
  this plan does not authorize acquisition. After review and merge, one
  explicit human authorization is required for one local-VM run.
- Decision rule: H5 is `answered` only if all three replicas complete all
  three checkpoints once, their decoded suffixes and page roles agree, every
  Memo or LongBinary column has exactly one group, every empty page is
  assigned, `Gamma.Note` has the predicted single group, and its newly added
  owned-map pages equal Gamma's newly appearing long-value pages. Replica
  failure, disagreement, decode failure, or a missing prediction is an honest
  `no_outcome`; digest, bound, result-shape, or checkpoint defects reject
  validation. There is no automatic retry after the first DAO mutation.
- Interpretation: this entry fixes an acquisition and analysis contract only.
  It establishes no new format fact, Rust writer correctness, compatibility,
  support result, or support-matrix movement. The optional property-blob
  question is explicitly excluded.
- Usage: future `EXP-0075`; issue `#100`;
  `file:oracle/windows-dao/acquisition/long-value-maps.plan.json`;
  `file:oracle/windows-dao/scripts/dev/SystemCatalog.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/system_catalog.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: pending independent evidence-boundary, hypothesis, plan-pin,
  producer, analyzer, and no-retry review before merge and local acquisition

### EXP-0075 — Validated no-outcome long-value column-map result

- Recorded: 2026-09-01, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO `no_outcome`
  result derived from the canonical analyzer report
- Question: For the three fresh replicas preregistered by `EXP-0074`, does
  H5 assign every empty-image page, cover every Memo or LongBinary column,
  and correlate `Gamma.Note`'s owned-map additions with newly appearing
  Gamma long-value pages?
- Origin: project-authored clean-room experiment at merged preregistration
  commit `2c00b884df22080141058d8999e5d986a8785958`, using the exact evidence
  boundary fixed by `EXP-0074`; one explicit human authorization preceded the
  single acquisition, and no redispatch occurred
- Environment: private local Windows development VM; the x86 provider probe
  reported `ready`; external `environment.json`, 4,277 bytes, SHA-256
  `95a9c81956f323b60ec38f739a31834c948868ef6079eb5a32728e102f756d70`
- Protocol: run ID `20260901T215352Z-dev-dao` executed the pinned
  `long-value-maps` job once under plan SHA-256
  `5b2fc593ddfaaa303c37e885d68ab1964e99779593f307ea3bfb702a4c0621d9`.
  Host and guest verified the plan and staged inputs. The guest result was
  `pass`: replicas 1--3 each completed `empty`, `table`, and `row` once with
  no recorded replica error. Checkpoint sizes agreed across replicas:
  40,960, 47,104, and 55,296 bytes respectively.
- Artifacts: external `long-value-maps-job-result.json`, 151,099 bytes,
  SHA-256
  `a9e1a7571cf04c807e20549326625235500e195b929b328cbc256583ca727a46`;
  external `result.json`, 174,965 bytes, SHA-256
  `dce19140b0a99566d12e91b9f9e537af236d5be0ccafb94e6e61eea57cbc9a03`;
  external canonical `long-value-maps-report.json`, 14,289 bytes, SHA-256
  `973b0ac01f34590834bcf818126fa0b7144d135a3886ebfdf93d0e1cb2ec139e`,
  reproduced byte-identically with the staged pinned analyzer. The report has
  `document_type` `long_value_maps_report`, status `no_outcome`, H5 status
  `no_outcome`, and false compatibility and support-movement flags. All nine
  MDB checkpoints remain external.
- H5 observation (`no_outcome`): every empty-image page was assigned, and
  page 7 resolved through the `MSysObjects.LvExtra` available-map locator.
  `MSysQueries.Expression` decoded as one group at page 12 rows 5 and 6.
  `MSysObjects` decoded seven groups whose column ordinals, in stored order,
  were `9, 8, 13, 16, 15, 14, 12`; the report's expected ordinal-order list
  was `8, 9, 12, 13, 14, 15, 16`. Thus each expected long-value column was
  represented once in the reported lists, but the pinned ordered-list
  `suffix_complete` predicate was false at every checkpoint and
  `all_long_value_columns_have_one_suffix_group` was false.
- Gamma observation: `Gamma.Note` decoded as exactly one group at owned map
  page 21 row 2 and available map page 21 row 3 in both post-create
  checkpoints. Its owned-page set changed from empty at `table` to pages 23,
  24, and 25 at `row`. Under the pinned table-owned-page classifier, Gamma's
  long-value-page list remained empty at both checkpoints. Consequently
  `gamma_new_long_value_pages` was empty and
  `note_owned_map_tracks_external_long_value_page` was false.
- Replica identities: replica 1 empty/table/row SHA-256 values were
  `e3ff941049b234b7cd72374ce2692d26ec12702bd2bf6f36e72a0c249a466588` /
  `2ca6001d35dac6960fc2797214587af9745dbde8f169f377abc6fd865468898f` /
  `319dbaed30bb014333ac710d804a94a07ca973518e0105c0ac953a37521207a4`;
  replica 2 values were
  `a2555f15259f2845c9f7969a6d5c0379730ea7220b86a30ee196c622fbc8853c` /
  `f354d43f5e28a51c628463c9cd076e3adaeed99872a88ce54d4685bd2e22ca6a` /
  `5728e764eec95c2711f2322ba35e2d57a7e2c9ff35ac1543f5847e8cd0c05ebd`;
  replica 3 values were
  `179151e483fcc56cf4b7896ade0ece8a4864987989dfc1ce69faa20ce134d76d` /
  `cbe087d42e03160b46446b836868c022c7c6b2d17c8d5feb7947679106ed9582` /
  `1614933b3c9e0237f19a58371a0873bdecd95c10a7b3f2d88c44aa3b6e1c1e20`.
- Interpretation: H5 is not accepted. The result closes page 7's role under
  the pinned grammar and records the bounded suffix and map observations, but
  it does not establish the preregistered coverage-and-transition conjunction.
  Per `EXP-0074`, analyzer repair or a second acquisition is not a retry path;
  any follow-up requires a new preregistration and human decision. This result
  establishes no Rust writer correctness, compatibility, support result, or
  support-matrix movement.
- Usage: future separately preregistered issue `#100` work; `EXP-0074`;
  `file:oracle/windows-dao/acquisition/long-value-maps.plan.json`;
  `file:oracle/windows-dao/scripts/system_catalog.py`
- Rights: project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: plan binding, single-dispatch record, artifact identities, report
  reproducibility, all question fields, replica completion, and false claim
  flags checked

### EXP-0076 — Preregistered corrected long-value column-map experiment

- Recorded: 2026-09-01, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no provider
  acquisition or format observation has occurred under this plan
- Question: Across three new fresh replicas, does exact order-insensitive
  suffix coverage assign every empty-image page, and do `Gamma.Note`'s
  owned-map additions exactly equal the newly appearing globally identified
  long-value pages?
- Origin: project-authored clean-room follow-up using only `EXP-0051`,
  `EXP-0057`, `EXP-0059`, `EXP-0060`, `EXP-0061`, `EXP-0073`, and the
  validated `EXP-0075` no-outcome as recorded above. This is a distinct
  experiment, not a reinterpretation or redispatch of `EXP-0074`.
- Correction boundary: `EXP-0075` recorded two failed comparisons. Its system
  suffix groups were not stored in ordinal order, despite each expected
  column appearing once, so H6 uses exact set equality. Its Gamma column pages
  carried the global `long_value` role but were absent from Gamma's table-owned
  map, so H6 compares Note's owned additions with newly appearing pages having
  the `EXP-0061` global LVAL signature. No other hypothesis changes.
- Protocol: repeat the bounded three-replica `empty`, `table`, and `row`
  scenario fixed by `EXP-0074`, using new fresh databases and one
  4,101-character `Gamma.Note` value per replica. Close and release DAO before
  each capture; retain the nine MDBs externally and commit none.
- Preregistration artifact:
  `oracle/windows-dao/acquisition/long-value-maps-followup.plan.json`, SHA-256
  `1052458ff87814bf8ac2d5dd09da740eb61565c0aaf27654228c9352c85dd46c`.
  It pins the host client, provider probe, guest runner, dispatcher, publisher,
  producer, and analyzer; host and guest reject mismatches before mutation.
- Observation: `preregistration.acquisition_started` is `false`. Committing
  the plan does not itself authorize acquisition. After independent review and
  merge, one explicit human authorization permits one local-VM run.
- Decision rule: H6 is answered only when all replicas agree, every Memo or
  LongBinary column appears exactly once regardless of group order, every
  empty page is assigned, `Gamma.Note` has one stable group, and the nonempty
  pages added to its owned map exactly equal newly appearing global
  `long_value` pages. Any missing predicate is an honest `no_outcome`; input,
  digest, bound, shape, or checkpoint defects reject validation. There is no
  automatic retry after the first DAO mutation.
- Interpretation: this entry fixes only a corrected acquisition and analysis
  contract. It establishes no new format fact, Rust writer correctness,
  compatibility, support result, or support-matrix movement.
- Usage: future `EXP-0077`; issue `#100`;
  `file:oracle/windows-dao/acquisition/long-value-maps-followup.plan.json`;
  `file:oracle/windows-dao/scripts/dev/SystemCatalog.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/system_catalog.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: pending repeated independent evidence-boundary, plan-pin, producer,
  analyzer, decision-rule, and no-retry review before merge and acquisition

### EXP-0077 — Validated corrected long-value column-map result

- Recorded: 2026-09-01, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO accepted result
  derived from the canonical analyzer report
- Question: For the three new fresh replicas preregistered by `EXP-0076`,
  does exact order-insensitive suffix coverage assign every empty-image page,
  and do `Gamma.Note`'s owned-map additions exactly equal the newly appearing
  globally identified long-value pages?
- Origin: project-authored clean-room experiment at merged preregistration
  commit `587246515120eec028f0bcf1de34d1adbae23908`, using the exact evidence
  boundary fixed by `EXP-0076`; one explicit human authorization preceded the
  single acquisition, and no redispatch occurred
- Environment: private local Windows development VM; the x86 provider probe
  reported `ready`; external `environment.json`, 4,277 bytes, SHA-256
  `3e64c3751cff09f864efaa18704158b78f72b38dae6c7acc349318a286a86e54`
- Protocol: run ID `20260901T221155Z-dev-dao` executed the pinned
  `long-value-maps-followup` job once under plan SHA-256
  `1052458ff87814bf8ac2d5dd09da740eb61565c0aaf27654228c9352c85dd46c`.
  Host and guest verified the plan and staged inputs. The guest result was
  `pass`: replicas 1--3 each completed `empty`, `table`, and `row` once with
  no recorded replica error. Checkpoint sizes agreed across replicas:
  40,960, 47,104, and 55,296 bytes respectively.
- Artifacts: external `long-value-maps-followup-job-result.json`, 151,274
  bytes, SHA-256
  `0ea1f2c3a0a604cf83737fe83a06ab8ec40475fac6d5cf774cc8c4aa6ce3b0c0`;
  external `result.json`, 175,140 bytes, SHA-256
  `b0d9c40c502a23395944dd2ff90da04e78c5796c992dd9d43317fa2aa46a66c0`;
  external canonical `long-value-maps-followup-report.json`, 14,690 bytes,
  SHA-256
  `2278e5adc7f31208eae155a9785c31597aa6bb3d81d47f4a009bea0b05fc6b4c`,
  reproduced byte-identically with the staged pinned analyzer. The report has
  `document_type` `long_value_maps_followup_report`, status `accepted`, H6
  status `answered`, and false compatibility and support-movement flags. All
  nine MDB checkpoints remain external.
- H6 observation: every empty-image page was assigned. Page 7 resolved as
  `long_value_map_rows` through the `MSysObjects.LvExtra` available-map
  locator. Every Memo or LongBinary column had exactly one suffix group under
  exact order-insensitive coverage; the seven `MSysObjects` groups retained
  stored ordinal order `9, 8, 13, 16, 15, 14, 12`, and
  `suffix_set_complete` was true.
- Gamma observation: `Gamma.Note` had exactly one stable group at owned map
  page 21 row 2 and available map page 21 row 3. Its owned-page set changed
  from empty at `table` to pages 23, 24, and 25 at `row`. Those pages exactly
  equaled the newly appearing pages with the global `long_value` role, so
  `note_owned_map_tracks_external_long_value_page` was true in every replica.
- Replica identities: replica 1 empty/table/row SHA-256 values were
  `49d7ed52af6733ac891ca670a608a3c6edc9467b26d641b191b1083ad5bb8db4` /
  `215e0c9bae9857169e425cfe82fa079aea3e139395cc37fcb0a164cd132d147b` /
  `96b5583bf57b78a9ac9cafb93d3887e8a0b5b33fec9aa1e675fc9ec3d0d3c95b`;
  replica 2 values were
  `2289cdf431a044d115227250d8baf2d5e3d10b49137de756f9d584a8941143ac` /
  `501fc7249e0501e54c4a6e458a55e1a27d564df01f99ccf87a3e1ac360f27444` /
  `ee42be07df43588c2019fc002e294dd429da5437a3226d3e27076081832504aa`;
  replica 3 values were
  `bb45894ee9ce14ca6ef3102de5deda651e9ca8612c152bf701c19221a7ba11d1` /
  `ad61a610bea0e1fa9f01bf1af7a71c8091cabfef2b12b2bf4d506b0fa21c64f6` /
  `66400e4c76c2608bbec6e5f46a66e3af9999210a0e6982da0194a23ae8209ad0`.
- Interpretation: H6 is accepted within its preregistered bounds. It assigns
  page 7, establishes one 10-byte suffix group per Memo or LongBinary column
  independent of stored group order, and correlates `Gamma.Note`'s owned-map
  additions with newly appearing global LVAL pages. It establishes no Rust
  writer correctness, compatibility, support result, or support-matrix
  movement.
- Usage: issue `#100`; `EXP-0076`;
  `file:oracle/windows-dao/acquisition/long-value-maps-followup.plan.json`;
  `file:oracle/windows-dao/scripts/system_catalog.py`
- Rights: project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: independent single-dispatch, artifact-identity, report-reproduction,
  H6-decision, evidence-boundary, and false-claim reviews completed

### EXP-0078 — Preregistered fixed bootstrap-composer semantics experiment

- Recorded: 2026-09-01, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no provider
  acquisition or format observation has occurred under this plan
- Question: Across three fresh empty-to-`Alpha(Id Long)` replicas, what are
  the exact lossless raw `MSysObjects.ParentIdName` keys, the exact opaque
  external `Alpha.LvProp` value, and the fixed page-0 transition?
- Origin: project-authored clean-room successor using only `EXP-0058`,
  `EXP-0060`, `EXP-0061`, `EXP-0062`, `EXP-0069`, `EXP-0071`, `EXP-0073`,
  and `EXP-0077` as recorded above. Retained raw artifacts and post-hoc
  observations from earlier experiments are analyzer design inputs only and
  are not admitted evidence for this experiment.
- Protocol: create three new independent Jet 3 databases; capture each closed
  at exactly 20-page `empty` and after adding only empty table `Alpha` with
  one Long field `Id`, at exactly 23-page `alpha`. Close and release DAO before
  every copy, validate the bounded DAO metadata shape, record SHA-256 before
  and after its read-only metadata access, and retain all six MDBs externally.
- Preregistration artifact:
  `oracle/windows-dao/acquisition/bootstrap-composer-semantics.plan.json`,
  SHA-256
  `96df220c867723c8f43a2079ae67e0da8c674e5bde7e50427b69f26ab82994ca`.
  It pins the host client, provider probe, guest runner, dispatcher, publisher,
  producer, analyzer, and analyzer dependency. Host and guest reject pinned
  producer mismatches before mutation; the host rechecks every staged analysis
  input immediately before evaluating the result.
- Observation: `preregistration.acquisition_started` is `false`. Committing
  this plan does not authorize acquisition. After the exact reviewed plan and
  inputs are committed, one explicit human authorization permits one local-VM
  acquisition. There is no automatic retry after the first DAO mutation.
- Decision rule: each question is answered only when its structural decode and
  row correlations succeed and the complete observation agrees across all
  three replicas. Metadata repair, post-mutation producer failure, decode or
  correlation failure, or replica disagreement is an honest `no_outcome`;
  pre-mutation, plan, input, digest, bound, checkpoint, or result-shape defects
  reject or abort without a scientific outcome.
- Interpretation: this entry fixes only an acquisition and analysis contract
  for exact values in one fresh transition. It establishes no general
  composite/text key grammar, property format, page-0 counter, Rust writer
  correctness, compatibility, support result, or support-matrix movement.
- Usage: future successor result; issue `#100`;
  `file:oracle/windows-dao/acquisition/bootstrap-composer-semantics.plan.json`;
  `file:oracle/windows-dao/scripts/dev/SystemCatalog.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/bootstrap_composer_semantics.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: first isolated protocol, VM wiring, and analyzer review completed;
  fixes pending repeated independent review before commit and acquisition

### EXP-0079 — Accepted fixed bootstrap-composer semantics result

- Recorded: 2026-09-01, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO accepted result
  derived from the canonical analyzer report
- Question: For the three fresh empty-to-`Alpha(Id Long)` replicas
  preregistered by `EXP-0078`, what are the exact lossless raw
  `MSysObjects.ParentIdName` keys, the exact opaque external `Alpha.LvProp`
  value, and the fixed page-0 transition?
- Origin: project-authored clean-room experiment at preregistration commit
  `474c378b96fcdb03a1c11c731c51c96d9cb9bdff`, using the exact evidence
  boundary fixed by `EXP-0078`. The human
  operator explicitly authorized the acquisition; two host preflight failures
  occurred before any VM connection or DAO mutation, then one acquisition was
  dispatched after the existing VM and key-only SSH were ready. No scientific
  retry occurred.
- Environment: private local Windows development VM; Windows NT
  10.0.20348.0 build 20348 on AMD64; x86 Windows PowerShell Desktop
  5.1.20348.558; DAO.DBEngine.36 provider 3.6 from x86 `dao360.dll` file
  version 03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`;
  culture/UI culture `en-US`; ANSI code page 1252; VM time zone `Pacific
  Standard Time` at UTC-07:00. The provider probe reported `ready`; external
  `environment.json`, 4,277 bytes, SHA-256
  `b30028bf555c90934b396fc2a064de138d6675cea15def1b7dd780bce78630e7`
- Protocol: run ID `20260902T011637Z-dev-dao` executed the pinned
  `bootstrap-composer-semantics` job once under plan SHA-256
  `96df220c867723c8f43a2079ae67e0da8c674e5bde7e50427b69f26ab82994ca`.
  Host and guest verified the plan and staged inputs. The guest result was
  `pass`: replicas 1--3 each completed exactly `empty` and `alpha` once with
  no recorded error; checkpoint sizes were 40,960 and 47,104 bytes, and DAO
  metadata access changed no checkpoint digest.
- Artifacts: external `bootstrap-composer-semantics-job-result.json`, 100,020
  bytes, SHA-256
  `cf05ea239ce951115ebf28c9a8fe4d2cb8231e21c7a153b55d3e3b0f2c683eaf`;
  external `result.json`, 116,191 bytes, SHA-256
  `04d49c7979f3cb34f64ccf6f529e91841931ef0a4ff2b6e3fe4a02453f659752`;
  external canonical `bootstrap-composer-semantics-report.json`, 10,621 bytes,
  SHA-256
  `8ea017170549db18a5dfd69bc538afca3fa0132a2ee275810a7257a153680978`,
  reproduced byte-identically with the staged pinned analyzer. The report has
  status `accepted`, all three question statuses `answered`, and false
  compatibility and support-movement flags. All six MDBs remain external.
- Fixed key observation: the complete lossless key bytes by catalog row were
  `Tables=7f8f0000007f7760616d667600`,
  `Databases=7f8f0000007f64607760616076667600`,
  `Relationships=7f8f0000007f75666d60776a727076696a737600`,
  `MSysDb=7f8f0000027f6f767d76646100`,
  `MSysObjects=7f8f0000017f6f767d7672616b6662777600`,
  `MSysACEs=7f8f0000017f6f767d766062667600`,
  `MSysQueries=7f8f0000017f6f767d76747866756a667600`,
  `MSysRelationships=7f8f0000017f6f767d7675666d60776a727076696a737600`,
  and `Alpha=7f8f0000017f606d73696000`. Every page-9 locator correlated
  one-to-one with its decoded catalog row in every replica. The observation is
  fixed bytes only; it does not establish component or text-key encoding.
- Fixed `LvProp` observation: `Alpha.LvProp` used external header
  `2b0000400016000000000000`, targeting page 22 row 0 with length 43. The
  opaque payload was
  `4b4b4400100000008000080052657175697265641700000001000800000002004964090001010000010000`,
  SHA-256
  `0bcca4af126edbf7a0a5435551a576e454308b95733ac2b658014a5221981abe`.
  No property grammar is inferred.
- Fixed page-0 observation: offset 1538 was `0` at `empty` and `2` at `alpha`,
  and it was the only changed page-0 offset in all replicas. This does not
  establish a general counter or update rule.
- Replica identities: replica 1 empty/alpha SHA-256 values were
  `3314351885fc0e0a90243e7be98241e2cbf6e9dc6f19368e927a0b6d90502f92` /
  `642c90e8cdec8faf577affdba44faffbc0c608714cce71fe4089df445ff9b3f1`;
  replica 2 values were
  `7fb50c4cdcd90478cf21730d11181ce7b1c635115a963d129a63ca86b36f2367` /
  `7ff1cebd03f7afefe14fd7618299f34b02df00d2de07de7ab7111a2c30eface1`;
  replica 3 values were
  `de65b67975ef1b419caf387c40842ae7f51167add2309df87e9c1314528f0c54` /
  `b3516a730940c13b3d5f20fc4bc216ce6fe1be16f74c3187be0096d1cf797c1a`.
- Interpretation: all three bounded questions are accepted. The fixed bytes
  may complete the crate-private fresh-image composer, but this result does not
  validate that composer and establishes no general key/property/page-0 rule,
  DAO compatibility, support result, or support-matrix movement.
- Usage: issue `#100`; `EXP-0078`;
  `file:crates/jet3/src/bootstrap_composer.rs`;
  `file:oracle/windows-dao/acquisition/bootstrap-composer-semantics.plan.json`;
  `file:oracle/windows-dao/scripts/bootstrap_composer_semantics.py`
- Rights: project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: independent single-dispatch, artifact-identity, report-reproduction,
  fixed-value transcription, reader-structure, evidence-boundary,
  environment-identity, and false-claim reviews completed

### EXP-0080 — Preregistered bootstrap-composer DAO validation

- Recorded: 2026-09-01, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no provider
  acquisition or composer-validation observation has occurred under this plan
- Question: Do three deterministic copies of the crate-private 20-page empty
  image and 23-page `Alpha(Id Long)` image pass the exact bounded read-only DAO
  endpoints fixed by the plan without changing, while three fresh DAO-created
  Alpha controls pass the same Alpha endpoints without changing?
- Origin: project-authored clean-room validation successor using `EXP-0079`'s
  accepted fixed values and the crate-private composer as inputs. The candidate
  producer remains an ignored unit-test exporter; no public creation or
  filesystem-publication API is introduced.
- Protocol: locally verify a complete SHA-256 manifest covering Cargo.lock, the
  workspace and crate manifests, `rust-toolchain.toml`, and every
  `crates/jet3/src/*.rs`; run the exact library-only ignored exporter and require
  candidate identities 40,960 bytes / SHA-256
  `f762dbc12d80eb3fb5dae53fb58696219d48b7fa1a15d5deb5c1f9333d8862d6`
  and 47,104 bytes / SHA-256
  `8552db1c7d0083429fcbbcf4dd59a5f1d8f36383c8bdef4d9decc06247cf77ca`.
  Dispatch exactly three replicas once. Each replica copies and opens both
  candidates read-only, creates one fresh DAO Alpha control, executes the
  ordered endpoint frontiers, closes and releases all COM objects, and records
  sizes and hashes before and after access. Publish at most nine MDBs and retain
  them only outside the repository.
- Endpoints: the empty candidate must open as version 3.0 with exactly the four
  system TableDefs and four matching `Tables` documents. Alpha candidates and
  controls must additionally resolve `TableDefs.Item("Alpha")`, resolve exactly
  one Long field `Fields.Item("Id")`, enumerate bounded TableDef and Field
  properties with `Id.Required` false, open an empty snapshot, and resolve the
  Alpha table document.
- Preregistration artifacts:
  `oracle/windows-dao/acquisition/bootstrap-composer-validation.plan.json`,
  SHA-256
  `2b2d9b3071bc9406e341b7ca9409107f5eb0527727e54e8de1deabc3d429def7`;
  candidate-source manifest
  `oracle/windows-dao/acquisition/bootstrap-composer-validation.sources.json`,
  SHA-256
  `459e8e3dbeae9114d0241ce18e609b67ba2ec43ce475c837fe1f24213179a7a5`.
  The plan pins the host client, provider probe, guest runner, dispatcher,
  publisher, dedicated producer, standalone analyzer, source manifest, and
  exact candidate identities. Host and guest reject applicable pin mismatches
  before DAO work; the host rechecks staged analysis inputs before evaluation.
- Observation: `preregistration.acquisition_started` is `false`. The user has
  explicitly authorized this and future local Windows VM usage for the current
  session. That authorization permits this single acquisition only after the
  exact reviewed plan and inputs are committed. After the first DAO control
  mutation, no automatic retry is permitted; any failure is a scientific
  result pending a new human decision.
- Decision rule: with all three unchanged controls passing, identical complete
  candidate frontiers are `observed_accepted`, while identical partial
  candidate frontiers are a valid negative `not_observed_accepted`. Candidate
  change, control failure or change, replica disagreement, or an incomplete
  scientific job is `no_outcome`. Pin, inventory, bound, malformed-shape, or
  result-integrity defects reject validation without a canonical report.
- Interpretation: this entry fixes only a validation of two deterministic
  images at the named endpoints. It establishes no general Jet 3 or DAO
  compatibility, public creation API, safe destination publication, arbitrary
  schema/index/relationship/row support, support result, or support-matrix
  movement.
- Usage: future successor result; issue `#100`;
  `file:oracle/windows-dao/acquisition/bootstrap-composer-validation.plan.json`;
  `file:oracle/windows-dao/scripts/dev/BootstrapComposerValidation.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/bootstrap_composer_validation.py`
- Rights: future project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: two isolated review passes completed across Rust/source binding,
  Windows/DAO execution and publication, and plan/analyzer/result integrity;
  all first-pass findings were fixed and every second-pass review reported no
  remaining findings

### EXP-0081 — Preregistered direct-launch bootstrap-composer validation

- Recorded: 2026-09-01, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration successor;
  no provider acquisition or composer-validation observation has occurred under
  this successor plan
- Question: the question, candidate identities, endpoint frontiers, bounds,
  decision rules, evidence boundary, and one-scientific-attempt policy are
  exactly those fixed by `EXP-0080`.
- Origin: `EXP-0080` was committed at
  `d8b3dd3cf3d21ab7b67f46b1deef750610211eab` with plan SHA-256
  `2b2d9b3071bc9406e341b7ca9409107f5eb0527727e54e8de1deabc3d429def7`.
  Its first dispatch never reached the staged runner: Windows rejected the
  9,432-character outer encoded PowerShell command line as too long. No outbox
  was created, and the provider probe and DAO did not execute, so this was a
  pre-mutation transport abort rather than a scientific attempt.
- Protocol change: replace only the nested encoded launcher with one bounded,
  shell-safe direct command to the x86 Windows PowerShell executable resolved
  through `%WINDIR%`. The client restricts the remote root to an ASCII
  shell-safe path grammar and rejects a serialized command over 8,000 UTF-16
  code units. A read-only SSH preflight executed that environment-resolved x86
  executable successfully. The staged pinned runner still receives the same
  validated paths and arguments; every scientific operation and bound remains
  unchanged.
- Preregistration artifact: the existing
  `oracle/windows-dao/acquisition/bootstrap-composer-validation.plan.json` is
  replaced, not stacked with a revision file, and now has SHA-256
  `7b2b5aa51477495ed1109279e4988fb6428d9712a999ea7aeb3462d2f46dd7ac`.
  It pins the direct-launch host client at SHA-256
  `15f85181899e511bd1bf993203fb37e9d21e12470cecf2a0640c9cc23cceffc8`;
  all other producer, analyzer, source-manifest, and candidate pins are
  unchanged from `EXP-0080`.
- Observation: `preregistration.acquisition_started` remains `false` for this
  successor. The user's explicit authorization for this and future local VM
  usage in the current session remains applicable after review and commit. No
  retry is permitted after the first DAO control mutation.
- Decision rule: unchanged from `EXP-0080`. Transport, pin, inventory, bound,
  malformed-shape, or result-integrity defects reject without a canonical
  report; well-formed scientific control/candidate failures follow the fixed
  accepted-negative or `no_outcome` rules.
- Interpretation: this successor changes transport only and establishes none
  of the compatibility, public API, safe publication, arbitrary-schema, or
  support claims excluded by `EXP-0080`.
- Usage: future successor result; issue `#100`; `EXP-0080`;
  `file:oracle/windows-dao/acquisition/bootstrap-composer-validation.plan.json`;
  `file:scripts/windows-dao-dev.py`
- Rights: future project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: isolated protocol review and two transport/client-contract review
  passes completed; shell-safety, UTF-16 command bounds, exact option ordering,
  plan/client pins, and pre-mutation classification have no remaining findings

### EXP-0082 — Validation-rejected bootstrap-composer DAO result

- Recorded: 2026-09-01, OpenAI Codex
- Kind: validation-rejected SHA-256-pinned, development-only local DAO result;
  no canonical analyzer report or accepted scientific answer exists
- Question: For the two deterministic candidates preregistered by `EXP-0081`,
  do all three replicas pass the fixed read-only DAO endpoint frontiers without
  changing while the fresh DAO Alpha controls validate the endpoint method?
- Origin: project-authored clean-room experiment at preregistration commit
  `787ee7d374fec2837cdf6ef46568cc2782acdd75`, using plan SHA-256
  `7b2b5aa51477495ed1109279e4988fb6428d9712a999ea7aeb3462d2f46dd7ac`.
  The user authorized local VM use. Run ID
  `20260902T015852Z-dev-dao` was dispatched exactly once; no retry occurred.
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558;
  culture/UI culture `en-US`; ANSI code page 1252; VM time zone `Pacific
  Standard Time` at UTC-07:00. The provider probe reported `ready` for x86
  `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version 03.60.9765.0,
  SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
  External `environment.json` is 4,277 bytes, SHA-256
  `5b797a3b1bac11bc288dd2a78210f5c68dfe943c0130c2c5b9a18f871aed081b`.
- Protocol result: the guest producer returned `pass`: all three replicas
  completed one empty candidate, one Alpha candidate, and one fresh DAO Alpha
  control, and published exactly nine MDBs. Every candidate retained its
  preregistered size and SHA-256 before and after DAO access. Every control was
  47,104 bytes, passed all eight Alpha endpoints, and retained its own digest
  before and after access.
- Raw candidate observation: in every replica both candidates failed at the
  first endpoint with no completed frontier. DAO reported `Unrecognized
  database format` for each guest-local path. The empty candidate remained
  SHA-256
  `f762dbc12d80eb3fb5dae53fb58696219d48b7fa1a15d5deb5c1f9333d8862d6`;
  the Alpha candidate remained SHA-256
  `8552db1c7d0083429fcbbcf4dd59a5f1d8f36383c8bdef4d9decc06247cf77ca`.
  This raw producer observation is not an accepted analyzer answer.
- Control observation: replicas 1--3 passed `open_database`, `version`,
  `tabledefs`, `direct_lookup`, `field`, `properties`, `snapshot`, and
  `document`. Their unchanged SHA-256 identities were respectively
  `e8565a35e9d5b5efb0956efc2e240dfe48f597f62d42a892b55cfbdf5c90be30`,
  `a26f9dae2cccbe7b19b4daed4e8b2987c6ce84f7bbb65eb93de0689d3596a2b3`,
  and `06625e1ff4d1b3b2a4a10cf04f5b59ec9cf8842a3c734bedd15305f7efd4e831`.
- Validation rejection: the pinned analyzer rejected the first control's
  bounded `table_properties` sequence because it imposed Python code-point
  ordering, while the pinned Windows producer used PowerShell `Sort-Object`.
  The plan required bounded property enumeration, not that cross-runtime sort
  equivalence. No canonical report was written. Additionally, raw COM details
  embedded replica-specific database paths, so a successor must normalize the
  diagnostic path before applying detail-agreement rules rather than reanalyze
  this result as accepted.
- Artifacts: external
  `bootstrap-composer-validation-job-result.json`, 68,164 bytes, SHA-256
  `2d83c0395cd6eda18e93f4c4c5f30382f82a10486006bfb5aacf23134c37202f`;
  external `result.json`, 78,815 bytes, SHA-256
  `654b10cd08d26020c3364a81a49cdf7311187eed57268dd78572ece5a3f44401`;
  nine external MDBs with the identities above. No MDB or provider binary is
  committed.
- Interpretation: status is `validation_rejected`. The retained producer JSON
  motivates a corrected successor but does not establish an accepted negative
  answer, composer correctness, DAO compatibility, public creation, safe
  publication, arbitrary schema support, a support result, or support-matrix
  movement. Because DAO mutation occurred, any redispatch requires a new human
  decision even within the otherwise authorized VM session.
- Usage: issue `#100`; `EXP-0081`; future separately preregistered successor;
  `file:oracle/windows-dao/acquisition/bootstrap-composer-validation.plan.json`;
  `file:oracle/windows-dao/scripts/bootstrap_composer_validation.py`
- Rights: project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: independent outcome, artifact-identity, rejection,
  environment-identity, evidence-boundary, and non-reinterpretation review
  completed with no remaining findings

### EXP-0083 — Preregistered normalized bootstrap-composer validation

- Recorded: 2026-09-01, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration successor;
  no provider acquisition has occurred under this successor plan
- Question: the candidates, DAO endpoint frontiers, controls, bounds, decision
  rules, and excluded claims remain exactly those fixed by `EXP-0080` and
  carried through `EXP-0081`.
- Origin: `EXP-0082` completed its producer once but was validation-rejected
  without a canonical report. The rejection exposed two method defects: an
  analyzer-only cross-runtime property-order constraint absent from the plan,
  and replica-specific database paths inside otherwise equivalent bounded COM
  diagnostic details.
- Protocol correction: property sequences are accepted in their bounded
  producer order while requiring nonempty unique names and bounded integer
  types. The producer replaces only the exact current database path in an
  endpoint exception with `<DATABASE>` before bounding the detail. Endpoint
  status, frontier, normalized detail, and snapshot must still agree across
  candidate replicas. No DAO endpoint, candidate byte, control mutation, or
  scientific decision rule changes.
- Preregistration artifact: the existing
  `oracle/windows-dao/acquisition/bootstrap-composer-validation.plan.json` is
  replaced in place and now has SHA-256
  `2a817c1abd4008818057df867732b4fce733e6d43dfbbaa7d1155c767aa64e29`.
  The corrected dedicated producer is pinned at SHA-256
  `ebae76a459ffa2690ee1e2a507cc169753ee9ec8fd5ac5b3dcc0187ba2baa9ef`;
  the corrected analyzer is pinned at SHA-256
  `98c33992e9459d05023fee55eb981e480ade3d71ff7c6e3c3e88072ef44cc26a`.
  All other input, source-manifest, candidate, and transport pins remain those
  reviewed for `EXP-0081`.
- Observation: `preregistration.acquisition_started` is `false`. Because the
  `EXP-0082` run mutated DAO controls, the user's earlier session-wide VM
  authorization is not by itself a redispatch decision under the project
  protocol. A new explicit human decision is required after this successor is
  reviewed and committed. At most one successor dispatch is permitted, with no
  retry after its first DAO mutation.
- Decision rule: unchanged. With unchanged passing controls, three identical
  complete candidate frontiers are `observed_accepted`, and three identical
  partial frontiers are `not_observed_accepted`. Candidate change, control
  failure/change, disagreement, or incomplete scientific execution is
  `no_outcome`; pin, inventory, bound, malformed-shape, or integrity defects
  reject without a canonical report.
- Interpretation: this corrects validation mechanics only. It does not
  reinterpret `EXP-0082`, establish composer correctness or DAO compatibility,
  introduce a public creation or publication API, support arbitrary schemas,
  or move any support claim or matrix.
- Usage: future successor result; issue `#100`; `EXP-0082`;
  `file:oracle/windows-dao/acquisition/bootstrap-composer-validation.plan.json`;
  `file:oracle/windows-dao/scripts/dev/BootstrapComposerValidation.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/bootstrap_composer_validation.py`
- Rights: future project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: two isolated review passes across outcome/protocol, Windows producer,
  and analyzer/test integrity completed; all findings were fixed and the final
  reviews reported no remaining findings

### EXP-0084 — Preregistered page-zero bootstrap hypothesis validation

- Recorded: 2026-09-01, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration successor;
  no provider acquisition has occurred under this successor plan
- Question: do the fixed empty and `Alpha(Id Long)` candidates containing the
  new opaque page-zero hypothesis pass the same bounded DAO endpoint frontiers
  fixed by `EXP-0080`, with the corrected validation mechanics from
  `EXP-0083`?
- Origin: project-authored clean-room successor for issue `#100`. Its candidate
  hypothesis uses a post hoc, read-only comparison of project-generated
  artifacts retained externally from accepted `EXP-0079`; that comparison is
  design input rather than admitted evidence. `EXP-0083` was not acquired and
  is superseded before dispatch.
- Environment: no provider execution has occurred under this entry. The
  retained design input has the local Windows/DAO environment recorded by
  `EXP-0079`; any future one-run environment must be captured independently by
  the pinned development protocol.
- Protocol: generate the two candidates from the fully pinned Rust source
  manifest, verify their exact identities, and execute at most one three-replica
  run through
  `oracle/windows-dao/acquisition/bootstrap-composer-validation.plan.json`.
  The plan retains the corrected analyzer/diagnostic mechanics from `EXP-0083`
  and the endpoints, controls, bounds, decision rules, and excluded claims from
  `EXP-0080`.
- Design input: a post hoc, read-only comparison of the three empty and three
  Alpha MDBs retained externally from accepted run
  `20260902T011637Z-dev-dao` found byte-identical page-zero images within each
  state and only byte 1538 differing across each empty/Alpha pair. This
  inspection was not preregistered by `EXP-0079`; it is hypothesis design input
  only, not admitted format evidence, a reinterpretation of `EXP-0079` or
  `EXP-0082`, or a compatibility result. No provider acquisition or DAO
  operation occurred during the inspection.
- Candidate hypothesis: page-zero byte 1, fixed opaque bytes 24--149, and fixed
  bytes 1536--2047 are reproduced only for these two bootstrap candidates;
  byte 1538 retains the separately established empty/Alpha values. Global-map
  page 1 owns itself at `[4,8)`. Fixed catalog rows carry opaque `Owner` value
  `0203`, except `MSysDb` and `Alpha` carry `0301`. These are scoped candidate
  constants/ranges, not a general page-zero, owner, SID, or catalog grammar;
  variable dates and inactive definition/data-page padding remain excluded.
- Supersession: `EXP-0083` was never acquired and is superseded because the
  exact candidate hypothesis changed before dispatch. The same corrected
  property-order and diagnostic-path validation mechanics are retained.
- Artifacts: the existing
  `oracle/windows-dao/acquisition/bootstrap-composer-validation.plan.json` is
  replaced in place and has SHA-256
  `11fd306504782f6403f6fa44d6ec805bb1385b5725f1dad2dfc841c3863634ec`.
  It pins source manifest SHA-256
  `d951a4826c36a4a63f343d35d417288b3650ce9a427efce281d602dd1f014272`,
  producer SHA-256
  `92685419f5e61b40297c28a893df4e79989b2d6ea2d72952cbe516b6ff1784d2`,
  and analyzer SHA-256
  `1bd7cf09bac1ba088bffc76e5e1da935ed5a6915c283bc14fefbd3f9f5cda7c3`.
  The 40,960-byte empty candidate is SHA-256
  `8fad368409747adadf47704074a77e79e0bd0c5eae656566bdc72a5876f479e7`;
  the 47,104-byte Alpha candidate is SHA-256
  `b798de9209637361245703b0132f59c06dd7cb3d051d214415d6ed6a76768df2`.
- Observation: `preregistration.acquisition_started` is `false`. Because the
  `EXP-0082` run mutated DAO controls, the user's earlier session-wide VM
  authorization is not a redispatch decision. A new explicit human decision
  is required after this successor is reviewed and committed. At most one
  successor dispatch is permitted, with no retry after its first DAO mutation.
- Decision rule: unchanged. With unchanged passing controls, three identical
  complete candidate frontiers are `observed_accepted`, and three identical
  partial frontiers are `not_observed_accepted`. Candidate change, control
  failure/change, disagreement, or incomplete scientific execution is
  `no_outcome`; pin, inventory, bound, malformed-shape, or integrity defects
  reject without a canonical report.
- Interpretation: a future accepted result applies only to these exact
  candidates and endpoints. This preregistration does not establish composer
  correctness or DAO compatibility, introduce a public creation or publication
  API, support arbitrary schemas, establish page-zero grammar, or move a
  support claim or matrix.
- Usage: future successor result; issue `#100`; `EXP-0079`; `EXP-0082`;
  `EXP-0083`; `file:crates/jet3/src/bootstrap_composer.rs`;
  `file:oracle/windows-dao/acquisition/bootstrap-composer-validation.plan.json`
- Rights: retained and future project-generated MDBs remain outside the
  repository and are neither committed nor redistributed
- Review: 2026-09-01 isolated format, protocol, and retained-artifact reviews;
  all findings were fixed before commit, and the final three reviews reported
  no remaining findings

### EXP-0085 — Accepted bounded bootstrap-composer DAO validation result

- Recorded: 2026-09-01, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO accepted result
  derived from a canonical analyzer report
- Question: do all three copies of the exact EXP-0084 20-page empty and
  23-page `Alpha(Id Long)` candidates pass their complete bounded read-only DAO
  endpoint frontiers unchanged while three fresh DAO Alpha controls validate
  the method?
- Origin: project-authored clean-room experiment at preregistration commit
  `7d4b09e3fae4ccd51d7269e4cf2ece8775fc0974`, using exactly the EXP-0084
  evidence boundary. After that commit, the user explicitly authorized one
  three-replica redispatch. The run was dispatched once and was not retried.
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558; .NET
  4.0.30319.42000; culture/UI culture `en-US`; ANSI code page 1252; OEM code
  page 437; `Pacific Standard Time` at UTC-07:00. The provider probe reported
  `ready` for x86 `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
- Protocol: run ID `20260902T024641Z-dev-dao` executed the committed plan
  SHA-256
  `11fd306504782f6403f6fa44d6ec805bb1385b5725f1dad2dfc841c3863634ec`
  once. Each replica copied and read-only opened one pinned empty candidate and
  one pinned Alpha candidate, created one fresh DAO Jet 3 Alpha control, ran
  the fixed bounded endpoint frontiers, and checked each database identity
  before and after access. The producer completed all three replicas once
  without retry; the pinned analyzer then applied the preregistered decision
  rule.
- Artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `b6c52424eaf03dc1770a97b88980f48c5c13cfaaa34db91ead2a7728f161d258`;
  external `bootstrap-composer-validation-job-result.json`, 126,826 bytes,
  SHA-256
  `0b94521fdffac7d0a6f12953d7e914dd039a27a356b91d9e88d729643f234e6d`;
  external `result.json`, 145,352 bytes, SHA-256
  `140ca255320fcb351788f894e8b0c9df28ee7a43b9fad393aa2f407096de731f`;
  external canonical `bootstrap-composer-validation-report.json`, 5,624 bytes,
  SHA-256
  `dcf0217e1f98d044631107bb882d1c7a0c0096aa0fab7bd3380a760a286a9577`,
  reproduced byte-identically by rerunning the pinned analyzer. The exact
  external MDB inventory was three 40,960-byte empty candidates, each SHA-256
  `8fad368409747adadf47704074a77e79e0bd0c5eae656566bdc72a5876f479e7`;
  three 47,104-byte Alpha candidates, each SHA-256
  `b798de9209637361245703b0132f59c06dd7cb3d051d214415d6ed6a76768df2`;
  and three 47,104-byte controls with respective SHA-256 identities
  `fd1ac9045e2c1db459013988c9cb7c2346c6f6a74d96073b5746412b371c09c3`,
  `4ba15ea3a3da2e6e060e5283c8573a022f2c60612022451e8e8ea8c7a6390c74`,
  and `54c9f2a527938f21dfab593d7bc81446a301765a2230d35d32903111d4918327`.
- Observation: the producer and all three replicas returned `pass`. Every
  control passed all eight Alpha endpoints unchanged. Every empty candidate
  passed `open_database`, `version`, `tabledefs`, and `documents` unchanged.
  Every Alpha candidate passed `open_database`, `version`, `tabledefs`,
  `direct_lookup`, `field`, `properties`, `snapshot`, and `document`
  unchanged; observations agreed across replicas, `Field.Required` was false,
  and no metadata was repaired. The canonical report has status `accepted`,
  both questions `observed_accepted`, `compatibility_claim: false`, and
  `support_movement: false`.
- Interpretation: DAO 3.6 observedly consumed these exact pinned empty and
  Alpha candidate bytes at every preregistered bounded read-only endpoint in
  all three replicas without changing them. This is a bounded structural
  consumption result for these two images only. It does not establish overall
  composer or writer correctness, general Jet 3 or DAO compatibility, a
  page-zero/owner/SID/catalog grammar, arbitrary schemas/indexes/relationships
  or initial rows, a public creation API, filesystem publication safety, a
  hosted differential/support result, or support-matrix movement.
- Usage: issue `#100`; acceptance gate for the exact crate-private empty and
  `Alpha(Id Long)` composer slice; `EXP-0084`. No production format constant
  cites this result.
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: 2026-09-01 independent single-dispatch, artifact-identity,
  job/result binding, report-reproduction, decision-rule, environment,
  evidence-boundary, candidate non-mutation, and false-claim review; all three
  isolated final reviews reported no findings


### EXP-0086 — Preregistered schema-generalization experiment

- Recorded: 2026-09-01, Claude Fable 5.1
- Kind: SHA-256-pinned, development-only local DAO preregistration; no
  acquisition has occurred under this plan
- Question: across three fresh replicas, what is the exact catalog name-key
  encoding, which catalog and access-control rows and appended pages does each
  of four distinct table creates produce, and how do the resulting long-value
  property payloads decompose under one pinned chunk framing?
- Origin: project-authored clean-room successor for issue `#100`, using only
  `EXP-0058`, `EXP-0060`, `EXP-0061`, `EXP-0062`, `EXP-0073`, `EXP-0077`,
  `EXP-0079`, and `EXP-0085` as recorded above. Retained raw artifacts and
  post-hoc observations from earlier experiments are analyzer design inputs
  only and are not admitted evidence for this experiment.
- Motivation: the crate-private composer accepted by `EXP-0085` encodes fixed
  observed bytes for exactly one empty-to-`Alpha(Id Long)` transition. A typed
  planner for arbitrary tables, columns, and indexes cannot derive a new
  table's index key, catalog and access-control rows, page assignment, or
  property payload from those fixed values, so this experiment resolves each
  of them before any planner code is written.
- Protocol: create three independent pairs of fresh Jet 3 databases. In the
  first, capture closed checkpoints `empty`, then after adding `Alpha` with one
  Long field, `Beta` with Long, Text(50), and Memo fields, `Gamma` with a
  primary unique Long index, and `Delta` with a non-primary Text index. In the
  second, attempt each preregistered probed table name once and capture the
  closed `names` checkpoint. Probed names cover CP1252 bytes 0x20-0x7E and
  0xA0-0xFF except the five Access rejects, grouped so no name mixes the two
  ranges and built from code points so the script encoding cannot affect the
  observation, in both a forward and a reversed ordering so a context-free byte
  map is distinguishable from a position-dependent one. A name DAO rejects is
  recorded once and never retried. Close and release DAO before every copy,
  record SHA-256 before and after the bounded read-only metadata access, and
  retain all eighteen MDBs externally.
- Design input: before this plan was pinned, its producer and analyzer were
  exercised end to end against throwaway databases on the same VM through the
  ad-hoc development-only helper, outside this plan's harness path. That
  exercise corrected two producer defects, name collisions under the provider's
  case- and accent-folding comparison and groups that straddled the two probed
  ranges, and replaced a too-narrow key-framing hypothesis with the primary
  weight and secondary nibble sections pinned here. Those observations are
  design input only. They are not admitted evidence, no value from them appears
  in any answer, and the canonical result comes solely from the single
  authorized run of this plan.
- Preregistration artifact:
  `oracle/windows-dao/acquisition/schema-generalization.plan.json`, SHA-256
  `75742ec80e011791c1961c08889fc00f75ae36fd3f3b2a60402694e42d2a5bb9`.
  It pins the host client, provider probe, guest runner, dispatcher, publisher,
  producer SHA-256
  `188340bf1fed58d3ef7ed6b7180fe4fdafd0f4cf14cbfbfcb0c9433d8f1fd5c1`,
  analyzer SHA-256
  `add7667b20d47537d6255df22be42f27d8100b6f43b80bb0b2fb71d049249af7`,
  and the analyzer dependency. Host and guest reject pinned producer mismatches
  before mutation; the host rechecks every staged analysis input immediately
  before evaluating the result. The analyzer independently rebuilds the probed
  name inventory from the pinned rules and rejects any run that did not attempt
  exactly those names. It also requires each probe outcome to agree with the
  captured catalog, requires every appended page to carry a decoded role, and
  requires each of the four creates to have produced its preregistered table
  and schema in both the decoded catalog and the captured DAO snapshot.
- Observation: `preregistration.acquisition_started` is `false`. Committing
  this plan does not authorize acquisition. After the exact reviewed plan and
  inputs are committed, one explicit human authorization permits one local-VM
  acquisition. There is no automatic retry after the first DAO mutation.
- Decision rule: each question is answered only when its structural decode and
  correlations succeed and the complete observation agrees across all three
  replicas. Metadata repair, post-mutation producer failure including a replica
  that stopped before or during the probe phase, decode failure, a create that
  did not produce its preregistered schema, a branched index root, or replica
  disagreement is an honest `no_outcome`;
  pre-mutation, plan, input, digest, bound, checkpoint, inventory, or
  result-shape defects reject or abort without a scientific outcome.
- Interpretation: this entry fixes only an acquisition and analysis contract.
  It establishes no name-key encoding, collation map, catalog or
  access-control row pattern, page-allocation policy, property grammar beyond
  the pinned framing, Rust writer correctness, compatibility, support result,
  or support-matrix movement. It deliberately derives no map, expansion rule,
  or secondary weight assignment for name bytes above 0x7E; a planner slice
  built on an accepted result must reject those name bytes with a structured
  error until a separate experiment resolves them.
- Usage: future successor result; issue `#100`; `EXP-0085`;
  `file:oracle/windows-dao/acquisition/schema-generalization.plan.json`;
  `file:oracle/windows-dao/scripts/dev/SchemaGeneralization.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/schema_generalization.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: 2026-09-01 protocol, VM wiring, and analyzer review over three
  rounds; findings on probe-inventory enforcement, both key images,
  appended-page attribution, probe/catalog correlation, post-mutation partial
  failure handling, and fixed-create outcome correlation were resolved before
  authorization


### EXP-0087 — Accepted schema-generalization DAO result

- Recorded: 2026-09-02, Claude Opus 5
- Kind: validated SHA-256-pinned, development-only local DAO accepted result
  derived from a canonical analyzer report
- Question: the six preregistered `EXP-0086` questions on `ParentId`/`Name`
  key framing, ASCII name collation, extended name keys, catalog and
  access-control rows, page-zero and page assignment, and long-value property
  framing.
- Origin: project-authored clean-room experiment using exactly the `EXP-0086`
  evidence boundary. The plan and every pinned input were committed to branch
  `test/schema-generalization-preregistration` at
  `840fcdeba7fa0bb548637585f8d5f08e55db7a28` before acquisition; those exact
  bytes reached `main` as squash commit
  `7194b0fa11b92c1a22d2e05b761143c1c7294045` (PR `#144`) and are identified
  here by their SHA-256 pins. After the preregistration commit the user
  explicitly authorized one acquisition. The run was dispatched once and was
  not retried.
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558; .NET
  4.0.30319.42000; culture/UI culture `en-US`; ANSI code page 1252; OEM code
  page 437; `Pacific Standard Time` at UTC-07:00. The provider probe reported
  `ready` for x86 `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
- Protocol: run ID `20260902T035711Z-dev-dao` executed the committed plan
  SHA-256
  `75742ec80e011791c1961c08889fc00f75ae36fd3f3b2a60402694e42d2a5bb9`
  once. Each of three replicas created two fresh `dbVersion30` CP1252
  databases: one captured closed at the `empty`, `alpha`, `beta`, `gamma`, and
  `delta` checkpoints, and one at `names` after attempting the 24 preregistered
  probed table names. The pinned analyzer decoded every checkpoint, rebuilt the
  probe inventory independently, correlated it with the captured catalog,
  correlated each fixed create against both the decoded catalog and the bounded
  DAO `TableDefs` snapshot, and applied the preregistered decision rule.
- Artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `93bf1b5704b38f6aa4b052b4904cf54783bf41859e38b212b094fbb7a162e963`;
  external `schema-generalization-job-result.json`, 368,464 bytes, SHA-256
  `56838c6a4f2fe4b791829a64743c5c4015f61c5d578390056ff1d6f8dfdec455`;
  external `result.json`, 467,118 bytes, SHA-256
  `e392cd8bb95f75abfc4df2ff598bb41edd612d250c2a49084a405c38378784b4`;
  external canonical `schema-generalization-report.json`, 32,978 bytes, SHA-256
  `306501a7fd93f29e7e00df3223608017c97b706f573175a6f58a6ddb06ea021b`,
  reproduced byte-identically by rerunning the pinned analyzer. The exact
  external MDB inventory was eighteen databases, all retained outside the
  repository: three 40,960-byte `empty`
  (`ff38fd466998c92f52c1b45499d84d6eb8c525df22e18766feda757380490996`,
  `7980ce46bd22fd8e60a14e1ee06e5930e2c371ce0db7d441505c0262b3c169ab`,
  `11979f5946de747c61afa2664f07445e1fd8fc5068faa7b472ab531fbc80e0cd`),
  three 47,104-byte `alpha`
  (`b6e7bf7f780bf724afcbbef96e96f5fc413bdb44fe84e0a351305e35ecefe866`,
  `4977d084f98534132c95f901f24971eef5413ec1053bf26a1704b5f0942ff932`,
  `7425856a64b6fad7ddb02756662d0ca7e1bad45d918b955a89a6059fbfc118bc`),
  three 51,200-byte `beta`
  (`d18f0af09a9fcfdb35df6c46da31dcfac0d6fd3c9e7834b2422103864fdd4802`,
  `2354f9975032fbaba97d19c2320520966605058e7da506564ff3e10a3e362825`,
  `18abd6a5fa980ade86155a018c136766b117f561c87653e02ad99d910520df05`),
  three 57,344-byte `gamma`
  (`e4943a1626b3be1e5d9b2d080b4528bef9e9ced42f1b2f16985511f5dace0a11`,
  `e891bf1858900e00b11f25708ae9c5c1939eb1e98c0bb6a07e31effc0fa39f44`,
  `2f62521af54eb5dfaad8883b851bbcb1bdcfe358eb92ea42aea0f2ece35782ed`),
  three 63,488-byte `delta`
  (`a917c084177353b7800930d0c89492d6b321ea551ff8e0c77ee6b811ff369076`,
  `87ab7217b452a402f1b35c5ccd58a38890e62d7df3a073c49dfa6fe3d891da3a`,
  `d0dc0f97097475b08d7d5034f2f54c86cc8ef7643b7dee69aa0653dd916c022b`),
  and three 143,360-byte `names`
  (`71dc5b2aa1b2e057658e9acff35054554b468ddffc911e87ee91f7d8a8b0200c`,
  `2893eb3418c4f7d80ebb956429e7d82beb2207b1625e552f73542a3fc619aa34`,
  `369cb6e8b196920f61206128444c87de493c8af73a45f320a5d30991d8a6317c`).
- Observation: the producer returned `pass` for all three replicas without
  retry, DAO accepted all 24 probed names, and the canonical report has status
  `accepted` with all six questions `answered`,
  `compatibility_claim: false`, and `support_movement: false`. Every
  observation below was byte-identical across all three fresh replicas.
  - Key framing: the `MSysObjects` `ParentId`/`Name` root was a single leaf in
    every checkpoint, holding exactly one losslessly reconstructed key per
    decoded catalog row, each correlating to exactly one row. Every key is the
    `EXP-0062` non-null Long encoding of `ParentId`, then `0x7f`, then primary
    weight bytes none of which has a zero high nibble, then a nibble stream of
    a leading zero nibble, zero or more secondary nibbles, a terminating zero
    nibble, and zero padding to the byte boundary. Twelve keys were decoded in
    the four-table image and thirty-two in the probed-name image.
  - ASCII collation: one context-free CP1252 byte-to-primary-weight map
    explains every observed name whose bytes are all at most `0x7E`, at every
    position, in both orderings and in all three replicas, with an empty
    secondary section. All 90 probed ASCII bytes are mapped, none is unmapped,
    and DAO rejected none of the probed names. Observed map, source byte to
    weight byte:

        20=11 22=13 23=14 24=15 25=16 26=17 27=18 28=19 29=1a 2a=1b 2b=1c
        2c=1d 2d=1e 2f=20 30=56 31=57 32=58 33=59 34=5a 35=5b 36=5c 37=5d
        38=5e 39=5f 3a=21 3b=22 3c=23 3d=24 3e=25 3f=26 40=27 41=60 42=61
        43=62 44=64 45=66 46=67 47=68 48=69 49=6a 4a=6b 4b=6c 4c=6d 4d=6f
        4e=70 4f=72 50=73 51=74 52=75 53=76 54=77 55=78 56=7a 57=7b 58=7c
        59=7d 5a=7e 5c=29 5e=2b 5f=2c 61=60 62=61 63=62 64=64 65=66 66=67
        67=68 68=69 69=6a 6a=6b 6b=6c 6c=6d 6d=6f 6e=70 6f=72 70=73 71=74
        72=75 73=76 74=77 75=78 76=7a 77=7b 78=7c 79=7d 7a=7e 7b=2e 7c=2f
        7d=30 7e=31

    Upper- and lowercase letters share one weight, so case is not recoverable
    from a key. The map is not injective and no inverse is claimed.
  - Extended name keys: the twelve names containing a byte above `0x7E` were
    recorded losslessly and agreed across replicas. Names over `0xA0`–`0xBF`
    carried one primary weight per byte and an empty secondary section; names
    over `0xC0`–`0xFF` carried fewer primary weights than source bytes and a
    non-empty secondary nibble stream whose order tracks the name's byte order.
    No map, expansion rule, or secondary assignment is derived from them.
  - Catalog and access-control rows: each of the four creates produced exactly
    its preregistered table and schema in both the decoded catalog and the DAO
    `TableDefs` snapshot, and added exactly one `MSysObjects` row and two
    `MSysACEs` rows, removing none. The added object row carried `Type` 1,
    `Flags` 0, `Owner` `0301`, `ParentId` `0x0F000001`, a non-null `LvProp`
    long-value reference, and null in every remaining column. The two added ACE
    rows carried the created object's `Id` with `SID` `0301`/`ACM` 983294 and
    `SID` `0201`/`ACM` 1048319, both non-inheritable.
  - Page zero and page assignment: each create changed exactly one page-zero
    offset, byte 1538, advancing it 0, 2, 4, 6, 8 across the four creates. Each
    create appended a `definition_root` page numbered equal to the new object's
    `Id`, then a `map_rows` page; a create carrying an index appended an
    `index_root` page after it, and the first create in a database additionally
    appended a `long_value` page. The `EXP-0073` role decoder attributed every
    appended page to a decoded structure and left none unassigned.
  - Long-value property framing: every non-null catalog `LvProp` value was one
    `EXP-0061` single-page external header naming one in-bounds unflagged
    `LVAL` row, and every payload began with `4b 4b 44 00` and was exactly
    covered by chunks of a four-byte little-endian inclusive length and a
    two-byte little-endian kind. Each payload held exactly one leading kind
    `0x0080` chunk whose body was exactly covered by two-byte length-prefixed
    name entries, followed by one further chunk per column. All other chunk
    bytes remain lossless and uninterpreted.
- Interpretation: this is a bounded structural observation of what DAO 3.6
  produced for these exact scenarios in this exact environment. Within that
  boundary it establishes the `ParentId`/`Name` key framing, the ASCII primary
  weight map, the per-create catalog/ACE row pattern, the page-zero counter
  step, and the appended-page assignment order that a typed schema planner
  needs. It does not establish writer correctness, DAO acceptance of any
  project-composed bytes, compatibility, a hosted differential or support
  result, or support-matrix movement; only a later DAO differential over
  composed candidates can. It deliberately derives no weight, expansion rule,
  or secondary assignment for name bytes above `0x7E`, no `MSysObjects` `Id`
  allocation rule beyond the observed equality with the definition root page,
  no rule for page-zero byte 1538 beyond the observed step, and no property
  grammar beyond the pinned framing. A planner slice built on this result must
  reject name bytes above `0x7E` with a structured error until a separate
  experiment resolves them.
- Usage: issue `#100`; `EXP-0086`; `EXP-0062`; `EXP-0073`; `EXP-0079`
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: three independent outcome, artifact-identity, report-reproduction,
  decision-rule, evidence-boundary, and false-claim review rounds; findings on
  analyzer verification scope and the direct payload-necessity claim were
  resolved, and final audits reported no remaining findings


### EXP-0088 — Preregistered null-LvProp acceptance experiment

- Recorded: 2026-09-02, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no
  acquisition has occurred under this plan
- Question: across three fresh replicas, does DAO 3.6 consume unchanged an
  otherwise fixed `Alpha(Id Long)` composer image whose catalog `LvProp` is
  physically null, while the observed first-create page layout and map
  references are retained and page 22 is an empty `LVAL` page?
- Origin: project-authored clean-room successor for issue `#149`, using only
  `EXP-0061`, `EXP-0085`, and `EXP-0087` as recorded above. The exact fixed
  candidate is the `EXP-0085` image; the null candidate is a preregistered
  discriminator and not an admitted format fact.
- Motivation: `EXP-0087` observed a non-null property payload on every created
  table but did not derive its grammar. This experiment tests the cheapest
  bounded alternative needed by the composer: whether a null catalog value is
  accepted without also guessing that the observed first-create long-value
  page or its map ownership may be omitted.
- Protocol: before any DAO mutation, generate, stage, and identity-check three
  copies apiece of the fixed and null candidates. For each of three replicas,
  create and close one fresh DAO Jet 3 `Alpha(Id Long)` control, then read the
  fixed candidate, null candidate, and fresh control through the same ordered,
  bounded endpoints. Candidate databases are opened read-only and hashed
  immediately before and after access. The endpoint set covers database
  version, exact table inventory and name lookup, exact `Id` field lookup and
  bounded sorted property enumeration, an empty snapshot, and exact container
  document lookup and inventory. Retain up to nine MDBs externally, exactly
  nine for a passing job.
- Preregistration artifacts:
  `oracle/windows-dao/acquisition/lvprop-null.plan.json`, SHA-256
  `36c2b86a317297f408f606131fa0f58d7ea305ad7daedd0ec7a31eb17cd513e3`,
  and `oracle/windows-dao/acquisition/lvprop-null.sources.json`, SHA-256
  `3493b9937a53daa5c38d409c7af9ebe4694ba609b26b66259f7dac7214b67d76`.
  The plan pins every host and guest input, including producer SHA-256
  `952066d689df5daa87723826912c95707357910f4a6cca450471648ac3755236`
  and analyzer SHA-256
  `a277cc1e0ed777aca0a3e95830740bcdc83de5c6771169ef4585f778aeef192c`.
  It also pins the 47,104-byte fixed candidate as
  `b798de9209637361245703b0132f59c06dd7cb3d051d214415d6ed6a76768df2`
  and the 47,104-byte null candidate as
  `c9d012d6277a0a35ae4248581fc9458d9b270e56277819e84dc7f1f5e8009e21`.
  Focused Rust tests bind their structural differences and reproduce both
  identities from the pinned source inventory.
- Observation: `preregistration.acquisition_started` is `false`. The user has
  explicitly authorized experiments and merging, which permits one dispatch
  only after these exact reviewed bytes reach `main` and the checked client
  verifies every pin. There is no automatic retry after the first DAO control
  mutation; a later failure is a scientific result.
- Decision rule: the fixed candidate must be `observed_accepted` in all three
  replicas. The null candidate is answered either when all three replicas pass
  unchanged and match their same-replica fresh controls, or when all three
  stop identically at one endpoint or complete unchanged with the same stable
  semantic mismatch. A fresh or fixed control failure, mutation, replica
  disagreement, incomplete scientific job, or unclassifiable observation is
  an honest `no_outcome`; pin, inventory, bound, candidate-identity, or
  result-integrity defects reject without a canonical report.
- Interpretation: this entry fixes only an acquisition and analysis contract.
  It establishes no property grammar, permission to omit page 22 or its map
  references, acceptance of arbitrary schemas, indexes, relationships, or
  initial rows, writer or publication correctness, compatibility, hosted
  differential result, or support-matrix movement.
- Usage: issue `#149`; `EXP-0061`; `EXP-0085`; `EXP-0087`;
  `file:oracle/windows-dao/acquisition/lvprop-null.plan.json`;
  `file:oracle/windows-dao/acquisition/lvprop-null.sources.json`;
  `file:oracle/windows-dao/scripts/dev/LvPropNull.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/lvprop_null.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: 2026-09-02 protocol, candidate construction, VM wiring, recovery,
  publisher, analyzer, decision-rule, and evidence-boundary review over three
  rounds; findings on fixed-positive gating, candidate pre-copying, recovery
  identity, partial-job retention and bounds, exact MDB inventory, property
  ordering, and claim scope were resolved before authorization


### EXP-0089 — Validation-rejected null-LvProp DAO result

- Recorded: 2026-09-02, OpenAI Codex
- Kind: validation-rejected SHA-256-pinned, development-only local DAO result;
  no canonical analyzer report or accepted scientific answer exists
- Question: for the fixed and null-`LvProp` candidates preregistered by
  `EXP-0088`, do all three replicas pass the fixed read-only DAO endpoint
  frontier unchanged while fresh DAO Alpha controls validate the method?
- Origin: project-authored clean-room experiment using the exact `EXP-0088`
  bytes merged as commit `bb5abdc493d80df77d77e43c3fa2797900fed19e`
  (PR `#156`) and plan SHA-256
  `36c2b86a317297f408f606131fa0f58d7ea305ad7daedd0ec7a31eb17cd513e3`.
  The user authorized local experiments. Run ID
  `20260902T172334Z-dev-dao` was dispatched exactly once; no retry occurred.
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558; .NET
  4.0.30319.42000; culture/UI culture `en-US`; ANSI code page 1252; OEM code
  page 437; `Pacific Standard Time` at UTC-07:00. The provider probe reported
  `ready` for x86 `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
- Protocol result: the guest producer and wrapper returned `pass`. All three
  replicas completed the fixed candidate, null candidate, and fresh DAO Alpha
  control with no replica error, and exactly nine 47,104-byte MDBs were
  published. Every raw image reported all eight endpoints complete and
  retained the same size and SHA-256 before and after access. All nine raw
  snapshots were byte-for-byte equal. These are retained producer observations,
  not an accepted analyzer answer.
- Raw artifact identities: each fixed candidate retained SHA-256
  `b798de9209637361245703b0132f59c06dd7cb3d051d214415d6ed6a76768df2`,
  and each null candidate retained SHA-256
  `c9d012d6277a0a35ae4248581fc9458d9b270e56277819e84dc7f1f5e8009e21`.
  Fresh controls for replicas 1--3 retained SHA-256 respectively
  `61f0d87c5a4014d3cc3153620fb01fb2d119475afb159de6f1f915d93a18fa96`,
  `d2c603884a96f7b0be965261f1a84964efc3a58f81b9f76446f6e0deb7d6e0db`,
  and `39b058ced606e80b6d63c1e5eb3a14a63ee7b3e59ffbabc85dc3f6a3b0106eb9`.
- Validation rejection: the plan explicitly required sorted bounded table and
  field property collections. The producer attempted `Sort-Object -Property
  name`, but its ordered dictionaries serialized all eighteen collections in
  an unsorted order while marking the property endpoint complete. The pinned
  analyzer correctly rejected the first sequence with
  `replicas[0].images[0].endpoints.snapshot.table_properties properties are not
  sorted by name`. No `lvprop-null-report.json` was written. A successor may
  prospectively admit bounded producer order while preserving uniqueness and
  complete cross-image equality; this result must not be retroactively
  reanalyzed as accepted.
- JSON artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `2a4850ed55e624d35aeb896c65c2cde1912505a496827c991fe1da4cc24a04e0`;
  external `lvprop-null-job-result.json`, 176,260 bytes, SHA-256
  `11a4153478c7d1f899e0c445f826a1b3ea3ceeca147ff12a1cf739852f7cf3a2`;
  external `result.json`, 196,569 bytes, SHA-256
  `0cf85926816c6518a265a1e1fac8811a6dd11a4505d3f71ba73f8558c6cf7e60`.
- Interpretation: status is `validation_rejected`. The raw producer result
  motivates a corrected successor but establishes neither candidate as
  `observed_accepted`. It establishes no null-`LvProp` construction fact,
  property ordering or grammar, permission to omit page 22 or its map
  references, arbitrary schema acceptance, writer correctness, compatibility,
  public API or publication correctness, hosted differential result, or
  support-matrix movement. Because DAO mutation occurred, any redispatch
  requires a separately preregistered successor and a renewed explicit human
  decision.
- Usage: issue `#149`; `EXP-0088`; future separately preregistered successor;
  `file:oracle/windows-dao/acquisition/lvprop-null.plan.json`;
  `file:oracle/windows-dao/scripts/lvprop_null.py`
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: independent artifact-identity, protocol-result, sole-rejection,
  evidence-boundary, and non-reinterpretation review completed with no
  remaining findings


### EXP-0090 — Preregistered producer-order null-LvProp validation

- Recorded: 2026-09-02, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration successor;
  no provider acquisition has occurred under this successor plan
- Question: the exact candidates, three-replica control structure, eight
  read-only endpoint frontiers, bounds, decision rules, and excluded claims are
  those fixed by `EXP-0088`.
- Origin: `EXP-0089` completed its producer once but was validation-rejected
  without a canonical report. The producer attempted to sort bounded DAO
  property collections but serialized them in producer order. The plan
  required sorted collections, and the analyzer enforced Python case-folded
  order. The raw observations from that rejected run are method-design input
  only and are not reanalyzed or admitted as an answer.
- Protocol correction: property sequences are accepted in their bounded
  producer order while still requiring at most 64 entries, exact `name` and
  `type` fields, nonempty unique names of at most 256 characters, signed
  32-bit integer types, the `Required` property, and `Field.Required` false.
  Snapshot equality remains exact and order-sensitive. Replica disagreement,
  including disagreement among fresh controls, yields `no_outcome`; one stable
  null-candidate/control order difference is a semantic mismatch under the
  existing accepted-negative rule. The analyzer neither sorts nor normalizes
  an acquired sequence. Every other producer, analyzer, control, candidate,
  endpoint, bound, retention, and decision predicate is unchanged.
- Design diagnostic: removing only the rejected ordering predicate in memory
  allowed every other validator to consume the retained `EXP-0089` job result.
  This confirms the successor's correction is minimal but is not a canonical
  report, does not answer either question, and contributes no format fact.
- Preregistration artifact: the existing
  `oracle/windows-dao/acquisition/lvprop-null.plan.json` is replaced, not
  stacked with a revision file, and now has SHA-256
  `a7a466c9f7f3dbc27869342f5de36ac13420818b47980c12f18104335306d0a5`.
  It pins the corrected analyzer at SHA-256
  `5aa0de8e59250adc860477e7763fa6db911ad1edadbdcd952d2ce4f9b4d2f1d2`.
  The 98-file source manifest SHA-256
  `3493b9937a53daa5c38d409c7af9ebe4694ba609b26b66259f7dac7214b67d76`,
  producer SHA-256
  `952066d689df5daa87723826912c95707357910f4a6cca450471648ac3755236`,
  all other input pins, and both 47,104-byte candidate identities remain
  unchanged from `EXP-0088`.
- Observation: `preregistration.acquisition_started` remains `false` for this
  successor. Because `EXP-0089` crossed the first DAO mutation, the earlier
  general authorization does not permit redispatch. One new three-replica run
  is allowed only after these exact reviewed bytes reach `main` and a human
  explicitly authorizes the successor; the checked client must then verify
  every merged pin. No later post-mutation failure may be retried without
  another new human decision.
- Decision rule: unchanged from `EXP-0088`. The fixed candidate must be
  `observed_accepted`; the null candidate may be a consistent
  `observed_accepted` or accepted negative observation. Control failure,
  mutation, disagreement including property-sequence order, incomplete work,
  or an unclassifiable observation is an honest `no_outcome`; pin, inventory,
  bound, candidate-identity, or result-integrity defects reject without a
  canonical report.
- Interpretation: this successor changes only property-sequence validation.
  It establishes no property ordering semantics or grammar, null-`LvProp`
  construction fact, permission to omit page 22 or its map references,
  arbitrary schema acceptance, writer or publication correctness,
  compatibility, hosted differential result, or support-matrix movement.
- Usage: future successor result; issue `#149`; `EXP-0088`; `EXP-0089`;
  `file:oracle/windows-dao/acquisition/lvprop-null.plan.json`;
  `file:oracle/windows-dao/scripts/lvprop_null.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: three independent protocol-correction, exact-pin, analyzer,
  decision-rule, evidence-boundary, and non-reinterpretation review rounds;
  findings on stable order-mismatch classification, target-only replica
  disagreement, sorted-versus-casefold wording, and normalization ambiguity
  were resolved, and final audits reported no remaining findings


### EXP-0091 — Accepted null-LvProp DAO result

- Recorded: 2026-09-02, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO accepted result
  derived from a canonical analyzer report
- Question: for the exact fixed and null-`LvProp` candidates preregistered by
  `EXP-0090`, do all three replicas pass the bounded read-only DAO endpoint
  frontier unchanged and match fresh DAO Alpha controls in the exact
  producer-order observations?
- Origin: project-authored clean-room experiment using the exact `EXP-0090`
  successor bytes merged as commit
  `6035d5daa37be18708ceff6f62c1395f49930646` (PR `#158`) and plan SHA-256
  `a7a466c9f7f3dbc27869342f5de36ac13420818b47980c12f18104335306d0a5`.
  After the `EXP-0089` validation rejection and successor merge, the user
  explicitly authorized one new run. Run ID `20260902T181521Z-dev-dao` was
  dispatched exactly once and was not retried.
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558; .NET
  4.0.30319.42000; culture/UI culture `en-US`; ANSI code page 1252; OEM code
  page 437; `Pacific Standard Time` at UTC-07:00. The provider probe reported
  `ready` for x86 `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
- Protocol: the producer created three fresh DAO Jet 3 `Alpha(Id Long)`
  controls and read the three pre-copied fixed candidates, three pre-copied
  null candidates, and controls through the eight ordered endpoints. It
  returned `pass` for all three replicas without retry and published exactly
  nine 47,104-byte MDBs. The pinned analyzer rechecked the job-result contract,
  every retained MDB identity, bounds, inventories, endpoint shapes, candidate
  pins, control gates, and order-sensitive snapshot correlations before
  applying the decision rule.
- Artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `f46c7d2962510205cd14dc161b6bfeae971a66e9ab98e73ec3b8e195aa607cd1`;
  external `lvprop-null-job-result.json`, 176,260 bytes, SHA-256
  `cb9768f02c6aec89afe2163231dff47ac50818c0b85eaee695c7d8c64441236d`;
  external `result.json`, 196,569 bytes, SHA-256
  `048b67b39495ef75cf12849ee614e9b6529cb44ff2c1b6bcac8f3023291d175b`;
  external canonical `lvprop-null-report.json`, 6,921 bytes, SHA-256
  `92983fcfb16776f28e81c770774df17039bfb33cb1c949b153d60beb80371fe7`,
  reproduced byte-identically by rerunning the pinned analyzer.
- Retained MDB identities: the three fixed candidates remained SHA-256
  `b798de9209637361245703b0132f59c06dd7cb3d051d214415d6ed6a76768df2`,
  and the three null candidates remained SHA-256
  `c9d012d6277a0a35ae4248581fc9458d9b270e56277819e84dc7f1f5e8009e21`.
  Fresh controls for replicas 1--3 remained SHA-256 respectively
  `588bde05a78c3b5b19212f94f5de0ac3748e2a4bdfe7d55710d2bebcfce9e95f`,
  `bb1c3a8b7fe4a76568c551c60db30d45ad9d7305de4bddb5b8c42322af0fba83`,
  and `cb37e2c2cea23897e360603942f5b403ae11a4ac8b49524b0a86979e966dc2f6`.
  Every retained image had the same size and SHA-256 before and after DAO
  access; the report records `metadata_repaired: false` for all nine.
- Observation: the canonical report has status `accepted`, with both
  `fixed_candidate` and `null_candidate` status `observed_accepted`,
  `compatibility_claim: false`, and `support_movement: false`. In every
  replica, both candidates and the fresh control completed `open_database`,
  `version`, `tabledefs`, `direct_lookup`, `field`, `properties`, `snapshot`,
  and `document`. The candidate observations matched the fresh controls and
  agreed across replicas exactly, including producer property order.
- Interpretation: in this exact 23-page first-create construction, DAO 3.6
  consumed unchanged an empty `Alpha` table with one nullable Long field whose
  catalog `LvProp` is physically null and whose mapped page 22 is an empty
  `LVAL` page. At the bounded endpoint frontier, its observations were the same
  as the fixed payload-bearing candidate and fresh DAO-created controls. This
  establishes that the recorded Alpha property payload is not required for
  DAO's bounded read-only consumption of this exact construction. It does not
  establish a general property grammar, acceptance of null `LvProp` for
  arbitrary schemas, permission to omit page 22 or either map reference,
  property ordering semantics, writer or publication correctness, general
  compatibility, a hosted differential or support result, or support-matrix
  movement.
- Usage: issue `#149`; `EXP-0085`; `EXP-0088`; `EXP-0089`; `EXP-0090`;
  future composed-candidate differentials
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: pending independent outcome, artifact-identity, report-reproduction,
  decision-rule, evidence-boundary, and false-claim review


### EXP-0092 — Preregistered multiple-index page-assignment experiment

- Recorded: 2026-09-02, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no provider
  acquisition has occurred under this plan
- Question: across three replicas beginning from identity-checked copies of one
  fresh empty Jet 3 database apiece, what page, usage-map-row, physical-index,
  and logical-index assignments does DAO 3.6 produce when the database's first
  user table carries one index, two simple indexes, three mixed indexes, or one
  mixed-direction composite index plus a secondary index?
- Origin: project-authored clean-room experiment for issue `#150`, using the
  bounded grammars recorded by `EXP-0059`, `EXP-0061`, `EXP-0062`, and
  `EXP-0073`, and the create-time evidence in `EXP-0087` and `EXP-0091`.
  Retained earlier artifacts and post-hoc page-placement observations are design
  input only. `EXP-0087` observed user
  creates with zero or one index: definition root, map-rows page, then one index
  root. It did not observe a first create with an index or any one-shot user
  create with multiple indexes. `EXP-0073` observed three system indexes share
  one map page and use consecutive map rows and roots. Neither observation
  establishes the corresponding first-create user-table rule.
- Motivation: the planner currently refuses more than one index, and the
  composer places a first create's `LvProp` page after an index root by
  deduction rather than observation. Multiple indexes also expose two matters
  beyond page counting: DAO may order logical records and names differently
  from physical records, and the planner's primary-versus-ordinary kind cannot
  express the low-level writer's established unique non-primary flag class.
  The experiment therefore measures page placement, map-row placement,
  physical/logical ordering, and flags together rather than claiming that only
  page assignment is open.
- Controlled design: each arm creates the same empty table shape with the same
  three fixed Long fields. Table, field, and index names use established ASCII
  bytes at or below `0x7E`; corresponding names have equal encoded lengths
  across arms. Index append order and equal-length names deliberately separate
  physical creation order from logical/name order. Definitions remain within
  one root page. No arm inserts a row, declares a long-value column or
  relationship, uses an extended name byte, or needs a definition continuation.
- Arms: create one fresh empty `dbVersion30` database per replica, close it,
  retain it as `empty`, and copy and identity-check it before mutation into four
  independent first-create arms. The `one_index` arm uses one ascending index
  as the within-run count control and directly tests the previously deduced
  first-create index-root/`LvProp` order. The `two_simple` arm uses a primary
  index and an ordinary secondary. The `three_mixed` arm uses primary, unique
  non-primary, and non-unique indexes with distinguishing append/name orders
  and directions. The `composite_secondary` arm uses one unique non-primary
  mixed-direction composite index and one descending ordinary secondary. A
  passing run retains five MDBs per replica, exactly fifteen total.
- Protocol: after each arm's one `TableDefs.Append`, close DAO before copying or
  decoding the database. Hash and size every completed checkpoint before and
  after the bounded read-only DAO metadata pass; an identified recovery image
  is retained without metadata access and cannot contribute an observation.
  Capture the exact table, field, and index inventory; for every index capture
  name, `Primary`, `Unique`, `Required`, ordered fields, and descending
  attributes. Independently decode the catalog row, complete single-page table
  definition, table and index usage-map
  locators and rows, every empty index root and its owner, the first-create
  `LvProp` reference and `LVAL` page, and the role of every appended page using
  the `EXP-0073`/`EXP-0087` correlation method.
- Questions:
  - Page assignment: for each index count, which relative pages are the
    definition root, map-rows page or pages, index roots, and first-create
    `LVAL` page, and is the object identifier equal to the definition root?
  - Map assignment: do all indexes share the table's map page, which rows do
    their physical records name, and does each map row contain exactly its
    corresponding root?
  - Ordering and flags: how do index append order, physical ordinal, logical
    record/name order, primary class, unique/required flags, field order, and
    direction correlate?
  - Shape discriminator: does the equal-count `composite_secondary` arm use the
    same relative page and map assignment as `two_simple`, or does key arity,
    uniqueness, or direction change it?
- Preregistration artifacts:
  `oracle/windows-dao/acquisition/multiple-indexes.plan.json`, SHA-256
  `4832f4fe018af2ac951f9952eaa1a87766f3a3e274d7b083795c19620ae60329`;
  producer
  `oracle/windows-dao/scripts/dev/MultipleIndexes.DevJob.ps1`, SHA-256
  `a90f794521e514fb8ebd0b6a25f5e78db1eb54c6be12e10d1d2f2ee5017f21df`;
  analyzer
  `oracle/windows-dao/scripts/multiple_indexes.py`, SHA-256
  `13e1ffc001c365c2fd4b053d0c0b26eb4a4a0211a65091aa198e2dec5eee14e4`.
  The plan pins these and every host, guest, routing, publisher, and
  analyzer-dependency input.
- Observation: `preregistration.acquisition_started` is `false`. On 2026-09-02
  the user said “Go for it,” authorizing exactly one acquisition only after the
  exact pins replace the placeholders, are independently reviewed, and reach
  `main`. The checked client must verify those merged bytes before dispatch.
  Once the first DAO mutation begins, a failure is a scientific event and no
  retry is permitted without renewed explicit human authorization.
- Decision rule: each question is `answered` only when all four arms produce
  their exact declared empty schemas, all retained bytes remain unchanged,
  every reference correlates uniquely, every appended page has a decoded role,
  and the complete bounded relative observation agrees across all three
  replicas. A fully decoded, internally consistent, replicated placement that
  contradicts the motivating consecutive-page or shared-map hypothesis is an
  answered result, not `no_outcome`. Post-mutation producer failure, schema or
  metadata disagreement, changed bytes, incomplete decoding or attribution,
  ambiguous correlation, or replica disagreement is an honest `no_outcome`.
  Pre-mutation pin, input, inventory, bound, or result-integrity defects reject
  or abort without a scientific answer.
- Interpretation: this entry fixes only an acquisition and analysis contract.
  A later accepted result may establish assignments and index-definition
  ordering only for these exact empty first-create arms and at most three
  indexes. It cannot establish populated composite-key encoding, index-tree or
  row maintenance, behavior above three indexes, behavior when map rows spill
  or a map page fills, continuation-page placement, extended-name encoding,
  initial rows, arbitrary schemas, Rust writer or publication correctness,
  general Jet 3 or DAO compatibility, a hosted differential or support result,
  or support-matrix movement.
- Usage: future result for issue `#150`; `EXP-0059`; `EXP-0062`; `EXP-0073`;
  `EXP-0087`; `EXP-0091`;
  `file:oracle/windows-dao/acquisition/multiple-indexes.plan.json`;
  `file:oracle/windows-dao/scripts/dev/MultipleIndexes.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/multiple_indexes.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: three independent protocol, producer, publisher, analyzer,
  page-role-correlation, ordering, failure-recovery, decision-rule,
  evidence-boundary, and false-claim review rounds completed on 2026-09-02.
  Findings covering mutation classification, retained-artifact recovery,
  metadata identity, `LvProp` correlation, empty-root validation, failed-phase
  consistency, and continuation-page scope were fixed; the final exact-head
  reviews reported no remaining acquisition blocker.


### EXP-0093 — Accepted multiple-index page-assignment DAO result

- Recorded: 2026-09-02, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO accepted result
  derived from a canonical analyzer report
- Question: for the exact four empty first-create arms preregistered by
  `EXP-0092`, what page, usage-map-row, physical-index, logical-index, flag,
  key, and direction assignments does DAO 3.6 produce across three replicas?
- Origin: project-authored clean-room experiment using the exact `EXP-0092`
  bytes merged as commit
  `ef6eb855d2c665d0abc09be2e0f47d6ebe0d90d7` (PR `#160`) and plan SHA-256
  `4832f4fe018af2ac951f9952eaa1a87766f3a3e274d7b083795c19620ae60329`.
  After the exact pins were independently reviewed and merged, the user's
  2026-09-02 “Go for it” authorized one acquisition. Run ID
  `20260902T191841Z-dev-dao` was dispatched once and was not retried. This
  dispatch count is the observed operator action, not a cryptographic claim
  derived from the retained artifacts.
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558; .NET
  4.0.30319.42000; culture/UI culture `en-US`; ANSI code page 1252; OEM code
  page 437; `Pacific Standard Time` at UTC-07:00. The provider probe reported
  `ready` for x86 `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
  Guest-generated UTC fields were seven hours ahead of the host clock, so they
  are not used as wall-clock evidence; their internal ordering was coherent.
- Protocol: for each of three replicas, the producer created one fresh empty
  Jet 3 database, retained it, copied and identity-checked it into the `one`,
  `two`, `three`, and `composite` first-create arms, performed each arm's one
  `TableDefs.Append`, and retained all five images. All replicas reached phase
  `complete` with `mutation_started: true`, status `pass`, no error, and no
  recovery artifact. The pinned analyzer checked the job-result contract,
  complete inventories, before/after identities, exact DAO schemas, decoded
  catalog and table definitions, map locators and contents, `LvProp`, every
  appended page, and empty index roots before applying the preregistered rule.
- Artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `ce802f7dcd1fb2eea3fd49d4a8828e6707473cf9fa0069a9a8c8616e192c8f2b`;
  external `multiple-indexes-job-result.json`, 143,016 bytes, SHA-256
  `3ee3c5df53f4a1859188df7a1aee10eff7a29a750dcfdd972f55417400e72fc6`;
  external `result.json`, 168,174 bytes, SHA-256
  `74353d2534bab87c9ca338dbf9f25c123c40fa593e916c9b102a900e3874d00d`;
  external canonical `multiple-indexes-report.json`, 13,050 bytes, SHA-256
  `f5a8e27392c0d9d7ce20f08840988ce99842dab8c2c277901ca4528adce81750`,
  reproduced byte-identically by rerunning the staged pinned analyzer after
  independently rechecking the staged plan and all eight input identities.
- Retained MDB identities:
  - Replica 1: `empty`, 40,960 bytes,
    `da79cff2b94d62af707efd129880a4d4f42a24c9ef37ca0e415719e8bacc73de`;
    `one`, 49,152 bytes,
    `f08da1e69fadc3320db7cead1aba62a30d0702d2ed7686239cb04df9ee5cb290`;
    `two`, 51,200 bytes,
    `83c90839c22f0ecb54ad9a64123cf381f3c513aa2a30412304eaf2151e2fe6ec`;
    `three`, 53,248 bytes,
    `ff8ee9cbfc42076ab89de06b8cca626cb7e4364576b9d773a43072906a16b128`;
    `composite`, 51,200 bytes,
    `e87d36a8b10ace00ba21a6f26ed7583c699840e2f4019dfdaf655123ff90ed63`.
  - Replica 2: `empty`, 40,960 bytes,
    `f29e75f601338a8cfab1c1d7f6ac1be0f7b1e2ea499c96f67dffcb629f52fd56`;
    `one`, 49,152 bytes,
    `90642769ef803de8c39ab2a539a89378c96643834553db31ef35b909651d45ae`;
    `two`, 51,200 bytes,
    `ff77dea6405bb3197deae1acd5c104000c9283c5d53d965a5255785cd068e528`;
    `three`, 53,248 bytes,
    `984363e77769f5f6ffb5df924b611a92011c4f8c6bdb67b21da931b0d8cb5335`;
    `composite`, 51,200 bytes,
    `fb159bd0e652f4e072a81f5858fa3d9259bb6e31b4ebe27753e35f216e42539e`.
  - Replica 3: `empty`, 40,960 bytes,
    `d0ccc8400c55dc5970cea685aa2661d4e17b9883c423bdf883f8f5ef99563a3d`;
    `one`, 49,152 bytes,
    `2b46a3b00853a809f04c753b2998520fb6ea4a5b26e5a8f3666460b26d825b58`;
    `two`, 51,200 bytes,
    `de3475d11c3bd62b14651cb31901ebf2ea04cad2c7322ced4e9f350f2ec56a73`;
    `three`, 53,248 bytes,
    `8a1601dd7d84316b56dfc1af6a3a0736643ed9bbbe2b5244c569ca642e0744a6`;
    `composite`, 51,200 bytes,
    `7caa38f4425cb29379ac649f71f2df6f137b0409bebaa60e377ce8de5be6082a`.
  Every image retained the same size and SHA-256 through the bounded DAO
  metadata pass, and every arm's pre-mutation identity matched its replica's
  retained empty image.
- Observation: the canonical report has status `accepted`;
  `page_assignment`, `index_layout`, `map_placement`, and `replication` are all
  `answered`; `compatibility_claim` and `support_movement` are both `false`.
  The complete decoded observations agree across all three replicas. Each
  20-page empty base gained definition root page 20, shared map-rows page 21,
  and the created catalog row's `LvProp` `LVAL` page 22, followed by one empty
  index root per physical index at pages 23 onward. The `one`, `two`, `three`,
  and `composite` arms therefore contained 24, 25, 26, and 25 pages. Table
  owned and available locators used page 21 rows 0 and 1 and mapped no pages.
  Index locators used page 21 rows `2 + physical_ordinal`, and each mapped only
  its corresponding root; no second map page appeared through three indexes.
- Observation: physical index order matched append order: `ZPrimary`;
  `ZPrimary, ASecondx`; `ZPrimary, MUniqueX, ASecondx`; and
  `ZComposi, ASecondx`. Logical records appeared in name order while retaining
  physical references `0`; `1,0`; `2,1,0`; and `1,0`. Physical flags were
  `9`; `9,0`; `9,1,0`; and `1,0`, where the observed primary unique required,
  unique non-primary, and ordinary shapes were 9, 1, and 0. Ascending key
  direction was 1 and descending was 0; key order and directions matched the
  DAO metadata, including `Code` descending then `Sequence` ascending in the
  composite index. Every root was an owner-matched empty leaf with zero
  entries. Every arm's `LvProp` was the same 97-byte single-page external value
  at page 22 row 2, header `610000400216000000000000`, payload SHA-256
  `a7cc0a54b254f877029c0dd3c2a0808d3e252869024d13c7b21510b3e15f7ecc`.
- Interpretation: for these exact empty first-create arms, the `LvProp` page
  precedes all index roots; index roots and map rows follow physical append
  order; logical records use name order while referring back to those physical
  ordinals; and the two-index composite arm has the same relative page and map
  assignment as the two-simple-index arm. This overturns the current composer's
  deduced indexed-create order and requires correction before an index-capable
  public creation API. It establishes only the bounded assignments and
  index-definition observations above. It does not establish populated
  composite-key encoding, index-tree or row maintenance, behavior above three
  indexes, other flag combinations or schemas, map-page spill behavior,
  continuation-page placement, extended-name encoding, index plus long-value
  column layout, initial rows, arbitrary `LvProp` grammar or page omission,
  relationships, Rust writer or publication correctness, general Jet 3 or DAO
  compatibility, a hosted differential or support result, or support-matrix
  movement.
- Usage: issue `#150`; `EXP-0059`; `EXP-0061`; `EXP-0062`; `EXP-0073`;
  `EXP-0087`; `EXP-0091`; `EXP-0092`; future multiple-index planner and
  composer work
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: three independent outcome, artifact-identity, report-reproduction,
  decision-rule, bounded-observation, evidence-boundary, roadmap, and
  false-claim reviews completed on 2026-09-02. The staged analyzer was
  independently rerun byte-identically, and all three final reviews reported
  no findings.


### EXP-0094 — Table-definition continuation placement preregistration

- Recorded: 2026-09-02, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; acquisition
  is forbidden until this exact result is committed and merged and the standing
  human authorization for one run remains in effect
- Question: across three replicas beginning from identity-checked copies of one
  fresh empty Jet 3 database apiece, where does DAO place and how does it chain
  exactly zero, one, and two table-definition continuation pages?
- Origin: project-authored clean-room experiment for issue `#151`, using only
  the bounded definition grammar recorded by `EXP-0059`, the long-value
  framing recorded by `EXP-0061`, the null `LvProp` form accepted by
  `EXP-0091`, the catalog and page correlation recorded by `EXP-0073`, and the
  empty first-create framing in `EXP-0087` and `EXP-0093`. The 2,048-byte
  definition-root capacity, 2,040-byte
  continuation contribution, 18-byte physical-column records, and one-byte
  name length followed by CP1252 bytes yield exact no-index logical lengths
  `45 + 29n` for the fixed ten-byte names below. Earlier boundary placement is
  design input only; it does not establish the requested DAO placement.
- Controlled design: every arm uses only fixed `dbLong` fields, no index, no
  rows, and equal eight-byte ASCII table names. Every field name is ten ASCII
  bytes at or below `0x7E`: `F000AAAAAA` through the arm's final ordinal. The
  `zero` arm has 69 fields and logical length 2,046, two bytes below the root
  capacity. The `one` arm has 70 fields and logical length 2,075, crossing that
  capacity by 27 bytes. The `two` arm has 140 fields and logical length 4,105,
  crossing the root plus one continuation capacity of 4,088 by 17 bytes. These
  require zero, one, and two continuation contributions under the recorded
  grammar without using an extended name byte or exceeding DAO's field count.
- Protocol: each of three replicas creates and closes one fresh CP1252
  `dbVersion30` database, retains it as `empty`, then copies and identity-checks
  it into independent `zero`, `one`, and `two` working arms before mutation.
  Each arm receives exactly one `TableDefs.Append` and is closed before copying
  and bounded read-only DAO metadata capture. A passing run retains four MDBs
  per replica, exactly twelve total. Every image is hashed and sized before and
  after metadata access; every arm records its pre-mutation identity. The
  producer records `mutation_started`, a bounded phase, an ordered checkpoint
  prefix, and at most the active arm's recovery image, then stops that replica
  after a failure. A pre-mutation failure may stop the run after replica one;
  any post-mutation result must contain all three replica records.
- Analysis: the analyzer validates the exact result shape, plan binding, file
  type, name, inventory, size, digest, ordered checkpoint prefix, active-arm
  recovery, phase, mutation state, DAO schema, and unchanged metadata identity.
  It widens only the shared decoder's per-process work bound from 64 to 140
  columns; the admitted byte grammar is unchanged. It decodes every definition
  page and pointer, requires the controls' exact zero/one/two continuation
  counts and exact logical lengths, records each page's logical
  interval/capacity/used bytes, identifies
  the catalog object ID with the definition root, records table maps and the
  complete set of changed page-0 offsets plus counter before/after/delta, and
  requires a recorded role and owner for every appended page. Nonconsecutive
  continuation placement is accepted when
  the complete chain and all page roles correlate. Because a wide catalog
  `LvProp` may itself change storage class, analysis preserves its raw 12-byte
  header, classifies only the `EXP-0061` inline/single/chained flag, and for an
  external value validates exact 12-byte external framing, the bounded locator
  chain, exact declared payload length, terminal pointer, and active attributed
  `LVAL` rows. For every storage class, analysis records all attributed
  appended `LVAL` pages and distinguishes the subset referenced by the catalog
  property from unreferenced nuisance allocation. Null and consistently framed
  inline values are also admitted. The experiment does not preregister a
  single-page property assumption or require every appended `LVAL` page to be
  referenced.
- Questions:
  - Counts: do exact logical lengths 2,046, 2,075, and 4,105 carry zero, one,
    and two continuation pages?
  - Placement: where do the root and continuation pages land relative to the
    empty boundary, what pointer order and logical chunks do they carry, and
    what role and owner accounts for every appended page?
  - Counters: what complete changed-offset set and page-0 counter values and
    deltas accompany each bounded create and catalog object ID, without
    assigning an unrecorded semantic?
  - Replication: do all complete relative observations agree across replicas?
- Decision rule: an `accepted` report requires final exact plan and input pins,
  three complete replicas, exact four-checkpoint inventories, pre-mutation arm
  identity, unchanged metadata bytes, exact DAO schemas, fully decoded chains
  with the required continuation counts, total appended-page attribution,
  counter/catalog-root observations, and identical complete relative
  observations. A fully decoded nonconsecutive layout is answered evidence.
  Changed metadata bytes, schema or replica disagreement, an unexpected
  logical length or continuation count, incomplete decoding or page
  attribution, ambiguous correlation, or any post-mutation producer failure is
  `no_outcome`.
  A pre-mutation failure, pin/inventory/bound violation, inconsistent producer
  state, or result-contract defect rejects validation without a scientific
  answer. No retry is permitted after the first DAO mutation without a renewed
  explicit human decision.
- Preregistration artifacts: plan
  `oracle/windows-dao/acquisition/definition-continuation.plan.json`, SHA-256
  `9acd16d15c911f6552347271ab55827c7936ba829e3d10f190d6b9fe1a4d86e1`;
  producer `oracle/windows-dao/scripts/dev/DefinitionContinuation.DevJob.ps1`,
  SHA-256
  `7e812dec1b76c801d6e307e503f43a6d11c34e2e71195aad7ba8386f5223ed5e`;
  analyzer `oracle/windows-dao/scripts/definition_continuation.py`, SHA-256
  `240259fe0d8d93087d0f36255e7ef8d47fc3940a70ea03bdb178ce9311a72fd6`.
  The plan pins those files and the five shared host/guest/publication inputs
  plus the shared catalog decoder by their exact SHA-256 identities.
- Interpretation: a later accepted result may establish continuation count,
  placement, pointer order, page roles, and counter observations only for these
  exact empty first-create fixed-Long schemas through 140 fields and two
  continuation pages. It cannot establish longer chains, other field types or
  name lengths, indexed or populated wide definitions, long-value property
  grammar, general allocation or free-page reuse, continuation writing,
  extended names, public creation, initial rows, relationships, filesystem
  publication, writer correctness, general Jet 3 or DAO compatibility, hosted
  differential #102, or support-matrix movement.
- Usage: future result for issue `#151`; `EXP-0059`; `EXP-0061`; `EXP-0073`;
  `EXP-0087`; `EXP-0091`; `EXP-0093`;
  `file:oracle/windows-dao/acquisition/definition-continuation.plan.json`;
  `file:oracle/windows-dao/scripts/dev/DefinitionContinuation.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/definition_continuation.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: three review/fix passes completed on 2026-09-02. Independent reviews
  covered the protocol, boundary arithmetic, producer, analyzer, routing,
  publication, failure state, evidence boundary, nuisance `LvProp` allocation,
  and false-claim controls. The final pass reported no correctness or
  scientific-protocol blocker; one low external-LVAL inventory test gap was
  fixed before pinning.

### EXP-0095 — No-outcome definition-continuation DAO result

- Recorded: 2026-09-02, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO `no_outcome`
  derived from a canonical analyzer report
- Question: for the exact 69-, 70-, and 140-field first-create controls
  preregistered by `EXP-0094`, where does DAO place and how does it chain zero,
  one, and two table-definition continuation pages?
- Origin: project-authored clean-room experiment using the exact `EXP-0094`
  bytes merged as commit `3b4b823a1b13ba6874c5f553c1d3325c617db42b`
  (PR `#162`) and plan SHA-256
  `9acd16d15c911f6552347271ab55827c7936ba829e3d10f190d6b9fe1a4d86e1`.
  The user explicitly authorized local experiments. Run ID
  `20260902T201059Z-dev-dao` was dispatched exactly once; no retry occurred.
  That dispatch count is the observed operator action, not a fact derived from
  the retained artifacts.
- Input identity: the retained staged plan matched the plan SHA-256 above, and
  all eight staged inputs matched the SHA-256 identities embedded in that
  plan. The staged producer and analyzer identities were respectively
  `7e812dec1b76c801d6e307e503f43a6d11c34e2e71195aad7ba8386f5223ed5e`
  and
  `240259fe0d8d93087d0f36255e7ef8d47fc3940a70ea03bdb178ce9311a72fd6`.
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558; .NET
  4.0.30319.42000; culture/UI culture `en-US`; ANSI code page 1252; OEM code
  page 437; `Pacific Standard Time` at UTC-07:00. The provider probe reported
  `ready` for x86 `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
  Guest embedded UTC timestamps were seven hours ahead of the host run/report
  clock while reporting Pacific UTC-07:00; they are retained as reported but
  are not used as ordering or identity evidence.
- Producer result: all three replicas created, closed, retained, and
  metadata-checked their fresh 20-page empty controls without byte changes.
  Each then reached `capture_zero` after the 69-field arm's DAO append. The
  copied arm failed the producer's combined 2-KiB geometry/64-page bound, and
  recovery retention failed the same check. The result does not preserve the
  arm bytes or distinguish which clause of that combined bound failed. Each
  replica therefore retained only its empty checkpoint; the 70- and 140-field
  DAO table appends were not attempted.
- Analyzer result: the pinned analyzer accepted the exact failure-state and
  three-file inventory contract and wrote a deterministic canonical report
  with top-level status `no_outcome`. All five questions—continuation counts,
  placement, counters, producer outcome, and replication—are `no_outcome`
  because no completed created checkpoint was retained. Rerunning both the
  staged and merged analyzers reproduced the report byte-for-byte.
- JSON artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `921a618c04af7206e0539f043499e985865274eb642aaf75bc3b0fe6689bbf9d`;
  external `definition-continuation-job-result.json`, 12,934 bytes, SHA-256
  `cc76b1fd197dc4aee45ef096a4c492e19ea41c4d4ee03f734adc46f579228ac5`;
  external `result.json`, 17,746 bytes, SHA-256
  `51536ed8a5ddc4ed3c833a74f64f0372805ea98b5de1fb4392733c85ab55f238`;
  external canonical `definition-continuation-report.json`, 3,227 bytes,
  SHA-256
  `880c5126a332f6c67ae9b01107a354f252b88e30a1d216a71e4d368d57369a0b`.
- Retained MDB identities: replica 1 empty, 40,960 bytes, SHA-256
  `5ed3fc11013c0d07e838ffd86ae14508d072830ebf4769d4565001c27333a3c4`;
  replica 2 empty, 40,960 bytes, SHA-256
  `e7ea66818445cebc37f264c57b2bee3f3d5ab51b129fd66ef26177e06f63d82e`;
  replica 3 empty, 40,960 bytes, SHA-256
  `8d7295d726bf2ac56a2775d4431b9ed578dec19a801b8cfe77abb70d251cc3c7`.
  No created-arm MDB was published or retained as evidence.
- Interpretation: this result establishes no continuation count, placement,
  pointer, page-role, page-zero, map, catalog-property, allocation, or writer
  fact. It shows only that the exact `EXP-0094` capture bound did not retain a
  created checkpoint after the 69-field append-and-close path returned. Issue
  `#151` remains evidence-blocked. A successor must prospectively change and
  repin the capture bound; because the first DAO mutation occurred, it also
  requires a new explicit human decision before one new run.
- Usage: issue `#151`; `EXP-0094`; future separately preregistered successor
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: two independent review/fix passes completed on 2026-09-02 over
  artifact identity, failure state, report reproduction, issue tracking,
  evidence boundaries, and non-reinterpretation. Findings on the combined
  bound, append/close evidence, copied-versus-mutated later arms, dispatch-count
  provenance, and the reopened issue state were resolved; final reviews
  reported no remaining findings.


### EXP-0096 — CP1252 extended catalog-name preregistration

- Recorded: 2026-09-02, OpenAI Codex
- Kind: SHA-256-pinned development-only local DAO preregistration; acquisition
  has not occurred and is forbidden until the exact preregistration commit is
  merged
- Question: across three replicas and exact isolated arms, what catalog
  ParentId/Name key contributions does DAO emit for every defined CP1252 byte
  above `0x7E` in three singleton positions, a repeat, and both orders with its
  next defined neighbor; and does DAO reject the `0x7F` boundary and five
  undefined-slot controls?
- Origin: project-authored clean-room experiment for issue `#152`, using the
  catalog key grammar and ASCII context weights recorded by `EXP-0087` and no
  external MDB implementation. `EXP-0087` collectively covered `0xA0`-`0xFF`
  in twelve multi-byte probes and recorded aggregate primary/secondary
  behavior, but deliberately derived no per-byte map, expansion, or secondary
  assignment and did not probe `0x80`-`0x9F`; it therefore does not answer this
  experiment. Windows-1252 repertoire tables define the test inventory but are
  not treated as Jet collation evidence.
- Controlled design: the domain is all 123 defined values in `0x80`-`0xFF`,
  excluding `0x81`, `0x8D`, `0x8F`, `0x90`, and `0x9D`. Their ordered sequence
  is divided into 41 batches of exactly three. Each batch starts from a fresh
  copy of its replica's retained empty image and attempts one ASCII canary plus
  six uniquely ASCII-tagged one-Long-field table names per byte: singleton at
  the left, interior, and right insertion positions, a repeated pair, and both
  orders with the next defined byte. The neighbor skips undefined slots and
  wraps from `0xFF` to `0x80`. Thus each data arm has exactly 19 user creates,
  below the 32-row single leaf observed in `EXP-0087` after accounting for its
  eight catalog base rows. The prior 24-name checkpoint occupied 70 pages, so
  the new 128-page bound is prospective headroom rather than a page-count
  observation for these new arms.
  The domain is anchored to Python's strict CP1252/Unicode repertoire. The
  producer excludes the five undefined slots manually before .NET decoding;
  no claim is made that .NET itself rejects those byte values.
- Rejection controls: one independent arm attempts an ASCII canary and names
  containing U+007F, U+0081, U+008D, U+008F, U+0090, and U+009D. DAO receives
  Unicode BSTR names. The latter five are controls associated with undefined
  CP1252 slots, not evidence that those undefined bytes have mappings. Exact
  acceptance or rejection is recorded; an accepted control is an answer only
  if its catalog row and key decode and correlate without ambiguity.
- Protocol: each replica creates and closes one fresh CP1252 `dbVersion30`
  database and retains `empty`, `b00` through `b40`, and `reject` in exact
  order. Every working arm is copied and identity-checked against `empty`, one
  arm exists at a time, DAO is closed after every append and before capture,
  and bounded read-only metadata must leave each retained image byte-identical.
  A passing run retains exactly 43 MDBs per replica, 129 total. The producer
  records the exact UTF-16LE name, intended defined bytes, created/error result,
  normalized `create_tabledef` or `tabledefs_append` failure operation, bounded
  sequential DAO table/field/index inventory, mutation state, phase, ordered
  checkpoint prefix, and at most the active next checkpoint as recovery. Only
  exceptions thrown directly by those two name-bearing DAO calls continue as
  rejections; open, field, close, metadata, and cleanup failures stop the
  replica.
  A `capture_empty` failure retains `empty` as recovery or removes the partial
  root artifact. Explicit cleanup phases prevent a completed checkpoint from
  also appearing as recovery, and working MDBs live only in a non-published
  subdirectory.
- Analysis: the analyzer independently regenerates all 123 bytes, batches,
  neighbors, names, and files; validates the exact JSON, state, size, SHA-256,
  metadata identity, every retained checkpoint attempt, bounded sequential DAO
  metadata shape (including actual system indexes and no user indexes), exact
  phase-to-prefix/recovery state, and case-folded artifact inventory; then uses
  the `EXP-0087` grammar to correlate every created name with one catalog key.
  It preserves exact key bytes, primary sections, secondary nibbles, and row
  locators. It strips only the exact recorded ASCII context contribution and
  compares singleton positions, repeated contributions, and both pair orders.
  Expansions, position dependence, non-additivity, contrary order, and stable
  DAO rejection are retained as answered observations rather than forced into
  the hypothesis. An accepted rejection control retains its complete decoded
  key identity and secondary section without asserting an undefined-byte map.
  Every retained non-empty checkpoint in a failed prefix is decoded and
  correlated; a failure there is recorded as `decode_error` and yields
  `no_outcome`. Normalized failure operations remain in the canonical report
  and participate in exact replica comparison.
- Decision rule: `accepted` requires three complete exact replicas, 129 exact
  artifacts, unchanged metadata bytes, complete schema/key correlation for all
  created names, all defined-byte and rejection-control outcomes, and identical
  exact observations. Any post-mutation producer failure, incomplete arm,
  changed metadata, catalog/DAO/attempt disagreement, decode failure, loss of
  the bounded single-leaf grammar, or replica disagreement is `no_outcome`.
  A pre-mutation failure, pin/inventory/bound violation, malformed input,
  inconsistent producer state, or analyzer contract defect rejects validation.
  No retry is allowed after the first mutation without renewed human direction.
- Preregistration artifacts: plan
  `oracle/windows-dao/acquisition/extended-names.plan.json`, SHA-256
  `201e880ff1e7d08d5151df2fc53388ef296dfbd4158fc84a530510fffbc32236`;
  producer
  `oracle/windows-dao/scripts/dev/ExtendedNames.DevJob.ps1`, SHA-256
  `a177a80638d22605a2f7836005e9cd6b7b296fdcd7a264f49e6a325c5fa39356`;
  analyzer
  `oracle/windows-dao/scripts/extended_names.py`, SHA-256
  `732e8ca5edd597bf54dc73764031e4663d361f9f1c4f21747baab6dbdf352369`.
  All nine plan inputs carry exact lowercase SHA-256 pins.
- Interpretation: a later accepted result may establish exact catalog-key
  observations only for the preregistered CP1252 repertoire, contexts, locale,
  empty first-create batches, and single-leaf grammar. It cannot establish a
  general collation, undefined-byte mapping, longer names, larger trees, map or
  definition spill, rows, indexes, relationships, public creation, writer
  correctness, compatibility, hosted differential `#102`, or support movement.
- Usage: future result for issue `#152`; `EXP-0087`;
  `file:oracle/windows-dao/acquisition/extended-names.plan.json`;
  `file:oracle/windows-dao/scripts/dev/ExtendedNames.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/extended_names.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: three independent review/fix passes completed on 2026-09-02 over the
  scientific design, byte inventory, state machine, producer, analyzer,
  routing, publication, bounds, evidence retention, and claim boundary. Fixes
  covered capture and cleanup failure publication, failed-prefix decoding,
  rejection-operation retention, source reparse points, strict replica types,
  duplicate/recovery inventories, and the .NET/CP1252 wording. The final
  verification reported no remaining findings; 63 focused tests passed. No
  acquisition was performed.


### EXP-0097 — No-outcome CP1252 extended catalog-name DAO result

- Recorded: 2026-09-02, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO `no_outcome`
  derived from a canonical analyzer report
- Question: for the exact isolated arms preregistered by `EXP-0096`, what
  catalog ParentId/Name key contributions does DAO emit for every defined
  CP1252 byte above `0x7E` in the bounded singleton, repeat, and neighboring
  pair contexts, and what happens for the U+007F and five undefined-slot
  Unicode controls?
- Origin: project-authored clean-room experiment using the exact `EXP-0096`
  bytes merged as commit `9987d606dbd1c36608cff6a8c1160e26073548cd`
  (PR `#164`) and plan SHA-256
  `201e880ff1e7d08d5151df2fc53388ef296dfbd4158fc84a530510fffbc32236`.
  The user had explicitly authorized local experiments. An initial local
  client invocation stopped before opening SSH because required configuration
  was absent; it staged, dispatched, and acquired nothing and is not a run.
  Run ID `20260902T210813Z-dev-dao` was then the one and only DAO dispatch and
  was not retried. The dispatch count and pre-SSH failure are operator-history
  observations, not facts inferred from the retained artifacts.
- Input identity: the retained staged plan matched the exact plan digest above,
  and each of its nine staged inputs matched its embedded SHA-256 pin and the
  corresponding bytes on merged `main`. The staged and merged producer bytes
  were identical at
  `oracle/windows-dao/scripts/dev/ExtendedNames.DevJob.ps1` at SHA-256
  `a177a80638d22605a2f7836005e9cd6b7b296fdcd7a264f49e6a325c5fa39356`
  and the staged and merged analyzer bytes were identical at
  `oracle/windows-dao/scripts/extended_names.py` at SHA-256
  `732e8ca5edd597bf54dc73764031e4663d361f9f1c4f21747baab6dbdf352369`.
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558; .NET
  4.0.30319.42000; culture/UI culture `en-US`; ANSI code page 1252; OEM code
  page 437; `Pacific Standard Time` at UTC-07:00. The provider probe reported
  `ready` for x86 `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
  Guest embedded UTC timestamps were seven hours ahead of the host run/report
  clock while reporting Pacific UTC-07:00; they are retained as reported but
  are not used as wall-clock or dispatch-order evidence.
- Producer result: all three replicas reached phase `complete` with status
  `pass`, `mutation_started: true`, 43 checkpoints, and no recovery artifact.
  All 2,337 defined-arm attempts and all 21 rejection-arm attempts reported
  `created` with no failure operation or error. The rejection arms comprised
  seven attempts per replica: one ASCII canary, U+007F, and five Unicode BSTR
  controls corresponding to undefined CP1252 slots. The producer's bounded DAO
  operation path returned successfully through `TableDefs.Append` and close for
  each exact control, and post-close bounded DAO metadata listed every exact
  name. All 129 retained images kept identical size and SHA-256 through the
  metadata pass.
- Analyzer result: the pinned analyzer validated the complete result and
  artifact contract and wrote a deterministic canonical report with top-level
  status `no_outcome`, `compatibility_claim: false`, and
  `support_movement: false`. Both the staged and merged analyzers reproduced
  that report byte-for-byte. Each replica has the exact decode error
  `checkpoint reject: a catalog row has malformed identity fields`. All five
  questions—coverage, singleton positions, pair composition, secondary order,
  and replication—have status `no_outcome` for the reason `at least one
  retained checkpoint failed a recorded grammar or control`.
- JSON artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `1a7440a0e1610d5efe9e1b0d9c64f67ab64240c91ddda96688540f70a59f201b`;
  external `extended-names-job-result.json`, 6,429,106 bytes, SHA-256
  `0d629b009acee1e7e82915c79b4a60a7ab0b64288dcbba30d0037929a62d4166`;
  external `result.json`, 7,478,632 bytes, SHA-256
  `0de5e47df7b95401c7f037fb8b02a4c1ec9daf25664e11befaf12484ebc55501`;
  external canonical `extended-names-report.json`, 824,988 bytes, SHA-256
  `3c08b7499b23f02e31bf3b13d5ee18f54644be4e07d3acc92804b3db7fbf4a32`.
  The outer `result.json` is transport and status evidence only: its duplicated
  Unicode-bearing replica values contain 4,458 scalar differences from the
  byte-preserved job result because the dispatcher read UTF-8 using the Windows
  PowerShell default encoding. Exact name evidence comes only from the direct
  job result consumed by the pinned analyzer, not that duplicated outer copy.
- Retained MDB identities: exactly 129 files totaling 15,200,256 bytes: three
  40,960-byte empty images, 123 120,832-byte batch images, and three
  71,680-byte rejection-arm images. The SHA-256 of the filename-sorted JSON
  array of `name`/`size`/`sha256` objects, serialized as UTF-8 with indent 2,
  keys sorted, and one trailing LF, is
  `e409731b49974150b99ea54b3cc8b3d62a3d8fccb9830631c0cc5101b834e3cf`.
- Interpretation: the validated producer metadata agrees with the exact
  attempt-acceptance records, but this is not a general DAO name-acceptance
  claim. The preregistered decision was all-or-`no_outcome`; therefore the
  defined-arm bytes cannot be promoted post hoc after the rejection checkpoint
  failed catalog-key decoding. This result establishes no catalog-key mapping,
  primary weight, expansion, secondary-nibble ordering, undefined-byte
  mapping, general collation, writer correctness, compatibility, hosted
  differential result, or support movement. Issue `#152` remains open and
  evidence-blocked. Any new acquisition requires a separately pinned successor
  and renewed explicit human authorization.
- Usage: issue `#152`; `EXP-0087`; `EXP-0096`; future separately
  preregistered successor
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: direct artifact-identity, producer-state, inventory, and canonical
  classification checks completed on 2026-09-02; independent outcome-entry
  review is pending


### EXP-0098 — Definition-continuation bounded-capture successor preregistration

- Recorded: 2026-09-02, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; acquisition
  has not occurred and is forbidden until the exact preregistration commit is
  merged
- Question: for the unchanged exact 69-, 70-, and 140-field first-create
  controls from `EXP-0094`, where does DAO place and how does it chain zero,
  one, and two table-definition continuation pages when completed checkpoints
  may contain at most 256 pages?
- Prior evidence and design boundary: `EXP-0095` established only that all
  three replicas reached `capture_zero` and that the former combined
  geometry/64-page check retained no created checkpoint. It did not preserve
  that arm's byte length, divisibility, page count, failed clause, or bytes.
  `EXP-0059`'s separate wide-table observation referenced zero-based page index
  172, implying a file of at least 173 pages. That is design input for choosing
  256, the smallest power of two at least 173, as the completed-checkpoint
  bound, not evidence about these controls or the failed arm.
  The existing 512-page schema-generalization file ceiling is reused only as
  a recovery-salvage bound.
- Controlled design: the scenario names, order, schemas, questions, and three
  independent replicas are unchanged from `EXP-0094`. Each replica retains an
  empty control, copies and identity-checks all three working arms before any
  table append, then attempts `zero`, `one`, and `two` in order. The producer
  keeps at most twelve fixed-name ephemeral MDBs in one newly created,
  non-reparse working subdirectory that root publication never traverses, so a
  cleanup failure cannot contaminate the retained evidence inventory. It
  records exact `arm_baselines` in that order; `copy_arms` failure admits only
  the successfully checked prefix, while every append or capture phase requires
  all three. Every post-mutation producer failure remains `no_outcome`;
  stable failures, measured bound failures, and recovered bytes do not answer
  a scientific question.
- Measurement and capture: before enforcing checkpoint policy, the producer
  records the exact raw byte length, whether it is divisible by 2,048, the
  derived page count when divisible, and the first failed predicate in the
  fixed order `minimum_page_length`, `page_alignment`, then
  `checkpoint_bound_exceeded`. Completed checkpoints must be regular,
  non-reparse files of one through 256 pages and carry the measurement, size,
  and SHA-256 both before and after bounded read-only DAO metadata access.
  Failure records preserve a failed measurement even if no MDB can be
  retained. `failure_measurement` is reachable only during `create_database`
  after mutation starts, `capture_empty`, `copy_arms`, or a scenario append or
  capture; it must be null before `CreateDatabase` and for cleanup-only failure
  after phase `complete`.
- Recovery: after `CreateDatabase` sets `mutation_started`, an active checkpoint
  image that is an aligned sequence of one through 512 pages may be retained
  with exact size, SHA-256, measurement, reason, and `interpreted=false`. This
  includes the active `empty` image during `create_database` or `capture_empty`
  as well as an active scenario image during append or capture. An image above
  the completed-checkpoint bound is labeled
  `checkpoint_bound_exceeded`. Recovery receives no DAO metadata access, is
  never decoded, and cannot contribute to continuation, placement, counter,
  producer-completion, or replication answers. An image above 512 pages is not
  retained; its exact failed measurement remains in the job result.
- Decision rule: `accepted` is unchanged from `EXP-0094` except for the
  256-page completed-checkpoint ceiling and exact measurement contract. Any
  post-mutation failure, including a minimum-length, alignment,
  completed-checkpoint-bound, or recovery-bound failure, is `no_outcome`. A
  malformed or inconsistent measurement, an invalid or incomplete
  `arm_baselines` state, a completed checkpoint above 256 pages, a retained
  recovery above 512 pages, an unexpected file, or another input/result
  contract defect rejects validation. There is no automatic retry after
  mutation.
- Preregistration artifacts: plan
  `oracle/windows-dao/acquisition/definition-continuation.plan.json`, SHA-256
  `3e7172838bfd7d48b6042e1fe1a1855883be27eb3c2b8f7ad367368daa2c0cd9`;
  producer `oracle/windows-dao/scripts/dev/DefinitionContinuation.DevJob.ps1`,
  SHA-256
  `22ccb5c7bd57aef41031bb42bf031b71151c38ef8476dcbf0e9743a5304f45e6`;
  analyzer `oracle/windows-dao/scripts/definition_continuation.py`, SHA-256
  `2d1abfde48b9fbf9f3c7e3985919b513f28df5d6b87bf8de601eb870f5d6852d`.
  All eight staged inputs carry exact lowercase SHA-256 pins.
- Authorization: the prior standing decision was consumed by the
  `EXP-0095` post-mutation run. This preregistration does not authorize
  acquisition. One new run requires merge and a new explicit human decision.
- Interpretation: a later accepted report may establish only the same exact
  bounded facts and exclusions as `EXP-0094`. Raising the capture ceiling and
  retaining uninterpreted recovery do not establish larger databases,
  arbitrary wide schemas, longer chains, allocation policy, writer
  correctness, compatibility, or support.
- Usage: issue `#151`; `EXP-0059`; `EXP-0094`; `EXP-0095`;
  `file:oracle/windows-dao/acquisition/definition-continuation.plan.json`;
  `file:oracle/windows-dao/scripts/dev/DefinitionContinuation.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/definition_continuation.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: independent review/fix passes identified and corrected asymmetric
  publication bounds for completed versus recovery artifacts and acceptance of
  a later replica's pre-mutation failure after an earlier global mutation, while
  rejecting an impossible `before_create_database` phase that claims mutation
  already started. Further review isolated ephemeral working MDBs from root
  publication and bound `failure_measurement` to producer-reachable phases.
  Focused analyzer and shared development-contract tests cover the corrections;
  exact-head review remains pending before acquisition.


### EXP-0099 — Metadata-isolated extended-name successor preregistration

- Recorded: 2026-09-02, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no
  acquisition has been performed
- Question: for every defined CP1252 byte above `0x7E`, what exact bounded
  catalog ParentId/Name key contributions does DAO emit in the unchanged
  singleton, repeated, and neighboring-pair arms when Unicode boundary controls
  are validated only at the BSTR and post-close DAO metadata boundary?
- Prior evidence and correction boundary: `EXP-0097` records that the
  `EXP-0096` run completed all 2,358 DAO name attempts and exact metadata reads,
  but returned `no_outcome` because its analyzer passed the final controls arm
  to a physical catalog-key decoder. The all-or-`no_outcome` decision forbids
  promotion of the 41 defined arms from that run. This successor leaves every
  one of those defined-byte names and batches unchanged and changes only the
  scientific treatment of the separate controls arm. `EXP-0097` also records
  Unicode mojibake in the outer transport copy; commit `44362ea` changed the
  shared JSON readers to explicit UTF-8 before this successor was pinned.
- Defined arms: regenerate all 123 strict Python CP1252 values in `0x80`-`0xFF`
  except `0x81`, `0x8D`, `0x8F`, `0x90`, and `0x9D`; retain 41 ordered batches
  of three. Each fresh-empty copy attempts one ASCII canary and the unchanged
  six forms per byte: three singleton positions, a repeat, and both orders with
  the next defined byte. Each arm therefore attempts exactly 19 one-Long-field
  tables.
- Metadata-only controls: the final checkpoint is renamed `controls` and
  attempts one ASCII canary plus U+007F, U+0081, U+008D, U+008F, U+0090, and
  U+009D. The analyzer regenerates and requires each exact UTF-16LE name,
  validates its `CreateTableDef`/`TableDefs.Append` outcome and exact post-close
  TableDefs name/schema inventory, and identity-checks the retained MDB. It
  never invokes the catalog decoder for this checkpoint. An `accepted` control
  means only that the exact Unicode BSTR append succeeded and the exact name
  appeared in metadata; it establishes no CP1252-byte mapping, physical key, or
  collation behavior.
- Capture and bounds: each of three replicas retains `empty`, `b00` through
  `b40`, and `controls` in exact order: 43 checkpoints per replica and 129 MDBs
  for a passing run. Every arm begins byte-identical to its retained empty
  image; every completed image must remain unchanged through bounded read-only
  metadata access. Limits remain 128 pages per MDB, 32 TableDefs, 32 fields and
  16 indexes per TableDef, 8 MiB for the child job result, and at most one
  active-next-checkpoint recovery MDB per failed replica.
- Decode and replication: only `b00` through `b40` enter the recorded
  single-leaf catalog decoder. The report preserves complete keys, primary
  sections, secondary nibbles, object IDs, and row locators for each replica.
  Exact replica equality covers the complete intended attempts, created or
  rejected state, normalized name-bearing operation, isolated primary bytes,
  secondary nibbles, pair conclusions, and controls metadata outcomes. Object
  IDs and row locators remain visible but do not define equality because they
  are incidental to the collation questions.
- Transport contract: every replica carries one exact text/UTF-16LE sentinel
  containing all 123 defined experiment characters in byte order followed by
  U+007F, U+0081, U+008D, U+008F, U+0090, and U+009D.
  The child-result analyzer requires its exact code points. Static tests require
  the producer, dispatcher, and top-level runner to use BOMless UTF-8 writes,
  explicit UTF-8 reads, and unchanged `extended_names_replicas` pass-through.
  Linux has no Windows PowerShell runtime, so pre-merge tests cannot execute the
  two actual PowerShell serialization hops; the eventual preregistered run is
  the first dynamic verification of that transport path.
- Decision rule: `accepted` requires all final pins, complete ordered attempts
  and artifacts, unchanged images, exact DAO inventories, complete decoded
  defined-arm correlations, the exact sentinel, and three-replica equality of
  every question-bearing value. Stable rejection, expansion, position
  dependence, non-additivity, contrary pair order, or differing incidental
  locators is an answer. Any post-mutation producer failure, changed metadata
  identity, defined-arm decode/correlation failure, transport-sentinel mismatch,
  incomplete inventory, or question-bearing replica disagreement is
  `no_outcome`. A pre-mutation or result-contract defect is rejected. There is
  no automatic retry after mutation.
- Preregistration artifacts: plan
  `oracle/windows-dao/acquisition/extended-names.plan.json`, SHA-256
  `ee12b4c5ca9705907276d6a9cccc9de190b6c737b80e917f7f24f3078bf28254`;
  producer `oracle/windows-dao/scripts/dev/ExtendedNames.DevJob.ps1`, SHA-256
  `1d18a79590d2535185e2526abedad043239d2d5d5ce8be47a19ac2e4de04f160`;
  analyzer `oracle/windows-dao/scripts/extended_names.py`, SHA-256
  `5f8303e94139ea0eaa3ee425701433ceabefca6658ce6972c4f95b1e448397bc`.
  The plan pins all nine staged inputs.
- Authorization: the `EXP-0097` acquisition consumed the prior decision. This
  preregistration does not authorize acquisition. One run requires merge plus a
  renewed explicit human decision.
- Interpretation: a later accepted report may establish only the exact bounded
  defined-byte observations and exact metadata-control outcomes above. It
  cannot establish a mapping or physical key for the controls, arbitrary names,
  general collation, writer correctness, compatibility, or support movement.
- Usage: issue `#152`; `EXP-0087`; `EXP-0096`; `EXP-0097`;
  `file:oracle/windows-dao/acquisition/extended-names.plan.json`;
  `file:oracle/windows-dao/scripts/dev/ExtendedNames.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/extended_names.py`
- Rights: future project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: focused producer, analyzer, transport, state-machine, and artifact
  contract tests passed; no acquisition


### EXP-0100 — No-outcome definition-continuation successor result

- Recorded: 2026-09-02, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO `no_outcome`
  derived from a canonical analyzer report; the result contract was valid and
  was not rejected
- Question: for the unchanged exact 69-, 70-, and 140-field first-create
  controls preregistered by `EXP-0098`, where does DAO place and how does it
  chain zero, one, and two table-definition continuation pages?
- Origin: project-authored clean-room experiment using the exact `EXP-0098`
  bytes merged as commit `04a72fc7becc7df355e5c51ee081f68535dccd20` (PR
  `#167`) and plan SHA-256
  `3e7172838bfd7d48b6042e1fe1a1855883be27eb3c2b8f7ad367368daa2c0cd9`.
  The user explicitly authorized exactly one successor acquisition. Run ID
  `20260902T230927Z-dev-dao` was dispatched exactly once; no retry occurred.
  That dispatch count is the observed operator action, not a fact derived from
  the retained artifacts.
- Input identity: the retained staged plan matched the plan SHA-256 above, and
  all eight staged inputs matched the exact lowercase SHA-256 identities
  embedded in that plan. The retained plan and staged inputs also matched the
  merged preregistration files.
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558; .NET
  4.0.30319.42000; culture/UI culture `en-US`; ANSI code page 1252; OEM code
  page 437; `Pacific Standard Time` at UTC-07:00. The provider probe reported
  `ready` for x86 `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
  Guest embedded UTC timestamps were seven hours ahead of the host run/report
  clock while reporting Pacific UTC-07:00; they are retained as reported but
  are not used as ordering or identity evidence.
- Producer result: all three replicas reported `pass`, phase `complete`, and
  `mutation_started=true`. Each retained the exact ordered `empty`, `zero`,
  `one`, and `two` checkpoints with the required pre-mutation arm identities,
  measurements, unchanged bounded metadata identities, and no recovery image.
  The completed images were respectively 20, 67, 69, and 220 pages in every
  replica, within the prospectively raised 256-page bound.
- Analyzer result: the pinned analyzer accepted the complete producer,
  artifact, and result contracts, then classified the scientific result as
  `no_outcome`. Every replica had the exact decode error `zero has 1
  continuation pages; the control requires 0`. Continuation counts, placement,
  counters, producer outcome, and replication all have status `no_outcome`
  with the common reason `at least one complete checkpoint failed a recorded
  grammar or control`. Rerunning the staged and merged analyzers reproduced the
  canonical report byte-for-byte. The explicit-UTF-8 outer result and direct
  child job result also carried equal replica values.
- JSON artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `1d0d2771313c3c67c93e84c295cb4d9a688bd387555a455d14d21f78186f7f54`;
  external `definition-continuation-job-result.json`, 643,657 bytes, SHA-256
  `47d9552e1ddfc59b721c097359499a829239e93586eff7779f2c4b0c24a5dcc7`;
  external `result.json`, 786,331 bytes, SHA-256
  `6d6958496d582f3adfdc6a38e16fdff29a8ef7fbfc3efb769f8bc2984a531326`;
  external canonical `definition-continuation-report.json`, 11,538 bytes,
  SHA-256
  `57105444758f02112f1b33745f7bb13eb420e167dbbd661d7abfe52c906e97c6`.
- Retained MDB inventory: twelve external MDBs, four per replica, totaling
  2,310,144 bytes. The canonical filename-sorted JSON array of
  `{name,size,sha256}` objects, serialized with two-space indentation, sorted
  keys, and one trailing LF, is 1,929 bytes with SHA-256
  `5b8e3ad75d90107c0aacf2a69e065b1d05a01d8dc5091b10d74443db3a7893f4`.
  The MDBs and the inventory serialization remain outside the repository.
- Non-promotable diagnostic: separate inspection of the retained complete
  images found the `zero` logical definition length 2,046 on chain `[20, 66]`,
  the `one` length 2,075 on `[20, 68]`, and the `two` length 4,105 on
  `[20, 219, 218]`, corresponding to one, one, and two continuation pages.
  These values diagnose the failed zero-control assumption only. The
  preregistered all-or-`no_outcome` decision forbids promoting any count,
  placement, pointer, or other format fact from this run.
- Interpretation: this valid result establishes no continuation count,
  placement, pointer order, page-role, page-zero, map, catalog-property,
  allocation, writer, compatibility, or support fact. It shows that the exact
  `EXP-0098` producer captured all scenarios successfully, but the recorded
  zero-control expectation conflicts with the retained grammar decode. Issue
  `#151` remains evidence-blocked. Because DAO mutation occurred, another
  acquisition requires a separately pinned successor and renewed explicit
  human authorization.
- Usage: issue `#151`; `EXP-0059`; `EXP-0094`; `EXP-0095`; `EXP-0098`; future
  separately preregistered successor
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: three independent review/fix passes completed on 2026-09-02 over
  artifact identity, producer state, inventory, canonical report reproduction,
  UTF-8 transport equality, result classification, evidence boundaries, and
  cross-document consistency. Low wording findings were corrected; final
  exact-head verification found no remaining findings.


### EXP-0101 — Accepted extended catalog-name successor result

- Recorded: 2026-09-02, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO accepted result
  derived from a canonical analyzer report
- Question: for every defined CP1252 byte above `0x7E`, what exact bounded
  catalog ParentId/Name primary and secondary contributions occur in the six
  `EXP-0099` forms; and what exact BSTR/metadata outcome occurs for the separate
  Unicode controls?
- Origin: project-authored clean-room experiment using the exact `EXP-0099`
  bytes merged as commit `e74348d474d72049378b703e69739445a40bf461`
  (PR `#168`) and plan SHA-256
  `ee12b4c5ca9705907276d6a9cccc9de190b6c737b80e917f7f24f3078bf28254`.
  The user explicitly authorized exactly one successor acquisition. Run ID
  `20260902T231606Z-dev-dao` was dispatched exactly once; no retry occurred.
  That dispatch count is the observed operator action, not a fact derived from
  the retained artifacts.
- Input identity: the retained staged plan matched the plan SHA-256 above and
  the merged preregistration. All nine staged inputs matched both their exact
  lowercase plan pins and the merged files:
  - `scripts/windows-dao-dev.py`:
    `80a7b7e8d95c9f81675e0968ef143bc0460753f9e98983993469216b93444de8`
  - `oracle/windows-dao/scripts/probe-provider.ps1`:
    `695e357959f7882f2608dfcc32cf9d6bc5d1fd128126552d656daabbfe0b0ebd`
  - `oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1`:
    `eb1bf3290461b52d25c274ad72a053d487e5107cc902f5bd0c10dce2773f98b6`
  - `oracle/windows-dao/scripts/dev/Dispatch.DevJob.ps1`:
    `caaa8a956cbfb29d69af10d7e88ab19547f5f71a899ee9c023e19e25f721f4e8`
  - `oracle/windows-dao/scripts/dev/Publish.DevJob.ps1`:
    `c48d987bbaafd3b490510ec6ea75f3259b2fce148958f66f7b2ecf36048cdd04`
  - `oracle/windows-dao/scripts/dev/ExtendedNames.DevJob.ps1`:
    `1d18a79590d2535185e2526abedad043239d2d5d5ce8be47a19ac2e4de04f160`
  - `oracle/windows-dao/scripts/extended_names.py`:
    `5f8303e94139ea0eaa3ee425701433ceabefca6658ce6972c4f95b1e448397bc`
  - `oracle/windows-dao/scripts/schema_generalization.py`:
    `add7667b20d47537d6255df22be42f27d8100b6f43b80bb0b2fb71d049249af7`
  - `oracle/windows-dao/scripts/system_catalog.py`:
    `3a710d97a83aab55f9c56cbbeb2e7dd4f75078e8567d0134cd7bfa59f44ee7d9`
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558; .NET
  4.0.30319.42000; culture/UI culture `en-US`; ANSI code page 1252; OEM code
  page 437; `Pacific Standard Time` at UTC-07:00. The provider probe reported
  `ready` for x86 `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
  Guest embedded UTC timestamps were seven hours ahead of the host run/report
  clock while reporting Pacific UTC-07:00; they are retained as reported but
  are not used as ordering or identity evidence.
- Producer and artifact result: all three replicas reported `pass`, phase
  `complete`, and `mutation_started=true`. Each retained the exact ordered
  `empty`, `b00` through `b40`, and `controls` checkpoints, 43 per replica and
  129 total, with no recovery. Each replica made 779 attempts in defined arms:
  41 ASCII canaries plus six forms for each of all 123 defined bytes. It also
  made seven metadata-control attempts, for 786 per replica and 2,358 total.
  Every attempt succeeded. All 129 images retained the exact intended DAO
  schema and were byte-unchanged through bounded post-close metadata access.
- Transport result: the exact sentinel is `T`, then the Unicode characters
  obtained by strict CP1252 decoding of all 123 defined bytes in ascending byte
  order, then U+007F, U+0081, U+008D, U+008F, U+0090, U+009D, and `Z`. Its 131
  characters and exact 262-byte UTF-16LE encoding agreed in all three child
  replicas, the explicit-UTF-8 outer transport copies, and the canonical
  report.
- Analyzer result: the pinned analyzer produced 41 completely decoded physical
  catalog-key observations per replica and accepted the result. Coverage,
  singleton positions, pair composition, secondary order, and replication all
  have status `answered`; all question-bearing attempt, primary, secondary,
  composition, and control values agree across the three replicas. Incidental
  object IDs and row locators were preserved per replica but excluded from the
  equality decision. The report has `compatibility_claim=false` and
  `support_movement=false`. Rerunning both the staged and merged analyzers
  reproduced the canonical report byte-for-byte.
- Primary contributions: the following is the complete accepted map for all
  123 defined source bytes. Byte sets and inclusive ranges are hexadecimal;
  the right side is the exact isolated primary byte sequence. A sequence
  contains one or two bytes, and the non-injective map has no claimed inverse.

        80,8E,9E=10
        82,8B,91-92,9B=18
        83=32
        84,93-94,AB,BB=13
        85=33
        86=34
        87=35
        88=36
        89=37
        8A,9A=76
        8C,9C=7266
        95=38
        96-97,AD=1e
        98=39
        99=3a
        9F,DD,FD,FF=7d
        A0=11
        A1=3b
        A2=3c
        A3=3d
        A4=3e
        A5=3f
        A6=40
        A7=41
        A8=42
        A9=43
        AA=44
        AC=45
        AE=46
        AF=47
        B0=48
        B1=49
        B2=58
        B3=59
        B4=4a
        B5=4b
        B6=4c
        B7=4d
        B8=4e
        B9=57
        BA=4f
        BC=50
        BD=51
        BE=52
        BF=53
        C0-C5,E0-E5=60
        C6,E6=6066
        C7,E7=62
        C8-CB,E8-EB=66
        CC-CF,EC-EF=6a
        D0,F0=65
        D1,F1=70
        D2-D6,F2-F6=72
        D7=54
        D8,F8=81
        D9-DC,F9-FC=78
        DE,FE=7f
        DF=7676
        F7=55

  The isolated primary sequence was position-independent for every defined
  byte. For every byte, the repeated form was two singleton primary sequences,
  and the forward and reverse forms were the corresponding ordered singleton
  sequences, including the two-byte expansions.
- Secondary observations: each line below records the exact complete secondary
  nibble sequences for `single_left`, `single_middle`, `single_right`,
  `repeat`, `forward`, and `reverse`, in that order and keyed by the tagged
  byte. A hyphen is an empty sequence; a range is inclusive. Forward is the
  keyed byte followed by its next defined neighbor, wrapping `FF` to `80`;
  reverse swaps that order.

        80,82-88,8B-8C,8E,91-98,9B-9C,A0-BE,D7,DE,F7=-/-/-/-/-/-
        89,99=-/-/-/-/22a/22a
        8A,9A=22a/222a/222a/222aa/222a/222a
        9E,FE=-/-/-/-/2226/2226
        9F,D6,F6,FF=26/226/226/2266/226/226
        BF,D8,F8=-/-/-/-/223/223
        C0,C8,E0,E8=223/2223/2223/22233/22234/22243
        C1,C9,CD,DA,E1,E9,ED,FA=224/2224/2224/22244/22245/22254
        C2,E2=225/2225/2225/22255/22257/22275
        C3,E3=227/2227/2227/22277/22276/22267
        C4,E4=226/2226/2226/22266/22268/22286
        C5,E5=228/2228/2228/22288/2228/222228
        C6,E6=-/-/-/-/222229/2229
        C7,E7=229/2229/2229/22299/22293/22239
        CA,CE,EA,EE=2225/22225/22225/222255/222256/222265
        CB,EB=226/2226/2226/22266/22263/22236
        CC,EC=2223/22223/22223/222233/222234/222243
        CF,EF=226/2226/2226/22266/2226/2226
        D0,F0=-/-/-/-/227/227
        D1,F1=27/227/227/2277/2273/2237
        D2,D9,F2,F9=23/223/223/2233/2234/2243
        D3,F3=24/224/224/2244/2245/2254
        D4,F4=25/225/225/2255/2257/2275
        D5,F5=27/227/227/2277/2276/2267
        DB,FB=25/225/225/2255/2256/2265
        DC,FC=226/2226/2226/22266/22264/22246
        DD,FD=24/224/224/2244/224/224
        DF=-/-/-/-/22223/223

  Singleton secondary observations were position-dependent, and repeat
  secondary observations were not two singleton sequences, for exactly:
  `8A,9A,9F,C0-C5,C7-CF,D1-D6,D9-DD,E0-E5,E7-EF,F1-F6,F9-FD,FF`.
  Forward secondary composition was false for exactly
  `89,99,9E,BF-C4,C6-CE,D1-D5,D9-DC,DF-E4,E6-EE,F1-F5,F9-FC,FE`;
  reverse composition was false for exactly
  `89,99,9E,BF-C5,C7-CE,D1-D5,D9-DC,DF-E5,E7-EE,F1-F5,F9-FC,FE`.
  Each predicate was true for every other defined byte. These are exact results
  for the six fixed contexts, not a claim about untested names or a general
  collation algorithm.
- Metadata-only controls: DAO accepted all seven exact Unicode BSTR names and
  the post-close `TableDefs` metadata contained each exact name with the
  intended one-Long-field schema: ASCII `CREJECTB`; `R7FA` + U+007F + `Z`;
  `R81A` + U+0081 + `Z`; `R8DA` + U+008D + `Z`; `R8FA` + U+008F + `Z`;
  `R90A` + U+0090 + `Z`; and `R9DA` + U+009D + `Z`. No name-bearing operation
  failed. Because COM received Unicode BSTRs and the controls checkpoint was
  never physically decoded, this establishes no CP1252-byte mapping, physical
  catalog key, or collation fact for any control.
- JSON artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `b688b5c5cac8690ebf7cab8d3cb2bc086308a4f99dd1f79b5663ed64d3ad6940`;
  external `extended-names-job-result.json`, 6,432,244 bytes, SHA-256
  `9bda75463f7a41f43ca30c5ec47509d5895065d3ff3b521d9d9d5a84525c042e`;
  external `result.json`, 7,429,630 bytes, SHA-256
  `eae5b98addc812a73185b7e7878369146dac0adefd0943d9cb5cf6519d7f0c60`;
  external canonical `extended-names-report.json`, 3,190,664 bytes, SHA-256
  `a17190e2f2ee3178f29243b8495ce57a4e61e458137a2ec147073ce3a55a5871`.
- Retained MDB inventory: 129 external MDBs totaling 15,200,256 bytes: three
  40,960-byte empty images, 123 120,832-byte defined-batch images, and three
  71,680-byte controls images. The canonical filename-sorted JSON array of
  `{name,size,sha256}` objects, serialized with two-space indentation, sorted
  keys, and one trailing LF, is 19,497 bytes with SHA-256
  `3ee05466388a0f05a0cbb41fff2776b13c26af924e4021cd65a613105b825bae`.
  The MDBs and inventory serialization remain outside the repository.
- Interpretation: this result resolves issue `#152`'s bounded six-form evidence
  question for all defined CP1252 bytes above `0x7E`. It tested only three
  singleton positions, a repeat, and both orders with each byte's registered
  adjacent defined neighbor; it also observed noncompositional secondary
  behavior. It therefore does not justify accepting arbitrary names, more than
  two non-ASCII bytes, or untested byte pairs or contexts. An implementation
  derived now must retain the current blanket rejection or fail closed outside
  the exact evidenced contexts and contexts that follow solely from a
  positively observed composition predicate. General planner widening needs
  more evidence. The result also does not establish locale or code-page
  variants, general collation semantics, an inverse mapping, physical keys for
  the Unicode controls, rows, indexes, writer correctness, public creation,
  general Jet 3 or DAO compatibility, hosted differential `#102`, or
  support-matrix movement.
- Usage: issue `#152`; `EXP-0087`; `EXP-0096`; `EXP-0097`; `EXP-0099`;
  `file:oracle/windows-dao/acquisition/extended-names.plan.json`;
  `file:oracle/windows-dao/scripts/dev/ExtendedNames.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/extended_names.py`
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: three independent review/fix passes completed on 2026-09-02 over
  artifact identity, producer state, inventory, transport, canonical
  reproduction, mapping-table transcription, result classification, evidence
  boundaries, and cross-document claims. Mapping transcription and
  implementation-scope findings were corrected; final exact-head verification
  found no remaining findings.


### EXP-0102 — Established-control definition-continuation preregistration

- Recorded: 2026-09-02, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no
  acquisition has been performed
- Question: when the exact established `Alpha(Id Long)` small-definition
  control and the unchanged exact 70- and 140-field wide-definition targets
  are each created from an independently copied empty first-create image, do
  their 66-, 2,075-, and 4,105-byte definitions decode to exactly zero, one,
  and two continuation pages; and where do those definition pages land and
  point?
- Prior evidence and design boundary: `EXP-0087` records the exact
  `Alpha(Id Long)` first-create shape on one definition root with no
  continuation page, making it the zero-continuation control. `EXP-0059`
  records the bounded definition grammar, logical-length field, 2,048-byte
  root capacity, and 2,040-byte continuation contribution used to predict
  logical lengths 66, 2,075, and 4,105 and construct the one- and
  two-continuation hypotheses. `EXP-0100` validates the
  bounded producer, measurement, recovery, and publication contracts inherited
  here and diagnoses the former 2,046-byte zero-control premise as false. Its
  all-or-`no_outcome` decision makes every continuation count, placement,
  pointer, counter, and other format observation from that run non-promotable;
  consequently it supplies no evidence for either wide target's expected
  count or placement.
- Controlled design: each of three replicas creates and closes one fresh
  `dbVersion30` CP1252 empty database, retains it, and makes three size-,
  SHA-256-, and measurement-identical working copies before any table append.
  The `zero` arm appends only `Alpha` with one `dbLong` field named `Id`; the
  `one` arm appends only `ContOneX` with 70 `dbLong` fields named
  `F000AAAAAA` through `F069AAAAAA`; and the `two` arm appends only `ContTwoX`
  with 140 such fields through `F139AAAAAA`. No arm adds rows or indexes. The
  exact `EXP-0098` bounded capture, failure measurement, recovery,
  metadata-identity, root inventory, and state-machine contracts remain in
  force.
- Analysis and decision rule: the analyzer independently requires the exact
  DAO and decoded schema for every complete arm. It requires exact logical
  lengths 66, 2,075, and 4,105 and exact continuation counts zero, one, and
  two, respectively; validates chain pointers and complete logical coverage;
  attributes every appended page; and reports placement, page-zero counters,
  catalog roots, maps, and bounded `LvProp` framing. An `accepted` report
  requires all three complete replicas to agree on every relative observation.
  A complete valid observation contrary to any count hypothesis is
  `no_outcome`, not evidence promoted under a revised rule. A malformed
  producer/result/artifact contract is rejected. No automatic retry follows
  the first DAO mutation.
- Preregistration artifacts: plan
  `oracle/windows-dao/acquisition/definition-continuation.plan.json`, SHA-256
  `582f8aff6e7a29fae5594fa2819cf0595e0f61695604245c6df1cf5376e62f5b`;
  producer `oracle/windows-dao/scripts/dev/DefinitionContinuation.DevJob.ps1`,
  SHA-256
  `6971137487734353743879e19e1a4945787599a02fc10d982c9a55ed4a7e0330`;
  analyzer `oracle/windows-dao/scripts/definition_continuation.py`, SHA-256
  `dc40ff5e143344412210eb6ac86ce42bbed478f17de3ec447eee6f184064db4f`.
  The plan carries exact lowercase SHA-256 pins for all eight staged inputs and
  does not attempt to pin itself.
- Authorization: acquisition is forbidden until these exact preregistration
  bytes are merged. The user's 2026-09-02 instruction prospectively authorizes
  at most one three-replica acquisition after merge. Any failure after the
  first DAO mutation is a scientific result and consumes that authorization.
- Interpretation: a later accepted report may establish only the exact three
  schema shapes, counts, placements, pointers, page roles, relative counters,
  and replication described above. It cannot establish arbitrary boundary
  placement, other field or name shapes, chains longer than two continuation
  pages, allocation policy, writer correctness, compatibility, public
  creation, hosted differential `#102`, or support-matrix movement.
- Usage: issue `#151`; `EXP-0059`; `EXP-0061`; `EXP-0073`; `EXP-0087`;
  `EXP-0091`; `EXP-0093`; `EXP-0095`; `EXP-0098`; `EXP-0100`;
  `file:oracle/windows-dao/acquisition/definition-continuation.plan.json`;
  `file:oracle/windows-dao/scripts/dev/DefinitionContinuation.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/definition_continuation.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: pending independent review


### EXP-0103 — No-outcome established-control definition-continuation result

- Recorded: 2026-09-02, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO `no_outcome`
  derived from a canonical analyzer report; the result contract was valid and
  was not rejected
- Question: when the exact established `Alpha(Id Long)` small-definition
  control and the unchanged exact 70- and 140-field wide-definition targets
  are each created from an independently copied empty first-create image, do
  their 66-, 2,075-, and 4,105-byte definitions decode to exactly zero, one,
  and two continuation pages; and where do those definition pages land and
  point?
- Origin: project-authored clean-room experiment using the exact `EXP-0102`
  bytes merged as commit `19a0b612de62863344e2d8c2eb6eb3c8db86356b`
  (PR `#171`) and plan SHA-256
  `582f8aff6e7a29fae5594fa2819cf0595e0f61695604245c6df1cf5376e62f5b`.
  The user explicitly authorized exactly one acquisition. Run ID
  `20260903T001018Z-dev-dao` was dispatched exactly once; no retry occurred.
  That dispatch count is the observed operator action, not a fact derived from
  the retained artifacts.
- Input identity: the retained staged plan matched the plan SHA-256 above and
  the merged preregistration. All eight staged inputs matched the exact
  lowercase SHA-256 identities embedded in that plan and the merged files.
- Environment: private local Windows development VM; Windows NT 10.0.20348.0
  build 20348 on AMD64; x86 Windows PowerShell Desktop 5.1.20348.558; .NET
  4.0.30319.42000; culture/UI culture `en-US`; ANSI code page 1252; OEM code
  page 437; `Pacific Standard Time` at UTC-07:00. The provider probe reported
  `ready` for x86 `DAO.DBEngine.36` provider 3.6 from `dao360.dll` file version
  03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
  Guest embedded UTC timestamps were seven hours ahead of the host run/report
  clock while reporting Pacific UTC-07:00; they are retained as reported but
  are not used as ordering or identity evidence.
- Producer result: all three replicas reported `pass`, phase `complete`, and
  `mutation_started=true`. Each retained the exact ordered `empty`, `zero`,
  `one`, and `two` checkpoints, complete pre-mutation arm baselines, and no
  recovery image. The completed images were respectively 20, 23, 69, and 220
  pages in every replica, within the preregistered 256-page bound.
- Analyzer result: the pinned analyzer accepted the complete producer,
  artifact, and result contracts, then classified the scientific result as
  `no_outcome`. Every replica had the exact decode error `one appended page 22
  is unattributed`. Continuation counts, placement, counters, producer outcome,
  and replication all have status `no_outcome`. Rerunning the staged and merged
  analyzers reproduced the canonical report byte-for-byte.
- JSON artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `07cfc6dc719966b71880114c303e0f8411d81d9e69dbb46f8850f83eb5de5c73`;
  external `definition-continuation-job-result.json`, 505,531 bytes, SHA-256
  `80088eb0eaed94a95383227d68ca8977e34fd91d85ce3c39f8fdadaab8ac05bc`;
  external `result.json`, 618,829 bytes, SHA-256
  `24c8959f17417abca7dee6a8dca340f6ae025bca27e9b7f2b31d575c75382f9b`;
  external canonical `definition-continuation-report.json`, 11,475 bytes,
  SHA-256
  `7ecc86b9210d3ba0375b9ee250cc03b4e22136e1c9368454fd115af5536171b3`.
- Retained MDB inventory: twelve external MDBs, four per replica, totaling
  2,039,808 bytes. The canonical filename-sorted JSON array of
  `{name,size,sha256}` objects, serialized with two-space indentation, sorted
  keys, and one trailing LF, is 1,926 bytes with SHA-256
  `e75e55ed88a6c0cacc97c2675d48423fd850756a2a8dd67acb31d2c0e3970317`.
  The MDBs and the inventory serialization remain outside the repository.
- Interpretation: this valid result establishes no continuation count,
  placement, pointer order, page role, page-zero counter, map, catalog-property,
  allocation, writer, compatibility, or support fact. The page counts and
  decode failure above identify the retained artifacts and explain the
  classification; they do not promote any continuation diagnostic under the
  preregistered all-or-`no_outcome` rule. Issue `#151` remains evidence-blocked.
  Because DAO mutation occurred, another acquisition requires a separately
  SHA-256-pinned successor and a new explicit human decision.
- Usage: issue `#151`; `EXP-0059`; `EXP-0094`; `EXP-0095`; `EXP-0098`;
  `EXP-0100`; `EXP-0102`; future separately preregistered successor
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: focused artifact-identity, result-classification, evidence-boundary,
  and cross-document documentation checks passed


### EXP-0104 — Explicit-unassigned definition-continuation preregistration

- Recorded: 2026-09-02, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no
  acquisition has been performed
- Question: when the exact established `Alpha(Id Long)` small-definition
  control and the unchanged exact 70- and 140-field wide-definition targets
  are each created from an independently copied empty first-create image, do
  their 66-, 2,075-, and 4,105-byte definitions decode to exactly zero, one,
  and two continuation pages; where do those definition pages land and point;
  and what bounded decoded page-role record and raw tag does every appended
  page carry?
- Prior evidence and design boundary: `EXP-0087` records the exact
  `Alpha(Id Long)` zero-continuation control, `EXP-0059` records the bounded
  definition grammar used to predict the two wide target lengths and counts,
  and `EXP-0065` records the global allocation map's free versus in-use
  classification and bounded reuse observations.
  `EXP-0103` records a valid all-or-`no_outcome` result in which every analyzer
  replica diagnosed `one` appended page 22 as unattributed. That repeated
  diagnosis is design input only for this successor's reporting rule.
  `EXP-0103` expressly promotes no page role, tag, owner, continuation count,
  placement, pointer, counter, or other format fact, and this preregistration
  does not treat its diagnostic as evidence.
- Controlled design: the exact `EXP-0102` producer, three-replica execution,
  `Alpha`/70-field/140-field schemas, logical lengths, required zero/one/two
  continuation counts, bounds, checkpoints, arm baselines, failure state,
  recovery, metadata-identity, and publication contracts remain unchanged.
  Each arm still starts as an identity-checked copy of its replica's retained
  empty database, and no rows or indexes are added.
- Analysis and decision rule: every complete arm must still match the exact DAO
  and decoded schema, logical length, continuation count, chain pointers, and
  complete logical coverage. The analyzer enumerates every appended page with
  the role record returned by the bounded decoder, its owners, raw page tag,
  and globally-free status derived from the decoded global allocation map. An
  explicit `unassigned` role is reported only when that map marks the page
  free; it then no longer forces `no_outcome`. Every globally-free page's
  decoded role and owners describe only the bounded decoder's classification
  of retained bytes and establish no current owner, reuse history, allocation
  purpose, or semantic role. Every appended LVAL page referenced by the catalog
  `LvProp` must be globally in use. An unreferenced decoder-labeled LVAL page may
  be globally free and is retained as a bounded decoder observation without
  current ownership or purpose inference. An in-use `unassigned`
  page, a globally-free definition root or continuation page, absence of an
  appended page's role record, a referenced globally-free appended LVAL page,
  any schema/count/chain failure, producer
  failure, metadata mutation, incomplete arm, or replica disagreement remains
  `no_outcome`. Malformed producer, artifact, or
  result contracts remain rejected. Acceptance requires all three replicas to
  agree on the complete decoded observations, including any explicit
  `unassigned` records. Page-zero and catalog-root correlation remains
  observational; exact user-table/root resolution is already enforced, and no
  further semantic predicate is assigned. No automatic retry follows the first
  DAO mutation.
- Preregistration artifacts: plan
  `oracle/windows-dao/acquisition/definition-continuation.plan.json`, SHA-256
  `1ffa5af6bea302d89f61384fcb427dc889df9606e9c5f59db807d069da9b5c6f`;
  producer `oracle/windows-dao/scripts/dev/DefinitionContinuation.DevJob.ps1`,
  SHA-256
  `6971137487734353743879e19e1a4945787599a02fc10d982c9a55ed4a7e0330`;
  analyzer `oracle/windows-dao/scripts/definition_continuation.py`, SHA-256
  `be0665c7bab7edf460fa68dd565b00134268360111af8b0033bf443f95f18b2e`.
  The plan carries exact lowercase SHA-256 pins for all eight staged inputs and
  does not attempt to pin itself.
- Authorization: acquisition is forbidden until these exact preregistration
  bytes are merged. The user's 2026-09-02 instruction prospectively authorizes
  at most one three-replica acquisition of the exact merged successor. Any
  failure after the first DAO mutation is a scientific result and consumes that
  authorization.
- Interpretation: a later accepted report may establish only the exact three
  schema shapes, counts, placements, pointers, decoded page-role records, raw
  page tags, relative counters, and replication described above. A
  globally-free page record establishes only that bounded decoder output and
  raw tag; its role and owners do not establish a current owner or purpose. The
  same evidence boundary applies to an unreferenced decoder-labeled LVAL page.
  A referenced appended LVAL page is required to be globally in use. The
  result cannot establish arbitrary boundary placement, other field or name
  shapes, chains longer than two continuation pages, allocation policy, writer correctness,
  compatibility, public creation, hosted differential `#102`, or
  support-matrix movement.
- Usage: issue `#151`; `EXP-0059`; `EXP-0061`; `EXP-0065`; `EXP-0073`; `EXP-0087`;
  `EXP-0091`; `EXP-0093`; `EXP-0095`; `EXP-0098`; `EXP-0100`; `EXP-0102`;
  `EXP-0103`;
  `file:oracle/windows-dao/acquisition/definition-continuation.plan.json`;
  `file:oracle/windows-dao/scripts/dev/DefinitionContinuation.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/definition_continuation.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: two independent review/fix passes covered the free-page evidence
  boundary, appended-page inventory, definition and `LvProp` references,
  result-state classification, exact pins, and cross-document agreement. They
  restricted `unassigned` acceptance to globally-free pages, disclaimed current
  semantics for every globally-free decoded label, and required referenced LVAL
  pages to be globally in use. Final focused verification found no remaining
  findings.


### EXP-0105 — Accepted definition-continuation placement result

- Recorded: 2026-09-02, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO accepted result
  derived from the canonical analyzer report; descriptive provider observation
  only, not a writer-correctness, compatibility, or support result
- Question: when the exact established `Alpha(Id Long)` small-definition
  control and the exact 70- and 140-field wide-definition targets are each
  created from an independently copied empty first-create image, do their 66-,
  2,075-, and 4,105-byte definitions decode to exactly zero, one, and two
  continuation pages; where do those definition pages land and point; and what
  bounded decoded page-role record, raw tag, and global-map state does every
  appended page carry?
- Origin and binding: project-authored clean-room experiment using the exact
  `EXP-0104` preregistration merged as commit
  `cbeb187092edd15b92784ed72b5ebdcbe2e7645f` (PR `#173`) with plan SHA-256
  `1ffa5af6bea302d89f61384fcb427dc889df9606e9c5f59db807d069da9b5c6f`.
  Run `20260903T004148Z-dev-dao` produced the validated canonical report.
- Input identity: all eight staged inputs matched their exact lowercase SHA-256
  pins in the plan and the corresponding bytes in the merged commit.
- Authorization and dispatch: the user explicitly authorized one acquisition
  of the exact merged successor. Run `20260903T004148Z-dev-dao` was dispatched
  exactly once and was not retried. That dispatch count is the observed operator
  action, not a fact derived from the retained artifacts.
- Environment: Windows NT 10.0.20348.0 build 20348 on AMD64; x86 Windows
  PowerShell Desktop 5.1.20348.558; .NET 4.0.30319.42000; culture and UI culture
  `en-US`; ANSI code page 1252; OEM code page 437; `Pacific Standard Time` at
  UTC-07:00. The accepted x86 `DAO.DBEngine.36` provider reported version 3.6
  from `dao360.dll` file version 03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
- Producer and validation result: all three replicas reported `pass`, phase
  `complete`, and `mutation_started=true`. Each retained the exact four-file
  checkpoint inventory and three arm baselines. The canonical report has status
  `accepted`; continuation counts, placement, counters, producer outcome, and
  replication are all `answered`. The complete observations are identical
  across all three replicas. The report makes no compatibility claim and moves
  no support status.
- Definition observations: every created table's definition root is page 20,
  and every definition root and continuation page is globally in use with raw
  tag 2. The 66-byte `zero` definition has chain `[20]` and zero continuations. The
  2,075-byte `one` definition has chain `[20, 68]`; its root contributes 2,048
  logical bytes and page 68 contributes 27. The 4,105-byte `two` definition has
  chain `[20, 219, 218]`; pages 20, 219, and 218 contribute 2,048, 2,040, and 17
  logical bytes in chain order. These observations establish that continuation
  placement need not be numerically consecutive or ascending for these exact
  shapes.
- Appended-page observations: the `zero`, `one`, and `two` arms append 3, 49,
  and 200 pages, respectively. In every arm page 21 is an in-use `map_rows`
  page with raw tag 1. Every decoder-labeled LVAL page below has raw tag 1. The
  `zero` arm has catalog-`LvProp`-referenced, globally in-use LVAL page 22. In
  the `one` arm, pages 22--47 are globally free with decoded role
  `unassigned` and raw tag 9; pages 48--65 are globally free,
  decoder-labeled LVAL pages unreferenced by the catalog `LvProp`; and pages 66
  and 67 are referenced, globally in-use LVAL pages. In the `two` arm, pages
  22--47 are likewise globally free with decoded role `unassigned` and raw tag
  9; pages 48--214 are globally free, decoder-labeled LVAL pages unreferenced by
  the catalog `LvProp`; and pages 215--217 are referenced, globally in-use LVAL
  pages.
- Counter observation: page 0 changes at offset 1538, and its bounded counter
  moves from 0 to 2 in every arm. This is a relative observation for these
  exact creates, not a general semantic assignment for that byte.
- Evidence boundary: decoded roles and owners on globally free pages classify
  retained bytes only. They establish no current owner, allocation purpose,
  reuse history, or other semantic role. In particular, the decoder-labeled
  LVAL ranges above are not catalog `LvProp` references and are not evidence
  that those free pages remain allocated. The result covers only the exact
  `Alpha`, 70-field, and 140-field shapes. It does not establish placement for
  arbitrary schemas, general allocation policy, free-page reuse history,
  chains longer than two continuation pages, catalog `LvProp` grammar, writer
  correctness, compatibility, public creation, hosted differential `#102`, or
  support-matrix movement.
- JSON artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `7154adb958f9a4ff13ea521b94bca7435d6adfe41642ef5f280812b89793cb54`;
  external `definition-continuation-job-result.json`, 505,531 bytes, SHA-256
  `ce72a17d8c69661509025a9f5256fda3c3693102ee8578982df789a4f621fded`;
  external `result.json`, 618,829 bytes, SHA-256
  `8322a2baa6179025469c5b91770ca26f716e1be0ed0bcc4373ec732d62a82ecc`;
  external canonical `definition-continuation-report.json`, 48,188 bytes,
  SHA-256
  `a5643bc9c07b1770d7e43ef505efc3b08a1690580e3275ffbc3e930f358d3119`.
- Retained database inventory: exactly twelve external MDBs, four per replica,
  totaling 2,039,808 bytes. The canonical filename-sorted JSON array of
  `{name,size,sha256}` objects, serialized with two-space indentation, sorted
  keys, and one trailing LF, is 1,926 bytes with SHA-256
  `fa5aafd6613336f00953aa77a24958ed5fff71d43dcc829d5a007589c2b6af7a`.
  The MDBs and inventory serialization remain outside the repository and were
  not used directly as evidence for this entry; the facts above come from the
  validated canonical report.
- Interpretation: issue `#151` is evidence-complete for its exact
  preregistered questions and is close-ready. A bounded implementation may use
  these exact counts, pointer order, placement, and retained page-state
  observations while preserving the evidence boundary above.
- Usage: issue `#151`; `EXP-0059`; `EXP-0061`; `EXP-0065`; `EXP-0073`;
  `EXP-0087`; `EXP-0094`; `EXP-0095`; `EXP-0098`; `EXP-0100`; `EXP-0102`;
  `EXP-0103`; `EXP-0104`; future separately reviewed definition-continuation
  implementation
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: the first independent report-identity and provenance review/fix pass
  added exact staged-input, authorization, one-shot dispatch, environment,
  provider, and raw-tag bindings while preserving the evidence boundary.
  A second independent cross-document and roadmap-status review verified the
  canonical report and artifact identities, additive provenance, issue closure,
  and exact-shape limitations and found no remaining findings.

### EXP-0106 — Null-LvProp indexed and continuation preregistration

- Recorded: 2026-09-03, OpenAI Codex
- Kind: SHA-256-pinned, development-only local DAO preregistration; no
  acquisition has occurred
- Question: does DAO 3.6 read the exact null-`LvProp` indexed candidate and
  exact compact one-continuation candidate unchanged with the same bounded
  schema semantics as fresh DAO controls of each exact schema?
- Origin: issue `#178`, following the bounded Alpha acceptance in `EXP-0091`,
  the three-index observation in `EXP-0093`, and the continuation observation
  in `EXP-0105`. The accepted Alpha null-`LvProp` image is the positive
  candidate gate. The indexed arm uses the exact `IdxTri` schema. The wide arm
  uses the exact 70-field `ContOneX` schema but deliberately places its one
  continuation compactly at page 23; that placement is the tested hypothesis,
  not an admitted allocation rule.
- Protocol: generate the three candidates only from the manifest-pinned Rust
  source; stage and identity-check all candidates; copy and identity-check all
  nine candidate replicas before the first DAO mutation; then create fresh
  same-schema DAO controls and run the same eight read-only endpoints over six
  images per replica. Record size and SHA-256 before and after access. The
  analyzer requires exact roles, filenames, bounds, retained identities,
  schemas, and replicated outcomes.
- Decision rule: Alpha must be `observed_accepted`. Each target may be either
  `observed_accepted` or a stable `not_observed_accepted`; control failure,
  image mutation, replica disagreement, incomplete work, or an unclassifiable
  result yields `no_outcome`. Malformed data, pin or inventory failure, or a
  pre-mutation failure rejects without a report.
- Preregistration artifacts: plan
  `oracle/windows-dao/acquisition/lvprop-null-schemas.plan.json`, SHA-256
  `47ff5d3d9ac56f5d71a022e9d7221160577a2be1026977abded98b58e7ab40aa`;
  source manifest
  `oracle/windows-dao/acquisition/lvprop-null-schemas.sources.json`, SHA-256
  `ad1cce42ce80031c7554d3cc3c9c5dbcdd7044b7b76d99eeff07888fdbc144b0`;
  producer `oracle/windows-dao/scripts/dev/LvPropNullSchemas.DevJob.ps1`,
  SHA-256
  `4e6f661f275e1ae3d37a52fa069d9423279218104a242e14f327793b1da1d4b6`;
  analyzer `oracle/windows-dao/scripts/lvprop_null_schemas.py`, SHA-256
  `068fd5de60f74de17a10c4bddd33fa726414e38de9f447d48eb2bc03edb67ad7`.
  Candidate identities are Alpha 47,104 bytes at SHA-256
  `c9d012d6277a0a35ae4248581fc9458d9b270e56277819e84dc7f1f5e8009e21`,
  indexed 53,248 bytes at SHA-256
  `bb7e0d408a5e844dd0fbe6eae008a4ca31bd83f376e611339ad5f8385572835e`,
  and wide 49,152 bytes at SHA-256
  `81cfd7b86616f9928b71cab4398f26305d5dafdbe4bfa0a514e6f9b4146f1cf6`.
- Authorization: acquisition is forbidden until these exact reviewed bytes
  reach `main` and a human explicitly authorizes one run. Once the first DAO
  `CreateDatabase` begins, any failure is a scientific result and no retry is
  permitted without another human decision.
- Interpretation: a later accepted result may establish only whether DAO reads
  each exact candidate identity unchanged at the preregistered endpoints. It
  cannot establish null-`LvProp` acceptance for arbitrary schemas, a general
  property or continuation-allocation grammar, writer or publication
  correctness, compatibility, hosted differential `#102`, or support movement.
- Usage: future result for issue `#178`; `EXP-0091`; `EXP-0093`; `EXP-0105`;
  `file:oracle/windows-dao/acquisition/lvprop-null-schemas.plan.json`;
  `file:oracle/windows-dao/scripts/dev/LvPropNullSchemas.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/lvprop_null_schemas.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: pending independent review; no acquisition has occurred


### EXP-0107 — Accepted null-LvProp indexed and compact-continuation result

- Recorded: 2026-09-03, OpenAI Codex
- Kind: validated SHA-256-pinned, development-only local DAO accepted result
  derived from the canonical analyzer report; exact-candidate read observation
  only, not a general writer, compatibility, or support result
- Question: does DAO 3.6 read the exact null-`LvProp` Alpha, three-index
  `IdxTri`, and compact one-continuation `ContOneX` candidates unchanged with
  the same bounded schema semantics as fresh same-schema controls?
- Origin and binding: project-authored clean-room experiment using the exact
  `EXP-0106` preregistration merged as commit
  `c2038cefeca3db18c9eebf8592e6741d39dcac7f` (PR `#179`) with plan SHA-256
  `47ff5d3d9ac56f5d71a022e9d7221160577a2be1026977abded98b58e7ab40aa`.
  All seven staged executable inputs and all 101 manifest-pinned candidate
  source files matched the merged preregistration.
- Authorization and dispatch: after merge, the user explicitly authorized the
  single local-VM acquisition. Run `20260903T030100Z-lvprop-schemas` was
  dispatched exactly once and was not retried. That dispatch count is the
  observed operator action, not a fact derived from retained artifacts.
- Environment: Windows NT 10.0.20348.0 build 20348 on AMD64; x86 Windows
  PowerShell Desktop 5.1.20348.558; .NET 4.0.30319.42000; culture and UI
  culture `en-US`; ANSI code page 1252; OEM code page 437; `Pacific Standard
  Time` at UTC-07:00. The accepted x86 `DAO.DBEngine.36` provider reported
  version 3.6 from `dao360.dll` file version 03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
- Protocol and validation: before the first experimental DAO mutation, the
  producer copied and identity-checked all nine candidate replicas. It then
  created nine fresh DAO controls and ran all eight ordered read-only endpoints
  over candidate and control Alpha, indexed, and wide images in each of three
  replicas. Every replica and image reported `pass`. The pinned analyzer
  rechecked the exact job-result shape, plan and candidate pins, retained-file
  identities, bounds, inventory, controls, schema semantics, unchanged-image
  gate, and cross-replica agreement before applying the preregistered decision
  rule. Rerunning the staged analyzer reproduced the report byte-for-byte.
- Artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `07cd01ea7cf53aeea79c99e6779960e61e4f039ab1b762d25713c6da6a631291`;
  external `lvprop-null-schemas-job-result.json`, 287,019 bytes, SHA-256
  `d0bc2f4c35c67116d18a0f123e51f9a0d920679c0a70a465f39450b80e827a89`;
  external `result.json`, 343,855 bytes, SHA-256
  `a755d7cf26a5a1d524cc81e52f2c959970aa3743d9c4af296fa07921f743f0f9`;
  external canonical `lvprop-null-schemas-report.json`, 5,531 bytes, SHA-256
  `3fa9328d03cdeb0ef1da412c0be0a846227392f3ad027d64fe81c7b966481f7d`.
- Retained candidates: each Alpha replica remained 47,104 bytes at SHA-256
  `c9d012d6277a0a35ae4248581fc9458d9b270e56277819e84dc7f1f5e8009e21`;
  each indexed replica remained 53,248 bytes at SHA-256
  `bb7e0d408a5e844dd0fbe6eae008a4ca31bd83f376e611339ad5f8385572835e`;
  and each wide replica remained 49,152 bytes at SHA-256
  `81cfd7b86616f9928b71cab4398f26305d5dafdbe4bfa0a514e6f9b4146f1cf6`.
- Retained controls: Alpha replicas 1--3 remained 47,104 bytes at SHA-256
  `8f7d96f7cb46929f54790971e53bdf25d5c1ff583a8aaf65e03193098cf3e501`,
  `c1813e87cdadb0637f547dfa4d8395fc14f3ad5725769870dd93544ee20981fb`,
  and `495e6dc423360150437aedfb658a3341ddf29cf69d2db617e6192c853b3c15e5`.
  Indexed controls remained 53,248 bytes at SHA-256
  `bae6ff198fe5b267ab00522fd246aceb928dac1368bc39b84ec4f7092f743e04`,
  `d9247ab276f66fecddc5cef5dcdb6c636f2cf0c9990eaff12b9ee12dcc1bf4bf`,
  and `749f5bda65cd02fc3478de85e5a14e404ac2f336ba18a32c171837cb7b6d2d9a`.
  Wide controls remained 141,312 bytes at SHA-256
  `bf0a3100c693656b3e34f6456e300e7c2eb442f31d960b01548b6c1793d741f0`,
  `a233908a65a7c76aa3a22c0f637c085ec70608f7b34c39065f14cad581ba111d`,
  and `951abd0a0fba4fd12c74e2c2062f56201c233d82b5a4ac57e8648646ed9934ae`.
  All eighteen images had identical size and SHA-256 before and after DAO
  access; the retained MDB inventory totals 1,173,504 bytes.
- Observation: the canonical report has status `accepted`; `alpha`, `indexed`,
  and `wide` each have status `observed_accepted`; and
  `compatibility_claim=false` and `support_movement=false`. In all three
  replicas every candidate and fresh control completed `open_database`,
  `version`, `tabledefs`, `direct_lookup`, `fields`, `indexes`, `snapshot`, and
  `document`. Each candidate's normalized schema observation matched its fresh
  same-schema control exactly and agreed across replicas.
- Interpretation: DAO 3.6 consumed unchanged the exact composed `IdxTri` image
  with three indexes and a null catalog `LvProp`, and the exact composed
  70-field `ContOneX` image with a null catalog `LvProp` and one compact
  continuation at page 23. This answers issue `#178` for those exact candidate
  identities and permits a future separately reviewed implementation to target
  that exact compact continuation construction. It does not establish null
  `LvProp` acceptance for arbitrary schemas, page 23 as a general placement
  rule, other one-continuation sizes or schemas, multiple continuations,
  allocation policy, writer or publication correctness, compatibility, hosted
  differential `#102`, or support-matrix movement.
- Usage: issue `#178`; `EXP-0091`; `EXP-0093`; `EXP-0105`; `EXP-0106`; future
  separately reviewed bounded continuation implementation
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: pending independent outcome, artifact-identity, report-reproduction,
  decision-rule, evidence-boundary, and false-claim review


### EXP-0108 — Multi-table create preregistration

- Recorded: 2026-09-03, Claude Fable 5.1
- Kind: SHA-256-pinned, development-only local DAO preregistration; no
  acquisition has occurred
- Question: does DAO 3.6 read one exact composed database holding the
  `EXP-0087` Alpha, Beta, Gamma, and Delta tables unchanged, with the same
  bounded schema semantics as a fresh DAO control that creates the same four
  tables in the same order?
- Origin: issue `#100`, following the later-create observations in `EXP-0087`
  and the exact one-table null-`LvProp` acceptances in `EXP-0091` and
  `EXP-0107`. The composer plans later creates from `EXP-0087`: each appends
  its definition root, map page, and index roots after the previous table and
  carries no `LvProp` page, and every catalog `LvProp` is null. No experiment
  has observed DAO read a composed image holding more than one user table;
  this candidate is that tested hypothesis, not an admitted rule.
- Protocol: generate the single candidate only from the manifest-pinned Rust
  source; stage and identity-check it; copy and identity-check all three
  candidate replicas before the first DAO mutation; then, per replica, create
  one fresh DAO control holding the four tables in create order and run the
  same eight read-only endpoints over the candidate and the control, each
  endpoint covering all four user tables. Record size and SHA-256 before and
  after access. The analyzer requires exact roles, filenames, bounds, retained
  identities, field types and sizes, index flags, and replicated outcomes.
- Decision rule: the `quad` question is `observed_accepted` when all three
  controls and all three candidate replicas pass unchanged with identical
  semantics, and a stable `not_observed_accepted` when the controls pass while
  every candidate replica stops identically or completes with the same
  semantic mismatch; control failure, image mutation, replica disagreement,
  incomplete work, or an unclassifiable result yields `no_outcome`. Malformed
  data, pin or inventory failure, or a pre-mutation failure rejects without a
  report.
- Preregistration artifacts: plan
  `oracle/windows-dao/acquisition/multi-table-create.plan.json`, SHA-256
  `2a66746a9f897bef9e4583649249c245c955d6fb9b484408f4f90eb4cfebe127`; source manifest
  `oracle/windows-dao/acquisition/multi-table-create.sources.json`, SHA-256
  `06f483190ecb9351c698d0304bfc20df6b85a77e7db8fda2e72efd15015f5ba8`; producer
  `oracle/windows-dao/scripts/dev/MultiTableCreate.DevJob.ps1`, SHA-256
  `87f46a9d3da3bbf7cae7ecde7c3d797ec9e85b52060ed16df4db925617924c55`; analyzer
  `oracle/windows-dao/scripts/multi_table_create.py`, SHA-256
  `8632f1a53aad740f6f8514e8f86bfa813febbe951317f0047365a836b7037037`. The candidate identity is 63,488 bytes at SHA-256
  `f4bad46de7c24ba92c0c9472d128eed48a2dbf1469594372d1098068940545ee`: Alpha(Id Long); Beta(Id Long, Name Text 50, Note Memo);
  Gamma(Id Long) with primary index `PrimaryKey`; Delta(Label Text 30) with
  ordinary index `ByLabel`; roots at pages 20, 23, 25, and 28; one retained
  empty long-value page at page 22; page-zero byte 1538 at 8.
- Authorization: acquisition is forbidden until these exact reviewed bytes
  reach `main` and a human explicitly authorizes one run. Once the first DAO
  `CreateDatabase` begins, any failure is a scientific result and no retry is
  permitted without another human decision.
- Interpretation: a later accepted result may establish only whether DAO reads
  this exact candidate identity unchanged at the preregistered endpoints. It
  cannot establish acceptance for other table counts, orders, names, or
  schemas, a general `LvProp` or allocation rule, five or more tables, writer
  or publication correctness, compatibility, hosted differential `#102`, or
  support movement.
- Usage: future result for issue `#100`; `EXP-0087`; `EXP-0091`; `EXP-0107`;
  `file:oracle/windows-dao/acquisition/multi-table-create.plan.json`;
  `file:oracle/windows-dao/scripts/dev/MultiTableCreate.DevJob.ps1`;
  `file:oracle/windows-dao/scripts/multi_table_create.py`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: pending independent review; no acquisition has occurred


### EXP-0109 — Multi-table create successor preregistration

- Recorded: 2026-09-03, Claude Fable 5.1
- Kind: SHA-256-pinned, development-only local DAO preregistration; no
  experimental acquisition has occurred
- Question: the single `EXP-0108` `quad` question, unchanged.
- Origin: after `EXP-0108` merged as commit
  `cf1e224b0a67515072f94e5c94acef0f5dcddb0c` (PR `#185`) and the user
  authorized one run, dispatch `20260903T220845Z-multi-table` failed before
  any experimental DAO mutation: the staged remote runner passed its optional
  per-job paths as empty native arguments, which Windows PowerShell drops, so
  the staged dispatcher rejected the invocation for a missing
  `LvPropNullSchemasJobPath` value and no candidate or control was created,
  opened, or read. Only the provider probe's disposable readiness database
  was created. That pre-mutation failure rejects without a report under the
  `EXP-0108` decision rule and is an infrastructure defect, not a scientific
  result. This successor pins the corrected runner, which passes each optional
  path only when it is non-empty, and leaves the plan text, candidate identity,
  producer, analyzer, decision rule, and evidence boundary of `EXP-0108`
  unchanged.
- Protocol: as `EXP-0108`.
- Decision rule: as `EXP-0108`.
- Preregistration artifacts: plan
  `oracle/windows-dao/acquisition/multi-table-create.plan.json`, SHA-256
  `5778f3b561de27bc3506fd53b3aea9f6be894b05b205e14743ea49806a14aeb8`; source manifest
  `oracle/windows-dao/acquisition/multi-table-create.sources.json`, SHA-256
  `06f483190ecb9351c698d0304bfc20df6b85a77e7db8fda2e72efd15015f5ba8`; corrected runner
  `oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1`, SHA-256
  `591bea836a5200f2dc0248492cf5b00f6e5758b421c59b262f101f7ef37523b1`; producer
  `oracle/windows-dao/scripts/dev/MultiTableCreate.DevJob.ps1`, SHA-256
  `87f46a9d3da3bbf7cae7ecde7c3d797ec9e85b52060ed16df4db925617924c55`; analyzer
  `oracle/windows-dao/scripts/multi_table_create.py`, SHA-256
  `8632f1a53aad740f6f8514e8f86bfa813febbe951317f0047365a836b7037037`. The candidate identity remains 63,488 bytes at SHA-256
  `f4bad46de7c24ba92c0c9472d128eed48a2dbf1469594372d1098068940545ee`.
- Authorization: acquisition is forbidden until these exact reviewed bytes
  reach `main` and a human explicitly authorizes one run. Once the first DAO
  `CreateDatabase` begins, any failure is a scientific result and no retry is
  permitted without another human decision.
- Interpretation: as `EXP-0108`; this entry adds no format evidence.
- Usage: future result for issue `#100`; `EXP-0108`;
  `file:oracle/windows-dao/acquisition/multi-table-create.plan.json`;
  `file:oracle/windows-dao/scripts/dev/Invoke-Jet3DaoDevJob.ps1`
- Rights: future project-generated MDBs and provider outputs remain outside
  the repository and are neither committed nor redistributed
- Review: pending independent review; no experimental acquisition has occurred


### EXP-0110 — Accepted multi-table create result

- Recorded: 2026-09-03, Claude Fable 5.1
- Kind: validated SHA-256-pinned, development-only local DAO accepted result
  derived from the canonical analyzer report; exact-candidate read observation
  only, not a general writer, compatibility, or support result
- Question: does DAO 3.6 read the exact composed database holding the
  `EXP-0087` Alpha, Beta, Gamma, and Delta tables unchanged, with the same
  bounded schema semantics as a fresh DAO control that creates the same four
  tables in the same order?
- Origin and binding: project-authored clean-room experiment using the exact
  `EXP-0109` successor preregistration merged as commit
  `ea493e1020f29045bb1401ec897bcc15dbfa4bfd` (PR `#186`) with plan SHA-256
  `5778f3b561de27bc3506fd53b3aea9f6be894b05b205e14743ea49806a14aeb8`. The
  staged runner, probe, dispatcher, publisher, producer, analyzer, and all
  manifest-pinned candidate source files matched the merged preregistration
  before staging and again before analysis.
- Authorization and dispatch: after merge, the user's standing authorization
  for this required experiment covered one local-VM acquisition. Run
  `20260903T221303Z-multi-table` was dispatched exactly once and was not
  retried. The earlier `EXP-0108` dispatch `20260903T220845Z-multi-table`
  failed before any experimental DAO mutation and produced no observation.
- Environment: Windows NT 10.0.20348.0 build 20348 on AMD64; x86 Windows
  PowerShell Desktop 5.1.20348.558; .NET 4.0.30319.42000; culture and UI
  culture `en-US`; ANSI code page 1252; OEM code page 437; `Pacific Standard
  Time` at UTC-07:00. The accepted x86 `DAO.DBEngine.36` provider reported
  version 3.6 from `dao360.dll` file version 03.60.9765.0, SHA-256
  `4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac`.
- Protocol and validation: before the first experimental DAO mutation, the
  producer copied and identity-checked all three candidate replicas. It then
  created three fresh DAO controls holding Alpha, Beta, Gamma, and Delta in
  create order and ran all eight ordered read-only endpoints, each covering
  all four user tables, over the candidate and control of each replica. Every
  replica and image reported `pass`. The pinned analyzer rechecked the exact
  job-result shape, plan and candidate pins, retained-file identities, bounds,
  inventory, control size and semantics, field types and sizes, index flags,
  the unchanged-image gate, and cross-replica agreement before applying the
  preregistered decision rule. Rerunning the staged analyzer on the host
  reproduced the report byte for byte.
- Artifacts: external `environment.json`, 4,277 bytes, SHA-256
  `c26e49147783edc857b79d1dadc7b3b39135c9a0fffd09521bccfd54feb0e33b`;
  external `multi-table-create-job-result.json`, 88,109 bytes, SHA-256
  `9aefe3e1f422e1d6ee3d298823e8360fa1c13001292ad473c1ce14b759fea502`;
  external `result.json`, 104,637 bytes, SHA-256
  `5ba7139e7183dbb297bec3137e0f923d4d7735b789bc760bb9e01438f39bdfc9`;
  external canonical `multi-table-create-report.json`, 2,081 bytes, SHA-256
  `d4f06b024a949fbb68349e4ce539ff50af30fd9ad584e9dab218178a4ee696a9`.
- Retained candidates: each candidate replica remained 63,488 bytes at SHA-256
  `f4bad46de7c24ba92c0c9472d128eed48a2dbf1469594372d1098068940545ee`.
- Retained controls: replicas 1--3 remained 63,488 bytes at SHA-256
  `25f6a4bcf7f15bfaad460d2dce103017c949326b4ed302a999546c83852fdc0f`,
  `3a6f6506b9143b6105f66563928c8e2b390eb72b4c3a2a8f6b38ed6530ec35df`, and
  `80fa4119fb356afc90abbff2e0e97ee32dbb41a1ae24167e4d879cc23f8e4b8e`. All
  six images had identical size and SHA-256 before and after DAO access; the
  retained MDB inventory totals 380,928 bytes.
- Observation: the canonical report has status `accepted`, the `quad`
  question has status `observed_accepted`, and `compatibility_claim=false`
  and `support_movement=false`. In all three replicas the candidate and the
  fresh control completed `open_database`, `version`, `tabledefs`,
  `direct_lookup`, `fields`, `indexes`, `snapshot`, and `document`. Each
  candidate's normalized observation matched its control exactly and agreed
  across replicas: `TableDefs` and the `Tables` documents held exactly Alpha,
  Beta, Gamma, Delta, and the four system tables; Alpha and Gamma reported
  `Id` Long size 4; Beta reported `Id` Long size 4, `Name` Text size 50, and
  `Note` Memo size 0; Delta reported `Label` Text size 30; Gamma's
  `PrimaryKey` was primary, unique, and required on ascending `Id`; Delta's
  `ByLabel` was none of those on ascending `Label`; every snapshot was empty.
- Interpretation: DAO 3.6 consumed unchanged the exact composed 31-page image
  holding four user tables created from the `EXP-0087` later-create pattern,
  with a null catalog `LvProp` on every table and a long-value page only for
  the first create. This answers the `EXP-0108` question for that exact
  candidate identity and permits a future separately reviewed implementation to
  target that exact later-create construction, as `EXP-0107` did for the
  compact continuation. It does not establish acceptance for other table
  counts, orders, names, or schemas, a general `LvProp` or allocation rule,
  five or more tables, later creates with more than one index or a
  continuation, writer or publication correctness, compatibility, hosted
  differential `#102`, or support-matrix movement. Any implementation built on
  it must keep refusing layouts outside the observed construction and must not
  describe its output as DAO-compatible.
- Usage: issue `#100`; `EXP-0087`; `EXP-0091`; `EXP-0107`; `EXP-0108`;
  `EXP-0109`; future separately reviewed bounded multi-table implementation
- Rights: all project-generated MDBs and provider outputs remain outside the
  repository and are neither committed nor redistributed
- Review: pending independent outcome, artifact-identity, report-reproduction,
  decision-rule, evidence-boundary, and false-claim review


### EXP-0111 — Initial-row creation preregistration

- Recorded: 2026-09-04, OpenAI Codex
- Kind: SHA-256-pinned development-only local DAO experiment preregistration
- Question: does DAO read the exact composed `Rows(Id Long, Code Text 8)`
  database with rows `(1, "one")`, `(-2, "two")`, and `(null, null)` unchanged,
  with the same schema and row multiset as fresh DAO controls?
- Origin: project-authored candidate generated by
  `crates/jet3/examples/initial_row_candidate.rs` at source commit
  `df7c42e6e67803703d8814a27f71a9251f9fccd6`. The candidate uses one
  unindexed table and one appended data page, retaining the composed page-zero
  header without insertion counter updates. This is an experimental hypothesis.
- Environment: private local Windows VM, x86 PowerShell, `DAO.DBEngine.36`.
- Protocol: `oracle/windows-dao/acquisition/initial-rows.plan.json`, SHA-256
  `0fb2a14ec8d4e73b881f72b543233599733ef57f2edc78deb12b74c4897be291`. Commit and review this plan
  before acquisition. Host checks all input pins and the committed plan; guest
  checks its script pin and all three candidate copies before creating the first
  fresh control. Read controls and candidates read-only, record schema and row
  multiset, and require unchanged identities plus replica agreement. One dispatch
  only; a post-mutation failure requires a new human decision before any retry.
- Artifacts: exact candidate is 49,152 bytes, SHA-256
  `5d1dc9148f58d5a3c19c75b86368d7ee9deacf6a95c0fa6746944f8cc3b322f4`; retained externally.
  The plan pins the producer, analyzer, imported transport, and Rust example.
- Observation: acquisition has not started; no DAO outcome is recorded here.
- Interpretation: neither compatibility nor hosted support-matrix evidence.
  Scope excludes multiple data pages, indexes, long values, and updates to
  existing databases. Record one separate additive outcome from the validated
  canonical report after acquisition.
- Usage: issue `#100`; initial-row creation candidate experiment only.
- Rights: project-generated MDB bytes and provider binaries are not committed.
- Review: independent harness implementation review completed without blocking
  findings; five focused analyzer tests and PowerShell syntax check passed.


### EXP-0112 — Accepted initial-row creation result

- Recorded: 2026-09-04, OpenAI Codex (run timestamp is 2026-09-05 UTC)
- Kind: validated SHA-256-pinned, development-only local DAO result
- Question: does DAO read the exact `EXP-0111` initial-row candidate unchanged
  with the expected schema and row multiset in all three replicas?
- Origin and binding: `EXP-0111`, committed before acquisition as
  `6778b9209b8e569a71ba265bc3f7e5129e6f6498`; plan
  `oracle/windows-dao/acquisition/initial-rows.plan.json`, SHA-256
  `0fb2a14ec8d4e73b881f72b543233599733ef57f2edc78deb12b74c4897be291`.
  Candidate source commit is `df7c42e6e67803703d8814a27f71a9251f9fccd6`.
  The plan pins the PowerShell producer, Python analyzer, imported transport,
  and Rust candidate example; those inputs remain unchanged.
- Authorization and dispatch: the user's standing authorization covered the
  single local run `20260905T030500Z-initial-rows`. It was dispatched once,
  after preregistration was committed and reviewed; no retry occurred.
- Environment: result records a 32-bit process, `DAO.DBEngine.36`, and
  `Microsoft Windows NT 10.0.20348.0`.
- Protocol and validation: the analyzer validated the plan/result binding,
  pinned candidate starting identities, retained image identities, unchanged
  before/after sizes and hashes, control schema and rows, and agreement across
  three replicas. Running the unchanged analyzer on temporary copies of the
  retained artifacts reproduced `report.json` byte for byte without modifying
  the originals.
- Artifacts: retained under local outbox `20260905T030500Z-initial-rows`:
  `result.json`, 24,291 bytes, SHA-256
  `888e4dacdc3f161af330c0c87f976da94623512c3c9e299c0cc64e601c436812`;
  canonical `report.json`, 378 bytes, SHA-256
  `476645a252e22f6ef1e0d9ac2cef3581128326815f88874f3477623e6d7aef04`.
- Retained candidates: all three remained 49,152 bytes, SHA-256
  `5d1dc9148f58d5a3c19c75b86368d7ee9deacf6a95c0fa6746944f8cc3b322f4`.
  Controls also remained 49,152 bytes, with replica hashes respectively
  `073b0ad2cc21aea6201486e729c9ebbfd6719fd167129146f798b7690b7b26b8`,
  `872d0f5d9c43681a9d03516152b1f52e4cc0051d4e9cc27994a5f3a1068f0b82`, and
  `fe2a358cae1b52210c5ddc91051ef0934f91ba3b9466ee255872db66877062c9`.
- Observation: the validated canonical report records `observed_accepted` for
  three replicas. All candidates and controls completed the read-only schema
  and row endpoints: version `3.0`, exactly `Rows` plus the four expected
  system tables, ordered fields `Id` Long size 4 and `Code` Text size 8,
  no indexes, and row multiset `(1, "one")`, `(-2, "two")`, `(null, null)`.
- Interpretation: DAO consumed the exact composed one-table, one-data-page
  image unchanged while it retained the composed page-zero header without
  insertion counter updates. This establishes no general header-update rule,
  other schemas or values, multiple data pages, indexed inserts, long values,
  existing-database mutation, general compatibility, or hosted write
  differential coverage. The support matrix does not move.
- Usage: issue `#100`; `create_database_with_rows` exact-candidate caveat.
- Rights: MDB bytes and provider binaries remain outside the repository.
- Review: independent outcome, report reproduction, artifact identity,
  additive-provenance, and evidence-boundary review completed without findings.


### EXP-0115 — Multiple-data-page initial rows preregistration

- Recorded: 2026-09-04, OpenAI Codex
- Kind: SHA-256-pinned development-only local DAO experiment preregistration
- Question: does DAO read the exact composed `Rows(Id Long)` image containing
  every integer from -254 through 254 unchanged, with the same schema and row
  multiset as fresh DAO controls in all three replicas?
- Origin: project-authored `multi_page_row_candidate` example at reviewed source
  commit `826b1318669071afc360659201fd8c012ff07bd0`. `EXP-0060`, `EXP-0065`,
  and `EXP-0057` supply row, append, and map grammar; `EXP-0112` established
  only the earlier exact one-page candidate. This candidate packs data pages
  23, 24, and 25 with 254, 254, and 1 rows. All three are owned; only page 25
  is marked available because the others lack physical room for an all-null
  row. That availability policy and retaining the composed page-zero header
  are experimental hypotheses, not established DAO policies.
- Environment: private local Windows VM, x86 PowerShell, `DAO.DBEngine.36`.
- Protocol: `oracle/windows-dao/acquisition/multi-page-rows.plan.json`, SHA-256
  `997070626c1b1c9f3e2daeae6c9a99333060c47916a2ac46c34e9c60073a50a2`. Commit and review before one
  dispatch. Host verifies all pins and committed plan; guest verifies producer
  and prepares all three pinned candidate copies before the first control
  creation. Controls insert the inclusive preregistered range. Read control
  and candidate schema and rows read-only, require unchanged identities and
  exact replicated semantics. The analyzer rechecks input pins before applying
  the decision rule. Post-mutation failure requires a new human decision before
  another dispatch.
- Artifacts: exact candidate is 53,248 bytes, SHA-256
  `57498e634b9e4e7102efd4f4c2d673cccd42c344b51590177c7704232df675af`. The plan pins
  the producer, analyzer, imported transport, and Rust candidate example.
- Observation: acquisition has not started; no DAO result recorded here.
- Interpretation: exact-candidate schema/row endpoints only. No matching control
  physical layout, general allocation policy, arbitrary schema or value support,
  indexes, long values, existing-database mutation, compatibility claim, or
  hosted support-matrix movement.
- Usage: issue `#100`; separately validated multiple-data-page initial rows.
- Rights: all MDB bytes and provider binaries remain outside the repository.
- Review: independent harness review requested input revalidation during
  analysis; that fix and a focused rejection test are implemented. Six analyzer
  tests and a PowerShell parser-only check pass.


### EXP-0116 — Accepted multiple-data-page initial-row result

- Recorded: 2026-09-04, OpenAI Codex (run timestamp is 2026-09-05 UTC)
- Kind: validated SHA-256-pinned, development-only local DAO result
- Question: does DAO read the exact `EXP-0115` candidate containing 509 Long
  values across three data pages unchanged, matching fresh controls in all
  three replicas?
- Origin and binding: `EXP-0115` preregistration committed before acquisition
  as `513b80e4dfd35124d80c4580c8c905717f7a5b83`; plan
  `oracle/windows-dao/acquisition/multi-page-rows.plan.json`, SHA-256
  `997070626c1b1c9f3e2daeae6c9a99333060c47916a2ac46c34e9c60073a50a2`.
  Candidate source is `826b1318669071afc360659201fd8c012ff07bd0`.
- Authorization and dispatch: user authorization covered single run
  `20260905T032600Z-multi-page-rows`, dispatched once after the exact plan was
  committed and reviewed. No retry occurred.
- Environment: result records a 32-bit process, `DAO.DBEngine.36`, and
  `Microsoft Windows NT 10.0.20348.0`.
- Protocol and validation: the unchanged analyzer rechecked all preregistered
  input pins, plan/result binding, candidate starting identities, retained
  image identities, unchanged before/after sizes and hashes, expected control
  schema and row multiset, and agreement across three replicas. Running it on
  temporary copies of retained artifacts reproduced the canonical report byte
  for byte; retained originals were read only.
- Artifacts: local outbox `20260905T032600Z-multi-page-rows` retains
  `result.json`, 745,900 bytes, SHA-256
  `af1189cbe265c5b93871ef84e4570d5cd3804d619091bfb8e716dd515ca8632c`;
  canonical `report.json`, 466 bytes, SHA-256
  `c1bb6ac0e2294bccc36dd1c1f31daf7f88785996575333ff2fe914c491680f50`.
- Retained candidates: each remained 53,248 bytes, SHA-256
  `57498e634b9e4e7102efd4f4c2d673cccd42c344b51590177c7704232df675af`.
  Controls also remained 53,248 bytes, with replica hashes respectively
  `a515b61b4022f403809503051029c7692e77b6421ec466dbe1a9b612989e0fe3`,
  `500ead2e707f9d1acdfbd1a40538b0e5c888c9dc0bbb1de3ddfb23d98efbe7fe`, and
  `4bb744e1c1ee6687487c2414be9bf00b3d239a65571b9dc395b5fa09726ba9cf`.
- Observation: canonical report records `observed_accepted`, three replicas,
  `compatibility_claim=false`, and `support_movement=false`. All six images
  completed schema and row endpoints: version `3.0`, exactly `Rows` plus the
  four expected system tables, one `Id` Long field of size 4, no indexes, and
  exactly one occurrence of each integer from -254 through 254.
- Interpretation: DAO consumed unchanged the exact candidate whose three data
  pages hold 254, 254, and 1 rows, with all three owned, only the final page
  available, and the composed page-zero header retained. These are the pinned
  candidate's construction choices; this result establishes no general DAO
  availability threshold, header-update rule, or matching control layout.
  Other schemas or values, indexed rows, long values, existing-database
  mutation, general compatibility, and hosted support coverage remain outside
  this result. The support matrix does not move.
- Usage: issue `#100`; bounded multiple-data-page initial-row construction.
- Rights: MDB bytes and provider binaries remain outside the repository.
- Review: independent report, input and retained-artifact identity, row
  snapshot, additive-provenance, and evidence-boundary review completed
  without findings.


### EXP-0119 — Indexed initial-row candidate preregistration

- Recorded: 2026-09-04, OpenAI Codex
- Kind: SHA-256-pinned development-only local DAO experiment preregistration
- Question: does DAO read the exact primary, unique, and ordinary-duplicate
  initial-row candidates unchanged, including full index traversal and Seek?
- Origin: reviewed `indexed_row_candidate` example at
  `54d7d22b930b824c48d7afb0d44591f48c5b485d`. `EXP-0062` supplies
  Long-key and leaf framing; `EXP-0073` supplies distinct-key counts. `EXP-0116`
  established only an exact unindexed candidate. Each indexed arm creates
  `Rows(Id Long, Payload Text 255)` with ascending `ById`, 20 rows in caller
  order, and distinct payloads `a` through `t`, each repeated 255 times.
  Primary and unique keys are 9 through -10; ordinary keys are 9 through 0
  twice. The ordinary leaf has 20 entries and a definition distinct count of
  10; the other arms have 20 distinct keys. All use leaf page 23 and data
  pages 24--26 with 7, 7, and 6 rows.
- Environment: private local Windows VM, x86 PowerShell, `DAO.DBEngine.36`.
- Protocol: `oracle/windows-dao/acquisition/indexed-rows.plan.json`, SHA-256
  `5d78e83ad3b00f1a460610580d2da42aae793c9f7c143f423525e9cb0a5b812c`. Commit and independently
  review before one dispatch. Verify input pins on dispatch and analysis;
  prepare and verify all nine candidate copies before the first control
  creation. Each arm has three fresh DAO controls and three read-only candidate
  observations. Verify full schema/index metadata, snapshot row multiset,
  ordered index traversal containing every Id/payload pair, and Seek for every
  distinct key. Duplicate traversal tie order is unspecified; ordinary Seek
  may select either matching payload, while full traversal must contain both.
  Require every control to pass and all 18 images to remain unchanged before
  classifying candidate agreement per arm. Post-mutation failure requires a
  new human decision before another dispatch.
- Artifacts: each candidate is 55,296 bytes, with SHA-256 values:
  primary `79b5e6c1a03418a0c6c9a99170cd6c67660b0d57eb56142354c5cbccc63cfa11`;
  unique `cc4bd47bac0c57d7daa763b4675cb6dbe19dc886be733abf27edeebe7346b3c2`;
  ordinary `9f517c410f65a3d5ec3ebd7829f7203dffb9594e95846413ef980290c03c5cd9`.
  The plan pins producer, analyzer, imported transport, and Rust example.
- Observation: acquisition has not started; no DAO outcome recorded here.
- Interpretation: these three exact candidates only. No null/descending/
  composite-key rule, general B-tree allocation, DAO free-space or page-zero
  policy, update correctness, general compatibility, or hosted support claim.
- Usage: issue `#100`; bounded indexed initial-row construction.
- Rights: all MDB bytes and provider binaries remain outside the repository.
- Review: independent harness review completed without findings; six focused
  classifier tests and a PowerShell parser-only preflight passed.


### EXP-0120 — Accepted indexed initial-row matrix

- Recorded: 2026-09-04, OpenAI Codex (run timestamp is 2026-09-05 UTC)
- Kind: validated SHA-256-pinned, development-only local DAO result
- Question: does DAO consume the three exact `EXP-0119` indexed initial-row
  candidates unchanged, with matching schema, full traversal, and Seek results?
- Origin and binding: `EXP-0119` preregistration committed before acquisition
  as `aea93f2d6db996a07559e19dae5f90bc0e500c17`; plan
  `oracle/windows-dao/acquisition/indexed-rows.plan.json`, SHA-256
  `5d78e83ad3b00f1a460610580d2da42aae793c9f7c143f423525e9cb0a5b812c`.
  Candidate source is `54d7d22b930b824c48d7afb0d44591f48c5b485d`.
- Authorization and dispatch: user authorization covered the single run
  `20260905T034700Z-indexed-rows`, dispatched once after the exact plan was
  committed and independently reviewed. No retry occurred.
- Environment: result records a 32-bit process, `DAO.DBEngine.36`, and
  `Microsoft Windows NT 10.0.20348.0`.
- Protocol and validation: the unchanged analyzer verified input pins,
  plan/result binding, candidate starting pins, all retained identities,
  unchanged-image and complete-control gates, full expected schema and index
  flags, row multisets, ascending index traversal with every Id/payload pair,
  and Seek for every distinct key. Running it on temporary artifact copies
  reproduced the canonical report byte for byte; retained originals were
  read only.
- Artifacts: local outbox `20260905T034700Z-indexed-rows` retains
  `result.json`, 772,830 bytes, SHA-256
  `5085ce246792bd3985aeac42ea0e496ccd4d5a7dcaef0648a7be5315ea452438`;
  canonical `report.json`, 719 bytes, SHA-256
  `48982bdb6e11805fb33c3c7c3bcfeacedc051f8f2ef769ca87b0369e384421ca`.
- Retained candidates: all three replicas of each arm remained 55,296 bytes;
  primary SHA-256 `79b5e6c1a03418a0c6c9a99170cd6c67660b0d57eb56142354c5cbccc63cfa11`,
  unique `cc4bd47bac0c57d7daa763b4675cb6dbe19dc886be733abf27edeebe7346b3c2`,
  ordinary `9f517c410f65a3d5ec3ebd7829f7203dffb9594e95846413ef980290c03c5cd9`.
  Controls also remained 55,296 bytes. Their replica 1--3 hashes are:
  primary: `fe31c485f55d208cc92b4b7d35487b60870a0815ec7ae2b1edd3f77ee977ff72`,
  `c728febfed944743bf287cc6ec10786c97d0ac081e846593900215dee53b6756`,
  `2a6e36ff52501623110aed8e99f4e7993cf3f422ca7979411da782e6c015c2da`.
  unique: `fdadbf9272af30d38734e61bd481af587df92e64a12a017eda8c9c12ffc78598`,
  `e0881148cc2a39006fbaed4314aa98a2dc55acb2dbacf13ab4ac665d6f9844c5`,
  `4b6617fb3a313c01875776cc3c4b282f7223b82a2f6ff69b361b5c3396e1b6c8`.
  ordinary: `c6ce4fb9419a86586bf8972d7ceecb07ac9c06b856f79c05cc7e6b9d887ca21b`,
  `0c85b2875ce3a0aabfacb131f08fa2f13c73620f228abbed3a6971c4a59e3d62`,
  `9a8a6c907693e031475bfdc64a9e20d5fe2835b439181d4869ad5887abd0ffb0`.
- Observation: all three arms record `observed_accepted` with three replicas
  each; report flags are `compatibility_claim=false` and
  `support_movement=false`. All 18 images completed the endpoints unchanged.
  Each had `Rows(Id Long, Payload Text 255)`, one ascending `ById` index, and
  all 20 expected rows with distinct repeated-character payloads. Primary
  reported Primary/Unique/Required true; unique reported only Unique true;
  ordinary reported all three false. Primary and unique traversed keys -10
  through 9; ordinary traversed two occurrences of each key 0 through 9.
  Every expected Id/payload pair occurred once during full traversal, and Seek
  returned an expected matching pair for every distinct key.
- Interpretation: DAO read these exact one-leaf, three-data-page candidates
  unchanged, including the ordinary candidate's 20 leaf entries and definition
  prefix distinct count of 10. This validates those construction choices only;
  duplicate traversal tie order and which ordinary duplicate Seek selects
  remain unspecified. No general index, null, descending, composite, B-tree
  allocation, page-zero, free-space, or update rule is established. There is
  no general compatibility claim or hosted support-matrix movement.
- Usage: issue `#100`; bounded indexed initial-row creation.
- Rights: MDB bytes and provider binaries remain outside the repository.
- Review: outcome implementation pass complete; independent review pending.


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


### EXP-0113 — Preregistered first and second relationship creation observations

- Recorded: 2026-09-04, OpenAI Codex
- Kind: SHA-256-pinned local development experiment preregistration; no DAO
  acquisition or format observation has occurred under this plan
- Question: Which exact reciprocal logical records, hidden names, physical
  indexes/maps, relationship catalog IDs and rows, and standalone system
  text-index keys result from creating the first and second relationship
  between two fresh empty tables, without an intervening query?
- Origin: original project-authored observation harness using only the
  recorded `EXP-0057`, `EXP-0059`, `EXP-0060`, `EXP-0062`, and `EXP-0073`
  grammar; no third-party MDB implementation was inspected
- Protocol: three fresh CP1252 `dbVersion30` replicas, each creating
  `Parent(Id Long, Alternate Long)` with primary `ById` and unique
  `ByAlternate`, then `Child(ParentId Long, Alternate Long)`. Capture
  closed `base`, then `first` after `ParentChild` (`Id` to `ParentId`), then
  `second` after `AlternateLink` (`Alternate` to `Alternate`), both with
  attributes zero. Capture read-only DAO Relations metadata and verify
  unchanged bytes; retain MDBs externally.
- Preregistration artifact:
  `oracle/windows-dao/acquisition/relationship-create.plan.json`, SHA-256
  `2a8e70e1eb34d5cf8019d7bc4840e2d8798da8ea7e44d5e73d9b23a0bb9a926e`. The plan pins the acquisition script,
  analyzer, reused system-catalog decoder, and local transport helper.
- Decision: complete matching question-bearing values across all three
  replicas produce `answered`; scientific failures or disagreement produce
  `no_outcome`. Preserve physical locators and raw records. Invalid retained
  identities reject analysis. No retry after the first mutation without
  another human decision.
- Boundary: only these exact names, two relations, schemas and creation
  order are tested. No automatic generalized hidden-name, selector, ID or
  text-weight grammar, compatibility claim, or support-matrix movement.


### EXP-0114 — First and second relationship creation observations

- Recorded: 2026-09-05, OpenAI Codex
- Kind: validated local development observation under `EXP-0113`; not a
  hosted compatibility result or support-matrix update
- Preregistration: `oracle/windows-dao/acquisition/relationship-create.plan.json`,
  SHA-256 `2a8e70e1eb34d5cf8019d7bc4840e2d8798da8ea7e44d5e73d9b23a0bb9a926e`.
  No consumed plan, script, or decoder was changed after acquisition.
- Artifacts: local run `20260905T030800Z-relationship-create`, external
  `result.json` SHA-256
  `b3f7ca80ce87a64e1460f119f6ef7556ee7d243ef1ce1787aad3d71f15520929`,
  `report.json` SHA-256
  `4e2caa03b59390fb8ae342b0e4e4f9d71092b1470b43be693b049774b6a19a7b`.
  Read-only host re-analysis verified the pinned inputs and retained MDB
  identities and reproduced the report byte-identically. MDBs remain external.
- Environment and result: x86 `DAO.DBEngine.36` on Windows NT
  `10.0.20348.0`; `mutation_started=true`, no acquisition error. All nine
  checkpoints completed, read-only metadata opens left bytes unchanged, and
  every emitted question-bearing value agreed across three replicas. The
  report outcome is `answered`, with no reasons, `development_only=true`,
  `compatibility_claim=false`, and `support_matrix_movement=false`.
- Exact scenario: empty `Parent(Id Long, Alternate Long)` has primary
  `ById` on `Id`, then unique `ByAlternate` on `Alternate`; empty
  `Child(ParentId Long, Alternate Long)` initially has no indexes. The
  `first` checkpoint adds `ParentChild`, `Parent.Id` to `Child.ParentId`;
  `second` adds `AlternateLink`, `Parent.Alternate` to `Child.Alternate`.
  Both DAO relations have attributes zero; no saved query was created.
- Catalog observations: `ParentChild` has `Id=-2147483648` (`0x80000000`);
  `AlternateLink` has `Id=-2147483647` (`0x80000001`). Both have
  `ParentId=251658243` (`0x0f000003`), `Type=8`, `Flags=0`, and
  `Owner=0301`. Each ID has two `MSysACEs` rows: `SID=0301`, `ACM=983294`,
  and `SID=0201`, `ACM=1048575`, both `FInheritable=false`. These are the
  two observed IDs and access-control rows, not a generalized assignment rule.
- Relationship rows: `MSysRelationships` stores `ParentChild` at page 27
  row 0, with `szObject=Child`, `szColumn=ParentId`,
  `szReferencedObject=Parent`, and `szReferencedColumn=Id`. `AlternateLink`
  adds page 27 row 1 with the same object names and both column names
  `Alternate`. Each row has `grbit=0`, `ccolumn=1`, and `icolumn=0`.
- Reciprocal records: `Parent` remains rooted at page 20 and `Child` at
  page 25. The following little-endian fields describe the complete sourced
  logical-record selectors and references. Every row has context bytes
  `[17,19)=00 00` and class byte `[19]=2`.

  | Table / logical name | `[0,4)` | `[4,8)` | Side `[8]` | `[9,13)` | Related root `[13,17)` |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | Parent / `.rC` | 2 | 0 | 1 | 0 | 25 |
  | Child / `ParentChild` | 0 | 0 | 2 | 2 | 20 |
  | Parent / `.rD` | 3 | 1 | 1 | 1 | 25 |
  | Child / `AlternateLink` | 1 | 1 | 2 | 3 | 20 |

  The first two records appear at `first` and remain unchanged at `second`;
  the last two appear at `second`. Parent logical-name order is `.rC`,
  `ByAlternate`, `ById`, then `.rC`, `.rD`, `ByAlternate`, `ById`.
  Child order is `ParentChild`, then `AlternateLink`, `ParentChild`.
  Only these exact hidden names and selector values were observed; no
  formula for other index counts, names, or relationship orders is inferred.
- Index/map observations: Parent's two physical indexes remain empty at
  roots 23 and 24, flags `0x09` and `0x01`, with map locators page 21
  rows 2 and 3 naming those roots. Child gains empty ascending physical
  index 0 on column 0 at root 28, map page 26 row 2; then index 1 on
  column 1 at root 29, map page 26 row 3. Both child index flags are
  zero, and each map names only its root. Parent's owned/available maps
  remain empty at page 21 rows 0/1; Child's remain empty at page 26 rows
  0/1. Both user table row counts remain zero.
- System index observations: `MSysRelationships` owned/available maps at
  page 12 rows 8/9 both name page 27 after `first` and `second`; its row
  count advances from 0 to 1 to 2. Its physical index maps at page 12
  rows 10/11/12 continue to name roots 15/16/17, flags `0x02` each.
  All three leaf common prefixes are empty. Exact uncompressed keys are:

  | Root / field | Text | Key hex |
  | --- | --- | --- |
  | 15 / `szRelationship` | `ParentChild` | `7f73607566707762696a6d6400` |
  | 15 / `szRelationship` | `AlternateLink` | `7f606d776675706077666d6a706c00` |
  | 16 / `szObject` | `Child` | `7f62696a6d6400` |
  | 17 / `szReferencedObject` | `Parent` | `7f73607566707700` |

  At `first`, each root has one entry pointing to page 27 row 0.
  At `second`, root 15 stores `AlternateLink` pointing to row 1 before
  `ParentChild` pointing to row 0; roots 16 and 17 each repeat their
  respective key pointing to rows 0 then 1. Distinct-key counts at roots
  15/16/17 are `1/1/1` at `first` and `2/1/1` at `second`. These four
  keys do not establish a general standalone-text weight grammar.
- Allocation observations: page counts are 27, 29, and 30 at `base`,
  `first`, and `second`. No extant page is globally free. The raw byte at
  page-zero offset 1538 is respectively 0, 2, and 4; no counter formula
  or meaning is inferred from these values.
- Boundary: these observations support only the exact two empty-table
  schemas, index and relationship creation order, names, and zero cascade
  attributes above. They do not establish Rust relationship composition,
  general relationship naming/selector allocation, populated relationships,
  a DAO-compatible candidate, or public API support.
- Review: independent outcome review checked the retained JSON, input and
  artifact hashes, byte-identical report reconstruction, raw record fields,
  catalog values, index keys/maps, and scope boundaries; no findings.


### EXP-0117 — Preregistered first-relationship candidate DAO validation

- Recorded: 2026-09-05, OpenAI Codex
- Kind: SHA-256-pinned local development preregistration; no acquisition
  or acceptance observation has occurred under this plan
- Question: Does DAO read the exact composed first `ParentChild` candidate
  with the same table/field/index metadata, relation fields/options, and
  empty user rows as three fresh same-schema DAO controls, without changing
  either image during read-only opens?
- Basis: `EXP-0114` supplies the exact relationship records, map placements,
  IDs/ACEs, and index keys; the candidate retains the existing null-`LvProp`
  bootstrap construction. This combination has no prior DAO acceptance.
- Candidate: 59,392 bytes, SHA-256
  `9afa3647fc3619cad95002eebdddf0dd14a9fe067a9621dcd0a3635b3582609d`,
  exported from reviewed composer commit `1afb82de04dbe343dd55c14b974997b72ac364e3`
  by the ignored `export_relationship_candidate` test. MDB bytes remain
  outside the repository.
- Plan: `oracle/windows-dao/acquisition/relationship-candidate.plan.json`,
  SHA-256 `01357b430a038a450f88fef8e88e1c8477e99979d52f603eceb4763378af41f0`. The plan pins
  acquisition/analyzer/transport inputs and the bounded candidate module
  and exporter; the image hash itself binds the acquisition candidate.
- Protocol: three fresh controls, each with `Parent(Id Long, Alternate Long)`
  and primary `ById`, unique `ByAlternate`, then `Child(ParentId Long,
  Alternate Long)` and zero-attribute `ParentChild` from `Id` to `ParentId`.
  Read each control and candidate separately read-only; capture user-table
  and field attributes, every index's flags and ordered field bindings,
  relation fields/options, and complete empty-row snapshots. Rehash after
  closing; no physical-byte equality with the controls is required.
- Decision: unchanged complete candidate/control agreement across all three
  replicas is `observed_accepted`; identical candidate failures/differences
  with valid unchanged controls are `not_observed_accepted`; incomplete
  acquisition, changed bytes, control failure, or replica disagreement is
  `no_outcome`. Invalid identities reject validation. Standalone analysis
  verifies input pins. No redispatch after first mutation without another
  human decision.
- Boundary: only this exact image and read-only endpoints; no generalized
  relationship API, second relation, populated tables, cascades, integrity
  enforcement, existing-database updates, or hosted support-matrix claim.


### EXP-0118 — Accepted exact first-relationship composer candidate

- Recorded: 2026-09-05, OpenAI Codex
- Kind: validated local development DAO differential under `EXP-0117`;
  exact private-candidate acceptance, not generalized or hosted support
- Plan: `oracle/windows-dao/acquisition/relationship-candidate.plan.json`,
  SHA-256 `01357b430a038a450f88fef8e88e1c8477e99979d52f603eceb4763378af41f0`.
  No consumed plan, acquisition script, analyzer, or candidate source was
  changed after acquisition.
- Artifacts: run `20260905T033000Z-relation-candidate`, external `result.json`
  SHA-256 `2b0e5970dea5c04aac236c7aa168e9936d113ae918b597d193fce95072fde5e4`,
  `report.json` SHA-256
  `3b87a0c4e44fb974715b2f29bed4482b53532a29e24dd4272855635c686616a6`.
  Read-only re-analysis verified input pins and retained identities and
  reproduced the report byte-identically. MDBs remain external.
- Candidate: exactly 59,392 bytes (29 Jet 3 pages), SHA-256
  `9afa3647fc3619cad95002eebdddf0dd14a9fe067a9621dcd0a3635b3582609d`,
  exported from the private first-relationship composer reviewed in
  `EXP-0117`. This is the candidate combining `EXP-0114`'s first relation
  records with the existing null-`LvProp` bootstrap construction.
- Environment and result: x86 `DAO.DBEngine.36` on Windows NT
  `10.0.20348.0`. All three independent fresh DAO controls and candidate
  copies reached `complete`, with status `pass`, no endpoint error, and
  unchanged hashes across read-only opens. Complete candidate/control
  snapshots agreed in every replica. The report is `observed_accepted`
  with no reasons, `development_only=true`, `compatibility_claim=false`,
  and `support_matrix_movement=false`.
- DAO observations: version `3.0`; exactly user tables `Parent` and `Child`
  plus `MSysACEs`, `MSysObjects`, `MSysQueries`, and `MSysRelationships`.
  Parent fields are `Id`, `Alternate`; Child fields are `ParentId`,
  `Alternate`, in that order. Each field has DAO Type 4, Size 4, Attributes
  1. Both user-table Attributes are zero and both row snapshots are empty.
- Index observations: Parent exposes `ByAlternate`, unique and nonprimary
  on ascending `Alternate`, and `ById`, primary, unique and required on
  ascending `Id`. Both have `Foreign=false`, `IgnoreNulls=false`;
  `ByAlternate` has `Required=false`. Child exposes `ParentChild` on
  ascending `ParentId`, with `Foreign=true` and `Primary`, `Unique`,
  `Required`, and `IgnoreNulls` all false. Every index field has Attributes
  zero. These complete metadata snapshots match the controls; no physical
  byte equality with a DAO-created control was required.
- Relationship observation: exactly `ParentChild`, Table `Parent`,
  ForeignTable `Child`, Attributes zero, with one field named `Id` and
  ForeignName `ParentId`, matching the fresh controls. This establishes the
  recorded read-only metadata and empty-row endpoints only; no referential
  integrity mutation or cascade behavior was exercised.
- Boundary: this result accepts only the exact private image above. It
  establishes no general hidden-name/selector/text-key formula, renamed or
  differently indexed construction, second relationship, populated table,
  cascade behavior, public relationship API, existing-database update, or
  hosted support-matrix claim.
- Review: independent outcome review verified input and retained-image
  identities, byte-identical report reconstruction, all recorded metadata
  claims, and the exact-candidate evidence boundary; no findings.

## EXP-0121 — Preregistered renamed relationship candidate matrix

- Recorded: 2026-09-05, OpenAI Codex
- Kind: preregistered local development DAO differential; no acquisition yet
- Plan: `oracle/windows-dao/acquisition/parameterized-relationships.plan.json`,
  SHA-256 `f4168b5e575303cc52247756d64da072a898e997c84cc7f9253d1f16db8bfe5f`.
  Pins the new host/analyzer and PowerShell scripts, transport, private
  composer/plan/export source, and both exact candidate identities.
- Candidates: reviewed source `6058627` exports one-index `Accounts7` /
  `Events9` / `Account7Events9` (57,344 bytes, SHA-256
  `9d6d850ae06b4a4317f2640e468b0afae7ac248808c8c0ac2fbb211f2ce87927`)
  and two-index `Owners2` / `Details4` / `Owner2_Details4` (59,392 bytes,
  SHA-256 `25472960e097ac3539a1da8cf7d3d10e588637b209c991a578e095b74c629fd9`).
  Parent columns are `Code2 Long`, `Key1 Long`, with primary `Primary9` on
  `Key1` and, in the second arm, unique `Unique8` on `Code2`. Child columns
  are `Label3 Text(8)`, `Account4 Long`; the relation binds `Key1` to
  `Account4` with attributes zero. Both user tables are empty.
- Question: do both exact renamed candidates match fresh DAO controls at
  every captured table/column/index flag and binding, relation endpoint and
  option, and empty-row snapshot? Bounded `.rB`/`.rC` placements derive from
  `EXP-0059`/`EXP-0114`; applying `EXP-0087` standalone name weights remains
  an explicit candidate hypothesis. `EXP-0118` accepted only the original
  fixed-name candidate.
- Protocol: prepare all six hash-checked candidate copies before mutation;
  acquire three fresh controls and read-only candidate observations per
  arm, in finite plan order. Require unchanged file identities and exact
  within-arm replica/control agreement; no cross-arm or control-byte
  equality. Host analysis rechecks pins and retained identities. Failure
  after first mutation is an outcome, never an automatic retry.
- Boundary: commit and review before one acquisition. No public API,
  generalized relationship grammar, integrity-enforcement mutation,
  cascade, populated relation, update, or hosted support claim. Record one
  validated additive outcome as `EXP-0122`; retain MDBs externally.

## EXP-0122 — Accepted two renamed relationship constructions

- Recorded: 2026-09-05, OpenAI Codex
- Kind: validated local development DAO differential under `EXP-0121`
- Plan: `oracle/windows-dao/acquisition/parameterized-relationships.plan.json`,
  SHA-256 `f4168b5e575303cc52247756d64da072a898e997c84cc7f9253d1f16db8bfe5f`.
  Acquisition used the committed, reviewed plan and pinned inputs. Outcome
  validation preceded subsequent public-API source changes; the consumed
  plan, acquisition script, and analyzer remain unchanged.
- Artifacts: run `20260905T035000Z-parameterized-relations`, external
  `result.json` SHA-256
  `4d7b99001f3829a6fe9d421ab15641e7ab490f968fa4fe0b35f6a54149f23981`,
  `report.json` SHA-256
  `8b2527103cb4dc633bb5c82579c3ee40d9ea1da16d90cda2f33e666796a555e9`.
  Pinned-input analysis of temporary artifact copies verified every retained
  identity and reproduced the report byte-identically. MDBs remain external.
- Result: x86 `DAO.DBEngine.36`, Windows NT `10.0.20348.0`; both arms and
  the matrix report `observed_accepted`, with no reasons. All three fresh
  controls and three candidate replicas per arm reached `complete` with
  status `pass`, no errors, and unchanged hashes. Every captured metadata
  and row observation agreed within each arm and between candidate/control.
- Exact images: one-index `Accounts7` / `Events9` / `Account7Events9`,
  57,344 bytes, SHA-256
  `9d6d850ae06b4a4317f2640e468b0afae7ac248808c8c0ac2fbb211f2ce87927`;
  two-index `Owners2` / `Details4` / `Owner2_Details4`, 59,392 bytes,
  SHA-256 `25472960e097ac3539a1da8cf7d3d10e588637b209c991a578e095b74c629fd9`.
- Common metadata: DAO version `3.0`, exactly the two declared user tables
  plus `MSysACEs`, `MSysObjects`, `MSysQueries`, `MSysRelationships`. User
  table Attributes are zero. Parent columns, in order, are `Code2`, `Key1`,
  each Type 4, Size 4, Attributes 1. Child columns are `Label3` (Type 10,
  Size 8, Attributes 2), then `Account4` (Type 4, Size 4, Attributes 1).
  All user row snapshots are empty.
- Index metadata: parent `Primary9` on ascending `Key1` is primary, unique,
  and required. The second arm additionally exposes `Unique8` on ascending
  `Code2`, unique, nonprimary, and not required. Both parent indexes have
  Foreign and IgnoreNulls false. The child exposes its relationship-named
  index on ascending `Account4`, Foreign true, with Primary, Unique,
  Required, and IgnoreNulls false. Every index field has Attributes zero.
- Relation metadata: exactly the declared named relation in each arm,
  pointing from its parent table's `Key1` to its child table's `Account4`,
  Attributes zero. This observes both bounded one/two-parent-index shapes
  with reordered linked columns and digit-bearing names (including the
  second relation name's underscore), using the candidate's standalone
  `EXP-0087` text-key composition. It does not establish all possible name
  weights, selector formulas, or schema combinations.
- Boundary: these exact candidates at read-only metadata and empty-row
  endpoints only. No physical byte equality with controls, referential
  integrity mutation, cascade, populated relation, existing-database update,
  general compatibility, or hosted support claim. All report compatibility
  and support-matrix flags remain false.

## EXP-0125 — Preregistered descending and composite Long key discovery

- Recorded: 2026-09-05, OpenAI Codex
- Kind: preregistered local development DAO format observation; no acquisition
- Plan: `oracle/windows-dao/acquisition/long-key-layout.plan.json`, SHA-256
  `a5b107359cc713ecb613826745fea1118084d9c6f0cc482a527cdc6f4a0a9f4a`.
  Pins the new acquisition/analyzer scripts, existing original
  `system_catalog.py` row/definition decoder, `relationship_create.py` leaf
  decoder, and SSH transport. The plan contains every input row.
- Question: which exact raw index keys bind to non-null descending Long and
  two-Long mixed-direction values, including repeated full ordinary keys?
  `EXP-0062` supplies bounded leaf framing and locators, but its retained
  summary JSON contains no raw key-to-row bindings and its composite case
  mixed Text, Integer, and Long. Those outputs inform design only here.
- Arms: fresh `Rows(A Long, B Long, Tag Long)` with `ByKey`; ten distinct
  descending `A` keys in a unique index; twelve distinct ascending `A` /
  descending `B` pairs in a unique index; fourteen descending `A` / ascending
  `B` rows in an ordinary index, with two repeated full pairs and distinct
  Tag payloads. Inputs include both signed extremes, values around zero and
  byte boundaries, and repeated leading components. No nulls or intentional
  failing insertions are included.
- Hypothesis: ascending Long components use `7f` plus big-endian sign-bit-
  flipped values; descending complements every component byte, and composite
  keys concatenate components in declared order. This is an explicit test
  hypothesis; stable differing bytes still answer the observation question.
- Protocol: three fresh controls per arm; close after insertion, hash, open
  read-only for declared metadata, full snapshots and ordered index scans,
  close and rehash. Recover exact raw keys through the established single-
  leaf decoder, bind every page/slot to decoded values, and require complete
  one-to-one row coverage and directed DAO/decoded semantic ordering. Compare
  raw key/value bindings, physical key fields/flags/counts, and captured DAO
  metadata within each arm. Retain locators and prefix/map/root observations
  without requiring allocator locations or duplicate payload order to match.
- Decision: all nine captures must complete unchanged with matching
  question-bearing observations. Record hypothesis agreement separately.
  Incomplete acquisition, changed bytes, or correlation disagreement is
  `no_outcome`; bad pins/result or retained identities reject validation.
  Commit and independently review before one acquisition. Failure after
  first mutation never triggers an automatic retry.
- Boundary: these finite non-null Long cases only; no other key types,
  arbitrary component counts, general branch/allocation policy, candidate
  acceptance, updates, compatibility, or hosted support claim. Retain all
  MDBs externally and record one validated additive outcome as `EXP-0126`.

### EXP-0123 — Initial Memo/OLE payload candidate preregistration

- Recorded: 2026-09-05, OpenAI Codex
- Kind: committed preregistration for a development-only DAO validation;
  acquisition has not started and no outcome is asserted.
- Question: Does DAO accept both exact writer-policy candidates with the
  expected complete schema, Memo strings/OLE bytes, and null rows while
  leaving every candidate unchanged?
- Plan: `oracle/windows-dao/acquisition/long-value-rows.plan.json`, SHA-256
  `d30fa45eee7ec8abf163538680bd5b5818b8fa05a15941422ad55bfd5b45c128`. Its input pins cover the producer, analyzer,
  low-level SSH transport and deterministic Rust example. Both dispatch and
  analysis verify the same input pins; acquisition requires this committed plan.
- Generator: `initial_long_value_candidate` from reviewed source
  `2d3adf2e985e28c81f21cf1d9b07fdf52849341f`.
- Candidate identities:
  - Memo: 73728 bytes, SHA-256
    `9302db2b9364aad2cf1fe8bf5b6d1ecb161e3f0b29d838990dc9dd2b695e98e5`.
  - OLE: 73728 bytes, SHA-256
    `8b13bd5cb99a8415fc90dc9c123cfdfcf673b14257affd384e0ed2f56c727d8c`.
- Both candidates contain unindexed `Rows(Id Long, Payload Memo/OLE)` and
  ten rows: Id1..9 carry lengths 1, 32, 33, 512, 2036, 2037, 2048, 4064 and
  4096; Id10 carries null. The plan fixes every payload character/byte.
  This tests inline/single/chained construction with twelve LVAL pages
  before one data page and retained composed page-zero bytes. EXP-0061,
  EXP-0077 and EXP-0065 supply the cited primitive grammar; the cutoffs and
  placement are candidate choices, not inferred DAO storage thresholds.
- Acquisition design: one job, three fresh same-schema DAO controls and
  three unchanged candidate replicas per type. Read complete schema and
  exact Id/payload multisets. Compare complete Memo strings and lowercase
  OLE hex, including null versus empty; retain all twelve files and identities.
- Decision: accept an arm only after the complete six-pair job, every control
  passes, all twelve images remain unchanged, and its three observations
  agree. Identical candidate failures yield `not_observed_accepted`; an
  incomplete job, changed image, control failure or replica disagreement
  yields `no_outcome`. Binding/pin/retention inconsistencies reject validation.
  A failure after the first mutation is a result, never an automatic retry.
- Preflight: six classifier tests passed; x86 VM PowerShell `Parser.ParseFile`
  accepted the producer without executing it or DAO. Independent experiment
  review precedes acquisition. Root owns the single authorized dispatch.
- Boundary: development-only, no support-matrix movement or general
  compatibility, storage-cutoff, empty-payload, multi-long-column, indexed
  long-value or mutation claim. Add the validated outcome once as EXP-0124.

### EXP-0124 — Initial Memo/OLE payload candidates accepted locally

- Recorded: 2026-09-05, OpenAI Codex
- Kind: validated development-only DAO outcome from the single authorized
  run `20260905T041400Z-initial-long-values`; no retry or support movement.
- Preregistration: EXP-0123, committed as
  `a0b0d9a`, plan `oracle/windows-dao/acquisition/long-value-rows.plan.json`,
  SHA-256 `d30fa45eee7ec8abf163538680bd5b5818b8fa05a15941422ad55bfd5b45c128`.
  The pinned producer, analyzer, transport and example were unchanged.
  Candidate generator source was
  `2d3adf2e985e28c81f21cf1d9b07fdf52849341f`.
- Artifacts: retained under local VM
  `shared/outbox/20260905T041400Z-initial-long-values`. `result.json` is
  344,430 bytes, SHA-256
  `22240565e6d0b8e3b12ad975413d8794e56336a7f3cd0e55ad8ade55126d69d6`;
  canonical `report.json` is 577 bytes, SHA-256
  `57bfeca97671a8aee106dc3f7f8b24d80fcc185423fe198f150a46dfea84bdd1`.
  Running the pinned analyzer on temporary copies reproduced the report
  byte-for-byte, including input-pin and retained-image validation; retained
  originals were verified unchanged. Independent outcome review follows.
- Environment: Windows NT 10.0.20348.0, 32-bit PowerShell, DAO.DBEngine.36.
  The report records `observed_accepted` for both `memo` and `ole`, six
  complete candidate/control pairs, no job error, and three agreeing
  candidate observations per arm. Every fresh matched control passed and
  all twelve before/after identities matched their retained files.
- Candidate files: every replica is 73,728 bytes. Memo SHA-256
  `9302db2b9364aad2cf1fe8bf5b6d1ecb161e3f0b29d838990dc9dd2b695e98e5`;
  OLE SHA-256
  `8b13bd5cb99a8415fc90dc9c123cfdfcf673b14257affd384e0ed2f56c727d8c`.
- Fresh control identities (each 73,728 bytes):
  - memo r1:
    `f0b4de501ec645171fa7bbe3ea83ed2e51d73525084b2f0582aa1bfc954604c0`.
  - memo r2:
    `8f93982a50f92e9367aac41e35a198de9d0c4d3e14c565d935dc42490c03d490`.
  - memo r3:
    `261caeee2b4c03e96cb28200092cf62b481a0a32a7950805f65c520a49dcaa46`.
  - ole r1:
    `7adb23e766a6a9dfcfb7824730346b12f2f9281e55d65e849f7ffacbfa3df41c`.
  - ole r2:
    `80aa49f23ea81dc6291e01dbf90d3ded5dd889dca0049f76aa67dbb332eece3b`.
  - ole r3:
    `b449f49898e1e7d226cbc756f1991b2cd6ab575b384346cc63c9db463821c946`.
- Validated semantics: DAO version 3.0; exactly the four expected system
  tables and `Rows`; no user-table indexes. Fields are Id/type4/size4 and
  Payload/type12/size0 for Memo or type11/size0 for OLE. Both snapshots
  contain the complete expected Id/payload multiset: Id1..9 have lengths
  1, 32, 33, 512, 2036, 2037, 2048, 4064 and 4096; Id10 has null.
  At zero-based row position r and payload offset p, Memo character is
  ASCII `A + ((p+r) mod26)` and OLE byte is `(p+r) mod256`. Validation
  compared every Memo character and every OLE byte, not just lengths or
  prefixes; the null payload remained distinct from empty.
- Boundary: acceptance covers the two exact pinned writer-policy candidates
  and their read-only endpoints. It does not establish universal inline or
  external storage thresholds, general allocation/page-zero policy, empty
  payloads, multiple long columns, indexed long columns, subsequent writes,
  general compatibility, or hosted support. All report compatibility and
  support-movement flags are false.

## EXP-0126 — Descending and two-component Long index key bindings

- Recorded: 2026-09-05, OpenAI Codex
- Kind: validated local development DAO observation under `EXP-0125`
- Plan: `oracle/windows-dao/acquisition/long-key-layout.plan.json`, SHA-256
  `a5b107359cc713ecb613826745fea1118084d9c6f0cc482a527cdc6f4a0a9f4a`.
  Consumed acquisition, analyzer, decoder, transport, and plan inputs remain
  unchanged. Acquisition ran once from the committed reviewed plan.
- Artifacts: run `20260905T041200Z-long-key-layout`; external `result.json`
  SHA-256 `74173d65d5d70cfdc78b717d9ae2dda748f36de790dace6065ee673934b20828`;
  23,965-byte `report.json` SHA-256
  `5b74ed896e9c4bf116dd9a52f4bcd39fc3fc6e6494464788dff61e68a9aeedf7`.
  Analysis of temporary copies verified pinned inputs and every retained
  identity and reproduced the report byte-identically. MDBs remain external.
- Result: `answered`, no reasons, all nine captures complete and unchanged.
  Three fresh replicas per arm agreed on every question-bearing value;
  every key locator resolved once to a planned row and all rows were covered.
  DAO snapshot and index traversal agreed with decoded rows and directed
  semantic order. All nine `hypothesis_matches` values are true. This is
  format observation, not acceptance of a Rust-created candidate.
- Observed keys: each non-null ascending Long component is `7f` followed by
  the four big-endian value bytes with the sign bit flipped. Descending
  complements all five bytes, including the marker (`80`). Both tested
  two-Long mixed-direction keys concatenate the components in declared field
  order. For example, descending `2147483647` is `8000000000`;
  ascending/descending `(-2147483648,2147483647)` is
  `7f000000008000000000`; descending/ascending
  `(2147483647,-2147483648)` is `80000000007f00000000`.
  Every recorded signed boundary and repeated-leading-component binding in
  the finite `EXP-0125` input matrix agrees with this construction.
- Index/row observations: `Rows(A Long,B Long,Tag Long)` has root 20, leaf
  root 23, data page 24, and index map page 21 row 2 in all captures. Leaf
  common-prefix length is zero. The descending unique arm has ten rows and
  ten physical distinct-key entries; the ascending/descending unique arm has
  twelve of each. The descending/ascending ordinary arm has fourteen rows,
  fourteen leaf entries, and physical distinct-key count twelve: `(0,0)` and
  `(-1,0)` each have two entries with different Tag payloads/row locators.
  Unique physical flags are 1, ordinary flags 0; recorded direction bytes
  are 0 for descending and 1 for ascending. Locators are retained evidence,
  not a general allocation rule or duplicate tie-order guarantee.
- Boundary: these exact non-null one/two-Long cases establish the recorded
  component construction and full-key distinct counting. Nulls, other types,
  more components, arbitrary branches, allocator policy, candidate
  compatibility, updates, and hosted support remain outside this result.
  Report compatibility and support-matrix flags remain false.


## EXP-0141 — Hosted write differential preregistration

- Status: preregistered; no hosted acquisition performed by this entry.
  Plan: `oracle/windows-dao/acquisition/write-v1_2.plan.json`, SHA-256
  `d5556c11bb1526d3fb067a6fd1c2196fdc4ffd7f52fa8dd03ef4104a5bfbeaf8`.
- Reviewed scaffold: `622bb14`. The plan pins the workflow, separate write
  inventory, public fixture generator, canonical snapshot/coverage producers,
  protocol validators, DAO helpers and evaluator. Both jobs use the same
  dispatched committed source revision; no build attestation is introduced.
- One authorized workflow dispatch runs the twelve declared `dao_open_rust`
  scenarios: empty database, supported scalars/nulls, AutoIncrement, primary,
  unique/descending, ordinary/composite indexes, multiple data pages/tables,
  Memo/OLE payload forms and a populated relationship. The existing read
  inventory remains a separate unchanged contract.
- Ubuntu creates each fixture with one public API call, captures its identity
  before reading, and checks the complete requested semantics. Windows 2022
  downloads those exact files, produces read-only Rust snapshots, probes its
  stock x86 DAO provider and observes the same files read-only. There is no
  runtime installation, license acceptance or Windows publication workaround.
- Require every scenario/source/image/receipt/platform binding and unchanged
  image hash, full canonical schema/typed-row/relationship agreement, independent
  request assertions, and every index's complete traversal and full-key Seek.
  Preserve duplicate payloads; Seek may choose any matching duplicate row.
- Missing provider, incomplete acquisition or any failed comparison prevents
  `matched`. Retain preparation/provider diagnostics and failed artifacts;
  evaluator failures emit `no_outcome`. No automatic retry or redispatch.
  Record one validated additive EXP-0142 result; no MDB/provider bytes in git.
- Coverage is explicitly bounded by the write inventory's deferrals. Earlier
  local `no_outcome` entries remain unchanged, including EXP-0132 and EXP-0139.
  Neither this plan nor self-validation establishes general compatibility,
  completion of #100 or support-matrix movement.

## EXP-0138 — Generated AutoIncrement candidates and subsequent inserts accepted locally

- Status: validated `observed_accepted` for all three EXP-0137 arms and all
  nine candidate/control pairs in the single local run
  `20260905T051300Z-autoincrement-candidate`. Independent review pending.
  Plan SHA-256:
  `503fc74b9179c3ea8c33a6f9606e5fdbf7d737c04b6dd9fbe3d9bd7594550cca`.
- Retained external `result.json`: 3,927,335 bytes, SHA-256
  `1680bc9f863d8a50757d793bb74bbab2c29931f903f073af11fb1ac7d02421a9`.
  `report.json`: 95,313 bytes, SHA-256
  `3610562d87ceabb1aabf2786e71d1722e8478b6a2640bb6d53430844b5cfcd0f`.
  The pinned analyzer reproduced the report byte-identically from temporary
  copies and verified all 36 retained initial/post-insert file identities.
- Accepted candidate identities, before copying for writable insertion:
  unindexed, 51,200 bytes, SHA-256
  `2d7e3910f86587a428eab82c0f9f8c46e4d97478bd7b9949a3da7aed7bdd65dc`;
  indexed, 51,200 bytes, SHA-256
  `d101881cf90ba1f71906b84b7cbab2473818fbad8551e522e9d868bb2a7ab2d2`;
  multi, 59,392 bytes, SHA-256
  `df823f4c3e40b442dc9220303e2ff81c0e669af55a280547c3c720b35dc9b0b2`.
- All three replicas agreed with fresh DAO controls on complete declared
  metadata and rows. `Id` was Long, size 4, attributes 17; `Tag` was Long,
  size 4, attributes 1. Indexed tables exposed ascending `PrimaryKey(Id)`
  with primary/unique/required true, foreign/IgnoreNulls false, and field
  attributes zero. Table inventories and attributes also matched exactly.
- Unindexed `Rows` began with 300 rows and last-generated state 300; one
  omitted-ID insertion with Tag 1001 generated ID 301 and left 301 rows
  with state 301. Indexed `Rows` similarly advanced 10 to 11. Complete
  indexed traversal and Seek inventories contained 10 then 11 matching rows.
- In the multi-table candidate, unindexed `Rows` independently advanced
  300 to 301, while indexed `Later` advanced 1 to 2. `Later` began with
  `(Id=1, Tag=-1)` and added `(Id=2, Tag=1001)`; its complete traversal and
  Seek inventories contained one then two rows. All other initial rows
  paired IDs and Tags 1 through the declared count. Decoded complete rows,
  TDEF counts and signed last-generated states agreed with DAO throughout.
- Controls passed; every read-only before/after identity was unchanged, and
  each writable copy started with its observed source's exact identity.
  Acquisition recorded mutation started and no error. The analysis used
  the preregistered private 256-slot decoder limit without further changes.
- This establishes only the three pinned scalar/index/table shapes and one
  subsequent generated insert per table. No explicit Auto ID assignment,
  high seed, deletion, overflow, relationship, LVAL, general compatibility
  or hosted support claim follows. Original EXP-0132 remains `no_outcome`;
  EXP-0136 remains a separate secondary analysis of that earlier acquisition.

## EXP-0137 — Generated AutoIncrement candidate preregistration

- Status: preregistered; no acquisition recorded here. Plan:
  `oracle/windows-dao/acquisition/autoincrement-candidate.plan.json`, SHA-256
  `503fc74b9179c3ea8c33a6f9606e5fdbf7d737c04b6dd9fbe3d9bd7594550cca`.
  Inputs pin the dedicated exporter, acquisition/analyzer scripts, original
  system-catalog decoder and transport. Candidate identities are pinned in
  the plan; source library commit `d106f83` has completed review and checks.
- Three arms: 300 generated unindexed rows; 10 generated rows with an
  ascending AutoIncrement primary index; and two tables with independent
  counters (300 unindexed rows and one indexed row). All tables have
  `Id AutoIncrement` and `Tag Long`. The dedicated exporter preserves earlier
  experiment candidates and uses the reviewed public generation API.
- Three fresh DAO controls per arm match the complete declared schema and
  insertion order. Observe each control and pinned candidate read-only with
  unchanged identities, then independently copy each closed file and insert
  once per table while omitting `Id`. Record its actual generated ID, close,
  and observe the post-insert copy read-only. Expected successors are 301,
  11 and, in the two-table arm, independently 301 and 2.
- Compare complete table/column/index metadata, every row, complete ascending
  traversal and Seek for every indexed ID, exact last-generated state and
  row count before and after insertion. Original decoding uses a private
  instance with its declared row-directory limit raised from 64 to 256;
  other structural checks remain unchanged. No analysis limit is retrofitted
  after acquisition. Ordinary row order and allocator locations are excluded
  from equality; no row, index entry or state is omitted.
- Require all nine complete pairs, valid controls, unchanged read-only
  identities, correct copy-start identities and three matching replicas.
  Repeatable candidate mismatch is `not_observed_accepted`; incomplete or
  invalid controls, acquisition error or disagreement yields `no_outcome`.
  Input/result or retained-identity mismatches reject validation. One attempt,
  no automatic retry after the first mutation; retain all 36 files externally.
- EXP-0136 supplies the state observation; EXP-0132 remains `no_outcome`.
  Record one EXP-0138 outcome. This tests only the declared generated starts
  and one subsequent insert, without explicit Auto ID assignment, high seeds,
  deletion, overflow, relationships, LVAL or general compatibility/support.

## EXP-0136 — Retained AutoIncrement state observations

- Status: validated `answered` secondary analysis under EXP-0135, not a new
  acquisition or a revision of EXP-0132's preserved `no_outcome`.
  Plan SHA-256:
  `13ab8dab2f129fa8a859545e896fa3376836cde938c14e673a842d1d87b5c96f`.
- External report: `20260905T045600Z-autoincrement-secondary/report.json`,
  1,080,091 bytes, SHA-256
  `670cc25e591afb33a987e6a8c6e26852e0543ab5a24939b903c25e876dfe01e6`.
  Reproduced byte-identically from temporary copies of the pinned original
  result/report and all 36 captures; originals remain unchanged. The only
  decoder adjustment was its private row-directory limit from 64 to 256.
- All three replicas passed the original correlations and all three finite
  hypotheses. In the AutoIncrement arm, TDEF `[16,20)` held signed
  little-endian values 0, 1, 255, 256, 256 and 257 at the empty, one, n255,
  n256, deleted and next checkpoints respectively. Row counts were 0, 1,
  255, 256, 255 and 256. Generated IDs matched insertion Tags; the next
  insertion after deleting Tag 256 generated ID 257 in every replica.
- Paired ordinary Long controls held zero throughout TDEF `[16,20)` with
  the same row counts and explicit ID/Tag values. User-definition changes
  in both arms were confined to existing row-count bytes `[12,16)` and,
  only for generated inserts in the AutoIncrement arm, `[16,20)`. Deletion
  changed row count but retained the last generated number. Thus this slot
  records last-generated state for these finite positive cases, rather than
  current row count or the largest surviving ID.
- This supports a bounded initial construction that generates IDs 1 through
  at most 256 and persists that last generated value. The state observed at
  257 followed deletion/reopening; no arbitrary high seed, explicit Auto ID,
  overflow, indexed generation or generalized update grammar was tested.
  Rust candidate acceptance and subsequent DAO insertion remain separate
  validation work. No compatibility or hosted support claim follows.

## EXP-0135 — Retained AutoIncrement captures secondary-analysis plan

- Status: post-acquisition secondary analysis planned; expanded analysis has
  not run. This is not a new preregistered acquisition. EXP-0132 remains an
  unchanged `no_outcome` from the original EXP-0131 experiment.
- Plan: `oracle/windows-dao/acquisition/autoincrement-reanalysis.plan.json`,
  SHA-256 `13ab8dab2f129fa8a859545e896fa3376836cde938c14e673a842d1d87b5c96f`.
  It pins the original result/report and all 36 retained MDB captures from
  `20260905T044800Z-autoincrement-layout`, plus the new harness and unchanged
  original analyzer, decoder and acquisition plan.
- Known limitation motivating this analysis: the original helper rejected
  24 captures containing a 169-slot page against its limit of 64. The sole
  adjustment is `MAX_ROWS_PER_PAGE` from 64 to 256 in a private instance of
  the original decoder. All structural checks and original schema, row,
  identity, replica and classification rules remain in effect.
- Commit and independent review precede expanded decoding. The harness
  checks pinned artifacts before and after analysis and creates a separate
  report outside the source outbox. That report identifies the original
  `no_outcome`, all artifact identities and the exact analysis adjustment.
  The original finite hypotheses remain hypotheses; stable disagreement is
  an answered observation under the unchanged decision rule.
- No DAO call, redispatch, new capture or source mutation is part of this
  experiment. No additional decoder changes or automatic retry are allowed.
  Record one validated secondary outcome as EXP-0136; neither this plan nor
  a successful decode establishes general compatibility or hosted support.

## EXP-0132 — AutoIncrement discovery produced no outcome

- Status: validated `no_outcome` from the single local EXP-0131 run
  `20260905T044800Z-autoincrement-layout`. Independent review pending.
- Consumed plan SHA-256:
  `c27de50741883f787a2280d0e92fde43089288ff284d77c4408f22dcc9f577f5`.
  Retained `result.json`: 1,678,940 bytes, SHA-256
  `91f10571fd8885b860b2a7c58f5ee40bc3050d64c1b14e31886340a367600fcb`.
  Retained `report.json`: 215,446 bytes, SHA-256
  `367f10da6f4966412dc3e02503d8e71cfa2f3d482db3edbab0b600e66319bb65`.
  Both remain under the run's external shared outbox. The pinned original
  analyzer reproduced the complete report byte-identically in memory;
  validation checked all 36 retained capture identities.
- Acquisition recorded `mutation_started=true`, no guest error, and 36
  checkpoint observations with DAO status `pass` and unchanged before/after
  identities. This does not satisfy the preregistered analysis decision rule.
- Analysis accepted only the 12 empty/one-row observations. Each of the 24
  `n255`, `n256`, `deleted` and `next` captures failed original decoding with
  `page 23 row directory: 169 rows exceed the bound of 64`. The report also
  records `Acquisition incomplete or failed: None` because its complete
  observation gate failed. Its hypotheses list is empty.
- The experiment therefore establishes no persisted-counter interpretation
  or next-generated-ID result. The original plan, scripts, result and
  `no_outcome` report are preserved; no retry or redispatch was performed.
  A separately preregistered analysis of retained artifacts would be a new
  experiment, not a revision of this result. No compatibility or hosted
  support claim follows.

## EXP-0131 — AutoIncrement persisted-state discovery preregistration

- Status: preregistered; no acquisition or outcome recorded by this entry.
- Plan: `oracle/windows-dao/acquisition/autoincrement-layout.plan.json`, SHA-256
  `c27de50741883f787a2280d0e92fde43089288ff284d77c4408f22dcc9f577f5`.
  The plan pins its PowerShell acquisition script, Python analyzer, original
  system-catalog decoder and VM transport. Dispatch requires the committed
  plan; dispatch and standalone analysis verify these input hashes.
- Question: identify persisted changes accompanying generated Long IDs and
  observe the next generated ID after closing, reopening and deleting the
  last generated row. EXP-0059 establishes AutoIncrement column metadata;
  neither that evidence nor EXP-0065/A9 establishes persisted counter semantics.
- Design: three replicas of two fresh unindexed tables, each named `Rows`
  with Long `Id` and `Tag`. The AutoIncrement arm omits `Id` on every insert;
  the ordinary Long control explicitly assigns `Id=Tag`. Each database has
  six closed checkpoints: empty, Tag 1, Tags 1 through 255, append Tag 256,
  delete Tag 256, then append Tag 257. Every checkpoint reopens the working
  database; all 36 closed copies are observed read-only and hashed before
  and after observation.
- Capture: complete DAO column metadata and signed row values, original
  decoder row locators/counts/maps/data pages, and complete raw page-zero,
  user-TDEF and catalog-TDEF/data pages. Successive captures retain exact
  changed byte ranges with both physical page locations. Replica comparison
  requires matching question-bearing metadata, rows and TDEF `[12,35)`;
  allocator addresses and catalog timestamps remain observations rather
  than requirements for equality.
- Hypotheses, not format facts: TDEF `[16,20)` contains the last generated
  signed little-endian Long, remains unchanged after deletion, and stays
  zero in the ordinary control; generated IDs equal the finite Tag sequence.
  A stable disagreement is an answered observation. The actual ID generated
  for Tag 257 is recorded without requiring it to equal 257.
- Decision: all declared captures, unchanged identities, schema/row
  correlations, surviving IDs and replica comparisons must pass. An
  incomplete acquisition or failed observation yields `no_outcome`; invalid
  input/result identities reject validation. No retry after the first DAO
  mutation. Retain MDBs and reports externally; record one validated outcome
  in EXP-0132.
- Scope: local discovery only. No explicit AutoIncrement ID assignment,
  high seed, signed overflow, indexed generation, Rust candidate acceptance,
  general compatibility or hosted support claim.

## EXP-0128 — Descending/composite initial-index candidates accepted locally

- Recorded: 2026-09-05, OpenAI Codex
- Kind: validated local development DAO differential under `EXP-0127`
- Plan: `oracle/windows-dao/acquisition/composite-index.plan.json`, SHA-256
  `3ee36358cb911f6fb008c591ebb0d061650854bb4d089bb87f1e11c12a9a8b92`.
  One acquisition used the committed reviewed plan; consumed inputs remain
  unchanged. Candidate generation used reviewed source `caf60a5`.
- Artifacts: run `20260905T043000Z-composite-index`; 478,899-byte external
  `result.json`, SHA-256
  `25d053eb1dd49945276967c4bdac5a96b19efd8a8fe8da3e6b44207cb5d34a21`;
  19,888-byte `report.json`, SHA-256
  `000f7758a1a05f5aaf05708cc573f757c5ec09f0454163f20d646849124302cf`.
  Pinned analysis on temporary artifact copies verified all eighteen retained
  identities and reproduced the report byte-identically. MDBs remain external.
- Result: x86 `DAO.DBEngine.36`, Windows NT `10.0.20348.0`; all three arms
  are `observed_accepted`, nine complete candidate/control pairs, no acquisition
  error, `controls_ok=true`, `unchanged=true`. Each arm's three normalized
  candidate/control observations agree, with status `pass` and endpoint
  `complete`. Every read-only open left the corresponding image unchanged.
- Exact candidates: all are 51,200 bytes. Descending unique SHA-256
  `43ebc902fd654a238ff5abf52671eb79ac37b9083c102cf315d74c72a4dc71f3`;
  ascending/descending unique
  `b1fe69cd81f6895bdee726d44d848236bf11192d6807e77eb09e8c1c3f6ccf55`;
  descending/ascending ordinary
  `50fde5c49bd1c5ca84d033887b1db0423d60a4af5b31fe4bb2637a6488bfb12c`.
- Schema/index observations: version `3.0`, exactly `Rows` and the four
  expected system tables; user-table Attributes zero. Fields `A`, `B`, `Tag`
  occur in that order, each Type 4, Size 4, Attributes 1. Every arm exposes
  only `ByKey`, with Primary, Foreign, Required, and IgnoreNulls false.
  Unique is true for the two unique arms and false for the ordinary arm.
  Fields are descending `A`, ascending `A` then descending `B`, or descending
  `A` then ascending `B`, respectively; raw field Attributes are 1 for
  descending and 0 for ascending. All captured metadata matches controls.
- Row/traversal/Seek observations: the three arms contain every exact planned
  `A/B/Tag` row, respectively 10, 12, and 14 rows. Full index traversal contains
  every row once in declared directed key order. Seek succeeds for all 10,
  12, and 12 distinct full keys, respectively; each returned complete row
  belongs to the input set and matches every queried index component.
  The ordinary arm retains both `(0,0)` rows (Tag 0 and 12) and both `(-1,0)`
  rows (Tag 4 and 13). Duplicate traversal tie order and which matching Tag
  Seek chooses were intentionally unspecified after full-row validation.
- Boundary: acceptance covers these exact one-leaf candidates and read-only
  schema, rows, traversal, and full-key Seek endpoints. It does not establish
  other names/values, null keys, other types, extra components, composite
  primary indexes, arbitrary branches/allocation, subsequent mutations,
  general compatibility, or hosted support. Report compatibility and support
  movement flags remain false.

## EXP-0127 — Preregistered descending/composite initial-index candidates

- Recorded: 2026-09-05, OpenAI Codex
- Kind: preregistered local development DAO candidate differential; no acquisition
- Plan: `oracle/windows-dao/acquisition/composite-index.plan.json`, SHA-256
  `3ee36358cb911f6fb008c591ebb0d061650854bb4d089bb87f1e11c12a9a8b92`.
  Pins the new host/analyzer and PowerShell scripts, existing transport,
  reviewed Long index encoder, deterministic example, and all three images.
- Candidates: reviewed source `caf60a5`, `composite_index_candidate` example;
  each image is 51,200 bytes. Descending unique SHA-256
  `43ebc902fd654a238ff5abf52671eb79ac37b9083c102cf315d74c72a4dc71f3`;
  ascending/descending unique
  `b1fe69cd81f6895bdee726d44d848236bf11192d6807e77eb09e8c1c3f6ccf55`;
  descending/ascending ordinary
  `50fde5c49bd1c5ca84d033887b1db0423d60a4af5b31fe4bb2637a6488bfb12c`.
  All use `Rows(A Long,B Long,Tag Long)`, `ByKey`, and the exact 10/12/14-row
  inputs of `EXP-0125`. `EXP-0126` established the observed component bytes;
  it did not establish candidate acceptance.
- Question: do the exact candidates match fresh DAO controls at every
  captured table/field/index flag and ordered binding, full row snapshot,
  directed index traversal, and Seek for every distinct full key, without
  modifying either image?
- Protocol: verify all nine candidate copies before first mutation. Acquire
  three fresh controls and read-only candidate observations per arm. Require
  all nine pairs complete, every control to satisfy the declared semantics,
  and all eighteen files unchanged before classifying an arm. Compare full
  normalized observations across each arm's three replicas and between
  candidate/control. Ordinary duplicate payload tie order is unspecified;
  traversal must contain every complete row. Seek may return either duplicate
  only after checking the returned complete row and every queried component.
- Decision: agreeing complete candidate/control semantics are
  `observed_accepted`; stable candidate rejection/difference after the full
  control/identity gate is `not_observed_accepted`; incomplete acquisition,
  failed control, changed bytes, or replica disagreement is `no_outcome`.
  Analysis rechecks input and retained-image pins; invalid binding rejects
  validation. Commit and review before one acquisition; no automatic retry
  after first mutation.
- Boundary: only these three one-leaf images. No null/other-type/additional-
  component grammar, branch/allocation policy, general compatibility, updates,
  or hosted support claim. Retain all MDBs externally and record one validated
  additive outcome as `EXP-0128`.

### EXP-0146 — Retained multi-level candidates accepted by secondary analysis

- Recorded: 2026-09-05, OpenAI Codex; development-only secondary outcome.
  EXP-0145 plan SHA-256
  `a27b80e7745ea2dca35f78053a1b43d29ebb73d4db56d84bd6bd5ab814c454e4`
  was committed at `a8fe92a` and independently reviewed before execution.
  No DAO execution or new acquisition occurred.
- Secondary report: externally retained
  `20260905T060000Z-multi-level-secondary/report.json`, 362234 bytes,
  SHA-256 `eb7ac58ea13c48cfd9aa990b2ac69122e0eec2293e75e91898b40eb8204a86ef`.
  It reuses all eighteen pinned MDBs from
  `20260905T054000Z-multi-level-index`, the original 9682811-byte result
  SHA-256 `4adee25ea2276ea8b080441ac6eadf293869a7d5cee881145d82ab602f612718`,
  and the original 150657-byte report SHA-256
  `8c665dccb340155c3998022305cefbaa2b590c687167236abb2f18b2dcaf23cb`.
  Original EXP-0139 plan SHA-256
  `7d0f368b31e2295b826118cea4e290b8ddc6661d108aea99eb870db84e13ccc2`,
  candidate source `5cd9e5a49888013059dfb88e8ab0a6ef942a91cd`, individual
  candidate/control identities and consumed inputs remain pinned by EXP-0145.
- The validated secondary report classifies primary, composite and relationship
  arms as `observed_accepted`: all eighteen observations pass full metadata,
  typed rows, ordered traversal, the original finite Seek probes, complete
  key/locator separators, sibling chains, leaf depth, and map membership.
  All nine fresh matched controls pass and all original identities remain
  unchanged. This covers the exact 27801-row primary candidate, later-table
  12929-row composite candidate, and 201-parent/27801-child relationship
  candidate, each with three agreeing replicas.
- The independent header/height question is `answered`; each arm and role
  repeats its observed class-height pair set across all three replicas.
  Heights count edges to leaves. Primary and composite DAO control roots have
  raw header byte 21 equal to 2 at subtree height 2, lower branches have 1 at
  height 1, and leaves have 0 at height 0. Candidate primary/composite roots
  and the candidate child root instead have byte 1 at height 2; their lower
  branches also have 1 and leaves 0. Those exact candidate policies were
  accepted without requiring their bytes to equal control heights.
- Relationship controls have a single parent leaf `(byte 0, height 0)` and
  a child branch `(1, 1)` above leaves `(0, 0)`. Candidate parents have a
  branch `(1, 1)` above leaves `(0, 0)`; candidate children additionally have
  the root pair `(1, 2)`. The report retains every node, raw byte, derived
  height and per-index counts. These finite observations justify recognizing
  the observed branch value 2 alongside 1; they do not establish an arbitrary
  height encoding, require byte/height equality, or prescribe writer updates.
- The pinned secondary analyzer reproduced the report byte-for-byte using
  temporary copies. All original outbox files and the secondary report remained
  unchanged. EXP-0140's original three `no_outcome` classifications are carried
  verbatim in the secondary report and remain the original experiment outcome.
  No consumed input or parser code changes accompany this record. Independent
  outcome review is pending; no general compatibility or support movement.

### EXP-0145 — Retained multi-level index secondary-analysis preregistration

- Plan: `oracle/windows-dao/acquisition/multi-level-index-reanalysis.plan.json`,
  SHA-256 `a27b80e7745ea2dca35f78053a1b43d29ebb73d4db56d84bd6bd5ab814c454e4`; outcome reserved as EXP-0146.
- This is explicitly post-acquisition analysis of EXP-0139 run
  `20260905T054000Z-multi-level-index`, with no DAO execution or new acquisition.
  EXP-0140 and its original three `no_outcome` classifications remain unchanged.
  The plan pins the original result/report, all eighteen retained MDB identities,
  the original plan and consumed inputs, and the separate secondary analyzer.
- Prior read-only diagnosis identified branch header byte 21 equal to 2 in
  primary/composite control roots where the original decoder admitted only 1.
  The secondary decoder admits branch values 1/2 and leaf value 0, retaining
  existing graph, separator, sibling, locator, map, and full semantic checks.
  It records each raw byte and independently derived subtree height. The finite
  byte/height relation is reported separately from candidate acceptance; the
  candidate's all-branches-1 policy is not rewritten to match controls.
- All nine original controls must pass and all eighteen source identities remain
  unchanged. Three agreeing candidate observations per arm classify the separate
  secondary result. Complete metadata, rows, traversal and original finite Seek
  probes remain required. Changed pins reject analysis; incomplete observations
  yield `no_outcome`. The new report must be outside the original outbox and
  cannot overwrite an existing file.
- Four focused synthetic tests cover header values and independently computed
  three-level heights, retained original outcomes and failed-control/error gates,
  input drift, and refusal to write into the source directory. Independent review
  is pending before execution. No secondary observation is recorded in this entry;
  no parser fact, general compatibility, or support-matrix movement is claimed.

### EXP-0140 — Multi-level candidate run retained as no_outcome

- Recorded: 2026-09-05, OpenAI Codex
- Kind: validated development-only outcome from the single authorized run
  `20260905T054000Z-multi-level-index`; no retry or support movement.
- Preregistration: EXP-0139, commit `412dc99`, plan
  `oracle/windows-dao/acquisition/multi-level-index.plan.json`, SHA-256
  `7d0f368b31e2295b826118cea4e290b8ddc6661d108aea99eb870db84e13ccc2`.
  Generator source: `5cd9e5a`. Consumed producer, analyzer, structure/catalog
  decoders, transport, example and plan remained unchanged.
- Artifacts: local VM
  `shared/outbox/20260905T054000Z-multi-level-index`. `result.json` is
  9,682,811 bytes, SHA-256
  `4adee25ea2276ea8b080441ac6eadf293869a7d5cee881145d82ab602f612718`;
  canonical `report.json` is 150,657 bytes, SHA-256
  `8c665dccb340155c3998022305cefbaa2b590c687167236abb2f18b2dcaf23cb`.
  The pinned analyzer reproduced the report byte-for-byte on temporary copies.
  All eighteen retained MDB identities and their unchanged read-only
  before/after identities were verified; retained originals were untouched.
  Independent outcome review follows.
- Environment: Windows NT 10.0.20348.0, 32-bit PowerShell, DAO.DBEngine.36.
  All nine candidate/control pairs completed; the result records no job error.
  The validated report classifies `primary`, `composite` and `relationship`
  as `no_outcome`. Six primary/composite control structural observations fail
  with `Index node header`. The all-controls prerequisite therefore fails
  for the entire matrix, including the relationship arm.
- Recorded observations: all nine candidate observations and all three
  relationship control observations have `passed=true`. Candidate primary
  Rows contains 27,801 entries/distinct keys across 143 nodes and three levels.
  Candidate composite Rows, following the empty table, contains 12,929 leaf
  entries and 33 distinct keys across 105 nodes and three levels. Candidate
  relationship Parents contains 201 entries/distinct keys across three nodes
  and two levels; Children contains 27,801 entries and three distinct keys
  across 143 nodes and three levels. Their recorded checks cover complete
  typed rows, traversal, planned boundary/missing Seek, maximum-child
  separators, sibling chains, locator bindings and map membership. These
  successful subobservations do not override the preregistered control gate
  or constitute candidate acceptance.
- Retention: each arm's three candidate files retain the exact EXP-0139
  identity. Control files and their unchanged identities are listed below.

| Arm / candidate replicas | Bytes each | SHA-256 |
| --- | ---: | --- |
| `primary-candidate-r1..r3.mdb` | 677,888 | `319337d779bfeceb7971ff133794ef738b5dcf44f3c55885d1c2591b5af15593` |
| `composite-candidate-r1..r3.mdb` | 475,136 | `249529faf1161a6a7ceb255794fb65d6fd5080d66c0e8fefdae88a2eba2ae035` |
| `relationship-candidate-r1..r3.mdb` | 694,272 | `beec4f2af1e0a84396053ef39ac9b76b5465db822f759cc7c35c1d11a9342147` |

| Retained control MDB | Bytes | SHA-256 |
| --- | ---: | --- |
| `primary-control-r1.mdb` | 839,680 | `23828c3ebaf78bdf15f924ce069c246df2e1d54a864a3f972ed808b3316b6a89` |
| `primary-control-r2.mdb` | 839,680 | `6bdbd1d45fb084befa3711063bc17a53174f794ac128e2da87daa2e090d0d138` |
| `primary-control-r3.mdb` | 839,680 | `cf3131459fe08d6cd028da27932e5dba00588c1ef38211b88498f69c38f613f1` |
| `composite-control-r1.mdb` | 577,536 | `7ba5500bf8b8484472ae45e977cacbe3ffa7139f959207badedb3fea07710d26` |
| `composite-control-r2.mdb` | 577,536 | `9c48cc94f82ca93ee179e0bf4c4d01d346994e2264e33c799d7d0b066fe326ab` |
| `composite-control-r3.mdb` | 577,536 | `909f2dfb034c5d84637eaf0edbbf21f2b9249b9b213c507eade150b439ef96c1` |
| `relationship-control-r1.mdb` | 481,280 | `5275c7c0a7b072e00c77860dd79d5d3b191629f15004625d521a3909c0d6c2a9` |
| `relationship-control-r2.mdb` | 481,280 | `caedbb7cfe2e7fa8855ef1f93972516957ddf0c966e5f545de2493caf7dfd22b` |
| `relationship-control-r3.mdb` | 481,280 | `5169e8a825568533c3d8319ea4a3cf1cc50d998ca5ee9354448bcb1c5d2cdea5` |

- Boundary: this records the original validated `no_outcome` once. It does
  not establish a new header interpretation, general tree/allocation policy,
  candidate acceptance, compatibility or hosted support. Investigating the
  retained control-header failure requires a separately pinned secondary
  analysis; the consumed plan and original result/report remain unchanged.
  The report keeps `development_only=true`, `compatibility_claim=false` and
  `support_movement=false`.

### EXP-0139 — Multi-level Long index candidate preregistration

- Recorded: 2026-09-05, OpenAI Codex
- Kind: committed development-only DAO preregistration; acquisition has not
  started and no outcome or support movement is asserted.
- Plan: `oracle/windows-dao/acquisition/multi-level-index.plan.json`, SHA-256
  `7d0f368b31e2295b826118cea4e290b8ddc6661d108aea99eb870db84e13ccc2`. Pins cover producer, analyzer, dedicated structure decoder,
  reused catalog decoder, low-level transport and Rust example. Dispatch and
  analysis validate the same inputs; the exact plan must be committed before
  acquisition. Independent harness review is required before dispatch.
- Generator: `multi_level_index_candidate` at reviewed source
  `5cd9e5a49888013059dfb88e8ab0a6ef942a91cd`. The implementation passed
  independent review and `just ready`; the lookup-accounting review fix
  changes no candidate bytes. No MDB bytes are committed.
- Hypothesis: DAO accepts the writer's uncompressed balanced multi-level
  construction, retaining its original root and appending non-contiguous
  descendants after data. EXP-0062 supplies branch/leaf headers, complete
  maximum-child key/row-locator separators, tail children and sibling links;
  EXP-0126 supplies Long direction/composition, EXP-0073 distinct-key counts,
  and EXP-0057/0065 map roles. The construction is not a DAO split or allocation
  policy inference.
- Three arms, each with three unchanged candidate replicas and three fresh
  matched DAO controls. `primary` has Rows(Id Long, Payload Long), primary
  ById ascending: position 0..27800 gives [27800-position, position].
  `composite` has an empty first table and later Rows(A Long, B Long, Payload
  Long), ordinary ByKey(B descending, A ascending): position 0..12928 gives
  [floor(position/400), floor(position/800)-9, position]. `relationship` has
  Parents(Id Long, Payload Long), primary ById, with [position-100, position]
  for 0..200; Children has the same fields with [position%3-1, position] for
  0..27800. ParentChildren links their Id columns and creates the child
  ordinary foreign index. Parent keys include unused values; duplicate child
  and composite runs cross leaf boundaries. Controls use the same insertion
  order, one transaction per table, then relationship creation; no compaction.
- Candidate identities:

| Arm | Bytes | SHA-256 |
| --- | ---: | --- |
| `primary` | 677,888 | `319337d779bfeceb7971ff133794ef738b5dcf44f3c55885d1c2591b5af15593` |
| `composite` | 475,136 | `249529faf1161a6a7ceb255794fb65d6fd5080d66c0e8fefdae88a2eba2ae035` |
| `relationship` | 694,272 | `beec4f2af1e0a84396053ef39ac9b76b5465db822f759cc7c35c1d11a9342147` |

- Endpoints: complete DAO database/table/field/index/relation metadata, full
  typed row multisets and full ordered index traversal returning every
  payload. Seek is limited to the explicit matching/missing boundary keys
  in the plan: seven primary queries, four composite queries, seven parent
  and six child queries. Any duplicate selected by Seek must be a complete
  matching row; duplicate tie order is unspecified. These finite probes do
  not establish arbitrary Seek or update behavior.
- Structural preflight and analysis: derive roots through the catalog, decode
  all rows, bind every leaf locator exactly once, compare keys with row values,
  and check every complete separator, tail, sibling chain and uniform leaf
  depth. Report actual index/data/available maps and descendant membership.
  Candidate main trees must have three levels (143/105/143 nodes respectively);
  the relationship parent has two levels and three nodes. Control depths,
  compression and page locations are observations, not equality gates.
  Control map membership may include unused index pages; candidates may not.
  The decoder explicitly allows 8,192 image pages, 1,019 directory slots and
  32 tree levels, and handles inline and indirect control maps using existing
  grammar. No consumed decoder is changed.
- Classification: all nine controls must pass, all read-only identities remain
  unchanged, and the complete inventory must have no job error. Three agreeing
  candidate observations yield `observed_accepted` or `not_observed_accepted`
  per arm; disagreement, incomplete jobs, changed files or failed controls
  yield `no_outcome`. Pin and retained-identity mismatches reject validation.
  Retain all eighteen MDBs and complete result/report JSON. SSH timeout is
  3,600 seconds; interruption or unexpected failure after mutation is a
  scientific result, with no automatic retries or redispatch.
- Preflight: focused semantic/classifier/structure tests and a VM PowerShell
  `Parser.ParseFile` check passed. The latter executed no producer or DAO.
  Candidate raw decoding validated all planned depths and complete bindings;
  generation after AutoIncrement integration reproduced identical bytes.
- Boundary: exact pinned images and declared read-only probes only. No new
  scalar/Text/Binary/null key grammar, indirect writer allocation, general
  B-tree update policy, compatibility or hosted support claim. Record the
  single validated outcome as EXP-0140.

### EXP-0134 — Populated relationship and bounded integrity probes accepted locally

- Recorded: 2026-09-05, OpenAI Codex
- Kind: validated development-only outcome from the single authorized run
  `20260905T050200Z-relationship-rows`; no retry or support movement.
- Preregistration: EXP-0133, commit `08f879a`, plan
  `oracle/windows-dao/acquisition/relationship-rows.plan.json`, SHA-256
  `ddeb3bdc88edc4c57163366fb77dfbf60eb5ed422d9a7943c78617be52ace5d2`.
  Pinned producer, analyzer, transport and example remained unchanged.
  Generator source: `77269833fb21b4fdb77c4fab9b6e7d8ec23ac314`.
- Artifacts: local VM
  `shared/outbox/20260905T050200Z-relationship-rows`. `result.json` is
  1,284,881 bytes, SHA-256
  `ebdae873bfb4a4618312792dccb52b81f5dbf270b2f4181356483a52a719b942`;
  canonical `report.json` is 849 bytes, SHA-256
  `9dfce7f02afddf33cac58607748cce37518b742f0f0891471d2b4a14a6e475b2`.
  The pinned analyzer reproduced the report byte-for-byte on temporary
  copies, validating input pins and all 24 retained MDB identities. Retained
  originals were verified unchanged. Independent outcome review follows.
- Environment: Windows NT 10.0.20348.0, 32-bit PowerShell, DAO.DBEngine.36.
  Read-only `populated` acceptance and each of `valid_child`, `orphan_child`
  and `duplicate_parent` report `observed_accepted`. Three original pairs
  and nine separate writable probe pairs completed with every matched
  control passing and no job error.
- Readback: all three original candidate replicas matched the complete
  planned table inventory, field metadata, index flags, relationship and
  every parent/child value. Accounts7 contains three parent rows; Events9
  contains twenty duplicate-key child rows with distinct 255-character
  payloads across three data pages. Full parent/child index traversal
  returned every expected pair; Seek for keys 1, 2 and 3 returned a complete
  matching row. Duplicate tie order and which matching duplicate Seek
  selects remain unspecified. All six original files stayed unchanged.
- Integrity: on independent copies, inserting child ('valid',2) succeeded
  in all candidate/control pairs, with exactly twenty-one child rows and
  unchanged parent rows afterward. Orphan child ('orphan',999) and duplicate
  parent (Code2=6,Key1=1) were rejected at Update, each preserving the full
  logical state. Every post-state included complete metadata, full payloads,
  index traversal and Seek. Observed native errors matched each control:
  orphan 3201 (HRESULT -2146825087), duplicate 3022 (HRESULT -2146825266);
  successful inserts had no native error or HRESULT. These codes were
  observations, not guessed acquisition gates. Read-only observations of
  each writable copy also left its post-operation bytes unchanged.
- Retention: every file below is 65,536 bytes. The three original candidates
  share the preregistered SHA-256
  `5a0bbe9329896ce19a46096d2b2a36bfc8dd2a2b16b8215dd377ff56fb05ab5b`.
  Probe starting identities matched the corresponding original candidate
  or control. Remaining original and final probe identities follow.

| Retained MDB | SHA-256 |
| --- | --- |
| `populated-control-r1.mdb` | `633e60b30c1abaec882562f6e9a84ee188a9ff741fa4626da9abd4d319f84b23` |
| `populated-control-r2.mdb` | `edd01569a58febab4780b6b47659830435edabb14c39196b5e10606f0115ea0f` |
| `populated-control-r3.mdb` | `f1f7b597b3bdf3b5908c451eb6972ea6490f5e0afc3b77955d01312107f59188` |
| `populated-candidate-valid_child-r1.mdb` | `b85df4ab3fb16f90e7e88546c1048247829b028fb22bfa18c54ec7236ee8b9b5` |
| `populated-control-valid_child-r1.mdb` | `125e6fb230194ec0df8580055f4edb6521e21c61147670d3095050b4b7f16740` |
| `populated-candidate-orphan_child-r1.mdb` | `5a0bbe9329896ce19a46096d2b2a36bfc8dd2a2b16b8215dd377ff56fb05ab5b` |
| `populated-control-orphan_child-r1.mdb` | `633e60b30c1abaec882562f6e9a84ee188a9ff741fa4626da9abd4d319f84b23` |
| `populated-candidate-duplicate_parent-r1.mdb` | `5a0bbe9329896ce19a46096d2b2a36bfc8dd2a2b16b8215dd377ff56fb05ab5b` |
| `populated-control-duplicate_parent-r1.mdb` | `633e60b30c1abaec882562f6e9a84ee188a9ff741fa4626da9abd4d319f84b23` |
| `populated-candidate-valid_child-r2.mdb` | `b85df4ab3fb16f90e7e88546c1048247829b028fb22bfa18c54ec7236ee8b9b5` |
| `populated-control-valid_child-r2.mdb` | `e952bfbae99866a5e3727db1f7ea0016ed00710f627a2330e89d43afa222cc25` |
| `populated-candidate-orphan_child-r2.mdb` | `5a0bbe9329896ce19a46096d2b2a36bfc8dd2a2b16b8215dd377ff56fb05ab5b` |
| `populated-control-orphan_child-r2.mdb` | `edd01569a58febab4780b6b47659830435edabb14c39196b5e10606f0115ea0f` |
| `populated-candidate-duplicate_parent-r2.mdb` | `5a0bbe9329896ce19a46096d2b2a36bfc8dd2a2b16b8215dd377ff56fb05ab5b` |
| `populated-control-duplicate_parent-r2.mdb` | `edd01569a58febab4780b6b47659830435edabb14c39196b5e10606f0115ea0f` |
| `populated-candidate-valid_child-r3.mdb` | `b85df4ab3fb16f90e7e88546c1048247829b028fb22bfa18c54ec7236ee8b9b5` |
| `populated-control-valid_child-r3.mdb` | `53b3b3882b83a957711b37c48816eb9b3530738b9be2431ef2c365c761f5dd2d` |
| `populated-candidate-orphan_child-r3.mdb` | `5a0bbe9329896ce19a46096d2b2a36bfc8dd2a2b16b8215dd377ff56fb05ab5b` |
| `populated-control-orphan_child-r3.mdb` | `f1f7b597b3bdf3b5908c451eb6972ea6490f5e0afc3b77955d01312107f59188` |
| `populated-candidate-duplicate_parent-r3.mdb` | `5a0bbe9329896ce19a46096d2b2a36bfc8dd2a2b16b8215dd377ff56fb05ab5b` |
| `populated-control-duplicate_parent-r3.mdb` | `f1f7b597b3bdf3b5908c451eb6972ea6490f5e0afc3b77955d01312107f59188` |

- Boundary: only this pinned multi-page, non-null Long relationship candidate
  and these three finite DAO insert probes. No nullable/composite keys,
  cascades, general allocation or integrity policy, Rust update capability,
  general compatibility claim or hosted support movement follows. The
  report explicitly keeps `development_only=true`, `compatibility_claim=false`
  and `support_movement=false`.

### EXP-0133 — Populated relationship and integrity preregistration

- Recorded: 2026-09-05, OpenAI Codex
- Kind: committed development-only DAO preregistration; acquisition has not
  started and no outcome or support movement is asserted.
- Plan: `oracle/windows-dao/acquisition/relationship-rows.plan.json`, SHA-256
  `ddeb3bdc88edc4c57163366fb77dfbf60eb5ed422d9a7943c78617be52ace5d2`. Input pins cover the producer, analyzer,
  low-level transport and Rust example. Dispatch and analysis verify the
  same pins; acquisition requires this committed plan and independent review.
- Generator: `relationship_row_candidate` at reviewed source
  `77269833fb21b4fdb77c4fab9b6e7d8ec23ac314`. Candidate is
  65,536 bytes, SHA-256
  `5a0bbe9329896ce19a46096d2b2a36bfc8dd2a2b16b8215dd377ff56fb05ab5b`.
- Candidate: Accounts7 has three Long-key parent rows. Events9 has twenty
  repeated child keys with distinct 255-character payloads across three
  data pages. Account7Events9 links Accounts7.Key1 to Events9.Account4;
  the child foreign leaf carries twenty entries and three distinct keys.
  The plan fixes complete schemas, relation/index flags and every row value.
- Read-only endpoint: three fresh matched DAO controls and three unchanged
  candidate replicas; complete table/schema/index/relation metadata and all
  row payloads, plus full parent/child index traversal and Seek1,2,3.
  Duplicate traversal tie order and which valid duplicate Seek selects are
  unspecified; every complete expected row must still occur in traversal.
- Integrity endpoints: separate fresh writable candidate/control copies for
  each replica and each operation. Insert valid child ('valid',2); attempt
  orphan child ('orphan',999); attempt duplicate parent (Code2=6,Key1=1).
  Require the valid insert to add exactly one child and the rejected inserts
  to leave complete logical state unchanged. Capture actual native rejection
  numbers/HRESULT and compare matched candidate/control identities without
  guessed numeric gates. Read both indexes and every full row after each probe.
  Six originals remain unchanged; retain those and eighteen probe files.
- Classification: read-only acceptance and each integrity endpoint have
  separate outcomes. Integrity requires accepted originals, complete probe
  inventory and all matched controls passing. Three agreeing candidate
  observations produce `observed_accepted` or `not_observed_accepted`;
  incomplete jobs, failed controls, changed originals or disagreement give
  `no_outcome`. Pin/binding/retention failures reject validation. Expected
  Update rejections are recorded endpoints; unexpected post-mutation failure
  remains a scientific result, never a redispatch or retry.
- Preflight: eight focused classifier tests and VM PowerShell
  `Parser.ParseFile` passed. The parser check executed no producer or DAO.
- Boundary: this exact image and three finite DAO insert probes only; no
  nullable/composite relationship keys, cascades, general allocation or
  enforcement policy, Rust update implementation, general compatibility or
  hosted support claim. Record one validated outcome as EXP-0134.

### EXP-0130 — Multi-table initial-row candidates accepted locally

- Recorded: 2026-09-05, OpenAI Codex
- Kind: validated development-only outcome from the single authorized run
  `20260905T043600Z-multi-table-rows`; no retry or support movement.
- Preregistration: EXP-0129, commit `270c3d5`, plan
  `oracle/windows-dao/acquisition/multi-table-rows.plan.json`, SHA-256
  `95f5711c27f45fdadef43fd242ae8b06afc75ebf7ad8c2f9fe665ee22eb459f9`.
  The pinned producer, analyzer, transport and example remained unchanged.
  Generator source was reviewed implementation `4c4f2b3`.
- Artifacts: local VM
  `shared/outbox/20260905T043600Z-multi-table-rows`. `result.json` is
  1,117,558 bytes, SHA-256
  `310ef57b73577ffa1fabcda722916a5bfc257479f6350a398d373045e9761c13`;
  canonical `report.json` is 596 bytes, SHA-256
  `7e15a6e9bf03222e0ce5780759e8489071c1ffdca99f78defb05d454d1cbec31`.
  The pinned analyzer reproduced the report byte-for-byte on temporary
  copies, validating input pins and every retained identity. All retained
  originals were verified unchanged. Independent outcome review follows.
- Environment: Windows NT 10.0.20348.0, 32-bit PowerShell, DAO.DBEngine.36.
  Both `mixed` and `empty-first` report `observed_accepted`, with six complete
  candidate/control pairs, no job error, and three agreeing candidate
  observations per arm. Every control passed expected semantics, and all
  twelve files had matching before/after and retained identities.
- Candidate replicas: mixed is 77,824 bytes, SHA-256
  `86a8e79ad23c0610d1c9c1cc70603efbd3186db1dfb6f42295fe857fc16ae17b`;
  empty-first is 57,344 bytes, SHA-256
  `fda4ae1e0775762daae9df21e78f36729c550f2c2fcbd700694399af3cf5abca`.
- Fresh control identities:
  - mixed r1, 77,824 bytes:
    `55a7bbadfdd0a86ff530e4c63fc55d2450f863c2424442eae552802dd4be566d`.
  - mixed r2, 77,824 bytes:
    `2e40d5f716a0372f711871f0ac4a353857b58e796e13b1d6d1dd15ad366fdc06`.
  - mixed r3, 77,824 bytes:
    `e9191b192831dfcec8d7638610f48a7c5e6f4f417578250406333d0134b6a95d`.
  - empty-first r1, 57,344 bytes:
    `3e0c2c3aa3b6bf2336753f8d04695fd69b75074c1d8eb0b95a7ac888d9a0508d`.
  - empty-first r2, 57,344 bytes:
    `b7217f20b9e9ac0c073ba5af45f59de9cedd39d3e00c281989353212d8ad2168`.
  - empty-first r3, 57,344 bytes:
    `140c8ebacd804fedbcdfb626a7b5c7162b9e5e93b14bb04607b849350d3e66bb`.
- Validated mixed snapshot: DAO version3.0, the four expected system
  tables, and Numbers/Keys/Notes/Empty. Numbers has all 509 Long values
  -254 through 254. Keys has Long values 3, -1 and 2; its ById index is
  primary, unique and required, with Foreign and IgnoreNulls false and one
  ascending Id field with Attributes0. Complete index traversal and Seek
  queries return -1, 2 and 3 with the matching row values. Notes has the
  complete 4096-character Memo `A + (offset mod26)` and a distinct null row;
  Empty has no rows. Every table's complete field/index inventory matched.
- Validated empty-first snapshot: exactly the expected system tables plus
  Empty and Binary, no user indexes. Empty(Id Long) has no rows; Binary has
  one complete 2048-byte OLE value `byte = offset mod256`. Id fields expose
  type4/size4, Memo type12/size0, and OLE type11/size0 in the applicable arms.
  Payload validation compared every character/byte, not just length or prefix.
- Boundary: these exact multi-table images and read-only endpoints only.
  This observes their combination of later table placement, initial rows,
  index traversal and long payloads; it does not establish a general
  allocator, arbitrary schemas/indexes, relationships with rows,
  AutoIncrement, empty long payloads, subsequent writes, general
  compatibility or hosted support. Report compatibility and support flags
  remain false.

### EXP-0129 — Multi-table initial-row candidate preregistration

- Recorded: 2026-09-05, OpenAI Codex
- Kind: committed development-only DAO preregistration. Acquisition has not
  started; this entry asserts no outcome or support movement.
- Plan: `oracle/windows-dao/acquisition/multi-table-rows.plan.json`, SHA-256
  `95f5711c27f45fdadef43fd242ae8b06afc75ebf7ad8c2f9fe665ee22eb459f9`. Input pins cover the
  producer, analyzer, low-level SSH transport and deterministic Rust example.
  Dispatch and analysis share input-pin verification; the plan must be committed.
- Generator: `multi_table_row_candidate` at reviewed source
  `4c4f2b34f3037b5b17c7027b629d4e396c9790ef`.
- Candidate identities:
  - mixed: 77824 bytes, SHA-256
    `86a8e79ad23c0610d1c9c1cc70603efbd3186db1dfb6f42295fe857fc16ae17b`.
  - empty-first: 57344 bytes, SHA-256
    `fda4ae1e0775762daae9df21e78f36729c550f2c2fcbd700694399af3cf5abca`.
- Hypothesis: the exact four-table mixed and two-table empty-first candidates
  expose their complete declared schemas and initial rows unchanged. Mixed
  combines 509 Numbers rows, three primary-indexed Keys rows, a 4096-character
  Memo plus null in Notes, and an empty table. Empty-first precedes a chained
  2048-byte OLE row with an empty table. The plan fixes every payload byte and
  every field/index flag; Keys traversal and Seek validate the later table's
  indexed row references. EXP-0087/0065 supply layout roles and EXP-0116/0120/
  0124 provide individual-writer observations, not combination acceptance.
- Acquisition: one job, three fresh matched DAO controls and three candidate
  replicas per arm. Read all images read-only, compare every table's complete
  schema and row multiset, Memo strings/OLE bytes, Keys traversal and Seek,
  and unchanged before/after identities. Retain all twelve files externally.
- Decision: accept an arm only after a complete six-pair job, all controls
  pass, all images remain unchanged, and its three candidate observations
  agree. Identical candidate failures yield `not_observed_accepted`; control
  failure, changed images, incomplete jobs or replica disagreement yield
  `no_outcome`. Binding/pin/retention errors reject validation. Failures after
  the first mutation are scientific results, never automatic retries.
- Preflight: seven classifier tests passed; VM `Parser.ParseFile` accepted
  the PowerShell producer without executing it or DAO. Independent experiment
  review precedes root's single authorized acquisition.
- Boundary: exact candidates only; no general allocation, schema/index,
  relationship, AutoIncrement, empty-payload, update, compatibility or hosted
  support claim. Record the validated outcome once as EXP-0130.

## EXP-0143 — Scalar and nullable index-key discovery preregistration

- Recorded: 2026-09-05, OpenAI Codex.
- Kind: development-only preregistration; acquisition has not started. This
  entry establishes no format transform or compatibility/support claim.
- Plan: `oracle/windows-dao/acquisition/scalar-index-layout.plan.json`, SHA-256
  `c692ca69614f176b3391d3dd8020de1109e30957a9aaaf65abd5b09ac20de863`.
  Pins cover the producer, analyzer, existing original catalog/leaf decoders
  and local SSH transport. Both dispatch and analysis verify inputs; dispatch
  also requires the exact plan committed before acquisition.
- Questions: exact ascending/descending keys for Boolean, Byte, Integer,
  Currency, Single, Double, Date and four-byte Binary values; nullable Long
  one/two-component keys, duplicate-null insertion behavior, and the effects
  of Unique, Required and IgnoreNulls. EXP-0062/0073 supply existing leaf/row
  framing; EXP-0126 supplies prior non-null Long observations. No scalar key
  transform, null uniqueness rule or null-omission policy is assumed.
- Matrix: sixteen scalar direction arms and ten nullable Long arms, each
  repeated with three fresh controls: 78 images, at most twelve attempts
  per image. Values include signed endpoints, negative/zero/positive values,
  finite floating normal/subnormal minima, exact Binary bytes, and repeated
  null/non-null full keys with distinct Tag payloads. Tag is not indexed.
- Capture: create declared table/index, attempt each row exactly once, and
  retain actual Update success or DAO error numbers/HRESULT. A declared
  rejected insertion is cancelled before the next distinct attempt. Any
  assignment/setup/cleanup failure aborts acquisition; completed operations
  and raw images remain retained. Close and reopen read-only for complete
  schema, index flags/directions, saved rows and ordered index traversal;
  record unchanged before/after image identities.
- Analysis: bind uninterpreted raw leaf keys through page/slot locators to
  every indexed saved row and DAO traversal. Observe null omissions; reject
  missing non-null bindings. Compare question-bearing raw key/value bindings,
  saved/omitted rows, index records/flags/counts and actual operation outcomes
  across three replicas. Provider value normalization is a separate diagnostic;
  an observation supplies keys only for the values actually saved.
- Decision: `answered` requires all 78 captures and complete correlations with
  stable observations. Unexpected acquisition failure, incomplete/changed
  capture, unsupported decoding or disagreement yields `no_outcome`; identity
  or pin mismatches reject validation. No automatic retry after first mutation.
- Bounds: existing decoder limits remain unchanged; each image has at most
  twelve rows, three fields and one leaf index. No parser edits, Text collation,
  GUID/Memo/OLE index grammar, candidate acceptance, general Binary lengths,
  NaN/infinity, existing-row updates or hosted support movement in this slice.
- Validation: five focused classifier/binding/pin tests and Python compilation
  passed; Windows `Parser.ParseFile` accepted the producer without executing
  it or DAO. Independent experiment review precedes acquisition. Retain all
  raw files externally; record one additive validated EXP-0144 outcome.

## EXP-0144 — Scalar/null discovery stopped with no outcome

- Recorded: 2026-09-05, OpenAI Codex; validated development-only `no_outcome`.
- Consumed EXP-0143 plan SHA-256:
  `c692ca69614f176b3391d3dd8020de1109e30957a9aaaf65abd5b09ac20de863`,
  analyzer/producer source `bfb19ef`. Their bytes and earlier entries remain
  unchanged. One acquisition ran; no retry or successor acquisition occurred.
- Retained local run: `20260905T054700Z-scalar-index-layout` under the external
  VM shared outbox. Result: 703682 bytes, SHA-256
  `47e9be13836af6bb67b6007bcd731c73c96f871744b7998cb22734f0cb562624`.
  Validated report: 3284 bytes, SHA-256
  `e338265e299b875b0a8de0426fd2f2c285d675822ed84919b1bfbdb061b3242d`.
  The pinned analyzer reproduced the report byte-for-byte on a temporary
  copy; all original retained files stayed unchanged.
- Acquisition: 36 completed captures of the planned 78, covering both
  directions for Boolean, Byte, Integer, Currency, Single and Double, three
  replicas each. Every completed capture reports `pass` and all 36 retained
  image hashes/sizes equal their recorded before/after identities.
- The 37th attempt is `date-ascending`, replica 1, with no recorded Update
  operation. `mutation_started` is true and acquisition stopped with
  `System.InvalidCastException: Specified cast is not valid.` The additional
  partial Date image is 49152 bytes, SHA-256
  `b13a0441901bbdd876433a48fe5007c6529626dfbb111b8a2336155ec99c6c2e`;
  read-only decoding finds zero saved rows. The empty guest log and result
  provide no statement/stack location, so they do not identify the precise
  failed cast or establish a DAO Date rejection. Remaining Date/Binary/null
  captures were not completed.
- Analysis: zero validated observations. All 36 captures fail the planned
  saved-row correlation: DAO `values` are serialized as an object containing
  `value` and `Count`, while the frozen classifier expects an array. The
  report records these 36 reasons plus the incomplete acquisition error.
  This is a producer/contract mismatch, not an established scalar encoding
  disagreement. No corrected normalization or expanded analysis was applied
  to the consumed run.
- Boundary: no key transforms, null policies, Date/Binary acceptance, general
  compatibility or hosted support movement follow from this `no_outcome`.
  Retain all 37 raw images externally. Any secondary read-only interpretation
  needs a separately declared analysis; any new DAO acquisition requires a
  separately reviewed successor plan and the repository's human retry decision.

## EXP-0147 — Remaining Date/Binary/null index discovery successor plan

- Recorded: 2026-09-05, OpenAI Codex; unacquired development-only successor.
  EXP-0143 inputs and EXP-0144 `no_outcome` remain unchanged. Standing user
  authorization covers DAO experiments/mutations; root dispatches this distinct
  plan once only after independent review and final checks.
- Plan: `oracle/windows-dao/acquisition/scalar-index-remaining.plan.json`,
  SHA-256 `c0378eca7d65ae58bd8d4a5b124b476ea1bc51989de75d579cfebd7a3d7555ae`.
  New producer/analyzer files have separate input pins, checked before dispatch
  and analysis; dispatch requires the exact committed plan. Existing original
  catalog/leaf decoders and local one-dispatch transport stay unchanged.
- Scope: exactly the fourteen remaining arms, three fresh controls each:
  ascending/descending Date and four-byte Binary, plus ten nullable Long
  ordinary/unique/Required/IgnoreNulls one/two-component arms. Each control
  has at most twelve attempts. The 36 completed scalar captures from EXP-0143
  are neither reacquired nor reinterpreted by this plan.
- Corrections: values use plain CLR arrays instead of PowerShell's decorated
  `New-Object` array wrapper. Date conversion is precomputed and assigned with
  the explicit `[datetime]` cast used by the original read producer. Original
  artifacts do not identify the precise failing cast; this change does not
  prove DAO Date acceptance. Unexpected errors now retain endpoint and script
  stack alongside the original error text and completed attempt records.
- Gates: retain actual Update outcomes, null omissions, complete saved rows,
  exact raw key-to-row-locator bindings, schema/flag/direction metadata and
  full DAO traversal. Compare three replicas per arm without guessing nullable
  uniqueness/omission policies or index transforms. All 42 captures must be
  complete, unchanged and correlated for `answered`; otherwise preserve an
  honest `no_outcome`. Binding/pin failures reject validation; no automatic retry.
- Validation: four focused Python classifier/binding/pin tests and compilation
  passed. Windows x86 PowerShell parsed the new producer and ran only extracted
  helper functions against in-memory fake fields: all 120 declared Date/Binary/
  nullable row conversions and JSON array roundtrips matched exact planned
  values. The retained test is `tests/check_scalar_index_remaining.ps1` under
  `oracle/windows-dao`; it instantiates no DAO and executes no acquisition.
- Boundary: no scalar encoding promotion, completed-capture reanalysis, Text
  collation, GUID/Memo/OLE indexes, arbitrary Binary lengths, existing-row
  updates, candidate acceptance, general compatibility or hosted support claim.
  Retain raw images/results externally and record one validated EXP-0148 outcome.

## EXP-0148 — Remaining Date/Binary/null index observations answered

- Recorded: 2026-09-05, OpenAI Codex; validated local development discovery
  `answered`, not candidate acceptance or hosted support. Consumed EXP-0147
  plan SHA-256 `c0378eca7d65ae58bd8d4a5b124b476ea1bc51989de75d579cfebd7a3d7555ae`,
  producer/analyzer source `9fb6874`, and original EXP-0144 `no_outcome` remain
  unchanged. The earlier 36 captures were not reacquired or reanalyzed.
- Retained run: `20260905T060000Z-scalar-index-remaining` in the external VM
  shared outbox. Result: 900612 bytes, SHA-256
  `2d078ba65dbab7455f6aacb8408a784d521d09553fdc079cd69c554bf9bcccfd`.
  Report: 137822 bytes, SHA-256
  `88a33d7e37f4e45f3a8ad305ff8fc9ff4c2eac4442b26e9765de19208d882fb9`.
  Pinned analysis reproduced the report byte-for-byte on a temporary copy;
  every original retained file remained unchanged.
- All fourteen arms have three complete, agreeing captures: 42 observations,
  no report reasons or acquisition error. All 42 retained MDB identities equal
  recorded before/after hashes and sizes. Complete DAO schema, index flags,
  saved rows, traversal and raw key/row-locator correlations pass. Every saved
  payload equals its declared value; stable Update rejections are listed below.
- Date: six OA day values `-10000,-1,0,1,36526,2958465` were saved exactly.
  Ascending raw keys respectively are `7f3f3c77ffffffffff`,
  `7f400fffffffffffff`, `7f8000000000000000`, `7fbff0000000000000`,
  `7fc0e1d5c000000000` and `7fc146924080000000`. For these exact inputs,
  descending keys complement every ascending byte, including marker `7f` to
  `80`, and traversal reverses value order. This observes finite Date keys;
  it does not establish arbitrary Date/floating encodings or explain the
  original failed producer's unlocalized cast.
- Four-byte Binary: exact inputs `ffffffff`, `00000000`, `00000001`,
  `01000000`, `7fffffff`, `80000000` all saved. Ascending keys equal marker
  `7f`, the four input bytes, four zero padding bytes, then `04`. Descending
  keys complement every byte, and traversal reverses bytewise order. This
  is evidence only for these four-byte inputs, not arbitrary-length framing.
- Nullable Long: ascending null key is `00`, descending null is `ff`.
  Non-null component bytes agree with prior EXP-0126 observations. Mixed
  ascending-A/descending-B composite keys concatenate the observed components:
  `(null,null)` is `00ff`; `(null,1)` is `00807ffffffe`; `(1,null)` is
  `7f80000001ff`. These repeated full null-bearing keys retain distinct row
  locators and complete Tag payloads.
- Per-replica saved rows / leaf entries / physical distinct-key count:

  | Arm | Rows | Entries | Count | Raw flags |
  | --- | ---: | ---: | ---: | ---: |
  | null-ordinary / null-descending | 8 | 8 | 6 | 0 |
  | null-unique | 7 | 7 | 6 | 1 |
  | null-unique-ignore | 7 | 5 | 5 | 3 |
  | null-ignore | 8 | 6 | 5 | 2 |
  | null-required | 6 | 6 | 5 | 8 |
  | composite-null-ordinary | 12 | 12 | 8 | 0 |
  | composite-null-unique | 11 | 11 | 8 | 1 |
  | composite-null-ignore | 12 | 10 | 7 | 2 |
  | composite-null-required | 6 | 6 | 5 | 8 |

- Unique single indexes accept both null rows and reject only the repeated
  non-null zero (Tag 6); unique composite accepts repeated `(null,null)`,
  `(null,1)` and `(1,null)`, rejecting repeated `(1,1)` (Tag 8). Observed
  rejection is DAO `3022`, HRESULT `-2146825266`, in every replica.
- IgnoreNulls single indexes omit the two stored null rows; the composite
  IgnoreNulls arm omits only the two all-null rows, retaining partial-null
  entries. Required single rejects Tags 1/5; Required composite rejects
  Tags 1/2/3/5/6/7, namely every attempted null-bearing row. Observed rejection
  is DAO `3058`, HRESULT `-2146825230`. Ordinary Required retains repeated
  non-null keys. These statements concern exactly the declared flags/shapes.
- Boundary: no generalized scalar/type grammar, arbitrary null-component
  policy, Text/GUID/Memo/OLE indexing, existing-row update behavior, writer
  acceptance or general compatibility claim. Report support and compatibility
  flags remain false. Keep all raw images externally, with no provider bytes
  or MDBs committed.

## EXP-0142 — Hosted write comparison ended with no outcome

- Recorded: 2026-09-05, OpenAI Codex; hosted `no_outcome`, not a complete
  matched write acquisition. Consumed EXP-0141 plan SHA-256
  `d5556c11bb1526d3fb067a6fd1c2196fdc4ffd7f52fa8dd03ef4104a5bfbeaf8`
  and all pinned inputs remain unchanged.
- Hosted run: `33948345313`, attempt 1, source revision
  `4d8e421b0d618482ac0b7a1589c77a94d293e513`. Both artifacts are retained
  externally under `20260905T055500Z-hosted-write-33948345313`: the exact
  `generated-write-REVISION` and `windows-dao-write-REVISION-1` directories.
  No hosted retry or corrected evaluation was performed.
- Stock Windows Server 2022 x86 provider probe reports `ready` for
  `DAO.DBEngine.36`, provider 3.6, DAO DLL version `03.60.9765.0`; no provider
  installation or license acceptance occurred. `environment.json`: 4264 bytes,
  SHA-256 `1685d43121b85182009919ba269a9bf883d3385c2a192ab284b3a11501c981ed`.
- Linux preparation completed all twelve recipes; Windows reader and DAO
  capture completed all twelve files. Every retained Windows MDB hash matches
  its Linux generated copy, preparation identity, and DAO before/after hashes;
  all twelve DAO manifest entries report `pass` with no error.
  `preparation.json`: 2144 bytes, SHA-256
  `6c38db2659871605e0154746f139989abf72ee75b89978b911c83d7315bab68a`.
  `reader.json`: 85 bytes, SHA-256
  `0f899448460cb7df36725c3956c2ddc5ee544afb0a9ddb068c59ef421e85548a`.
  `dao-manifest.raw.json`: 2863 bytes, SHA-256
  `c7dc7487ba6d9273c5221828925c090bab152be56e338778076540e781693e15`.
- Frozen evaluator result: 191 bytes, SHA-256
  `d285609b052ea2e6d29ff7b15ddf5567129d024aa3e82fdf7d60fe9b8513acd9`,
  with error `Incomplete index observation inventory`. Eleven earlier
  per-scenario comparison documents report `matched: true`; no relationship
  comparison document was emitted. The original evaluator reproduced those
  eleven documents and the failure report byte-for-byte in a temporary copy.
  Every original retained file remained unchanged.
- Diagnosis: the relationship raw snapshot and index probes enumerate
  `(Parent, ByKey)` before `(Child, ParentChild)`. Snapshot canonicalization
  sorts Child before Parent, while the frozen index checker requires probe
  inventory in that same order. Both required probe records are present;
  the failure is the order-sensitive association gate, not evidence that
  the provider omitted an index. No reordered evaluation was applied here.
- Boundary: the all-twelve decision rule was not satisfied. Preserve this
  honest original result; there is no support-matrix movement or general
  compatibility claim. Any corrected read-only association of retained probes
  requires a separately pinned and independently reviewed secondary plan.

## EXP-0153 — Retained hosted write index-association reanalysis plan

- Recorded: 2026-09-05, OpenAI Codex; post-acquisition read-only secondary
  plan, not an original blinded decision or a new hosted run. EXP-0141 inputs
  and EXP-0142 `no_outcome` remain unchanged; corrected evaluation has not run.
- Plan: `oracle/windows-dao/acquisition/hosted-write-reanalysis.plan.json`,
  SHA-256 `6092f41b7793bdf9f40491f0532d8ab3acfc951e0e30c82ef9f88b25625f8e4b`.
  Pins cover the original plan/runtime dependencies, new secondary analyzer,
  and all 244 files retained in both hosted run `33948345313` artifacts,
  including Linux/Windows MDBs, snapshots, receipts and original failure report.
- Prior diagnosis: both relationship index observations exist but use DAO
  Parent/Child enumeration order; canonical snapshots use Child/Parent order.
  Secondary association requires an exact unique set of `(table, index)`
  identities and reorders whole records before calling the unchanged original
  index checker. It neither changes data nor synthesizes missing probes.
- Execution: verify original and secondary runtime/artifact pins, ready stock
  provider receipt, expected source revision and identical Linux/Windows MDBs.
  Copy the Windows evaluation directory to a temporary directory, run the
  original full evaluator with only the association function replaced in memory,
  restore that function and remove the temporary copy. Verify all retained
  originals again before exclusively writing a new report outside their tree.
- Decision: secondary `matched` requires every original twelve-scenario gate,
  including complete typed rows, schema, relationships, coverage, identities,
  directed index traversal and complete distinct-key Seek payload checks.
  Missing/duplicate/unknown index identities still fail. Preserve original
  `no_outcome` in the new report; failed evaluation remains secondary
  `no_outcome`. No automatic support-matrix movement or broad compatibility claim.
- Validation: four focused synthetic association/environment/completeness/pin/containment
  tests and Python compilation passed. Committed preflight checks retained
  identities without corrected evaluation. Independent review precedes analysis;
  no DAO, provider activation, build, installation, retry or workflow dispatch.
  Record one additive validated EXP-0154 outcome; retain originals externally.
