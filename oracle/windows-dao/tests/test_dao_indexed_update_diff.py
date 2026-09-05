import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dao_indexed_update_diff as a

class IndexedTests(unittest.TestCase):
    def test_inventory_recipe_and_routing(self):
        scenarios=a.inventory()['scenarios'];self.assertEqual(len(scenarios),13)
        for case in scenarios[9:]:
            before=a.recipe(case,'before')['tables'][0]['rows'];after=a.recipe(case,'after')['tables'][0]['rows']
            self.assertEqual(sum(x!=y for x,y in zip(before,after)),1)
        with patch.object(a.allocation.rows,'old_command') as run:
            a.command(Path('.'),'snapshot',['cli','snapshot','file'])
            self.assertEqual(run.call_args.args[2][-2:],['--inventory','indexed-update'])

    def test_complete_index_observations_and_removed_missing_probes(self):
        case=copy.deepcopy(a.inventory()['scenarios'][9]);cases={'scenarios':[case]}
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);(out/'preparation.json').write_text(json.dumps({'source_revision':'review'}))
            for role in a.base.ROLES:
                root=out/case['id']/role;(root/'rust').mkdir(parents=True);(root/'database.mdb').write_bytes(b'fixture')
                rows=[{'Id':{'value':r[0]},'Value':{'value':r[1]},'Payload':{'value':r[2]}} for r in a.recipe(case,role)['tables'][0]['rows']]
                index={'name':'ByKey','fields':[{'name':'Id','descending':False}]}
                snapshot={'tables':[{'name':'Items','indexes':[index],'rows':[{'values':r} for r in rows]}]}
                (root/'rust/snapshot.json').write_text(json.dumps(snapshot))
                obs={'table':'Items','index':'ByKey','rows':sorted(rows,key=lambda r:r['Id']['value']),'seeks':[{'query':[r['Id']['value']],'row':r} for r in rows]}
                extra=[{'query':q,'row':next((r for r in rows if r['Id']['value']==q),None)} for q in case['index_queries']]
                doc={'scenario_id':case['id'],'role':role,'source_revision':'review','database_sha256':a.base.write.digest(root/'database.mdb'),'observations':[obs],'extra_seeks':extra}
                (root/'dao-indexes.raw.json').write_text(json.dumps(doc))
            path=out/case['id']/'after/dao-indexes.raw.json';original=json.loads(path.read_text())
            with patch.object(a,'old_evaluate',return_value={}),patch.object(a,'inventory',return_value=cases):
                self.assertTrue(a.evaluate_checked(out)['index_observations_verified'])
                for change in ['source','missing','removed','traversal']:
                    doc=copy.deepcopy(original)
                    if change=='source':doc['source_revision']='other'
                    elif change=='missing':doc['extra_seeks'].pop()
                    elif change=='removed':next(p for p in doc['extra_seeks'] if p['query']==0)['row']=doc['observations'][0]['rows'][0]
                    else:doc['observations'][0]['rows'].pop()
                    path.write_text(json.dumps(doc))
                    with self.assertRaises(a.base.ValidationError):a.evaluate_checked(out)
                path.unlink()
                with self.assertRaises(a.base.ValidationError):a.evaluate_checked(out)

if __name__=='__main__':unittest.main()
