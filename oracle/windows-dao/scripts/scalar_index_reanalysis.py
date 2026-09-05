#!/usr/bin/env python3
"""EXP-0149: selected completed EXP-0143 captures, strict wrapper normalization."""
import argparse
import copy
import json
from pathlib import Path
import subprocess

import scalar_index_layout as original

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/scalar-index-reanalysis.plan.json'


def normalize_snapshot(snapshot, arm):
    normalized = copy.deepcopy(snapshot)
    if len(arm['fields']) != 1:
        raise ValueError('Secondary selection requires one scalar key column')
    for group in ('rows', 'traversal'):
        for row in normalized[group]:
            wrapper = row['values']
            if (not isinstance(wrapper, dict) or set(wrapper) != {'value', 'Count'}
                    or type(wrapper['Count']) is not int or wrapper['Count'] != 1
                    or not isinstance(wrapper['value'], list) or len(wrapper['value']) != 1):
                raise ValueError('Unexpected scalar values wrapper or arity')
            row['values'] = wrapper['value']
    return normalized


def verify(source, committed=True):
    plan = json.loads(PLAN.read_text())
    for name, sha in plan['inputs'].items():
        if original.digest(ROOT / name) != sha:
            raise ValueError('Secondary input pin mismatch: ' + name)
    initial = original.verify_inputs()
    for name, expected in plan['retained'].items():
        if original.identity(source / name) != expected:
            raise ValueError('Retained source pin mismatch: ' + name)
    if committed:
        saved = subprocess.run(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT,
                               check=True, capture_output=True).stdout
        if saved != PLAN.read_bytes():
            raise ValueError('Secondary plan must be committed')
    return plan, initial


def build_report(source, plan, initial):
    result = json.loads((source / 'result.json').read_text(encoding='utf-8-sig'))
    previous = json.loads((source / 'report.json').read_text())
    arms = initial['arms'][:12]
    if ([a['name'] for a in arms] != plan['selected_arms']
            or any(a['family'] != 'scalar' or a['fields'][0]['type'] not in (1, 2, 3, 5, 6, 7) for a in arms)):
        raise ValueError('Unexpected post-acquisition arm selection')
    if (result['document_type'] != 'dao_scalar_index_layout_result'
            or result['plan_sha256'] != original.digest(original.PLAN)
            or result['development_only'] is not True or result['mutation_started'] is not True
            or result['environment']['process_bits'] != 32
            or result['environment']['provider'] != 'DAO.DBEngine.36'
            or result['error'] != plan['original_acquisition_error']
            or previous['outcome'] != 'no_outcome' or previous['observations'] != []):
        raise ValueError('Original result/report binding differs')
    expected = [(arm['name'], replica) for arm in arms for replica in range(1, 4)]
    if [(e['arm'], e['replica']) for e in result['replicas']] != expected:
        raise ValueError('Completed capture inventory differs')
    attempts = result['attempts']
    if ([(e['arm'], e['replica']) for e in attempts] != expected + [('date-ascending', 1)]
            or attempts[-1]['operations'] != []):
        raise ValueError('Original incomplete Date attempt differs')
    observations, reasons = [], []
    by_name = {arm['name']: arm for arm in arms}
    for entry, attempt in zip(result['replicas'], attempts):
        label = f"{entry['arm']}-r{entry['replica']}"
        if (entry['file'] != label + '.mdb' or original.identity(source / entry['file']) != entry['after']
                or attempt['operations'] != entry['operations']):
            raise ValueError('Completed retained identity or operation binding differs')
        if entry['before'] != entry['after']:
            reasons.append(label + ': read-only image changed')
        try:
            if entry['status'] != 'pass' or entry['endpoint'] != 'complete' or entry['error'] is not None:
                raise ValueError('Incomplete original capture')
            arm = by_name[entry['arm']]
            normalized = dict(entry, snapshot=normalize_snapshot(entry['snapshot'], arm))
            decoded = original.observe((source / entry['file']).read_bytes(), arm)
            matches = original.correlate(normalized, decoded, arm)
            observations.append({**{k: normalized[k] for k in ('arm', 'replica', 'operations', 'snapshot')},
                                 'decoded': decoded, 'requested_payloads_match': matches})
        except (original.catalog.DecodeError, ValueError, KeyError, TypeError) as error:
            reasons.append(label + ': ' + str(error))
    if len(observations) != 36:
        reasons.append('Selected completed captures did not all validate')
    for arm in arms:
        group = [original.comparable(o) for o in observations if o['arm'] == arm['name']]
        if len(group) != 3 or any(o != group[0] for o in group):
            reasons.append(arm['name'] + ': selected replica disagreement')
    return dict(document_type='dao_scalar_index_reanalysis_report', development_only=True,
                analysis_mode='post-acquisition selected completed captures only',
                plan_sha256=original.digest(PLAN), original_plan_sha256=original.digest(original.PLAN),
                original_result=plan['retained']['result.json'], original_report=plan['retained']['report.json'],
                original_outcome=previous['outcome'], original_acquisition_error=result['error'],
                original_incomplete_attempt=attempts[-1],
                original_partial_image=plan['retained']['date-ascending-r1.mdb'],
                selected_arms=plan['selected_arms'], selected_captures=36, original_planned_captures=78,
                outcome='answered' if not reasons else 'no_outcome', reasons=reasons,
                observations=observations, compatibility_claim=False, support_matrix_movement=False)


def analyze(source, output):
    plan, initial = verify(source)
    if output.exists() or output.resolve().is_relative_to(source.resolve()):
        raise ValueError('New secondary report must be outside retained source tree')
    report = build_report(source, plan, initial)
    verify(source)
    with output.open('x') as target:
        target.write(original.canonical(report) + '\n')
    print(output)
    print(report['outcome'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preflight', 'analyze'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'preflight':
        verify(args.source)
        print('Committed secondary plan, runtime inputs and retained files match.')
    else:
        if args.output is None:
            parser.error('analyze requires --output')
        analyze(args.source, args.output)


if __name__ == '__main__':
    main()
