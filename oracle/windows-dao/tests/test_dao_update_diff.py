import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dao_update_diff as update


def synthetic_capture(out):
    scenarios = update.inventory()['scenarios']; revision = 'test'
    prepared = {'source_revision': revision, 'producer_os': 'Linux', 'inventory_sha256': update.write.digest(update.INVENTORY), 'scenarios': []}
    manifest = {'source_revision': revision, 'inventory_sha256': prepared['inventory_sha256'], 'scenarios': []}
    for scenario in scenarios:
        root = out / scenario['id']; receipt = dict(scenario['request'], scenario_id=scenario['id'], offset=8, length=4, page=0, slot=0, preserved=True)
        for role in update.ROLES:
            directory = root / role; (directory / 'rust').mkdir(parents=True)
            (directory / 'database.mdb').write_bytes(b'prefix!!' + scenario['request'][role].to_bytes(4, 'little', signed=True) + b'unrelated')
            identity = update.write.digest(directory / 'database.mdb'); receipt[role + '_sha256'] = identity
            value = update.common.minimal_snapshot('rust'); value['scenario_id'] = scenario['id']; value['producer']['source_revision'] = revision; value['database_sha256'] = identity
            value['tables'], value['relationships'] = update.write.expected_schema(update.recipe(scenario, role))
            value = update.common.canonicalize_snapshot(value)
            update.common.write_canonical(directory / 'rust/snapshot.json', value)
            dao = copy.deepcopy(value); dao['producer']['kind'] = 'dao'
            update.common.write_canonical(directory / 'dao-snapshot.raw.json', dao)
            coverage = {'document_type': 'coverage_receipt', 'protocol_version': '1.2.0', 'scenario_id': scenario['id'], 'database_sha256': identity,
                'producer': value['producer'], 'outcome': 'success', 'error_class': None, 'branches': ['open.header_page'],
                'scenarios': [{'id': s['id'], 'missing_branches': [], 'forbidden_observed': [], 'outcome_matches': True, 'satisfied': True} for s in scenarios]}
            update.common.write_canonical(directory / 'rust/coverage.json', coverage)
            manifest['scenarios'].append({'scenario_id': scenario['id'], 'role': role, 'before': identity, 'after': identity, 'status': 'pass', 'error': None})
        prepared['scenarios'].append({'scenario_id': scenario['id'], 'status': 'prepared', 'verification': receipt})
    update.common.write_canonical(out / 'preparation.json', prepared)
    update.common.write_canonical(out / 'reader.json', {'source_revision': revision, 'reader_os': 'Windows', 'verifications': [s['verification'] for s in prepared['scenarios']]})
    update.common.write_canonical(out / 'dao-manifest.raw.json', manifest)


class UpdateTests(unittest.TestCase):
    def test_complete_before_after_match_and_partial_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp); synthetic_capture(out); update.evaluate(out)
            report = update.load_json(out / 'report.json')
            self.assertEqual(report['outcome'], 'matched'); self.assertEqual(len(report['comparisons']), 6)
            manifest = update.load_json(out / 'dao-manifest.raw.json'); manifest['scenarios'].pop()
            update.common.write_canonical(out / 'dao-manifest.raw.json', manifest)
            with self.assertRaisesRegex(update.ValidationError, 'Incomplete DAO'): update.evaluate(out)
            self.assertEqual(update.load_json(out / 'report.json')['outcome'], 'no_outcome')

    def test_matching_producers_cannot_hide_wrong_change_or_unrelated_payload(self):
        scenario = update.inventory()['scenarios'][1]
        value = update.common.minimal_snapshot('rust'); value['scenario_id'] = scenario['id']
        value['tables'], value['relationships'] = update.write.expected_schema(update.recipe(scenario, 'after'))
        value = update.common.canonicalize_snapshot(value)
        update.write.assert_recipe(value, update.recipe(scenario, 'after'))
        for table, field in [('Items', 'Value'), ('Notes', 'Body')]:
            bad = copy.deepcopy(value); target = next(t for t in bad['tables'] if t['name'] == table)
            next(r for r in target['rows'] if r['values'][field]['kind'] != 'null')['values'][field] = {'kind': 'null', 'value': None}
            with self.assertRaises(update.ValidationError): update.write.assert_recipe(bad, update.recipe(scenario, 'after'))

    def test_request_binding_and_all_other_bytes_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp); synthetic_capture(out); scenario = update.inventory()['scenarios'][0]
            root = out / scenario['id']; receipt = update.load_json(out / 'preparation.json')['scenarios'][0]['verification']
            for field, wrong in [('row_index', 1), ('column', 'Value'), ('offset', 9)]:
                broken = dict(receipt); broken[field] = wrong
                with self.assertRaises(update.ValidationError): update.check_preservation(root, scenario, broken)
            path = root / 'after/database.mdb'; data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
            receipt['after_sha256'] = update.write.digest(path)
            with self.assertRaisesRegex(update.ValidationError, 'unrelated byte'): update.check_preservation(root, scenario, receipt)

    def test_windows_independent_verification_and_sources_required(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp); synthetic_capture(out)
            for name, field, wrong in [('reader.json', 'reader_os', 'Linux'), ('dao-manifest.raw.json', 'source_revision', 'other')]:
                path = out / name; original = path.read_bytes(); value = update.load_json(path); value[field] = wrong
                update.common.write_canonical(path, value)
                with self.assertRaises(update.ValidationError): update.evaluate(out)
                path.write_bytes(original)


if __name__ == '__main__': unittest.main()
