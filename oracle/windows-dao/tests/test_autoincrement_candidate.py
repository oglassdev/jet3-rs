import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import autoincrement_candidate as experiment


class CandidateTests(unittest.TestCase):
    def fixture(self, outbox):
        plan = {'arms': {arm: [{'name': 'Rows', 'count': 1, 'indexed': False}] for arm in experiment.ARMS}, 'candidates': {}}
        result = {'document_type': 'dao_autoincrement_candidate_result', 'development_only': True,
                  'plan_sha256': experiment.digest(experiment.PLAN), 'environment': {'process_bits': 32, 'provider': 'DAO.DBEngine.36'},
                  'mutation_started': True, 'error': None, 'replicas': []}
        for arm in experiment.ARMS:
            for replica in range(1, 4):
                item = {'arm': arm, 'replica': replica}
                for role in ('control', 'candidate'):
                    stages = {}
                    for stage in ('initial', 'post'):
                        path = outbox / f'{arm}-{role}-r{replica}-{stage}.mdb'
                        path.write_bytes(stage.encode())
                        identity = experiment.identity(path)
                        stages[stage] = {'before': identity, 'after': identity, 'status': 'pass', 'endpoint': 'complete', 'error': None}
                    stages['copy_before'] = stages['initial']['after']
                    stages['insert'] = {'status': 'pass', 'error': None, 'ids': [2]}
                    item[role] = stages
                plan['candidates'][arm] = item['candidate']['initial']['before']
                result['replicas'].append(item)
        return plan, result

    def test_complete_controls_identity_and_insert_gates(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'semantics', return_value=(True, {'complete': True})):
            outbox = Path(temporary)
            plan, result = self.fixture(outbox)
            self.assertEqual(set(experiment.build_report(result, outbox, plan)['outcomes'].values()), {'observed_accepted'})
            altered = copy.deepcopy(result)
            for pair in altered['replicas']:
                pair['candidate']['insert']['ids'] = [1]
            self.assertEqual(set(experiment.build_report(altered, outbox, plan)['outcomes'].values()), {'not_observed_accepted'})
            for field, value in [('error', 'post-mutation failure'), ('mutation_started', False)]:
                altered = copy.deepcopy(result); altered[field] = value
                self.assertEqual(set(experiment.build_report(altered, outbox, plan)['outcomes'].values()), {'no_outcome'})
            altered = copy.deepcopy(result); altered['replicas'].pop()
            self.assertEqual(set(experiment.build_report(altered, outbox, plan)['outcomes'].values()), {'no_outcome'})
            altered = copy.deepcopy(result); altered['replicas'][0]['control']['post']['before'] = {}
            self.assertFalse(experiment.build_report(altered, outbox, plan)['unchanged'])
            (outbox / 'unindexed-candidate-r1-initial.mdb').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                experiment.build_report(result, outbox, plan)

    def test_rows_and_persisted_state_must_both_match(self):
        table = {'name': 'Rows', 'count': 1, 'indexed': False}
        snapshot = {'version': '3.0', 'tables': ['MSysACEs', 'MSysObjects', 'MSysQueries', 'MSysRelationships', 'Rows'],
            'user_tables': [{'name': 'Rows', 'attributes': 0, 'fields': [
                {'name': 'Id', 'type': 4, 'size': 4, 'attributes': 17}, {'name': 'Tag', 'type': 4, 'size': 4, 'attributes': 1}],
                'indexes': [], 'rows': [[1, 1]], 'traversal': [], 'seek': []}]}
        observation = {'status': 'pass', 'endpoint': 'complete', 'error': None, 'snapshot': snapshot}
        raw = bytearray(2048); raw[16] = 1
        decoder = types.SimpleNamespace(DecodeError=ValueError,
            analyze_checkpoint=lambda data: {'tables': {20: {'name': 'Rows', 'definition': {'root': 20, 'row_count': 1}, 'data_pages': [23]}}},
            _page=lambda *args: raw, _table_rows=lambda *args: [{'values': [1, 1]}])
        with tempfile.TemporaryDirectory() as temporary, patch.object(experiment, 'decoder', return_value=decoder):
            path = Path(temporary) / 'test.mdb'; path.write_bytes(b'fixture')
            self.assertTrue(experiment.semantics(observation, [table], False, path)[0])
            raw[16] = 0
            self.assertFalse(experiment.semantics(observation, [table], False, path)[0])
            raw[16] = 1; snapshot['user_tables'][0]['rows'] = [[2, 1]]
            self.assertFalse(experiment.semantics(observation, [table], False, path)[0])

    def test_analysis_checks_pins_and_private_decoder_has_planned_bound(self):
        self.assertEqual(experiment.decoder().MAX_ROWS_PER_PAGE, 256)
        with patch.object(experiment, 'verify_inputs', side_effect=ValueError('Input pin mismatch')):
            with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                experiment.analyze(Path('unused'))


if __name__ == '__main__':
    unittest.main()
