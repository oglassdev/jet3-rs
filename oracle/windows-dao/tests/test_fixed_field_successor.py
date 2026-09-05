import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import fixed_field_successor as experiment


def snapshot(plan, arm, updated=False):
    tables=[]
    for table in experiment.arm_tables(plan,arm,updated):
        fields=[dict({k:f[k] for k in ('name','type','size')},attributes=1 if f['fixed'] else 2) for f in table['fields']]
        tables.append(dict(name=table['name'],attributes=0,indexes=[],fields=fields,rows=table['rows']))
    return dict(version='3.0',relations=[],queries=[dict(name=plan['query']['name'],sql=plan['query']['sql'],type=0)],user_tables=tables)


class FixedSuccessorTests(unittest.TestCase):
    def test_exact_typed_requests_and_negative_zero(self):
        plan=json.loads(experiment.PLAN.read_text())
        self.assertEqual(len(plan['arms']),10)
        for arm in plan['arms']:
            before=snapshot(plan,arm);after=snapshot(plan,arm,True)
            self.assertTrue(experiment.requested(before,plan,arm))
            self.assertTrue(experiment.requested(after,plan,arm,True))
            self.assertFalse(experiment.requested(before,plan,arm,True))
            wrong=copy.deepcopy(after);wrong['user_tables'][1]['rows'][0][2]='ff'
            self.assertFalse(experiment.requested(wrong,plan,arm,True))
        for name in ('single','double'):
            arm=next(a for a in plan['arms'] if a['name']==name)
            after=snapshot(plan,arm,True);after['user_tables'][0]['rows'][1][1]='00'*arm['field']['size']
            self.assertFalse(experiment.requested(after,plan,arm,True))

    def test_fixed_text_requires_saved_fixed_attributes(self):
        plan=json.loads(experiment.PLAN.read_text());arm=plan['arms'][-1]
        actual=snapshot(plan,arm);actual['user_tables'][0]['fields'][1]['attributes']=2
        self.assertFalse(experiment.requested(actual,plan,arm))

    def test_failure_result_and_input_pins(self):
        plan=json.loads(experiment.PLAN.read_text())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            result={'document_type':'dao_fixed_field_update_result','producer_os':'posix','source_revision':plan['source_revision'],
                    'plan_sha256':experiment.digest(experiment.PLAN),'phase':'create','error':{'endpoint':'assign','message':'failed'}}
            report=experiment.build_report(result,root,plan)
            self.assertEqual(report['outcome'],'no_outcome');self.assertEqual(report['observations'],[])
            script=root/'producer.py';script.write_text('original');p=root/'plan.json';p.write_text(json.dumps({'inputs':{'producer.py':experiment.digest(script)}}))
            with patch.object(experiment,'ROOT',root),patch.object(experiment,'PLAN',p):
                experiment.verify_inputs();script.write_text('changed')
                with self.assertRaisesRegex(ValueError,'Input pin'):experiment.verify_inputs()

    def test_complete_matrix_and_query_preservation_gate(self):
        plan=json.loads(experiment.PLAN.read_text())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); phases={name:{'document_type':'dao_fixed_field_update_phase','phase':name,'plan_sha256':experiment.digest(experiment.PLAN),
                'error':None,'mutation_started':name=='create','environment':{'process_bits':32,'provider':'DAO.DBEngine.36'},'observations':[]} for name in ('create','observe')}
            result={'document_type':'dao_fixed_field_update_result','producer_os':'posix','source_revision':plan['source_revision'],
                'plan_sha256':experiment.digest(experiment.PLAN),'phase':'complete','error':None,'updates':[],'phases':{}}
            for arm in plan['arms']:
                for replica in range(1,4):
                    ids={}
                    for role in ('original','updated'):
                        path=root/f"{arm['name']}-r{replica}-{role}.mdb";path.write_bytes(role.encode());ids[role]=experiment.identity(path)
                        obs={'arm':arm['name'],'replica':replica,'role':role,'observation':{'file':path.name,'before':ids[role],'after':ids[role],
                            'status':'pass','error':None,'snapshot':snapshot(plan,arm,role=='updated')}}
                        phases['observe']['observations'].append(obs)
                        if role=='original':phases['create']['observations'].append(copy.deepcopy(obs))
                    result['updates'].append({'arm':arm['name'],'replica':replica,'original_before':ids['original'],'original_after':ids['original'],'updated':ids['updated'],'locator':{}})
            def classify():
                for name,phase in phases.items():
                    path=root/(name+'.json');path.write_text(json.dumps(phase));result['phases'][name]=experiment.identity(path)
                with patch.object(experiment,'patch_check',return_value={}):return experiment.build_report(result,root,plan)
            self.assertEqual(classify()['outcome'],'observed_accepted')
            phases['observe']['observations'][1]['observation']['snapshot']['queries'][0]['sql']='SELECT 99;'
            self.assertEqual(classify()['outcome'],'no_outcome')


if __name__=='__main__':unittest.main()
