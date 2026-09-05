import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('autoincrement_layout', SCRIPTS / 'autoincrement_layout.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class AutoIncrementLayoutTests(unittest.TestCase):
    def fixture(self, outbox):
        result = {'document_type': 'dao_autoincrement_layout_result', 'development_only': True,
                  'plan_sha256': experiment.digest(experiment.PLAN), 'mutation_started': True,
                  'environment': {'process_bits': 32, 'provider': 'DAO.DBEngine.36'}, 'checkpoints': [], 'error': None}
        for replica in range(1, 4):
            for arm in experiment.ARMS:
                for checkpoint in experiment.CHECKPOINTS:
                    tags = experiment.expected_tags(checkpoint)
                    # A stable next ID of300 must remain a valid observation, not be forced to257.
                    rows = [[300 if arm == 'auto' and tag == 257 else tag, tag] for tag in tags]
                    state = (256 if checkpoint == 'deleted' else rows[-1][0] if rows else 0) if arm == 'auto' else 0
                    header = len(rows).to_bytes(4, 'little') + state.to_bytes(4, 'little')
                    decoded = {'rows': [{'page': 20 + replica, 'row': n, 'values': row} for n, row in enumerate(rows)],
                               'row_count': len(rows), 'columns': [], 'candidate_state_i32': state,
                               'state_header_hex': header.hex(), 'pages': {'user_definition': {'page': 20, 'hex': header.hex()}}}
                    name = f'{arm}-r{replica}-{checkpoint}.mdb'
                    (outbox / name).write_text(json.dumps(decoded))
                    identity = experiment.identity(outbox / name)
                    snapshot = {'version': '3.0', 'table_attributes': 0,
                        'tables': ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Rows'],
                        'index_count': 0, 'fields': [
                            {'name': 'Id', 'type': 4, 'size': 4, 'attributes': 17 if arm == 'auto' else 1},
                            {'name': 'Tag', 'type': 4, 'size': 4, 'attributes': 1}], 'rows': rows}
                    result['checkpoints'].append({'replica': replica, 'arm': arm, 'checkpoint': checkpoint,
                        'file': name, 'before': identity, 'after': identity, 'status': 'pass', 'error': None, 'snapshot': snapshot})
        return result

    def test_counter_refutation_and_unexpected_next_id_are_reported_as_observations(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'observe', side_effect=json.loads):
            outbox = Path(temporary)
            result = self.fixture(outbox)
            report = experiment.build_report(result, outbox, {})
            self.assertEqual(report['outcome'], 'answered')
            self.assertTrue(all(item['next_after_delete'] == 300 for item in report['hypotheses']))
            self.assertTrue(all(item['state_matches_last_generated'] for item in report['hypotheses']))
            self.assertTrue(all(not item['generated_ids_match_tags'] for item in report['hypotheses']))
            def alternate(data):
                decoded = json.loads(data)
                decoded['candidate_state_i32'] = -99
                return decoded
            with patch.object(experiment, 'observe', side_effect=alternate):
                report = experiment.build_report(result, outbox, {})
                self.assertEqual(report['outcome'], 'answered')
                self.assertTrue(all(not item['state_matches_last_generated'] for item in report['hypotheses']))

    def test_raw_ranges_retain_exact_bytes_and_locations(self):
        before = {'pages': {'header': {'page': 0, 'hex': '000102'}}}
        after = {'pages': {'header': {'page': 0, 'hex': '00ff02'}}}
        self.assertEqual(experiment.changed_ranges(before, after), [{'role': 'header', 'page_before': 0,
            'page_after': 0, 'start': 1, 'end': 2, 'before_hex': '01', 'after_hex': 'ff'}])

    def test_incomplete_rows_identity_drift_and_modified_inputs_cannot_promote(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'observe', side_effect=json.loads):
            outbox = Path(temporary)
            result = self.fixture(outbox)
            result['checkpoints'][3]['snapshot']['rows'][0][0] = 999
            self.assertEqual(experiment.build_report(result, outbox, {})['outcome'], 'no_outcome')
            result = self.fixture(outbox)
            result['checkpoints'].pop()
            result['error'] = 'DAO failed after mutation'
            self.assertEqual(experiment.build_report(result, outbox, {})['outcome'], 'no_outcome')
            result = self.fixture(outbox)
            result['checkpoints'][0]['before'] = {'size': 0, 'sha256': 'changed'}
            self.assertEqual(experiment.build_report(result, outbox, {})['outcome'], 'no_outcome')
            (outbox / result['checkpoints'][0]['file']).write_text('changed')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                experiment.build_report(result, outbox, {})
        with patch.object(experiment, 'verify_inputs', side_effect=ValueError('Input pin mismatch')):
            with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                experiment.analyze(Path('unused'))


if __name__ == '__main__':
    unittest.main()
