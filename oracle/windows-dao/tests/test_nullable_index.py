import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import nullable_index as experiment
import nullable_index_structure as structure


class NullableIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads(experiment.PLAN.read_text())

    def test_null_omission_direction_and_seek(self):
        for arm in experiment.ARMS:
            tables = self.plan['arms'][arm]
            snapshot = experiment.expected_snapshot(arm, tables)
            self.assertTrue(experiment.normalize(snapshot, tables)[0], arm)
            user = snapshot['user_tables'][1]
            if arm == 'ignore': self.assertEqual(len(user['traversal']), 8)
            if arm == 'composite-ignore':
                self.assertEqual(len(user['traversal']), 900)
                self.assertTrue(any(row[0] is None for row in user['traversal']))
                self.assertTrue(any(row[1] is None for row in user['traversal']))
            user['traversal'].pop()
            self.assertFalse(experiment.normalize(snapshot, tables)[0], arm)
        tables = self.plan['arms']['required']
        snapshot = experiment.expected_snapshot('required', tables)
        snapshot['user_tables'][1]['seek'][0]['row'] = [0, 0, 0]
        self.assertTrue(experiment.normalize(snapshot, tables)[0])
        snapshot['user_tables'][1]['seek'][0]['row'] = [0, 999, 999]
        self.assertFalse(experiment.normalize(snapshot, tables)[0])

    def test_null_encoding_payload_types_order_and_missing_seek(self):
        tables = self.plan['arms']['composite']
        table = tables[1]
        self.assertEqual(experiment.order_key([None, None, 0], table), bytes.fromhex('00ff'))
        self.assertEqual(experiment.order_key([None, 1, 0], table), bytes.fromhex('00807ffffffe'))
        self.assertEqual(experiment.order_key([1, None, 0], table), bytes.fromhex('7f80000001ff'))
        snapshot = experiment.expected_snapshot('composite', tables)
        snapshot['user_tables'][1]['traversal'].reverse()
        self.assertFalse(experiment.normalize(snapshot, tables)[0])
        snapshot = experiment.expected_snapshot('composite', tables)
        snapshot['user_tables'][1]['rows'][0][2] = True
        self.assertFalse(experiment.normalize(snapshot, tables)[0])
        snapshot = experiment.expected_snapshot('composite', tables)
        snapshot['user_tables'][1]['seek'][-1]['row'] = snapshot['user_tables'][1]['rows'][0]
        self.assertFalse(experiment.normalize(snapshot, tables)[0])

    def test_analysis_and_preflight_share_input_pin_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'input.py').write_text('changed')
            plan = root / 'plan.json'
            plan.write_text(json.dumps(dict(self.plan, inputs={'input.py': '0' * 64})))
            with patch.object(experiment, 'ROOT', root), patch.object(experiment, 'PLAN', plan):
                with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                    experiment.preflight(root)
                with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                    experiment.analyze(root)

    def fixture(self, root):
        original_rows = experiment.rows_for
        self.enterContext(patch.object(experiment, 'rows_for', side_effect=lambda arm, name: original_rows(arm, name)[:12]))
        plan = copy.deepcopy(self.plan)
        plan['inputs'] = {}
        result = dict(document_type='dao_nullable_index_result', development_only=True,
                      environment={'process_bits': 32, 'provider': 'DAO.DBEngine.36'},
                      mutation_started=True, error=None, replicas=[])
        for arm, tables in plan['arms'].items():
            plan['candidates'][arm] = {'size': 1, 'sha256': experiment.hashlib.sha256(b'x').hexdigest()}
            for replica in range(1, 4):
                pair = dict(arm=arm, replica=replica)
                for role in ('candidate', 'control'):
                    (root / f'{arm}-{role}-r{replica}.mdb').write_bytes(b'x')
                    pair[role] = dict(before=plan['candidates'][arm], after=plan['candidates'][arm], status='pass', endpoint='complete', error=None,
                                      snapshot=experiment.expected_snapshot(arm, tables))
                if arm in plan['rejection_probes']:
                    pair['probes'] = {}
                    for role in ('candidate', 'control'):
                        (root / f'{arm}-{role}-probe-r{replica}.mdb').write_bytes(b'x')
                        pair['probes'][role] = dict(original=plan['candidates'][arm], observation=copy.deepcopy(pair[role]),
                            operation=dict(status='rejected', endpoint='update', native_codes=[3022], hresult=-1, error='observed rejection'))
                result['replicas'].append(pair)
        plan_path = root / 'plan.json'
        plan_path.write_text(json.dumps(plan))
        result['plan_sha256'] = experiment.digest(plan_path)
        return plan_path, result

    def test_classifier_acceptance_failure_and_incomplete_control_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, result = self.fixture(root)
            def analyze():
                (root / 'result.json').write_text(json.dumps(result))
                with patch.object(experiment, 'PLAN', plan), patch.object(structure, 'observe', return_value=[]):
                    return experiment.analyze(root)['outcomes']
            self.assertEqual(set(analyze().values()), {'observed_accepted'})
            result['error'] = 'unexpected mutation failure'
            self.assertEqual(set(analyze().values()), {'no_outcome'})
            result['error'] = None
            result['replicas'][-1]['control']['status'] = 'fail'
            self.assertEqual(set(analyze().values()), {'no_outcome'})
            result['replicas'][-1]['control']['status'] = 'pass'
            for pair in result['replicas'][:3]:
                pair['candidate']['snapshot']['version'] = 'wrong'
            self.assertEqual(analyze()['unique'], 'not_observed_accepted')
            result['replicas'][0]['candidate']['snapshot']['version'] = '3.0'
            self.assertEqual(analyze()['unique'], 'no_outcome')
            (root / 'unique-candidate-r1.mdb').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'Retained identity mismatch'):
                analyze()

    def test_probe_control_error_identity_and_state_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan, result = self.fixture(root)
            def analyze():
                (root / 'result.json').write_text(json.dumps(result))
                with patch.object(experiment, 'PLAN', plan), patch.object(structure, 'observe', return_value=[]):
                    return experiment.analyze(root)['outcomes']
            original = copy.deepcopy(result)
            result['replicas'][0]['probes']['control']['operation']['status'] = 'updated'
            self.assertEqual(set(analyze().values()), {'no_outcome'})
            result = copy.deepcopy(original)
            for pair in result['replicas'][:3]:
                pair['probes']['candidate']['operation']['native_codes'] = [999]
            self.assertEqual(analyze()['unique'], 'not_observed_accepted')
            result = copy.deepcopy(original)
            result['replicas'][0]['probes']['candidate']['observation']['snapshot']['version'] = 'wrong'
            self.assertEqual(analyze()['unique'], 'no_outcome')


if __name__ == '__main__': unittest.main()
