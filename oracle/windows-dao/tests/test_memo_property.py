import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import memo_property as a

class PropertyTests(unittest.TestCase):
    def test_complete_matrix_binding_and_question_variability(self):
        plan=json.loads(a.PLAN.read_text())
        result=dict(document_type='dao_memo_property_result',plan_sha256=a.identity(a.PLAN)['sha256'],error=None,retention_failures=[],mutation_started=True,environment=dict(process_bits=32,provider='DAO.DBEngine.36'),cases=[])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            for arm in plan['arms']:
                for replica in range(1,4):
                    case=dict(arm=arm['name'],replica=replica,captures=[],operations=[]);result['cases'].append(case)
                    for c in arm['checkpoints']:
                        if c['target'] is not None:case['operations'].append(dict(checkpoint=c['name'],column=arm['columns'][c['target']],value=c['values'][c['target']],status='complete'))
                        path=out/f"{arm['name']}-r{replica}-{c['name']}.mdb";path.write_bytes(bytes(c['values']))
                        fields=[dict(name='Id',type=4,size=4,allow_zero_length=None,properties=[])]+[dict(name=n,type=12,size=0,allow_zero_length=v,properties=[]) for n,v in zip(arm['columns'],c['values'])]
                        snapshot=dict(version='3.0',tables=sorted(['MSysACEs','MSysObjects','MSysQueries','MSysRelationships',arm['table']]),relations=[],queries=[],indexes=[],attributes=0,row_count=0,fields=fields)
                        case['captures'].append(dict(checkpoint=c['name'],capture=dict(file=path.name,status='pass',error=None,before=a.identity(path),after=a.identity(path),snapshot=snapshot)))
            with patch.object(a,'raw',side_effect=lambda data,arm:dict(columns=[],payload_hex=data.hex())):
                self.assertEqual(a.build_report(result,out,plan)['outcome'],'answered')
                bad=copy.deepcopy(result);bad['cases'][0]['operations'].pop();self.assertEqual(a.build_report(bad,out,plan)['outcome'],'no_outcome')
                bad=copy.deepcopy(result);bad['cases'][0]['captures'][1]['capture']['snapshot']['fields'][1]['allow_zero_length']=False;self.assertEqual(a.build_report(bad,out,plan)['outcome'],'no_outcome')
                item=result['cases'][1]['captures'][1]['capture'];path=out/item['file'];path.write_bytes(b'x');item['before']=item['after']=a.identity(path)
                self.assertEqual(a.build_report(result,out,plan)['outcome'],'no_outcome')

    def test_changes_retain_growth_and_exact_offsets(self):
        self.assertEqual(a.changed(b'abc',b'aXcd'),[dict(offset=1,before=98,after=88),dict(offset=3,before=None,after=100)])

    def test_input_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').write_text('old');plan=root/'plan';plan.write_text(json.dumps(dict(inputs={'runtime':a.identity(root/'runtime')['sha256']})))
            with patch.object(a,'ROOT',root),patch.object(a,'PLAN',plan):
                a.verify_inputs();(root/'runtime').write_text('changed')
                with self.assertRaisesRegex(ValueError,'pin'):a.verify_inputs()

if __name__=='__main__':unittest.main()
