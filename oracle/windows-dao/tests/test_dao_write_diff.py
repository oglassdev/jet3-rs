import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import types

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import dao_write_diff as write


def requested_snapshot(scenario):
    result = write.common.minimal_snapshot('rust')
    result['scenario_id'] = scenario['id']
    result['tables'], result['relationships'] = write.expected_schema(scenario)
    return write.common.canonicalize_snapshot(result)


class WriteTests(unittest.TestCase):
    def test_separate_inventory_does_not_weaken_read_contract(self):
        declared = write.inventory()
        self.assertEqual(len(declared['scenarios']), 12)
        self.assertTrue(all(s['operation']['mode'] == 'dao_open_rust' for s in declared['scenarios']))
        with self.assertRaises(write.ValidationError):
            write.protocol.validate_document(declared)
        wrong = copy.deepcopy(declared); wrong['scenarios'][0]['operation']['mode'] = 'rust_read_dao'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'inventory.json'; path.write_text(json.dumps(wrong))
            with self.assertRaises(write.ValidationError): write.inventory(path)

    def test_matching_producers_still_must_satisfy_the_requested_recipe(self):
        scenario = next(s for s in write.inventory()['scenarios'] if s['id'] == 'DAO-WRITE-INDEX-COMPOSITE')
        snapshot = requested_snapshot(scenario)
        write.assert_recipe(snapshot, scenario)
        snapshot['tables'][0]['rows'].pop()
        snapshot = write.common.canonicalize_snapshot(snapshot)
        with self.assertRaisesRegex(write.ValidationError, 'declared creation request'):
            write.assert_recipe(snapshot, scenario)

    def test_full_traversal_and_seek_reject_omissions_and_wrong_duplicate_rows(self):
        scenario = next(s for s in write.inventory()['scenarios'] if s['id'] == 'DAO-WRITE-INDEX-ORDINARY')
        snapshot = requested_snapshot(scenario); table = snapshot['tables'][0]
        rows = sorted([r['values'] for r in table['rows']], key=lambda r: r['A']['value'])
        seeks = [{'query': [n], 'row': next(row for row in rows if row['A']['value'] == n)} for n in (0, 1)]
        observation = [{'table': 'Keys', 'index': 'ByKey', 'rows': rows, 'seeks': seeks}]
        write.assert_indexes(observation, snapshot)
        broken = copy.deepcopy(observation); broken[0]['rows'].pop()
        with self.assertRaises(write.ValidationError): write.assert_indexes(broken, snapshot)
        broken = copy.deepcopy(observation); broken[0]['seeks'].pop()
        with self.assertRaises(write.ValidationError): write.assert_indexes(broken, snapshot)
        broken = copy.deepcopy(observation); broken[0]['seeks'][1]['row']['B']['value'] = 123
        with self.assertRaises(write.ValidationError): write.assert_indexes(broken, snapshot)

    def test_scalar_and_relationship_requests_preserve_protocol_shapes(self):
        for scenario in write.inventory()['scenarios']:
            snapshot = requested_snapshot(scenario)
            write.assert_recipe(snapshot, scenario)
        scalar = write.typed({'kind': 'Currency'}, -12345)
        self.assertEqual(scalar['value'], '-1.2345')
        self.assertEqual(write.typed({'kind': 'Boolean'}, None), {'kind': 'boolean', 'value': False})

    def test_complete_evaluation_retains_no_outcome_on_identity_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            scenarios = write.inventory()['scenarios']
            prepared = {'producer_os': 'Linux', 'source_revision': 'test', 'inventory_sha256': write.digest(write.INVENTORY), 'scenarios': []}
            manifest = {'source_revision': 'test', 'inventory_sha256': write.digest(write.INVENTORY), 'scenarios': []}
            for scenario in scenarios:
                root = out / scenario['id']; (root / 'rust').mkdir(parents=True)
                (root / 'database.mdb').write_bytes(b'synthetic test identity')
                identity = write.digest(root / 'database.mdb')
                snapshot = requested_snapshot(scenario); snapshot['database_sha256'] = identity; snapshot['producer']['source_revision'] = 'test'
                write.common.write_canonical(root / 'rust/snapshot.json', snapshot)
                dao = copy.deepcopy(snapshot); dao['producer']['kind'] = 'dao'
                write.common.write_canonical(root / 'dao-snapshot.raw.json', dao)
                receipt = {'document_type': 'coverage_receipt', 'protocol_version': '1.2.0', 'scenario_id': scenario['id'],
                    'database_sha256': identity, 'producer': snapshot['producer'], 'outcome': 'success', 'error_class': None,
                    'branches': ['open.header_page'], 'scenarios': [{'id': s['id'], 'missing_branches': [], 'forbidden_observed': [], 'outcome_matches': True, 'satisfied': True} for s in scenarios]}
                write.common.write_canonical(root / 'rust/coverage.json', receipt)
                observations = []
                for table in snapshot['tables']:
                    for index in table['indexes']:
                        key = lambda row: tuple(row[f['name']]['value'] for f in index['fields'])
                        directed = lambda row: tuple(v * (-1 if f['descending'] else 1) for v, f in zip(key(row), index['fields']))
                        rows = sorted([r['values'] for r in table['rows']], key=directed)
                        queries = sorted(set(map(key, rows)))
                        observations.append({'table': table['name'], 'index': index['name'], 'rows': rows,
                            'seeks': [{'query': list(q), 'row': next(row for row in rows if key(row) == q)} for q in queries]})
                write.common.write_canonical(root / 'dao-indexes.raw.json', observations)
                prepared['scenarios'].append({'scenario_id': scenario['id'], 'status': 'prepared', 'database_sha256': identity})
                manifest['scenarios'].append({'scenario_id': scenario['id'], 'status': 'pass', 'error': None, 'before': identity, 'after': identity})
            write.common.write_canonical(out / 'preparation.json', prepared)
            write.common.write_canonical(out / 'dao-manifest.raw.json', manifest)
            write.common.write_canonical(out / 'reader.json', {'source_revision': 'test', 'reader_os': 'Windows'})
            write.evaluate(out)
            self.assertEqual(write.load_json(out / 'report.json')['outcome'], 'matched')
            root = out / scenarios[0]['id']
            for name, field, value in [('rust/snapshot.json', 'scenario_id', 'DAO-WRITE-SCALARS'),
                    ('dao-snapshot.raw.json', 'scenario_id', 'DAO-WRITE-SCALARS'),
                    ('rust/snapshot.json', 'producer', {'kind': 'rust', 'source_revision': 'unrelated'}),
                    ('dao-snapshot.raw.json', 'producer', {'kind': 'dao', 'source_revision': 'unrelated'}),
                    ('rust/coverage.json', 'producer', {'kind': 'rust', 'source_revision': 'unrelated'})]:
                path = root / name; original = path.read_bytes(); document = json.loads(original)
                document[field] = value; write.common.write_canonical(path, document)
                with self.assertRaises(write.ValidationError): write.evaluate(out)
                path.write_bytes(original)
            reader = out / 'reader.json'; original = reader.read_bytes()
            for document in [{'source_revision': 'other', 'reader_os': 'Windows'}, {'source_revision': 'test', 'reader_os': 'Linux'}]:
                write.common.write_canonical(reader, document)
                with self.assertRaises(write.ValidationError): write.evaluate(out)
            reader.unlink()
            with self.assertRaises(write.ValidationError): write.evaluate(out)
            reader.write_bytes(original)
            (out / scenarios[0]['id'] / 'database.mdb').write_bytes(b'changed')
            with self.assertRaisesRegex(write.ValidationError, 'identity changed'): write.evaluate(out)
            self.assertEqual(write.load_json(out / 'report.json')['outcome'], 'no_outcome')

    def test_first_reader_cannot_change_the_generated_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / 'output'
            def run(command, **kwargs):
                if command[0] == 'generator':
                    Path(command[2]).write_bytes(b'generated')
                else:
                    Path(command[2]).write_bytes(b'reader mutation')
                return types.SimpleNamespace(returncode=0, stdout='', stderr='')
            with patch.object(write.subprocess, 'run', side_effect=run):
                with self.assertRaisesRegex(write.ValidationError, 'reader changed'):
                    write.prepare(out, Path('generator'), Path('reader'), 'test')
            entry = write.load_json(out / 'preparation.json')['scenarios'][0]
            self.assertEqual(entry['status'], 'failed')
            self.assertEqual(entry['database_sha256'], write.hashlib.sha256(b'generated').hexdigest())


if __name__ == '__main__': unittest.main()
