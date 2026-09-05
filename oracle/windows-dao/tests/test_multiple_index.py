import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import multiple_index as a

def snapshot(arm):
    rows=copy.deepcopy(arm['rows'])
    return dict(version='3.0',tables=['MSysACEs','MSysObjects','MSysQueries','MSysRelationships','Rows'],relations=[],queries=[],attributes=0,
        fields=[dict(f,attributes=1,required=False,default_value='') for f in arm['fields']],
        indexes=[dict(name=i['name'],primary=i['primary'],unique=i['unique'],required=i['required'],ignore_nulls=i['ignore'],foreign=False,fields=[dict(name=arm['fields'][c]['name'],attributes=int(d)) for c,d in zip(i['columns'],i['directions'])]) for i in arm['indexes']],
        rows=rows,traversals={i['name']:sorted(rows,key=lambda r:a.key(r,arm,i)) for i in arm['indexes']},seeks={i['name']:[dict(query=q,row=next((r for r in rows if [r[c] for c in i['columns']]==q),None)) for q in i['queries']] for i in arm['indexes']})

class NumericTests(unittest.TestCase):
    def test_complete_matrix_and_failure_gates(self):
        plan=json.loads(a.PLAN.read_text());plan['images']={}
        result=dict(document_type='dao_multiple_index_result',plan_sha256=a.identity(a.PLAN)['sha256'],source_revision=plan['source_revision'],environment=dict(process_bits=32,provider='DAO.DBEngine.36'),mutation_started=True,error=None,retention_failures=[],pairs=[])
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
            result['pairs'][0]['captures']['candidate']['snapshot']['seeks']['ZPrimary'][0]['row']=None;self.assertEqual(classify(),'no_outcome');result=copy.deepcopy(baseline)
            result['pairs'][0]['probes']['candidate-duplicate-primary']['accepted']=True;self.assertEqual(classify(),'no_outcome');result=copy.deepcopy(baseline)
            result['pairs'][0]['captures']['candidate']['after']['size']+=1;self.assertEqual(classify(),'no_outcome');result=copy.deepcopy(baseline)
            result['pairs'].pop();self.assertEqual(classify(),'no_outcome')

    def test_complete_secondary_traversals_and_duplicate_seek_choices(self):
        plan=json.loads(a.PLAN.read_text());self.assertEqual(len(plan['arms']),2)
        for arm in plan['arms']:a.expected(snapshot(arm),arm)
        arm=plan['arms'][1];index=arm['indexes'][1];s=snapshot(arm)
        s['traversals']['AGroup'].reverse();s['traversals']['AGroup'].sort(key=lambda r:a.key(r,arm,index))
        for seek in s['seeks']['AGroup']:
            seek['row']=next(r for r in reversed(arm['rows']) if [r[c] for c in index['columns']]==seek['query'])
        self.assertEqual(a.expected(s,arm),a.expected(snapshot(arm),arm))
        s['traversals']['AGroup'].pop()
        with self.assertRaisesRegex(ValueError,'Complete traversal'):a.expected(s,arm)
        s=snapshot(arm);s['seeks'].pop('AGroup')
        with self.assertRaisesRegex(ValueError,'Index observation'):a.expected(s,arm)
        s=snapshot(arm);s['indexes'][1]['fields'][0]['attributes']=0
        with self.assertRaisesRegex(ValueError,'Requested index'):a.expected(s,arm)

    def test_input_pin_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);runtime=root/'runtime';runtime.write_bytes(b'original');plan=root/'plan';plan.write_text(json.dumps(dict(inputs={'runtime':a.identity(runtime)['sha256']})))
            with patch.object(a,'ROOT',root),patch.object(a,'PLAN',plan):
                a.verify_inputs();runtime.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'Input pin mismatch'):a.verify_inputs()

if __name__=='__main__':unittest.main()
