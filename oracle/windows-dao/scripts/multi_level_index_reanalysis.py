#!/usr/bin/env python3
"""EXP-0145: separately pinned read-only analysis of the retained EXP-0139 run.

The consumed analysis remains unchanged. This finite decoder admits branch
header byte 21 values 1 or 2, records that byte and subtree height separately,
and keeps all existing key/locator/separator/map and semantic checks.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

import multi_level_index as original
from multi_level_index_structure import catalog, require

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/multi-level-index-reanalysis.plan.json'


def tree(data, root, owner):
    nodes, seen, leaf_entries = [], set(), []
    def visit(number, depth):
        require(depth <= 32 and number not in seen, 'Repeated page or excessive index depth')
        seen.add(number)
        page = catalog._page(data, number, 'index node')
        branch = page[0] == 3
        require(page[0] in (3, 4) and page[1] == 1 and page[21] in ((1, 2) if branch else (0,)), 'Index node header')
        require(int.from_bytes(page[4:8], 'little') == owner, 'Index owner mismatch')
        prefix = page[20]
        previous, following, tail = [int.from_bytes(page[offset:offset + 4], 'little') for offset in (8, 12, 16)]
        require(bool(tail) == branch, 'Index tail child mismatch')
        area = page[248:]
        boundaries = [position * 8 + bit for position, byte in enumerate(page[22:248]) for bit in range(8) if byte & (1 << bit)]
        require(all(prefix < end <= 1800 for end in boundaries), 'Index boundary outside entry area')
        require(int.from_bytes(page[2:4], 'little') == 1800 - (boundaries[-1] if boundaries else 0), 'Index free space mismatch')
        require(bool(boundaries) or (not branch and prefix == 0), 'Empty branch or unmatched prefix')
        node = dict(page=number, depth=depth, previous=previous, next=following, tail=tail, prefix=prefix, entries=len(boundaries), children=[], header_class=page[21])
        nodes.append(node)
        start, entries = prefix, []
        for end in boundaries:
            entry = area[:prefix] + area[start:end]
            require(len(entry) >= (8 if branch else 4), 'Short index entry')
            entries.append(entry)
            start = end
        require(entries == sorted(entries), 'Unsorted complete index entries')
        if not branch:
            leaf_entries.extend(entries)
            node['subtree_height'] = 0
            return (entries[-1] if entries else None), 0
        child_heights = []
        for entry in entries:
            child = int.from_bytes(entry[-4:], 'big')
            node['children'].append(child)
            maximum, height = visit(child, depth + 1)
            child_heights.append(height)
            require(maximum == entry[:-4], 'Branch separator is not child maximum key and locator')
        node['children'].append(tail)
        maximum, height = visit(tail, depth + 1)
        require(all(child == height for child in child_heights), 'Unequal child subtree heights')
        node['subtree_height'] = height + 1
        return maximum, height + 1
    visit(root, 1)
    for depth in sorted({node['depth'] for node in nodes}):
        level = [node for node in nodes if node['depth'] == depth]
        for position, node in enumerate(level):
            require(node['previous'] == (level[position - 1]['page'] if position else 0)
                    and node['next'] == (level[position + 1]['page'] if position + 1 < len(level) else 0), 'Index sibling chain mismatch')
    require(len({node['depth'] for node in nodes if not node['children']}) == 1, 'Unequal leaf depth')
    require(leaf_entries == sorted(leaf_entries), 'Index traversal is not sorted')
    return nodes, leaf_entries


def verify(source, committed=True):
    plan = json.loads(PLAN.read_text())
    for name, identity in plan['inputs'].items():
        if original.digest(ROOT / name) != identity:
            raise ValueError('Secondary-analysis input pin mismatch: ' + name)
    initial = json.loads(original.PLAN.read_text())
    original.verify_inputs(initial)
    for name, identity in plan['retained'].items():
        if original.identity(source / name) != identity:
            raise ValueError('Retained source pin mismatch: ' + name)
    if committed:
        saved = subprocess.run(['git', 'show', f'HEAD:{PLAN.relative_to(ROOT)}'], cwd=ROOT, check=True, capture_output=True).stdout
        if saved != PLAN.read_bytes():
            raise ValueError('Secondary-analysis plan must be committed')
    return plan, initial


def height_summary(tables):
    result = []
    for table in tables:
        for index in table['indexes']:
            counts = Counter((node['header_class'], node['subtree_height']) for node in index['nodes'])
            result.append(dict(table=table['name'], root=index['root'], depth=index['depth'],
                header_height_counts=[dict(header_class=key[0], subtree_height=key[1], nodes=count) for key, count in sorted(counts.items())],
                all_classes_equal_height=all(a == b for a, b in counts)))
    return result


def build_report(source, plan, initial):
    result = json.loads((source / 'result.json').read_text())
    old = json.loads((source / 'report.json').read_text())
    if (result['document_type'] != 'dao_multi_level_index_result' or result['plan_sha256'] != original.digest(original.PLAN)
            or result['environment']['process_bits'] != 32 or result['environment']['provider'] != 'DAO.DBEngine.36'
            or result['development_only'] is not True or old['outcomes'] != {arm: 'no_outcome' for arm in original.ARMS}):
        raise ValueError('Original result/report binding mismatch')
    expected = [(arm, replica) for arm in original.ARMS for replica in range(1, 4)]
    actual = [(pair['arm'], pair['replica']) for pair in result['replicas']]
    if actual != expected:
        raise ValueError('Retained inventory differs from declared single run')
    observations, groups = [], {arm: [] for arm in original.ARMS}
    controls_ok, unchanged = True, True
    saved_tree = original.structure.tree
    try:
        # The isolated secondary decoder replaces only the tree function in memory.
        original.structure.tree = tree
        for pair in result['replicas']:
            arm, replica = pair['arm'], pair['replica']
            tables = initial['arms'][arm]
            expected_snapshot = original.normalize(original.expected_snapshot(arm, tables), tables)[1]
            for role in ('control', 'candidate'):
                observation = pair[role]
                path = source / f'{arm}-{role}-r{replica}.mdb'
                if original.identity(path) != observation['after']:
                    raise ValueError('Retained identity mismatch')
                if role == 'candidate' and observation['before'] != initial['candidates'][arm]:
                    raise ValueError('Original candidate binding mismatch')
                unchanged &= observation['before'] == observation['after']
                semantic_ok, structure_ok, details, reason = False, False, None, None
                try:
                    valid, snapshot = original.normalize(observation['snapshot'], tables)
                    semantic_ok = (valid and snapshot == expected_snapshot and observation['status'] == 'pass'
                                   and observation['endpoint'] == 'complete' and observation['error'] is None)
                    details = original.structure.observe(path.read_bytes(), tables,
                        {table['name']: original.rows_for(arm, table['name']) for table in tables}, role == 'candidate')
                    structure_ok = True
                except (ValueError, KeyError, TypeError, IndexError) as error:
                    reason = str(error)
                passed = semantic_ok and structure_ok
                summary = dict(arm=arm, replica=replica, role=role, semantic_match=semantic_ok,
                    structural_match=structure_ok, passed=passed, reason=reason, tables=details,
                    header_height=height_summary(details) if details is not None else None)
                observations.append(summary)
                if role == 'control':
                    controls_ok &= passed
                else:
                    groups[arm].append((passed, semantic_ok, structure_ok, reason))
    finally:
        original.structure.tree = saved_tree
    outcomes = {arm: 'no_outcome' for arm in original.ARMS}
    complete = result['error'] is None and result['mutation_started'] is True and unchanged and controls_ok
    if complete:
        for arm, group in groups.items():
            if all(item == group[0] for item in group):
                outcomes[arm] = 'observed_accepted' if group[0][0] else 'not_observed_accepted'
    relation_complete = all(observation['header_height'] is not None for observation in observations)
    relations = []
    for arm in original.ARMS:
        for role in ('candidate', 'control'):
            selected = [o for o in observations if o['arm'] == arm and o['role'] == role]
            values = [[dict(table=t['table'], all_classes_equal_height=t['all_classes_equal_height'],
                            class_height_pairs=[(x['header_class'], x['subtree_height']) for x in t['header_height_counts']])
                       for t in o['header_height']] for o in selected if o['header_height'] is not None]
            agrees = len(values) == 3 and all(value == values[0] for value in values)
            relation_complete &= agrees
            relations.append(dict(arm=arm, role=role, agrees=agrees, tables=values[0] if agrees else None))
    return dict(document_type='dao_multi_level_index_reanalysis_report', development_only=True,
        compatibility_claim=False, support_movement=False, plan_sha256=original.digest(PLAN),
        original_plan_sha256=original.digest(original.PLAN), original_result=plan['retained']['result.json'],
        original_report=plan['retained']['report.json'], original_outcomes=old['outcomes'], outcomes=outcomes,
        header_height_outcome='answered' if relation_complete and complete else 'no_outcome',
        header_height_relations=relations, observations=observations)


def analyze(source, output):
    plan, initial = verify(source)
    if output.exists() or output.resolve().is_relative_to(source.resolve()):
        raise ValueError('Secondary report must be new and outside the retained source directory')
    report = build_report(source, plan, initial)
    # Detect accidental changes to any retained source before publishing a new report.
    verify(source)
    with output.open('x') as target:
        target.write(original.canonical(report) + '\n')
    print(output)
    print(original.canonical(report['outcomes']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preflight', 'analyze'])
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.command == 'preflight':
        verify(args.source)
        print('Committed secondary plan, inputs and retained identities match.')
    else:
        if args.output is None:
            parser.error('analyze requires --output')
        analyze(args.source, args.output)


if __name__ == '__main__':
    main()
