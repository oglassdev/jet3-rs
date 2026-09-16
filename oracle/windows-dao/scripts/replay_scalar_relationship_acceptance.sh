#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -ne 4 ]; then
  echo "usage: $0 EVIDENCE_ROOT PREPARED_ROOT REPO OUTPUT_DIR" >&2
  exit 2
fi
evidence=$(realpath "$1")
prepared=$(realpath "$2")
repo=$(realpath "$3")
out=$(realpath -m "$4")
tools=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
python3 -B "$tools/scalar_relationship_creation_acceptance.py" \
  --repo "$repo" \
  --prepared-root "$prepared/creation-r2" \
  --native-report "$evidence/CREATION_REPORT.json" \
  --capture-run "$evidence/runs/20260916T113548Z-scalar-candidate-create-r1" \
  --producer "$evidence/scalar_relationship_candidate_readback_r3.ps1" \
  --inputs-zip "$evidence/scalar-creation-inputs-r2.zip" \
  --expected-refusals 14 \
  --report "$out/CREATION_ACCEPTANCE_REPORT.json"
python3 -B "$tools/scalar_relationship_creation_acceptance.py" \
  --repo "$repo" \
  --prepared-root "$prepared/creation-supplement-r1" \
  --native-report "$evidence/SUPPLEMENT_CREATION_REPORT.json" \
  --capture-run "$evidence/runs/20260916T113714Z-scalar-candidate-supplement-r1" \
  --producer "$evidence/scalar_relationship_supplement_candidate_readback.ps1" \
  --inputs-zip "$evidence/scalar-supplement-candidate-inputs.zip" \
  --expected-refusals 0 \
  --report "$out/SUPPLEMENT_CREATION_ACCEPTANCE_REPORT.json"
python3 -B "$tools/scalar_relationship_lifecycle_acceptance.py" \
  --repo "$repo" \
  --native-root "$evidence" \
  --prepared-root "$prepared/lifecycle-r2" \
  --native-report "$evidence/LIFECYCLE_SELECTED_REPORT.json" \
  --capture-run "$evidence/runs/20260916T114113Z-scalar-candidate-life-main-r3" \
  --producer "$evidence/scalar_relationship_stage_readback_r2.ps1" \
  --capture-input "$evidence/candidate-inputs.zip" \
  --prepared-root "$prepared/lifecycle-boolean-r1" \
  --native-report "$prepared/native-boolean-selected-r1.json" \
  --capture-run "$evidence/runs/20260916T120120Z-scalar-cand-boolean-r1" \
  --producer "$evidence/scalar_relationship_stage_readback_boolean.ps1" \
  --capture-input "$evidence/candidate-boolean-inputs.zip" \
  --prepared-root "$prepared/lifecycle-zero-bits-r1" \
  --native-report "$evidence/ZERO_BITS_REPORT.json" \
  --capture-run "$evidence/runs/20260916T120131Z-scalar-cand-zero-r1" \
  --producer "$evidence/scalar_relationship_stage_readback_zero-bits.ps1" \
  --capture-input "$evidence/candidate-zero-bits-inputs.zip" \
  --prepared-root "$prepared/lifecycle-supplement-r1" \
  --native-report "$evidence/SUPPLEMENT_LIFECYCLE_REPORT.json" \
  --capture-run "$evidence/runs/20260916T115600Z-scalar-cand-supp-r1" \
  --producer "$evidence/scalar_relationship_stage_readback_supplement.ps1" \
  --capture-input "$evidence/candidate-supplement-inputs.zip" \
  --prepared-root "$prepared/lifecycle-fixed-field-r1" \
  --native-report "$evidence/FIXED_FIELD_REPORT.json" \
  --capture-run "$evidence/runs/20260916T115923Z-scalar-cand-fixed-r1" \
  --producer "$evidence/scalar_relationship_stage_readback_fixed-field.ps1" \
  --capture-input "$evidence/candidate-fixed-field-inputs.zip" \
  --report "$out/LIFECYCLE_ACCEPTANCE_REPORT.json"
sha256sum "$out"/*.json
