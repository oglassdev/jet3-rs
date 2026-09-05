"""Classify populated relationships and independent writable-copy integrity probes."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('relationship_rows', Path(__file__).parents[1] / 'scripts/relationship_rows.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RelationshipRowsReportTests(unittest.TestCase):
    def classify(self, change=lambda result: None, tamper=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'producer.ps1'
            source.write_text('pinned producer')
            plan = {'inputs': {str(source): MODULE.digest(source)}, 'schema': {'version': '3.0'}, 'candidates': {}}
            for arm in MODULE.ARMS:
                path = root / f'{arm}.mdb'
                path.write_bytes(arm.encode())
                plan['candidates'][arm] = MODULE.identity(path)
            plan_path = root / 'plan.json'
            plan_path.write_text(json.dumps(plan))
            result = {'document_type': 'dao_relationship_rows_result', 'development_only': True,
                      'plan_sha256': MODULE.digest(plan_path), 'environment': {'process_bits': 32},
                      'mutation_started': True, 'error': None, 'replicas': [], 'probes': []}
            for arm in MODULE.ARMS:
                snapshot = MODULE.expected_snapshot(arm)
                for number in range(1, 4):
                    replica = {'arm': arm, 'replica': number}
                    for role in ('candidate', 'control'):
                        path = root / f'{arm}-{role}-r{number}.mdb'
                        path.write_bytes(arm.encode())
                        replica[role] = {'before': MODULE.identity(path), 'after': MODULE.identity(path),
                                         'status': 'pass', 'endpoint': 'complete', 'error': None,
                                         'snapshot': copy.deepcopy(snapshot)}
                    result['replicas'].append(replica)
            for replica in range(1, 4):
                for probe in MODULE.PROBES:
                    item = {'replica': replica, 'probe': probe}
                    for role in ('candidate', 'control'):
                        path = root / f'populated-{role}-{probe}-r{replica}.mdb'
                        path.write_bytes(probe.encode())
                        operation = {'status': 'updated' if probe == 'valid_child' else 'rejected',
                                     'endpoint': 'update', 'native_codes': [] if probe == 'valid_child' else [3201 if probe == 'orphan_child' else 3022],
                                     'hresult': None if probe == 'valid_child' else -1, 'error': None}
                        item[role] = {'before': plan['candidates']['populated'], 'operation': operation,
                                      'observation': {'before': MODULE.identity(path), 'after': MODULE.identity(path),
                                                      'status': 'pass', 'endpoint': 'complete', 'error': None,
                                                      'snapshot': MODULE.expected_snapshot('populated', probe)}}
                    result['probes'].append(item)
            change(result)
            (root / 'result.json').write_text(json.dumps(result))
            if tamper == 'input':
                source.write_text('diagnostic edit')
            elif tamper == 'probe_retained':
                (root / 'populated-candidate-valid_child-r1.mdb').write_bytes(b'changed probe')
            elif tamper == 'retained':
                (root / 'populated-candidate-r1.mdb').write_bytes(b'changed')
            with patch.object(MODULE, 'PLAN', plan_path), patch('builtins.print'):
                MODULE.analyze(root)
            return json.loads((root / 'report.json').read_text())

    def test_complete_acceptance_and_all_three_integrity_endpoints(self):
        report = self.classify()
        self.assertEqual(report['outcomes']['populated'], 'observed_accepted')
        self.assertTrue(all(value == 'observed_accepted' for value in report['integrity'].values()))

    def test_duplicate_child_seek_and_tie_order_are_not_overconstrained(self):
        def change(result):
            child = result['replicas'][0]['candidate']['snapshot']['user_tables'][1]
            child['rows'].reverse()
            child['traversal'].sort(key=lambda row: (row['account4'], row['label3']), reverse=False)
            for seek in child['seek']:
                seek['row'] = next(row for row in child['rows'] if row['account4'] == seek['query'])
        self.assertEqual(self.classify(change)['outcomes']['populated'], 'observed_accepted')

    def test_relation_foreign_index_and_full_payload_mismatches_are_negative(self):
        for field in ('relation', 'index', 'payload', 'traversal'):
            def change(result):
                for replica in result['replicas']:
                    snapshot = replica['candidate']['snapshot']
                    if field == 'relation':
                        snapshot['relations'][0]['attributes'] = 256
                    elif field == 'index':
                        snapshot['user_tables'][1]['indexes'][0]['foreign'] = False
                    elif field == 'payload':
                        snapshot['user_tables'][1]['rows'][0]['label3'] = 'wrong'
                    else:
                        snapshot['user_tables'][1]['traversal'].pop()
            self.assertEqual(self.classify(change)['outcomes']['populated'], 'not_observed_accepted')

    def test_probe_requires_expected_operation_and_exact_post_state(self):
        for field in ('operation', 'state'):
            def change(result):
                for item in result['probes']:
                    if item['probe'] == 'orphan_child':
                        if field == 'operation':
                            item['candidate']['operation']['status'] = 'updated'
                        else:
                            item['candidate']['observation']['snapshot']['user_tables'][1]['rows'].pop()
            self.assertEqual(self.classify(change)['integrity']['orphan_child'], 'not_observed_accepted')

    def test_native_codes_are_matched_to_controls_not_guessed(self):
        def different_native_code(result):
            for item in result['probes']:
                if item['probe'] == 'orphan_child':
                    for role in ('candidate', 'control'):
                        item[role]['operation']['native_codes'] = [9999]
        self.assertEqual(self.classify(different_native_code)['integrity']['orphan_child'], 'observed_accepted')
        def mismatch(result):
            for item in result['probes']:
                if item['probe'] == 'orphan_child':
                    item['candidate']['operation']['native_codes'] = [9999]
        self.assertEqual(self.classify(mismatch)['integrity']['orphan_child'], 'not_observed_accepted')

    def test_disagreement_and_control_failure_have_no_outcome(self):
        def disagreement(result):
            result['probes'][0]['candidate']['observation']['snapshot']['user_tables'][1]['rows'].pop()
        self.assertEqual(self.classify(disagreement)['integrity']['valid_child'], 'no_outcome')
        def control_failure(result):
            result['probes'][0]['control']['operation']['status'] = 'rejected'
        self.assertTrue(all(value == 'no_outcome' for value in self.classify(control_failure)['integrity'].values()))

    def test_incomplete_scientific_job_has_no_outcome(self):
        def change(result):
            result['probes'].pop()
            result['error'] = 'Unexpected DAO failure'
        report = self.classify(change)
        self.assertEqual(report['outcomes']['populated'], 'no_outcome')
        self.assertTrue(all(value == 'no_outcome' for value in report['integrity'].values()))

    def test_modified_inputs_and_retained_files_are_rejected(self):
        for tamper in ('input', 'retained', 'probe_retained'):
            with self.subTest(tamper=tamper), self.assertRaises(ValueError):
                self.classify(tamper=tamper)


if __name__ == '__main__':
    unittest.main()
