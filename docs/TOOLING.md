# Developer tooling

The repository-local [`mise.toml`](../mise.toml) is the canonical developer
tool inventory. From the repository root:

```sh
mise trust
mise install
mise exec -- just ready
```

Shell activation is optional. Prefix commands with `mise exec --` when mise is
not activated in the current shell.

## Mise-managed tools

| Tool | Pinned version | Project use |
| --- | --- | --- |
| Python | 3.13.7 | Support-matrix validation and DAO oracle tooling |
| Rust stable | 1.96.0 | Production builds, tests, docs, Clippy, and formatting |
| Rust nightly | nightly-2026-07-20 | Fuzzing |
| just | 1.46.0 | Everyday command recipes |
| jq | 1.8.2 | Ad-hoc JSON inspection |
| cargo-deny | 0.20.2 | Dependency license, source, ban, and advisory policy |
| cargo-fuzz | 0.13.2 | `just fuzz` |

The stable Rust installation includes `clippy` and `rustfmt`. A mise
post-install hook provisions the pinned nightly toolchain; keeping nightly out
of the active tool list leaves Rust 1.96.0 as the default while
`cargo +nightly-2026-07-20 ...` remains available. These pins mirror CI;
`rust-toolchain.toml` remains the toolchain contract outside mise.

## Host prerequisites

Mise does not replace the operating-system substrate. Development also
requires Git, a POSIX shell with core utilities, a native linker for the Rust
host, and network access when first installing tools or fetching locked Cargo
dependencies.

The local Windows DAO loop (`oracle/windows-dao/dao.py`) uses a system OpenSSH client and a machine-local
dockur/windows VM with x86 Windows PowerShell 5 and the licensed
`DAO.DBEngine.36` provider. Its disks, credentials, provider and shared
artifacts stay outside the repository; see
[`LOCAL_WINDOWS_VM.md`](LOCAL_WINDOWS_VM.md). DAO is never a production
dependency.

Python tooling uses only the standard library and `unittest`.
