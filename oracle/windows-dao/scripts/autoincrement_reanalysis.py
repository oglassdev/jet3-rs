#!/usr/bin/env python3
"""Secondary analysis of pinned EXP-0131 captures; never invokes DAO."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess

import autoincrement_layout as original

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/autoincrement-reanalysis.plan.json'


def load_private(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'oracle/windows-dao/scripts' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def analyzer():
    module = load_private('secondary_autoincrement', 'autoincrement_layout.py')
    decoder = load_private('secondary_catalog', 'system_catalog.py')
    if decoder.MAX_ROWS_PER_PAGE != 64:
        raise ValueError('Original row limit changed')
    decoder.MAX_ROWS_PER_PAGE = 256
    module.catalog = decoder
    return module


def verify(outbox):
    plan = json.loads(PLAN.read_text())
    if plan['experiment_id'] != 'autoincrement-reanalysis':
        raise ValueError('Unexpected secondary plan')
    for name, expected in plan['inputs'].items():
        if original.digest(ROOT / name) != expected:
            raise ValueError('Input pin mismatch: ' + name)
    original.verify_inputs()
    committed = subprocess.run(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT,
                               check=True, capture_output=True).stdout
    if committed != PLAN.read_bytes():
        raise ValueError('Secondary plan must be committed')
    for name, expected in plan['artifacts'].items():
        if original.identity(outbox / name) != expected:
            raise ValueError('Artifact pin mismatch: ' + name)
    source = json.loads((outbox / 'report.json').read_text())
    if source['outcome'] != 'no_outcome':
        raise ValueError('Source report must remain no_outcome')
    return plan


def analyze(outbox, output):
    plan = verify(outbox)
    # Never replace a source artifact or an existing report.
    if output.resolve().is_relative_to(outbox.resolve()):
        raise ValueError('Output must be outside the source outbox')
    if output.exists():
        raise ValueError('Output already exists')
    result = json.loads((outbox / 'result.json').read_text(encoding='utf-8-sig'))
    module = analyzer()
    report = module.build_report(result, outbox, original.verify_inputs())
    report['document_type'] = 'dao_autoincrement_secondary_report'
    report['source'] = {'experiment': 'EXP-0131', 'outcome_entry': 'EXP-0132',
                        'outcome': 'no_outcome', 'artifacts': plan['artifacts']}
    report['secondary_plan_sha256'] = original.digest(PLAN)
    report['analysis_change'] = {'module': 'system_catalog', 'constant': 'MAX_ROWS_PER_PAGE',
                                 'original': 64, 'secondary': 256}
    verify(outbox)
    with output.open('x') as destination:
        destination.write(original.canonical(report) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('preflight', 'analyze'))
    parser.add_argument('outbox', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'preflight':
        verify(args.outbox)
        print('Committed secondary plan, inputs and retained artifacts match.')
    else:
        if args.output is None:
            parser.error('analyze requires --output outside source outbox')
        print(analyze(args.outbox, args.output)['outcome'])


if __name__ == '__main__':
    main()
