# Windows DAO oracle

Microsoft DAO is an optional black-box test oracle. It is never a production
dependency, and no MDB bytes or provider binaries are committed.

The retained tooling has three purposes:

- `protocol/v1_2/` defines the shared read scenario and snapshot contract.
- `scripts/dev/` plus `scripts/windows-dao-dev.py` support private exploratory
  runs in the local Windows VM described by `docs/LOCAL_WINDOWS_VM.md`.
- `.github/workflows/windows-dao-hosted.yml` proves that a hosted Windows
  runner can provide the required x86 DAO environment. A preregistered
  differential or allocation experiment adds its own minimal producer.

Concluded A1-A4 and M3-M5 experiment machinery was removed after its results
were recorded in `docs/PROVENANCE.md`. Git history is the archive.

## Portable protocol checks

```sh
python3 -B oracle/windows-dao/scripts/build_v1_2_inventory.py --check
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py schemas
python3 -B oracle/windows-dao/scripts/validate_protocol_v1_2.py inventory \
  oracle/windows-dao/protocol/v1_2/scenarios.json
python3 -B -m unittest discover -s oracle/windows-dao/tests -v
```

These checks execute no DAO operation and make no compatibility claim.

## Experiment discipline

Preregister each hosted experiment as one SHA-256-pinned plan before acquiring
data. Record the validated outcome once as an additive `EXP-` entry. A failure
after the first DAO mutation is a scientific result and must not be retried
without a human decision.
