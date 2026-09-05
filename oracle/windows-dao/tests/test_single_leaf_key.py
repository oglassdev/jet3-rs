import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import single_leaf_key as a

def snapshot(arm,role):
    rows=copy.deepcopy(arm['rows'])
    if role!='original':
        for row in rows:
            if row[0]==arm['selected']:row[0]=arm['replacement']
    if role.endswith('-next'):rows.append(arm['follow'])
    fields=[dict(name='Id',type=4,size=4,attributes=1),dict(name='Value',type=4,size=4,attributes=1),dict(name='Payload',type=10,size=8,attributes=2)]
    index=dict(name='ByKey',primary=arm['primary'],unique=True,required=arm['primary'],foreign=False,ignore_nulls=False,fields=[dict(name='Id',attributes=int(arm['descending']))])
    return dict(version='3.0',queries=[],relations=[],tables=['Items','MSysACEs','MSysObjects','MSysQueries','MSysRelationships'],user_tables=[dict(name='Items',attributes=0,fields=fields,indexes=[index],rows=rows)],traversal=sorted(rows,key=lambda r:r[0],reverse=arm['descending']),seek=[dict(query=q,row=next((r for r in rows if r[0]==q),None)) for q in arm['queries']])

class KeyTests(unittest.TestCase):
    def test_complete_pairs_and_semantic_identity_failure_gates(self):
        plan=json.loads(a.PLAN.read_text());plan['images']={}
        result=dict(document_type='dao_single_leaf_key_result',plan_sha256=a.identity(a.PLAN)['sha256'],source_revision=plan['source_revision'],error=None,retention_failures=[],mutation_started=True,environment=dict(process_bits=32,provider='DAO.DBEngine.36'),pairs=[])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            for arm in plan['arms']:
                for replica in range(1,4):
                    pair=dict(arm=arm['name'],replica=replica,captures={},operations={'control':dict(status='complete',duplicate=None)})
                    for role in ['original','candidate','control','candidate-next','control-next']:
                        path=out/f"{arm['name']}-r{replica}-{role}.mdb";path.write_bytes(role.encode());ident=a.identity(path)
                        if role in ('original','candidate'):plan['images'][arm['name']+'-'+role+'.mdb']=ident
                        pair['captures'][role]=dict(file=path.name,status='pass',error=None,before=ident,after=ident,snapshot=snapshot(arm,role))
                        if role.endswith('-next'):pair['operations'][role]=dict(status='complete',duplicate=dict(accepted=False,error=dict(hresult=-2147467259),numbers=[3022]))
                    result['pairs'].append(pair)
            def classify():
                with patch.object(a,'patch_check',return_value={}):return a.build_report(result,out,plan)['outcome']
            self.assertEqual(classify(),'observed_accepted')
            result['pairs'][0]['captures']['candidate']['snapshot']['seek'][0]['row']=None
            self.assertEqual(classify(),'no_outcome')
            result['pairs'][0]['captures']['candidate']['snapshot']=snapshot(plan['arms'][0],'candidate')
            result['pairs'][0]['operations']['candidate-next']['duplicate']['accepted']=True
            self.assertEqual(classify(),'no_outcome');result['pairs'][0]['operations']['candidate-next']['duplicate']['accepted']=False
            result['source_revision']='wrong';self.assertEqual(classify(),'no_outcome');result['source_revision']=plan['source_revision']
            result['pairs'][0]['captures']['original']['after']={'size':0,'sha256':'wrong'};self.assertEqual(classify(),'no_outcome')
            result['pairs'].pop();self.assertEqual(classify(),'no_outcome')

    def test_request_rows_direction_boundaries_and_dense_limit(self):
        plan=json.loads(a.PLAN.read_text())
        self.assertEqual([len(arm['rows']) for arm in plan['arms']],[3,3,200])
        self.assertEqual(a.catalog.MAX_ROWS_PER_PAGE,256)
        values=[-2147483648,-1,0,1,2147483647]
        self.assertEqual(sorted(values,key=lambda v:a.key(v,False)),values)
        self.assertEqual(sorted(values,key=lambda v:a.key(v,True)),list(reversed(values)))
        self.assertEqual(a.key(-2147483648,False).hex(),'7f00000000')
        self.assertEqual(a.key(2147483647,True).hex(),'8000000000')
        for arm in plan['arms']:
            for role in ('original','candidate','candidate-next'):a.expected(snapshot(arm,role),arm,role)

    def test_input_pin_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime';runtime.write_bytes(b'original');plan=root/'plan';plan.write_text(json.dumps(dict(inputs={'runtime':a.identity(runtime)['sha256']})))
            with patch.object(a,'ROOT',root),patch.object(a,'PLAN',plan):
                a.verify_inputs();runtime.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'Input pin mismatch'):a.verify_inputs()

if __name__=='__main__':unittest.main()
