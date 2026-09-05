import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import memo_candidate as a

class MemoTests(unittest.TestCase):
    def test_complete_pairs_and_failure_gates(self):
        plan=json.loads(a.PLAN.read_text());plan['images']={}
        result=dict(document_type='dao_memo_candidate_result',plan_sha256=a.identity(a.PLAN)['sha256'],source_revision=plan['source_revision'],error=None,retention_failures=[],mutation_started=True,environment=dict(process_bits=32,provider='DAO.DBEngine.36'),pairs=[])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            for arm in plan['arms']:
                for replica in range(1,4):
                    operations={role+'-'+p['name']:dict(status='complete',row=p['row']) for role in ['candidate','control'] for p in plan['continuations']};pair=dict(arm=arm['name'],replica=replica,operations=operations,captures={});result['pairs'].append(pair)
                    for role in ['candidate','control',*operations]:
                        path=out/f"{arm['name']}-r{replica}-{role}.mdb";path.write_bytes(role.encode());identity=a.identity(path)
                        if role=='candidate':plan['images'][arm['name']+'.mdb']=identity
                        rows=a.expected_rows(arm,role,plan);fields=[dict(name='Id',type=4,size=4,attributes=1,allow_zero_length=None,properties=[]),dict(name=arm['memo'],type=12,size=0,attributes=2,allow_zero_length=True,properties=[])]
                        snapshot=dict(version='3.0',tables=sorted(['MSysACEs','MSysObjects','MSysQueries','MSysRelationships',arm['table']]),relations=[],queries=[],indexes=[],attributes=0,fields=fields,rows=[dict(id=i,payload=v,is_null=v is None,field_size=0 if v is None else len(v)*2) for i,v in rows])
                        pair['captures'][role]=dict(file=path.name,status='pass',error=None,before=identity,after=identity,snapshot=snapshot)
            with patch.object(a,'raw_check',return_value={}):
                self.assertEqual(a.build_report(result,out,plan)['outcome'],'observed_accepted')
                for defect in ['source','operation','empty','option','identity']:
                    bad=copy.deepcopy(result);pair=bad['pairs'][0]
                    if defect=='source':bad['source_revision']='wrong'
                    elif defect=='operation':pair['operations'].pop('candidate-empty-next')
                    elif defect=='empty':pair['captures']['candidate']['snapshot']['rows'][1].update(payload=None,is_null=True)
                    elif defect=='option':pair['captures']['candidate']['snapshot']['fields'][1]['allow_zero_length']=False
                    else:pair['captures']['candidate']['after']['size']+=1
                    self.assertEqual(a.build_report(bad,out,plan)['outcome'],'no_outcome')

    def test_route_and_input_pin(self):
        plan=json.loads(a.PLAN.read_text());self.assertIn(a.SCRIPT,plan['inputs']);self.assertTrue((a.ROOT/a.SCRIPT).is_file())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').write_text('old');path=root/'plan';path.write_text(json.dumps(dict(inputs={'runtime':a.identity(root/'runtime')['sha256']})))
            with patch.object(a,'ROOT',root),patch.object(a,'PLAN',path):
                a.verify_inputs();(root/'runtime').write_text('changed')
                with self.assertRaisesRegex(ValueError,'pin'):a.verify_inputs()

if __name__=='__main__':unittest.main()
