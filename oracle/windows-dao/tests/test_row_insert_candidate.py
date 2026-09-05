import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import row_insert_candidate as experiment


class CandidateTests(unittest.TestCase):
    def setUp(self): self.plan=json.loads(experiment.PLAN.read_text())

    def snapshot(self,arm,role):
        tables=[]
        for spec in arm['tables']:
            rows=copy.deepcopy(spec['rows'])
            if spec['name']==arm['table']:
                if arm['delete_id'] is not None:rows=[r for r in rows if r[0]!=arm['delete_id']]
                if role!='original':rows.append(arm['insert'])
                if role.endswith('-next'):rows.append(self.plan['insert'])
            tables.append(dict(name=spec['name'],attributes=0,indexes=[],fields=[dict(f,attributes=2 if f['type']==10 else 1) for f in self.plan['fields']],rows=rows))
        return dict(version='3.0',relations=[],tables=sorted(['MSysACEs','MSysObjects','MSysQueries','MSysRelationships','Items','Later']),queries=[dict(name='KeepQuery',sql='retained SQL',type=0)],user_tables=tables)

    def test_complete_classifier_and_failure_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);identity=experiment.identity
            result=dict(document_type='dao_row_insert_candidate_result',producer_os='posix',source_revision=self.plan['source_revision'],plan_sha256=identity(experiment.PLAN)['sha256'],phase='complete',error=None,phases={},updates=[])
            for phase in ('create','observe'):
                document=dict(document_type='dao_row_insert_candidate_phase',phase=phase,plan_sha256=result['plan_sha256'],error=None,retention_failures=[],mutation_started=True,environment=dict(process_bits=32,provider='DAO.DBEngine.36'),observations=[],operations=[])
                for arm in self.plan['arms']:
                    for replica in range(1,4):
                        roles=['original','control'] if phase=='create' else ['original','control','rust','control-next','rust-next']
                        for role in roles:
                            name=f"{arm['name']}-r{replica}-{role}.mdb";file=root/name;file.write_bytes(b'x')
                            document['observations'].append(dict(arm=arm['name'],replica=replica,role=role,observation=dict(file=name,before=identity(file),after=identity(file),status='pass',error=None,snapshot=self.snapshot(arm,role))))
                        for role in (['control'] if phase=='create' else ['control-next','rust-next']):document['operations'].append(dict(arm=arm['name'],replica=replica,role=role,result=dict(operation='insert' if phase=='create' else 'continue',status='complete')))
                        if phase=='create':result['updates'].append(dict(arm=arm['name'],replica=replica,original_before=identity(file),original_after=identity(file),rust=identity(file),locator={}))
                file=root/(phase+'.json');file.write_text(json.dumps(document));result['phases'][phase]=identity(file)
            with patch.object(experiment,'patch_check',return_value={'maps':{}}),patch.object(experiment,'shape',return_value=(None,None,{})):
                self.assertEqual(experiment.build_report(result,root,self.plan)['outcome'],'observed_accepted')
                for key,value in [('producer_os','nt'),('source_revision','wrong'),('error','failed'),('phase','create')]:
                    bad=dict(result,**{key:value});self.assertEqual(experiment.build_report(bad,root,self.plan)['outcome'],'no_outcome')
                bad=copy.deepcopy(result);bad['updates'].pop();self.assertEqual(experiment.build_report(bad,root,self.plan)['outcome'],'no_outcome')
                (root/'first-page-r1-rust.mdb').write_bytes(b'changed');self.assertEqual(experiment.build_report(result,root,self.plan)['outcome'],'no_outcome')

    def test_exact_patch_and_tombstone_preservation(self):
        for deleted in [False,True]:
            data=bytearray(24*2048);base=23*2048;root=20*2048
            data[base]=1;data[base+8:base+10]=(2).to_bytes(2,'little')
            data[base+10:base+14]=bytes.fromhex('f607f6c7' if deleted else 'f607ec07')
            start=2038 if deleted else 2028;data[base+2:base+4]=(start-14).to_bytes(2,'little')
            rows=[dict(page=23,row=0,values=[1,2,'old'])]
            if not deleted:rows.append(dict(page=23,row=1,values=[2,3,'old']))
            data[root+12:root+16]=len(rows).to_bytes(4,'little');maps=dict(owned=[23],available=[23]);arm=dict(name='after-tail' if deleted else 'first-page',delete_id=2 if deleted else None,insert=[88,-8800,'new'])
            encoded=bytes.fromhex('0358000000a0ddffff6e65770c090107')
            after=bytearray(data);after[base+start-len(encoded):base+start]=encoded
            after[base+14:base+16]=(start-len(encoded)).to_bytes(2,'little');after[base+8:base+10]=(3).to_bytes(2,'little')
            after[base+2:base+4]=(start-14-len(encoded)-2).to_bytes(2,'little');after[root+12:root+16]=(len(rows)+1).to_bytes(4,'little')
            def shape(raw,_):return (dict(root=20,row_count=len(rows) if raw==data else len(rows)+1),rows if raw==data else rows+[dict(values=arm['insert'])],maps)
            with patch.object(experiment,'shape',side_effect=shape):
                receipt=dict(root=20,page=23,slot=2)
                self.assertTrue(experiment.patch_check(data,after,arm,receipt)['page0_unchanged'])
                for offset in (1538,base+2038,base+100,base+12):
                    bad=bytearray(after);bad[offset]^=1
                    with self.assertRaisesRegex(ValueError,'preservation'):experiment.patch_check(data,bad,arm,receipt)
                with self.assertRaises(ValueError):experiment.patch_check(data,after,arm,dict(receipt,slot=1))

    def test_schema_rows_query_and_pins(self):
        arm=self.plan['arms'][0];value=self.snapshot(arm,'rust-next');experiment.expected(value,arm,'rust-next',self.plan)
        value['user_tables'][0]['rows'].pop()
        with self.assertRaises(ValueError):experiment.expected(value,arm,'rust-next',self.plan)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'x').write_text('changed');plan=root/'plan.json';plan.write_text(json.dumps(dict(inputs={'x':'0'*64})))
            with patch.object(experiment,'ROOT',root),patch.object(experiment,'PLAN',plan):
                with self.assertRaisesRegex(ValueError,'Input pin'):experiment.verify_inputs()


if __name__=='__main__':unittest.main()
