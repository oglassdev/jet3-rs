import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('scalar_index_layout', SCRIPTS / 'scalar_index_layout.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class ScalarIndexLayoutTests(unittest.TestCase):
    def plan(self):
        return json.loads(experiment.PLAN.read_text())

    @staticmethod
    def decoded(_data, arm):
        rows = [{'tag': i + 1, 'values': value} for i, value in enumerate(arm['rows'])]
        if arm['required']:
            rows = [row for row in rows if None not in row['values']]
        omitted = [row for row in rows if arm['ignore_nulls'] and None in row['values']]
        bound = [row for row in rows if row not in omitted]
        if arm['family'] == 'scalar':
            bound.sort(key=lambda row: experiment.semantic_key(row, arm))
        return {'rows': rows, 'row_count': len(rows), 'omitted_rows': omitted,
                'bindings': [dict(row, row_page=23, row=i, key_hex='deadbeef') for i, row in enumerate(bound)],
                'physical_index': {'entry_count': len(bound), 'flags': 1, 'keys': arm['fields']}}

    def fixture(self, outbox):
        plan = self.plan()
        result = {'document_type': 'dao_scalar_index_layout_result', 'development_only': True,
                  'plan_sha256': experiment.digest(experiment.PLAN), 'mutation_started': True,
                  'environment': {'process_bits': 32, 'provider': 'DAO.DBEngine.36'}, 'replicas': [], 'error': None}
        for arm in plan['arms']:
            decoded = self.decoded(None, arm)
            saved = {row['tag'] for row in decoded['rows']}
            operations = [{'tag': i + 1, 'status': 'updated' if i + 1 in saved else 'rejected',
                           'endpoint': 'update', 'native_codes': [] if i + 1 in saved else [9999],
                           'hresult': None if i + 1 in saved else -123} for i in range(len(arm['rows']))]
            snapshot = {'version': '3.0', 'tables': ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Rows'],
                        'fields': arm['fields'] + [{'name': 'Tag', 'type': 4, 'size': 4}],
                        'indexes': [{'name': 'ByKey', 'primary': False, 'unique': arm['unique'],
                                     'required': arm['required'], 'ignore_nulls': arm['ignore_nulls'],
                                     'fields': [{'name': f['name'], 'descending': d, 'attributes': int(d)}
                                                for f, d in zip(arm['fields'], arm['directions'])]}],
                        'rows': decoded['rows'], 'traversal': [{'tag': b['tag'], 'values': b['values']}
                                                            for b in decoded['bindings']]}
            for replica in range(1, 4):
                name = f"{arm['name']}-r{replica}.mdb"
                (outbox / name).write_bytes(b'synthetic')
                identity = experiment.identity(outbox / name)
                result['replicas'].append({'arm': arm['name'], 'replica': replica, 'file': name,
                                          'before': identity, 'after': identity, 'status': 'pass',
                                          'endpoint': 'complete', 'error': None,
                                          'operations': copy.deepcopy(operations), 'snapshot': copy.deepcopy(snapshot)})
        return plan, result

    def test_raw_bindings_reject_wrong_locator_and_nonnull_omission(self):
        arm = next(a for a in self.plan()['arms'] if a['name'] == 'null-ignore')
        definition = {'root': 20, 'columns': [{'name': 'A', 'type': 'Long', 'size': 4},
                                            {'name': 'Tag', 'type': 'Long', 'size': 4}],
                      'physical_indexes': [{'root': 24, 'map': {}}], 'logical_indexes': [{}], 'row_count': 2}
        rows = [{'page': 23, 'row': 0, 'values': [None, 1]}, {'page': 23, 'row': 1, 'values': [7, 2]}]
        leaf = {'entries': [{'row_page': 23, 'row': 1, 'key_hex': 'aabb'}], 'prefix_hex': ''}
        raw = bytearray(2048); raw[4:8] = (20).to_bytes(4, 'little')
        with patch.object(experiment.catalog, '_discover_catalog', return_value=({}, None, [{'values': ['Rows', 1, 20]}])), \
                patch.object(experiment.catalog, '_ordinal', side_effect=lambda _, name: ['Name', 'Type', 'Id'].index(name)), \
                patch.object(experiment.catalog, '_definition', return_value=definition), \
                patch.object(experiment.catalog, '_table_pages', return_value=([23], [])), \
                patch.object(experiment.catalog, '_table_rows', return_value=rows), \
                patch.object(experiment.catalog, '_page', return_value=raw), \
                patch.object(experiment.catalog, '_locator_pages', return_value={24}), \
                patch.object(experiment, 'leaf_entries', return_value=leaf):
            observed = experiment.observe(b'', arm)
            self.assertEqual(observed['bindings'][0]['values'], ['07000000'])
            self.assertEqual(observed['bindings'][0]['key_hex'], 'aabb')
            self.assertEqual(observed['omitted_rows'], [{'tag': 1, 'values': [None]}])
            leaf['entries'][0]['row'] = 9
            with self.assertRaisesRegex(experiment.catalog.DecodeError, 'locator'):
                experiment.observe(b'', arm)
            leaf['entries'] = []
            with self.assertRaisesRegex(experiment.catalog.DecodeError, 'non-null'):
                experiment.observe(b'', arm)

    def test_actual_rejections_and_null_omissions_answer_without_guessed_policy(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'observe', side_effect=self.decoded):
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'answered')
            entry = next(e for e in result['replicas'] if e['arm'] == 'null-required')
            entry['operations'][0]['native_codes'] = [8888]
            self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')

    def test_complete_rows_traversal_metadata_and_no_retry_failure_gates(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'observe', side_effect=self.decoded):
            outbox = Path(temporary)
            plan, original = self.fixture(outbox)
            for change in ('rows', 'traversal', 'schema', 'partial', 'changed'):
                result = copy.deepcopy(original)
                entry = result['replicas'][0]
                if change == 'rows': entry['snapshot']['rows'].pop()
                elif change == 'traversal': entry['snapshot']['traversal'].reverse()
                elif change == 'schema': entry['snapshot']['indexes'][0]['ignore_nulls'] = True
                elif change == 'partial': result['replicas'].pop(); result['error'] = 'post-mutation failure'
                else: entry['before'] = {'size': 0, 'sha256': 'changed'}
                with self.subTest(change=change):
                    self.assertEqual(experiment.build_report(result, outbox, plan)['outcome'], 'no_outcome')

    def test_retained_identity_and_both_pin_paths(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'observe', side_effect=self.decoded):
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            (outbox / result['replicas'][0]['file']).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                experiment.build_report(result, outbox, plan)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            producer = root / 'producer.py'; producer.write_text('changed')
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'experiment_id': 'scalar-index-layout', 'replicas': 3,
                                        'inputs': {'producer.py': '0' * 64}}))
            with patch.object(experiment, 'ROOT', root), patch.object(experiment, 'PLAN', plan):
                for operation in (experiment.preflight, lambda: experiment.analyze(root)):
                    with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                        operation()
            self.assertFalse((root / 'report.json').exists())


if __name__ == '__main__':
    unittest.main()
