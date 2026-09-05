#!/usr/bin/env python3
"""EXP-0161: post-acquisition deletion analysis; no DAO or capture path."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import shutil

import row_delete_layout as original

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/row-delete-reanalysis.plan.json'


def verify(outbox):
    plan = json.loads(PLAN.read_text())
    for name, expected in plan['inputs'].items():
        if original.identity(ROOT / name) != expected:
            raise ValueError('Input pin mismatch: ' + name)
    for name, expected in plan['artifacts'].items():
        if original.identity(outbox / name) != expected:
            raise ValueError('Artifact pin mismatch: ' + name)
    return plan


def active_bytes(image):
    raw = bytes.fromhex(image['hex'])
    entries = image['directory']
    if len(raw) != 2048 or raw[0] != 1 or entries is None:
        raise ValueError('Expected decoded data page')
    end = 10 + 2 * len(entries)
    if int.from_bytes(raw[8:10], 'little') != len(entries) or end > len(raw):
        raise ValueError('Directory boundary mismatch')
    active = set(range(end))
    previous = len(raw)
    for ordinal, entry in enumerate(entries):
        start, stop = entry['start'], entry['end']
        if entry['row'] != ordinal or not end <= start <= stop <= previous:
            raise ValueError('Stored row boundary mismatch')
        if raw[start:stop].hex() != entry['raw_hex']:
            raise ValueError('Stored row bytes mismatch')
        active.update(range(start, stop)); previous = start
    return raw, active


def spans(positions):
    result = []
    for position in sorted(positions):
        if result and result[-1][1] == position: result[-1][1] += 1
        else: result.append([position, position + 1])
    return result


def signature(checkpoints, transitions):
    result = original.question_signature(checkpoints, transitions)
    for movement, projected in zip(transitions, result['transitions']):
        for tracked, pair in zip(movement['tracked_data_pages'], projected['tracked']):
            images = [tracked[role]['image'] for role in ('before', 'after')]
            if not all(image and image['directory'] is not None for image in images):
                continue
            before, active_before = active_bytes(images[0])
            after, active_after = active_bytes(images[1])
            # Retain all changed bytes, even when outside either state's stored rows.
            selected = active_before | active_after | {i for i in range(2048) if before[i] != after[i]}
            selected.difference_update(range(4, 8))  # Existing owner-address exclusion.
            ranges = spans(selected)
            for state, raw in zip(pair, (before, after)):
                del state['page_bytes_without_owner']
                state['compared_ranges'] = [[start, end, raw[start:end].hex()] for start, end in ranges]
    return result


def slack_diagnostics(observations):
    diagnostics = []
    for observation in observations:
        for number, movement in enumerate(observation['transitions']):
            for tracked in movement['tracked_data_pages']:
                images = [tracked[role]['image'] for role in ('before', 'after')]
                if not all(image and image['directory'] is not None for image in images): continue
                before, first = active_bytes(images[0]); after, second = active_bytes(images[1])
                ignored = set(range(2048)) - first - second
                ignored = {i for i in ignored if before[i] == after[i]}
                diagnostics.append(dict(arm=observation['arm'], replica=observation['replica'],
                    transition=number, page=tracked['page'], unchanged_unused_ranges=
                    [[start, end, before[start:end].hex()] for start, end in spans(ignored)]))
    return diagnostics


def build_report(outbox):
    plan = verify(outbox)
    with tempfile.TemporaryDirectory(prefix='jet3-delete-secondary-') as directory:
        temporary = Path(directory)
        for name in plan['artifacts']: shutil.copyfile(outbox / name, temporary / name)
        original_plan = original.verify_inputs()
        result = json.loads((temporary / 'result.json').read_text(encoding='utf-8-sig'))
        prior = original.build_report(result, temporary, original_plan)
        prior['result_sha256'] = original.identity(temporary / 'result.json')['sha256']
        if (original.canonical(prior) + '\n').encode() != (temporary / 'report.json').read_bytes():
            raise ValueError('Original report does not reproduce')
        if prior['outcome'] != 'no_outcome': raise ValueError('Expected original no_outcome')
        spec = importlib.util.spec_from_file_location('private_delete_secondary', ROOT / 'oracle/windows-dao/scripts/row_delete_layout.py')
        private = importlib.util.module_from_spec(spec); spec.loader.exec_module(private)
        private.question_signature = signature
        revised = private.build_report(result, temporary, original_plan)
    if revised['observations'] != prior['observations']:
        raise ValueError('Secondary analysis changed raw observations')
    revised.update(document_type='dao_row_delete_secondary_report', secondary_analysis=True,
        plan_sha256=original.identity(PLAN)['sha256'], source_outcome=prior['outcome'],
        source_reasons=prior['reasons'], source_report=plan['artifacts']['report.json'],
        source_result=plan['artifacts']['result.json'], comparison_change=plan['comparison_change'],
        unused_bytes=slack_diagnostics(revised['observations']))
    verify(outbox)
    return revised


def analyze(outbox, output):
    if output.resolve().is_relative_to(outbox.resolve()):
        raise ValueError('Output must be outside source outbox')
    if output.exists(): raise ValueError('Output already exists')
    report = build_report(outbox)
    with output.open('x') as stream: stream.write(original.canonical(report) + '\n')
    print(report['outcome'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preflight', 'analyze'])
    parser.add_argument('outbox', type=Path); parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    verify(args.outbox)
    if subprocess.check_output(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT) != PLAN.read_bytes():
        raise ValueError('Secondary plan must be committed')
    if args.command == 'analyze':
        if args.output is None: parser.error('--output required')
        analyze(args.outbox, args.output)
    else: print('Committed secondary inputs and retained artifacts match.')


if __name__ == '__main__': main()
