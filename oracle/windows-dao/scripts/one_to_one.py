#!/usr/bin/env python3
"""One-to-one relationship creation, edits, row constraints and preservation cases."""
from __future__ import annotations

import argparse
import copy
from pathlib import Path
import relationship_forms_suite as f


def inventory():
    relation = f.rel('One', 'P', 'C', ['id'], ['qid'], unique=True)
    native_relation = f.relation('One', 'P', 'C', 1, [('id', 'qid')])
    inputs = {'base': f.BASE, 'one': f.BASE + [native_relation],
              'cascades': f.BASE + [f.relation('One', 'P', 'C', 4353, [('id', 'qid')])],
              'loose': f.BASE + [f.relation('One', 'P', 'C', 3, [('id', 'qid')])]}
    cases = []
    for kind, sql in {
        'plain': 'CREATE INDEX Existing ON C (qid)',
        'unique': 'CREATE UNIQUE INDEX Existing ON C (qid)',
        'descending': 'CREATE UNIQUE INDEX Existing ON C (qid DESC)',
        'required': 'CREATE UNIQUE INDEX Existing ON C (qid) WITH DISALLOW NULL',
        'ignore-null': 'CREATE UNIQUE INDEX Existing ON C (qid) WITH IGNORE NULL',
    }.items():
        inputs[kind] = f.BASE + ([f.sql('UPDATE C SET qid = 3 WHERE id = 3')] if kind == 'required' else []) + [f.sql(sql)]
        cases.append(f.case('create-' + kind, kind, [f.create(relation)]))
    inputs['primary'] = f.BASE + [f.sql('UPDATE C SET qid = 3 WHERE id = 3'),
                                 f.sql('DROP INDEX PrimaryKey ON C'),
                                 f.sql('CREATE UNIQUE INDEX PrimaryKey ON C (qid) WITH PRIMARY')]
    cases += [f.case('create-primary', 'primary', [f.create(relation)]),
              f.case('create-new', 'base', [f.create(relation)]),
              f.case('create-loose', 'base', [f.create(dict(relation, enforce=False))]),
              f.case('replace-many', 'one', [f.schema({'operation': 'replace_relationship', 'name': 'One',
                                                     'relationship': dict(relation, unique=False)})]),
              f.case('replace-loose', 'one', [f.schema({'operation': 'replace_relationship', 'name': 'One',
                                                      'relationship': dict(relation, enforce=False)})]),
              f.case('replace-enforced', 'loose', [f.schema({'operation': 'replace_relationship', 'name': 'One',
                                                            'relationship': relation})]),
              f.case('drop', 'one', [f.schema({'operation': 'drop_relationship', 'name': 'One'})]),
              f.case('rename-child-column', 'one', [f.schema({'operation': 'rename_column', 'table': 'C', 'column': 'qid', 'name': 'owner'})]),
              f.case('rename-parent', 'one', [f.schema({'operation': 'rename_table', 'table': 'P', 'name': 'Parents'})]),
              f.case('drop-child-table', 'one', [f.schema({'operation': 'drop_table', 'table': 'C'})]),
              f.case('mixed', 'one', [f.create(f.rel('Many', 'Q', 'C', ['id'], ['qid']))])]

    def insert(name, source, key, expect=0, error=None):
        cells = [{'long': 4}, None, None, None, None, None if key is None else {'long': key}]
        statement = 'INSERT INTO C (id, qid) VALUES (4, ' + ('NULL' if key is None else str(key)) + ')'
        return f.case(name, source, [f.mutate({'operation': 'insert', 'table': 'C', 'values': cells}, statement, expect)],
                      kind='residue' if expect else 'accepted', dao_error=error)

    cases += [insert('insert-new', 'one', 3), insert('insert-null', 'one', None),
              insert('duplicate-child', 'one', 1, 1, 3022), insert('orphan-child', 'one', 99, 1, 3201),
              insert('loose-duplicate', 'loose', 1), insert('loose-orphan', 'loose', 99)]
    for name, source, operation, sql, expect, error in [
        ('child-duplicate-update', 'one', 'update', 'UPDATE C SET qid = 1 WHERE id = 2', 1, 3022),
        ('parent-referenced-update', 'one', 'update', 'UPDATE P SET id = 9 WHERE id = 1', 1, 3200),
        ('parent-referenced-delete', 'one', 'delete', 'DELETE FROM P WHERE id = 1', 1, 3200),
        ('cascade-update', 'cascades', 'update', 'UPDATE P SET id = 9 WHERE id = 1', 0, None),
        ('cascade-delete', 'cascades', 'delete', 'DELETE FROM P WHERE id = 1', 0, None),
    ]:
        child = name.startswith('child')
        table, row = ('C', 2) if child else ('P', 1)
        request = {'operation': operation, 'table': table}
        if operation == 'update': request.update(column=5 if child else 0, value={'long': 1 if child else 9})
        cases.append(f.case(name, source, [f.mutate(request, sql, expect, locate={'table': table, 'id': row})],
                            kind='residue' if expect else 'accepted', dao_error=error))
        if name.startswith('cascade'): cases[-1]['affected_tables'] = ['C']
    inputs['duplicates'] = f.BASE + [f.sql('UPDATE C SET qid = 1 WHERE id = 2')]
    cases.append(f.case('create-duplicate', 'duplicates', [f.create(relation, 1)], kind='residue', dao_error=3022))

    keys = [{'column': 'a'}, {'column': 'b'}]
    columns = [f.col(n, 'long') for n in ['id', 'a', 'b']]
    inputs['composite'] = [
        f.table('P', columns, [f.primary(), {'name': 'Key', 'kind': 'unique', 'fields': keys}]),
        f.table('C', columns),
        *[f.sql(f'INSERT INTO {t} (id, a, b) VALUES ({i}, {a}, {b})')
          for t in ['P', 'C'] for i, a, b in [(1, 10, 20), (2, 'NULL', 20), (3, 10, 'NULL'), (4, 'NULL', 'NULL')]],
        f.relation('One', 'P', 'C', 1, [('a', 'a'), ('b', 'b')])]
    for label, a, b, expected in [('partial-null', None, 20, 0), ('all-null', None, None, 0), ('duplicate', 10, 20, 1)]:
        cells = [{'long': 5}] + [None if v is None else {'long': v} for v in [a, b]]
        sql = f'INSERT INTO C (id, a, b) VALUES (5, {"NULL" if a is None else a}, {"NULL" if b is None else b})'
        cases.append(f.case('composite-' + label, 'composite', [f.mutate({'operation': 'insert', 'table': 'C', 'values': cells}, sql, expected)],
                            kind='residue' if expected else 'accepted', dao_error=3022 if expected else None))
    return inputs, cases


def plan(args):
    args.out.mkdir(parents=True)
    inputs, cases = inventory()
    f.INPUTS.update(inputs)
    jobs = {'inputs': [], 'edits': []}
    template = []
    for replica in [1, 2]:
        suffix = f'-r{replica}'
        jobs['inputs'] += [{'name': name + suffix, 'ops': ops} for name, ops in inputs.items()]
        for source in cases:
            item = copy.deepcopy(source)
            item['normalize_table_dates'] = f.dated_tables(item)
            # Only newly built index trees and relationship catalog maps may differ in page placement.
            placement = {'create-plain': 2, 'create-descending': 2, 'create-required': 2,
                         'create-ignore-null': 2, 'create-primary': 1, 'create-new': 1,
                         'replace-many': 1, 'replace-enforced': 1, 'mixed': 2}
            if item['name'] in placement:
                item['placement_roles'] = [f"C/index/{placement[item['name']]}/owned", f.AVAILABLE, f.OWNED]
            item['name'] += suffix
            item['input_name'] += suffix
            template.append(item)
            jobs['edits'].append({'name': item['name'], 'input': item['input_name'],
                                  'steps': [step.get('dao') or {'request': step['request']} for step in item['steps']]})
    f.write(args.out / 'jobs.json', jobs)
    f.write(args.out / 'edits-template.json', {'cases': template})


def creation_plan(args):
    args.out.mkdir(parents=True)
    (args.out / 'creation').mkdir()
    jobs, cases = {'inputs': [], 'edits': []}, []
    for mode in ['new', 'unique', 'plain', 'primary', 'required', 'ignore-null', 'descending', 'mixed', 'self']:
        columns = [f.col('id', 'long'), f.col('pid', 'long')]
        child_index = {'name': 'Existing', 'kind': 'unique', 'fields': [{'column': 'pid'}]}
        if mode == 'plain': child_index['kind'] = 'ordinary'
        if mode == 'primary': child_index['kind'] = 'primary'
        if mode in ['required', 'ignore-null']: child_index['null_policy'] = 'required' if mode == 'required' else 'ignore_all_null'
        if mode == 'descending': child_index['fields'][0]['direction'] = 'descending'
        indexes = [] if mode == 'primary' else [f.primary()]
        if mode not in ['new', 'mixed', 'self']: indexes.append(child_index)
        children = [[{'long': 1}, {'long': 1}], [{'long': 2}, {'long': 2}]]
        if mode not in ['primary', 'required']: children += [[{'long': 3}, None], [{'long': 4}, None]]
        tables = [
            {'name': 'P', 'columns': columns, 'indexes': [f.primary()], 'rows': [[{'long': 1}, None], [{'long': 2}, None]]},
            {'name': 'C', 'columns': columns, 'indexes': indexes, 'rows': children},
        ]
        relations = [f.rel('One', 'C' if mode == 'self' else 'P', 'C', ['id'], ['pid'], unique=True,
                           cascade_updates=True, cascade_deletes=True)]
        if mode == 'mixed': relations.append(f.rel('Many', 'P', 'C', ['id'], ['pid']))
        request = {'tables': tables, 'relationships': relations}
        native = []
        for table in tables:
            # SQL creates optional indexes so the native direction/null-policy inputs are explicit.
            native.append(f.table(table['name'], columns, [f.primary()] if table['name'] == 'P' or mode != 'primary' else []))
            if table['name'] == 'C' and mode not in ['new', 'mixed', 'self']:
                unique = '' if mode == 'plain' else 'UNIQUE '
                direction = ' DESC' if mode == 'descending' else ''
                suffix = {'primary': ' WITH PRIMARY', 'required': ' WITH DISALLOW NULL', 'ignore-null': ' WITH IGNORE NULL'}.get(mode, '')
                native.append(f.sql(f'CREATE {unique}INDEX Existing ON C (pid{direction}){suffix}'))
            for row in table['rows']:
                native.append(f.sql(f"INSERT INTO {table['name']} (id, pid) VALUES ({f.literal(row[0])}, {f.literal(row[1])})"))
        for relation in relations:
            native.append(f.relation(relation['name'], relation['parent']['table'], 'C', 4353 if relation.get('unique') else 0, [('id', 'pid')]))
        for replica in [1, 2]:
            name = f'creation-{mode}-r{replica}'
            jobs['inputs'].append({'name': name, 'ops': native})
            cases.append(dict(f.case(name, name, [], kind='creation'), normalize_table_dates=['P', 'C']))
            f.write(args.out / 'creation' / (name + '.json'), request)
    f.write(args.out / 'jobs.json', jobs)
    f.write(args.out / 'edits-template.json', {'cases': cases})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument("--creation", action="store_true")
    args = parser.parse_args()
    (creation_plan if args.creation else plan)(args)


if __name__ == '__main__':
    main()
