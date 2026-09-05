#!/usr/bin/env python3
"""EXP-0153: read-only hosted write reanalysis by unique table/index identity."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import dao_write_diff as original

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/hosted-write-reanalysis.plan.json'
ORIGINAL_ASSERT_INDEXES = original.assert_indexes


def identity(path):
    return {'size': path.stat().st_size, 'sha256': original.digest(path)}


def assert_indexes(observations, snapshot):
    expected = [(table['name'], index['name']) for table in snapshot['tables'] for index in table['indexes']]
    keys = [(entry['table'], entry['index']) for entry in observations]
    if len(keys) != len(set(keys)) or set(keys) != set(expected):
        raise original.ValidationError('Missing, duplicate or unexpected table/index observation')
    by_key = dict(zip(keys, observations))
    # Reorder only whole records. All original row/traversal/Seek checks remain.
    ORIGINAL_ASSERT_INDEXES([by_key[key] for key in expected], snapshot)


def verify(source, committed=True):
    plan = json.loads(PLAN.read_text())
    for name, sha in plan['inputs'].items():
        if original.digest(ROOT / name) != sha:
            raise ValueError('Secondary input pin mismatch: ' + name)
    original.validate_plan(plan['original_plan_sha256'])
    for name, expected in plan['retained'].items():
        if identity(source / name) != expected:
            raise ValueError('Retained artifact pin mismatch: ' + name)
    if committed:
        saved = subprocess.run(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT,
                               check=True, capture_output=True).stdout
        if saved != PLAN.read_bytes():
            raise ValueError('Secondary plan must be committed')
    return plan


def build_report(source, plan):
    windows = source / plan['windows_artifact']
    acquired = windows / 'dao-write-v1_2'
    previous = original.load_json(acquired / 'report.json')
    environment = original.load_json(windows / 'environment.json')
    original.protocol.SCHEMA_SET.validate(environment)
    if previous['outcome'] != 'no_outcome' or previous['error'] != 'Incomplete index observation inventory':
        raise ValueError('Unexpected original outcome')
    if environment['status'] != 'ready' or environment['accepted_provider'] is None:
        raise ValueError('Retained provider was not ready')
    preparation = original.load_json(acquired / 'preparation.json')
    if preparation['source_revision'] != plan['source_revision']:
        raise ValueError('Retained source revision mismatch')
    generated = source / plan['generated_artifact']
    for entry in preparation['scenarios']:
        name = entry['scenario_id']
        if original.digest(generated / name / 'database.mdb') != original.digest(acquired / name / 'database.mdb'):
            raise ValueError('Linux/Windows retained image mismatch')
    with tempfile.TemporaryDirectory(prefix='hosted-write-secondary-') as temporary:
        staged = Path(temporary) / 'evaluation'
        shutil.copytree(acquired, staged)
        saved = original.assert_indexes
        try:
            original.assert_indexes = assert_indexes
            try:
                original.evaluate(staged)
            except Exception:
                # The original evaluator records its failed comparison as no_outcome.
                if not (staged / 'report.json').exists():
                    raise
            report = original.load_json(staged / 'report.json')
        finally:
            original.assert_indexes = saved
    report.update(document_type='dao_hosted_write_reanalysis_report',
                  analysis_mode='post-acquisition read-only reanalysis',
                  plan_sha256=original.digest(PLAN), original_plan_sha256=plan['original_plan_sha256'],
                  original_report=plan['retained'][plan['windows_artifact'] + '/dao-write-v1_2/report.json'],
                  original_outcome=previous['outcome'], source_revision=plan['source_revision'],
                  hosted_run=plan['hosted_run'], support_matrix_movement=False)
    return report


def analyze(source, output):
    plan = verify(source)
    if output.exists() or output.resolve().is_relative_to(source.resolve()):
        raise ValueError('New secondary report must be outside retained artifact directory')
    report = build_report(source, plan)
    verify(source)
    with output.open('x') as target:
        target.write(original.common.canonical_bytes(report).decode() + '\n')
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
        print('Committed secondary plan, runtime inputs and retained artifacts match.')
    else:
        if args.output is None:
            parser.error('analyze requires --output')
        analyze(args.source, args.output)


if __name__ == '__main__':
    main()
