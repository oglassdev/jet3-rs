import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import scalar_index_reanalysis as secondary
import test_scalar_index_layout as original_tests

ERROR = 'System.InvalidCastException: Specified cast is not valid.'


class ScalarReanalysisTests(unittest.TestCase):
    def test_only_exact_recorded_wrapper_and_scalar_arity_are_unwrapped(self):
        arm = {'fields': [{'name': 'A'}]}
        snapshot = {'rows': [{'tag': 1, 'values': {'value': [False], 'Count': 1}}], 'traversal': []}
        saved = copy.deepcopy(snapshot)
        self.assertEqual(secondary.normalize_snapshot(snapshot, arm)['rows'][0]['values'], [False])
        self.assertEqual(snapshot, saved)
        for values in ([False], {'value': [False]}, {'value': [False], 'Count': 2},
                       {'value': [False, True], 'Count': 1}, {'value': [False], 'Count': True}):
            broken = copy.deepcopy(snapshot); broken['rows'][0]['values'] = values
            with self.assertRaisesRegex(ValueError, 'wrapper'):
                secondary.normalize_snapshot(broken, arm)
        with self.assertRaisesRegex(ValueError, 'one scalar'):
            secondary.normalize_snapshot(snapshot, {'fields': [{}, {}]})

    def fixture(self, source):
        initial, result = original_tests.ScalarIndexLayoutTests().fixture(source)
        result['replicas'] = result['replicas'][:36]; result['error'] = ERROR
        for entry in result['replicas']:
            for group in ('rows', 'traversal'):
                for row in entry['snapshot'][group]:
                    row['values'] = {'value': row['values'], 'Count': 1}
        result['attempts'] = [{k: copy.deepcopy(entry[k]) for k in ('arm', 'replica', 'operations')} for entry in result['replicas']]
        result['attempts'].append({'arm': 'date-ascending', 'replica': 1, 'operations': []})
        (source / 'report.json').write_text(json.dumps({'outcome': 'no_outcome', 'observations': []}))
        plan = {'selected_arms': [a['name'] for a in initial['arms'][:12]], 'original_acquisition_error': ERROR,
                'retained': {'result.json': {}, 'report.json': {}, 'date-ascending-r1.mdb': {}}}
        return initial, result, plan

    def test_selected_answer_preserves_failed_full_run_and_original_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary); initial, result, plan = self.fixture(source)
            def analyze():
                (source / 'result.json').write_text(json.dumps(result))
                with patch.object(secondary, 'PLAN', source / 'report.json'), patch.object(secondary.original, 'observe', side_effect=original_tests.ScalarIndexLayoutTests.decoded):
                    return secondary.build_report(source, plan, initial)
            report = analyze()
            self.assertEqual(report['outcome'], 'answered')
            self.assertEqual(report['original_outcome'], 'no_outcome')
            self.assertEqual(report['original_acquisition_error'], ERROR)
            self.assertEqual((report['selected_captures'], report['original_planned_captures']), (36, 78))
            self.assertEqual(report['original_incomplete_attempt']['arm'], 'date-ascending')
            result['replicas'][0]['snapshot']['traversal'].reverse()
            self.assertEqual(analyze()['outcome'], 'no_outcome')
            result['attempts'][-1]['operations'] = [{}]
            with self.assertRaisesRegex(ValueError, 'incomplete Date'):
                analyze()

    def test_pin_drift_and_original_tree_preservation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'source'; source.mkdir()
            (root / 'runtime.py').write_text('changed')
            plan = root / 'plan.json'; plan.write_text(json.dumps({'inputs': {'runtime.py': '0' * 64}}))
            with patch.object(secondary, 'ROOT', root), patch.object(secondary, 'PLAN', plan):
                with self.assertRaisesRegex(ValueError, 'input pin mismatch'):
                    secondary.verify(source)
            with patch.object(secondary, 'verify', return_value=({}, {})), patch.object(secondary, 'build_report') as report:
                with self.assertRaisesRegex(ValueError, 'outside'):
                    secondary.analyze(source, source / 'nested' / 'report.json')
                report.assert_not_called()
            self.assertEqual(list(source.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
