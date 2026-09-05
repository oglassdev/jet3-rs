import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import empty_long_values as e

def operations(reject=False):
    return [dict(id=i,state=state,accepted=not(reject and i==2),error=dict(endpoint='empty/update',type='COM',hresult=-1,numbers=[3315],stack='captured') if reject and i==2 else None) for i,state in [(1,'null'),(2,'empty'),(3,'one')]]
def snapshot(arm,reject=False,normalize=False):
    rows=[dict(id=1,is_null=True,payload=None,field_size=0),dict(id=3,is_null=False,payload='A' if arm['type']==12 else '41',field_size=2 if arm['type']==12 else 1)]
    if not reject:rows.insert(1,dict(id=2,is_null=normalize,payload=None if normalize else '',field_size=0))
    return dict(version='3.0',tables=['MSysACEs','MSysObjects','MSysQueries','MSysRelationships','Rows'],relations=[],queries=[],indexes=[],attributes=0,fields=[dict(name='Id',type=4,size=4,attributes=1,properties=[]),dict(name='Payload',type=arm['type'],size=0,attributes=2,allow_zero_length=arm['allow_zero_length'] if arm['type']==12 else None,properties=[])],rows=rows)

class EmptyTests(unittest.TestCase):
    def test_empty_negative_and_null_normalization_are_valid_observations(self):
        plan=json.loads(e.PLAN.read_text())
        for arm in plan['arms']:
            for rejected,normalized in [(False,False),(False,True),(True,False)]:e.snapshot(snapshot(arm,rejected,normalized),arm,e.operations(operations(rejected)))
        bad=operations(True);bad[1]['error']['numbers']=[]
        with self.assertRaisesRegex(ValueError,'Unexpected'):e.operations(bad)
        bad=operations(True);bad[1]['error']['endpoint']='empty/add_new'
        with self.assertRaisesRegex(ValueError,'Unexpected'):e.operations(bad)
        bad=operations();bad[2]['accepted']=False;bad[2]['error']={}
        with self.assertRaisesRegex(ValueError,'Unexpected'):e.operations(bad)

    def test_complete_classifier_and_preservation_failure_gates(self):
        plan=json.loads(e.PLAN.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);result=dict(document_type='dao_empty_long_values_result',plan_sha256=e.identity(e.PLAN)['sha256'],error=None,retention_failures=[],mutation_started=True,environment=dict(process_bits=32,provider='DAO.DBEngine.36'),cases=[])
            for arm in plan['arms']:
                for replica in range(1,4):
                    path=out/f"{arm['name']}-r{replica}.mdb";path.write_bytes(b'x');reject=arm['name']=='memo-default'
                    result['cases'].append(dict(arm=arm['name'],replica=replica,operations=operations(reject),capture=dict(file=path.name,status='pass',error=None,before=e.identity(path),after=e.identity(path),snapshot=snapshot(arm,reject))))
            with patch.object(e,'raw_observation',return_value={}):
                self.assertEqual(e.build_report(result,out,plan)['outcome'],'answered')
                bad=copy.deepcopy(result);bad['cases'].pop();self.assertEqual(e.build_report(bad,out,plan)['outcome'],'no_outcome')
                bad=copy.deepcopy(result);bad['cases'][1]['operations'][1]['error']['numbers']=[9999];self.assertEqual(e.build_report(bad,out,plan)['outcome'],'no_outcome')
                bad=copy.deepcopy(result);bad['error']='failed';self.assertEqual(e.build_report(bad,out,plan)['outcome'],'no_outcome')
                (out/'memo-default-r1.mdb').write_bytes(b'changed');self.assertEqual(e.build_report(result,out,plan)['outcome'],'no_outcome')

    def test_standalone_analysis_revalidates_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').write_bytes(b'changed');plan=root/'plan';plan.write_text(json.dumps(dict(inputs={'runtime':'0'*64})))
            with patch.object(e,'ROOT',root),patch.object(e,'PLAN',plan):
                with self.assertRaisesRegex(ValueError,'pin'):e.analyze(root)

if __name__=='__main__':unittest.main()
