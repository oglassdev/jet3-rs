# Controlled M1 DAO evidence

Status: **PASS**

The complete protocol-1.1 M1 inventory passed under Microsoft DAO from exact
clean pushed commit `c2e5df29bcd5a779d6aa82582513e28b53f76598`.
The retained bundle is:

```text
%TEMP%\jet3-rs-dao-m1-executor\evidence\
  c2e5df29bcd5a779d6aa82582513e28b53f76598\
  20260725T010957Z-dao-m1
```

The checked independent command:

```powershell
python oracle/windows-dao/scripts/validate_m1_protocol.py bundle `
  $bundle
```

reported `PASS`. Important identities are:

- bundle manifest SHA-256:
  `9bc59d5db419e7283d8013d34e4fea16c3a9add8830c392294b8a8b6b1c32685`;
- report SHA-256:
  `628f01ab5d6b238c4a4c1b0cdebd4339a71d6f313b44665970446a65c3356b25`;
- inventory SHA-256:
  `76e433a5b0bb5e6d77b9da52842b52b0a41c201d472f8271ccb9908252ccc1d0`;
- environment SHA-256:
  `870ec9ceaaa6a5b9af0ebf16fbf0ef793b943718b49d9f003ed48cfd65af679f`;
- payload count and total: 33 files and 976,399 bytes.

All seven `DAO-GEN-*` scenarios and both `DAO-PAIR-*` comparisons in
`m1-inventory.json` passed. This evidence proves only the controlled DAO
generation/readback behavior described by `EXP-0007`. It contains no Rust
canonical snapshot or DAO-versus-Rust differential result.

The support matrix therefore remains deliberately `unverified` for Rust table
values, long values, indexes, and schema creation. Notes on those entries now
identify the M1 observation boundary, but the matrix carries no `dao_bundle`
evidence object: the current support validator intentionally fails closed
because DAO-bundle semantic integration is not implemented. Advancing a
product capability requires commit-bound Rust tests plus the required
independent and DAO differential evidence on the release commit.
