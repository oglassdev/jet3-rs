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
| Python | 3.13.7 | Validation, evidence, benchmark, fuzz, and DAO protocol tooling |
| Rust stable | 1.96.0 | Production builds, tests, docs, Clippy, formatting, and coverage |
| Rust nightly | nightly-2026-07-20 | Fuzzing and Miri |
| just | 1.46.0 | Everyday command recipes |
| jq | 1.8.2 | Benchmark metadata capture and shell contract tests |
| cargo-deny | 0.20.2 | Dependency license, source, ban, and advisory policy |
| cargo-llvm-cov | 0.8.6 | G6 coverage evidence |
| cargo-fuzz | 0.13.2 | Registered fuzz targets and campaigns |
| cargo-mutants | 26.2.0 | G6 mutation evidence in the supported v26 report format |

The stable Rust installation includes `clippy`, `rustfmt`, and the LLVM tools
component (`llvm-tools-preview` in CI/rustup terminology and `llvm-tools` in
mise's installed-component inventory). A mise post-install hook provisions the
separately pinned nightly toolchain with `miri`; keeping nightly out of the
active tool list leaves Rust 1.96.0 as the unambiguous default while `cargo
+nightly-2026-07-20 ...` remains available. These pins mirror the checked CI
and evidence contracts; `rust-toolchain.toml` remains the toolchain contract
for Rust-native tooling outside mise.

## Host prerequisites

Mise does not replace the operating-system substrate. Development and evidence
commands also require:

- Git;
- a POSIX shell plus ordinary core utilities (`sh`, `awk`, and either
  `sha256sum` or `shasum`);
- a native linker suitable for the Rust host; and
- network access when initially installing tools or fetching locked Cargo
  dependencies.

The optional remote Windows DAO runner additionally needs an OpenSSH client.

The local exploratory Windows DAO loop uses the same system OpenSSH client and
a machine-local dockur/windows VM. Its disks, credentials, licensed provider,
and shared artifacts stay outside the repository. See
[`LOCAL_WINDOWS_VM.md`](LOCAL_WINDOWS_VM.md) for the environment variables and
checked `just windows-dev-*` entry points.

The Windows DAO oracle additionally requires x86 Windows PowerShell 5 and the
exact licensed `DAO.DBEngine.36` provider described in
[`DAO_PROVIDER_BLOCKER.md`](validation/DAO_PROVIDER_BLOCKER.md). Mise cannot
provision or validate that external provider, and it is never a production
dependency.

No third-party Python packages are required by the checked `main` branch; its
Python suites use the standard library's `unittest`. GitHub Actions and its
pinned actions are CI services rather than local mise tools.
