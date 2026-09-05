"""Classify exact Memo strings and OLE bytes independently of producer pass flags."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('long_value_rows', Path(__file__).parents[1] / 'scripts/long_value_rows.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LongValueRowsReportTests(unittest.TestCase):
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
            result = {'document_type': 'dao_long_value_rows_result', 'development_only': True,
                      'plan_sha256': MODULE.digest(plan_path), 'environment': {'process_bits': 32},
                      'mutation_started': True, 'error': None, 'replicas': []}
            for arm in MODULE.ARMS:
                rows = MODULE.expected_rows(arm)
                snapshot = {**MODULE.schema(plan, arm), 'rows': rows}
                for number in range(1, 4):
                    replica = {'arm': arm, 'replica': number}
                    for role in ('candidate', 'control'):
                        path = root / f'{arm}-{role}-r{number}.mdb'
                        path.write_bytes(arm.encode())
                        replica[role] = {'before': MODULE.identity(path), 'after': MODULE.identity(path),
                                         'status': 'pass', 'endpoint': 'complete', 'error': None,
                                         'snapshot': copy.deepcopy(snapshot)}
                    result['replicas'].append(replica)
            change(result)
            (root / 'result.json').write_text(json.dumps(result))
            if tamper == 'input':
                source.write_text('diagnostic edit')
            elif tamper == 'retained':
                (root / 'memo-candidate-r1.mdb').write_bytes(b'changed')
            with patch.object(MODULE, 'PLAN', plan_path), patch('builtins.print'):
                MODULE.analyze(root)
            return json.loads((root / 'report.json').read_text())['outcomes']

    def test_exact_payloads_allow_row_order_differences(self):
        def change(result):
            result['replicas'][0]['candidate']['snapshot']['rows'].reverse()
        self.assertTrue(all(value == 'observed_accepted' for value in self.classify(change).values()))

    def test_payload_and_null_mismatches_are_negative(self):
        for row, payload in [(0, ''), (8, 'wrong'), (9, '')]:
            def change(result):
                for replica in result['replicas'][3:]:
                    replica['candidate']['snapshot']['rows'][row]['payload'] = payload
            self.assertEqual(self.classify(change)['ole'], 'not_observed_accepted')

    def test_schema_mismatch_is_negative(self):
        def change(result):
            for replica in result['replicas'][:3]:
                replica['candidate']['snapshot']['fields'][1]['type'] = 10
        self.assertEqual(self.classify(change)['memo'], 'not_observed_accepted')

    def test_disagreement_control_failure_and_mutation_have_no_outcome(self):
        def disagreement(result):
            result['replicas'][0]['candidate']['snapshot']['rows'].pop()
        self.assertEqual(self.classify(disagreement)['memo'], 'no_outcome')
        def control_failure(result):
            result['replicas'][0]['control']['snapshot']['rows'] = []
        def changed_image(result):
            result['replicas'][0]['control']['before']['sha256'] = '0' * 64
        for change in (control_failure, changed_image):
            self.assertTrue(all(value == 'no_outcome' for value in self.classify(change).values()))

    def test_incomplete_scientific_job_has_no_outcome(self):
        def change(result):
            result['replicas'].pop()
            result['error'] = 'CreateDatabase failed'
        self.assertTrue(all(value == 'no_outcome' for value in self.classify(change).values()))

    def test_modified_inputs_and_retained_files_are_rejected(self):
        for tamper in ('input', 'retained'):
            with self.subTest(tamper=tamper), self.assertRaises(ValueError):
                self.classify(tamper=tamper)


if __name__ == '__main__':
    unittest.main()
