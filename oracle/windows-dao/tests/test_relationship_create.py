import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('relationship_create', SCRIPTS / 'relationship_create.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


class RelationshipCreateTests(unittest.TestCase):
    def test_leaf_reconstructs_shared_prefix_and_row_locators(self):
        page = bytearray(2048)
        page[0] = 4
        page[20] = 2
        page[248:250] = b'\x7f\x60'
        payloads = [b'\x61\x00\x00\x00\x17\x00', b'\x62\x00\x00\x00\x17\x01']
        end = 2
        for payload in payloads:
            page[248 + end:248 + end + len(payload)] = payload
            end += len(payload)
            page[22 + end // 8] |= 1 << (end % 8)
        page[2:4] = (1800 - end).to_bytes(2, 'little')
        entries = experiment.leaf_entries(bytes(page), 0)['entries']
        self.assertEqual(entries, [
            {'key_hex': '7f606100', 'row_page': 23, 'row': 0},
            {'key_hex': '7f606200', 'row_page': 23, 'row': 1},
        ])
        page[2] ^= 1
        with self.assertRaises(experiment.catalog.DecodeError):
            experiment.leaf_entries(bytes(page), 0)

    def test_standalone_analysis_rejects_modified_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'decoder.py'
            source.write_text('original')
            plan = root / 'plan.json'
            plan.write_text(json.dumps({'experiment_id': 'relationship-create', 'replicas': 3,
                                        'inputs': {'decoder.py': experiment.digest(source)}}))
            source.write_text('modified')
            with patch.object(experiment, 'ROOT', root), patch.object(experiment, 'PLAN', plan):
                with self.assertRaisesRegex(ValueError, 'Input pin mismatch: decoder.py'):
                    experiment.analyze(root)

    def result(self, outbox):
        checkpoints = []
        for replica in range(1, 4):
            relations = []
            for checkpoint in experiment.CHECKPOINTS:
                if checkpoint != 'base':
                    first = checkpoint == 'first'
                    relations = relations + [{'name': 'ParentChild' if first else 'AlternateLink',
                        'table': 'Parent', 'foreign_table': 'Child', 'attributes': 0,
                        'fields': [{'name': 'Id' if first else 'Alternate', 'foreign_name': 'ParentId' if first else 'Alternate'}]}]
                name = f'relationship-r{replica}-{checkpoint}.mdb'
                (outbox / name).write_bytes(b'synthetic')
                identity = experiment.identity(outbox / name)
                checkpoints.append({'replica': replica, 'checkpoint': checkpoint, 'file': name,
                                    'before': identity, 'after': identity, 'relations': relations})
        return {'document_type': 'dao_relationship_create_result', 'development_only': True,
                'plan_sha256': experiment.digest(experiment.PLAN), 'mutation_started': True,
                'environment': {'process_bits': 32, 'provider': 'DAO.DBEngine.36'},
                'checkpoints': checkpoints, 'error': None}

    def test_report_requires_complete_matching_replicas_and_retained_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            outbox = Path(temporary)
            result = self.result(outbox)
            with patch.object(experiment, 'observe', return_value={}), patch.object(experiment, 'correlate', return_value=None):
                self.assertEqual(experiment.build_report(result, outbox)['outcome'], 'answered')
                with patch.object(experiment, 'observe', side_effect=[{}] * 8 + [{'raw_selector': 99}]):
                    self.assertEqual(experiment.build_report(result, outbox)['outcome'], 'no_outcome')
                result['checkpoints'].pop()
                result['error'] = 'DAO mutation failed'
                self.assertEqual(experiment.build_report(result, outbox)['outcome'], 'no_outcome')
                (outbox / result['checkpoints'][0]['file']).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    experiment.build_report(result, outbox)


if __name__ == '__main__':
    unittest.main()
