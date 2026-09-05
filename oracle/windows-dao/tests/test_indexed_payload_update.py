import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import indexed_payload_update as m


def snapshot(plan,arm,updated=False):
    tables=[]
    for spec in plan['tables']:
        index=arm['index'];indexes=[]
        if spec['name']==arm['table']:
            indexes=[dict(name='ByKey',primary=index['primary'],unique=index['unique'],required=index['primary'],foreign=False,ignore_nulls=False,fields=[dict(name=k['name'],attributes=int(k['descending'])) for k in index['fields']])]
        tables.append(dict(name=spec['name'],attributes=0,fields=[dict(f,attributes=1) for f in plan['fields']],indexes=indexes,rows=m.wanted_rows(plan,arm,updated) if indexes else spec['rows']))
    columns=[next(i for i,f in enumerate(plan['fields']) if f['name']==k['name']) for k in arm['index']['fields']]
    rows=m.wanted_rows(plan,arm,updated)
    traversal=sorted(rows,key=lambda r:tuple(r[i]*(-1 if k['descending'] else 1) for i,k in zip(columns,arm['index']['fields'])))
    seeks=[]
    for q in arm['queries']:
        matches=[r for r in rows if [r[i] for i in columns]==q];seeks.append(dict(query=q,row=matches[-1] if matches else None))
    return dict(version='3.0',relations=[],queries=[dict(name=plan['query']['name'],sql=plan['query']['sql'],type=0)],user_tables=tables,index_observations=dict(traversal=traversal,seek=seeks))


class IndexedPayloadTests(unittest.TestCase):
    def test_complete_index_semantics_and_missing_duplicate_rows(self):
        plan=json.loads(m.PLAN.read_text())
        for arm in plan['arms']:
            for updated in (False,True):
                value=snapshot(plan,arm,updated);self.assertTrue(m.requested(value,plan,arm,updated))
                for part in ('traversal','seek'):
                    bad=copy.deepcopy(value);bad['index_observations'][part].pop();self.assertFalse(m.requested(bad,plan,arm,updated))
                bad=copy.deepcopy(value);bad['index_observations']['traversal'].reverse();self.assertFalse(m.requested(bad,plan,arm,updated))
                bad=copy.deepcopy(value);bad['index_observations']['seek'][0]['row'][2]=999;self.assertFalse(m.requested(bad,plan,arm,updated))
                bad=copy.deepcopy(value);bad['user_tables'][0]['indexes'][0]['fields'][0]['attributes']^=1;self.assertFalse(m.requested(bad,plan,arm,updated))

    def test_full_matrix_and_no_outcome(self):
        plan=json.loads(m.PLAN.read_text())
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);phases={n:dict(document_type='dao_indexed_payload_update_phase',phase=n,plan_sha256=m.digest(m.PLAN),error=None,retention_failures=[],mutation_started=n=='create',environment=dict(process_bits=32,provider='DAO.DBEngine.36'),observations=[]) for n in ('create','observe')}
            result=dict(document_type='dao_indexed_payload_update_result',producer_os='posix',source_revision=plan['source_revision'],plan_sha256=m.digest(m.PLAN),phase='complete',error=None,updates=[],phases={})
            for arm in plan['arms']:
                for r in range(1,4):
                    ids={}
                    for role in ('original','updated'):
                        p=root/f"{arm['name']}-r{r}-{role}.mdb";p.write_bytes(role.encode());ids[role]=m.identity(p)
                        obs=dict(arm=arm['name'],replica=r,role=role,observation=dict(file=p.name,before=ids[role],after=ids[role],status='pass',error=None,snapshot=snapshot(plan,arm,role=='updated')))
                        phases['observe']['observations'].append(obs)
                        if role=='original':phases['create']['observations'].append(copy.deepcopy(obs))
                    result['updates'].append(dict(arm=arm['name'],replica=r,original_before=ids['original'],original_after=ids['original'],updated=ids['updated'],locator={}))
            def classify():
                for n,p in phases.items():
                    path=root/(n+'.json');path.write_text(json.dumps(p));result['phases'][n]=m.identity(path)
                with patch.object(m,'patch_check',return_value={}):return m.build_report(result,root,plan)['outcome']
            self.assertEqual(classify(),'observed_accepted')
            phases['observe']['observations'][0]['observation']['snapshot']['index_observations']['seek'].pop();self.assertEqual(classify(),'no_outcome')
            result['error']='failed';self.assertEqual(classify(),'no_outcome')

    def test_dispatch_and_analysis_input_pins(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);script=root/'producer';script.write_text('original');p=root/'plan';p.write_text(json.dumps(dict(inputs={'producer':m.digest(script)})))
            with patch.object(m,'ROOT',root),patch.object(m,'PLAN',p):
                m.verify_inputs();script.write_text('changed')
                with self.assertRaisesRegex(ValueError,'Input pin'):m.verify_inputs()
                with self.assertRaisesRegex(ValueError,'Input pin'):m.analyze(root)

if __name__=='__main__':unittest.main()
