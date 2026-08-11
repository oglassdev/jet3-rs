# Linux G1 handoff for `4f067b1`

This note records the Linux side of the cross-platform G1 handoff for commit
`4f067b1481bf828411b443b4db3b111447b0ad44`. It is an operational record, not
part of the commit-bound evidence bundle and not a compatibility claim.

## Linux result

The Linux platform runner completed all seven required commands successfully
from a clean checkout of the exact commit. The validated environment was Rust
1.96.0 (`ac68faa20c58cbccd01ee7208bf3b6e93a7d7f96`) on
`x86_64-unknown-linux-gnu`, with LLVM 22.1.2.

- Platform record SHA-256:
  `d4980c26031ba3b45cf85570087c16bd2d97f3289f7f9c809810e7c6ed3be47a`
- Transfer archive name:
  `g1-linux-4f067b1481bf828411b443b4db3b111447b0ad44.zip`
- Transfer archive SHA-256:
  `840c6bb15f73441c4dc6e399ebe577fca0f504dd28fdb97ea8e60d2b41d904c9`

The archive contains `linux/platform-record.json` and its seven hashed logs.
Validate the archive hash before extracting it. The Linux record must not be
regenerated from this documentation commit because G1 evidence must remain
bound to the earlier exact commit.

## macOS continuation

Transfer both exact-commit archives to the Mac:

- `g1-linux-4f067b1481bf828411b443b4db3b111447b0ad44.zip` from Linux; and
- `g1-windows-4f067b1481bf828411b443b4db3b111447b0ad44.zip` from Windows.

Before making source edits, detach the clean Mac checkout at the evidence
commit, install Rust 1.96.0 with Clippy, rustfmt, and LLVM tools, and run the
macOS platform runner described in `CI_EVIDENCE.md`:

```sh
set -euo pipefail
commit=4f067b1481bf828411b443b4db3b111447b0ad44
test -z "$(git status --porcelain)"
git cat-file -e "$commit^{commit}"
git switch --detach "$commit"
test "$(git rev-parse HEAD)" = "$commit"

rustup toolchain install 1.96.0 \
  --component clippy,rustfmt,llvm-tools-preview

out="${TMPDIR%/}/jet3-local-ci/$commit/macos"
test ! -e "$out"
python3 tools/ci_evidence.py run-platform \
  --repo-root . --platform macos --output "$out"
python3 - "$out/platform-record.json" "$commit" <<'PY'
from pathlib import Path
import sys

record_path = Path(sys.argv[1])
commit = sys.argv[2]
sys.path.insert(0, str(Path.cwd() / "tools"))
from ci_evidence import validate_platform_record

record = validate_platform_record(record_path, commit)
print("validated:", record["platform"], record["commit"])
PY
shasum -a 256 "$out/platform-record.json"
```

## Exact-commit aggregation on macOS

Extract the three archives/records beneath distinct platform directories in a
new input directory. Ensure the resulting layout has exactly one record at
each of `linux/platform-record.json`, `macos/platform-record.json`, and
`windows/platform-record.json`, with each record's `logs/` directory beside
it. Then create and independently verify the aggregate:

```sh
set -euo pipefail
commit=4f067b1481bf828411b443b4db3b111447b0ad44
inputs="${TMPDIR%/}/jet3-g1-inputs-$commit"
bundle="${TMPDIR%/}/g1-cross-platform-$commit"
test ! -e "$bundle"

python3 tools/ci_evidence.py aggregate \
  --input-root "$inputs" \
  --output "$bundle" \
  --expected-commit "$commit"
python3 tools/ci_evidence.py verify-aggregate \
  "$bundle" --expected-commit "$commit"
```

Only this validated three-platform aggregate may be selected through
`JET3_G1_EVIDENCE` for full acceptance. The Windows and Linux records alone do
not satisfy G1, and none of these self-checks establish DAO compatibility.
