import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import hosted_write_reanalysis as secondary
from test_dao_write_diff import requested_snapshot


class HostedWriteReanalysisTests(unittest.TestCase):
    def fixture(self):
        scenario = next(s for s in secondary.original.inventory()['scenarios'] if s['id'] == 'DAO-WRITE-RELATIONSHIP')
        snapshot = requested_snapshot(scenario)
        observations = []
        for table in snapshot['tables']:
            for index in table['indexes']:
                field = index['fields'][0]['name']
                rows = sorted([row['values'] for row in table['rows']], key=lambda row: row[field]['value'])
                queries = sorted({row[field]['value'] for row in rows})
                observations.append({'table': table['name'], 'index': index['name'], 'rows': rows,
                                     'seeks': [{'query': [key], 'row': next(row for row in rows if row[field]['value'] == key)} for key in queries]})
        return snapshot, observations

    def test_complete_records_match_by_unique_identity_in_either_order(self):
        snapshot, observations = self.fixture()
        secondary.assert_indexes(observations, snapshot)
        original = copy.deepcopy(observations)
        secondary.assert_indexes(list(reversed(observations)), snapshot)
        self.assertEqual(observations, original)
        with self.assertRaisesRegex(secondary.original.ValidationError, 'Incomplete index'):
            secondary.ORIGINAL_ASSERT_INDEXES(list(reversed(observations)), snapshot)

    def test_no_missing_duplicate_wrong_payload_or_seek_is_relaxed(self):
        snapshot, observations = self.fixture()
        for change in ('missing', 'duplicate', 'wrong_name', 'row', 'seek', 'seek_payload'):
            broken = copy.deepcopy(observations)
            if change == 'missing': broken.pop()
            elif change == 'duplicate': broken.append(copy.deepcopy(broken[0]))
            elif change == 'wrong_name': broken[0]['index'] = 'Other'
            elif change == 'row': broken[0]['rows'].pop()
            elif change == 'seek': broken[0]['seeks'].pop()
            else: broken[0]['seeks'][0]['row'] = {}
            with self.subTest(change=change), self.assertRaises((secondary.original.ValidationError, KeyError)):
                secondary.assert_indexes(broken, snapshot)

    def test_pin_drift_and_retained_output_containment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); source = root / 'source'; source.mkdir()
            runtime = root / 'runtime.py'; runtime.write_text('changed')
            plan = root / 'plan.json'; plan.write_text(json.dumps({'inputs': {'runtime.py': '0' * 64}}))
            with patch.object(secondary, 'ROOT', root), patch.object(secondary, 'PLAN', plan):
                with self.assertRaisesRegex(ValueError, 'input pin mismatch'):
                    secondary.verify(source)
            with patch.object(secondary, 'verify', return_value={}), patch.object(secondary, 'build_report') as report:
                for output in [source / 'report.json', source / 'nested' / 'report.json']:
                    with self.assertRaisesRegex(ValueError, 'outside'):
                        secondary.analyze(source, output)
                report.assert_not_called()
            self.assertEqual(list(source.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
