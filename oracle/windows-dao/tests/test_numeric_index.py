import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numeric_index as a

def snapshot(arm):
    rows=copy.deepcopy(arm['rows']);included=[r for r in rows if a.included(r,arm)]
    return dict(version='3.0',tables=['MSysACEs','MSysObjects','MSysQueries','MSysRelationships','Rows'],relations=[],queries=[],attributes=0,
        fields=[dict(f,attributes=1,required=False,default_value='') for f in arm['fields']],
        indexes=[dict(name='ByKey',primary=False,unique=True,required=arm['required'],ignore_nulls=arm['ignore'],foreign=False,fields=[dict(name=arm['fields'][i]['name'],attributes=int(d)) for i,d in enumerate(arm['directions'])])],
        rows=rows,traversal=sorted(included,key=lambda r:a.key(r,arm)),seek=[dict(query=q,row=next((r for r in included if r[:len(q)]==q),None)) for q in arm['queries']])

class NumericTests(unittest.TestCase):
    def test_complete_matrix_and_failure_gates(self):
        plan=json.loads(a.PLAN.read_text());plan['images']={}
        result=dict(document_type='dao_numeric_index_result',plan_sha256=a.identity(a.PLAN)['sha256'],source_revision=plan['source_revision'],environment=dict(process_bits=32,provider='DAO.DBEngine.36'),mutation_started=True,error=None,retention_failures=[],pairs=[])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            for arm in plan['arms']:
                for replica in range(1,4):
                    pair=dict(arm=arm['name'],replica=replica,captures={},probes={})
                    for role in ('candidate','control'):
                        for probe in [None,*arm['probes']]:
                            name=role if probe is None else role+'-'+probe['name'];path=out/f"{arm['name']}-r{replica}-{name}.mdb";path.write_bytes(name.encode());ident=a.identity(path)
                            if name=='candidate':plan['images'][arm['name']+'.mdb']=ident
                            pair['captures'][name]=dict(file=path.name,before=ident,after=ident,status='pass',error=None,snapshot=snapshot(arm))
                            if probe:pair['probes'][name]=dict(accepted=False,error=dict(message='rejected'),numbers=[probe['number']])
                    result['pairs'].append(pair)
            def classify():
                with patch.object(a,'raw_check',return_value={}):return a.build_report(result,out,plan)['outcome']
            self.assertEqual(classify(),'observed_accepted')
            baseline=copy.deepcopy(result)
            result['source_revision']='wrong';self.assertEqual(classify(),'no_outcome');result=copy.deepcopy(baseline)
            result['pairs'][0]['captures']['candidate']['snapshot']['seek'][0]['row']=None;self.assertEqual(classify(),'no_outcome');result=copy.deepcopy(baseline)
            result['pairs'][0]['probes']['candidate-duplicate']['accepted']=True;self.assertEqual(classify(),'no_outcome');result=copy.deepcopy(baseline)
            result['pairs'][0]['captures']['candidate']['after']['size']+=1;self.assertEqual(classify(),'no_outcome');result=copy.deepcopy(baseline)
            result['pairs'].pop();self.assertEqual(classify(),'no_outcome')

    def test_exact_bits_null_policies_and_equal_key_ties(self):
        plan=json.loads(a.PLAN.read_text());self.assertEqual(len(plan['arms']),8)
        for arm in plan['arms']:a.expected(snapshot(arm),arm)
        arm=next(x for x in plan['arms'] if x['name']=='mixed-include');s=snapshot(arm);s['traversal'].reverse();s['traversal'].sort(key=lambda r:a.key(r,arm))
        self.assertEqual(a.expected(s,arm),a.expected(snapshot(arm),arm))
        s['traversal'].pop()
        with self.assertRaisesRegex(ValueError,'Complete traversal'):a.expected(s,arm)
        for typ,width in ((6,4),(7,8)):
            field=dict(type=typ,size=width)
            with self.assertRaisesRegex(ValueError,'Excluded'):a.component((1<<(width*8-1)).to_bytes(width,'little').hex(),field,False)
        self.assertEqual(a.component(None,dict(type=5,size=8),True),b'\xff')
        with self.assertRaisesRegex(ValueError,'Boolean null'):a.component(None,dict(type=1,size=1),False)

    def test_input_pin_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime';runtime.write_bytes(b'original');plan=root/'plan';plan.write_text(json.dumps(dict(inputs={'runtime':a.identity(runtime)['sha256']})))
            with patch.object(a,'ROOT',root),patch.object(a,'PLAN',plan):
                a.verify_inputs();runtime.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'Input pin mismatch'):a.verify_inputs()

if __name__=='__main__':unittest.main()
