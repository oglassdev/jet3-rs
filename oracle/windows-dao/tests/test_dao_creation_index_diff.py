import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dao_creation_index_diff as expansion
import dao_write_diff as historical


def snapshot(case):
    value = expansion.original.common.minimal_snapshot('rust')
    value['scenario_id'] = case['id']
    value['tables'], value['relationships'] = expansion.original.expected_schema(case)
    return expansion.original.common.canonicalize_snapshot(value)


def observations(value, case):
    result = []
    for table in value['tables']:
        for index in table['indexes']:
            rows = sorted([r['values'] for r in table['rows']], key=lambda r: expansion.directed(r, index))
            by_key = {expansion.query_values(r, index): r for r in rows}
            result.append(dict(table=table['name'], index=index['name'], rows=rows,
                seeks=[dict(query=list(q), row=by_key[q]) for q in sorted(expansion.queries(rows, index, case))]))
    return result


class CreationIndexTests(unittest.TestCase):
    def test_deterministic_recipes_and_separate_historical_runtime(self):
        cases = expansion.inventory()['scenarios']
        self.assertEqual([len(c['tables'][0]['rows']) for c in cases], [27801, 126, 201])
        self.assertEqual(len(historical.inventory()['scenarios']), 12)
        self.assertEqual([t['depth'] for c in cases for t in c['trees']], [3, 2, 2, 2, 2])
        for case in cases:
            value = snapshot(case)
            expansion.original.assert_recipe(value, case)
            actual = observations(value, case)
            expansion.assert_indexes(actual, value)
            if case['recipe'] == 'long-depth-three': self.assertEqual(len(actual[0]['seeks']), 9)
            if case['recipe'] == 'nullable-numeric': self.assertEqual(len(actual[0]['seeks']), 120)

    def test_complete_traversal_seek_and_unique_index_inventory(self):
        for case in expansion.inventory()['scenarios'][1:]:
            value = snapshot(case); actual = observations(value, case)
            for change in ('missing_row', 'reversed', 'missing_seek', 'wrong_seek', 'duplicate_index'):
                broken = copy.deepcopy(actual)
                if change == 'missing_row': broken[0]['rows'].pop()
                elif change == 'reversed': broken[0]['rows'].reverse()
                elif change == 'missing_seek': broken[0]['seeks'].pop()
                elif change == 'wrong_seek': broken[0]['seeks'][0]['row'] = {}
                else: broken.append(broken[0])
                with self.subTest(case=case['id'], change=change), self.assertRaises(expansion.fail):
                    expansion.assert_indexes(broken, value)
            # Record ordering is irrelevant; duplicate-key Seek may select any complete matching row.
            expansion.assert_indexes(list(reversed(actual)), value)
            for table in value['tables']:
                for index, obs in zip(table['indexes'], actual):
                    for seek in obs['seeks']:
                        seek['row'] = next(r for r in obs['rows'] if expansion.query_values(r, index) == tuple(seek['query']))
            expansion.assert_indexes(actual, value)

    def test_complete_evaluator_and_retained_environment_layout_identity_gates(self):
        write = expansion.original
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary); cases = expansion.inventory()['scenarios']
            emit = write.common.write_canonical
            emit(out / 'environment.json', dict(document_type='dao_environment', protocol_version='1.2.0', status='ready',
                host=dict(process_architecture='x86'), accepted_provider=dict(prog_id='DAO.DBEngine.36', database_version='dbVersion30')))
            prepared = dict(producer_os='Linux', source_revision='test', inventory_sha256=expansion.digest(expansion.INVENTORY), scenarios=[])
            manifest = dict(source_revision='test', inventory_sha256=prepared['inventory_sha256'], environment_sha256=expansion.digest(out / 'environment.json'), scenarios=[])
            for case in cases:
                root = out / case['id']; (root / 'rust').mkdir(parents=True)
                (root / 'database.mdb').write_bytes(b'synthetic identity')
                sha = expansion.digest(root / 'database.mdb'); value = snapshot(case)
                value['database_sha256'] = sha; value['producer']['source_revision'] = 'test'
                emit(root / 'rust/snapshot.json', value)
                dao = copy.deepcopy(value); dao['producer']['kind'] = 'dao'; emit(root / 'dao-snapshot.raw.json', dao)
                emit(root / 'dao-indexes.raw.json', observations(value, case))
                emit(root / 'layout.json', dict(scenario_id=case['id'], trees=case['trees']))
                emit(root / 'rust/coverage.json', dict(document_type='coverage_receipt', protocol_version='1.2.0', scenario_id=case['id'],
                    database_sha256=sha, producer=value['producer'], outcome='success', error_class=None,
                    branches=sorted(case['required_branches']), scenarios=[dict(id=c['id'], missing_branches=[], forbidden_observed=[], outcome_matches=True, satisfied=True) for c in cases]))
                prepared['scenarios'].append(dict(scenario_id=case['id'], status='prepared', database_sha256=sha, layout_sha256=expansion.digest(root / 'layout.json')))
                manifest['scenarios'].append(dict(scenario_id=case['id'], status='pass', error=None, before=sha, after=sha))
            emit(out / 'preparation.json', prepared); emit(out / 'dao-manifest.raw.json', manifest)
            emit(out / 'reader.json', dict(source_revision='test', reader_os='Windows'))
            expansion.evaluate(out); self.assertEqual(expansion.load(out / 'report.json')['outcome'], 'matched')
            for path in [out / 'environment.json', out / cases[0]['id'] / 'layout.json', out / cases[1]['id'] / 'dao-indexes.raw.json']:
                saved = path.read_bytes(); path.unlink()
                with self.assertRaises(Exception): expansion.evaluate(out)
                self.assertEqual(expansion.load(out / 'report.json')['outcome'], 'no_outcome'); path.write_bytes(saved)
            environment = expansion.load(out / 'environment.json'); environment['host']['process_architecture'] = 'x64'
            emit(out / 'environment.json', environment)
            manifest['environment_sha256'] = expansion.digest(out / 'environment.json'); emit(out / 'dao-manifest.raw.json', manifest)
            with self.assertRaisesRegex(ValueError, 'provider'): expansion.evaluate(out)


if __name__ == '__main__': unittest.main()
