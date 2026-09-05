import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import row_delete_release as experiment


class CandidateTests(unittest.TestCase):
    def setUp(self): self.plan=json.loads(experiment.PLAN.read_text())

    def snapshot(self,arm,role):
        tables=[]
        for spec in arm['tables']:
            rows=copy.deepcopy(spec['rows'])
            if spec['name']==arm['table']:
                if role!='original':rows=[r for r in rows if r[0]!=arm['selected_id']]
                if role.endswith('-next'):rows.append(self.plan['insert'])
            tables.append(dict(name=spec['name'],attributes=0,indexes=[],fields=[dict(f,attributes=2 if f['type']==10 else 1) for f in self.plan['fields']],rows=rows))
        return dict(version='3.0',relations=[],tables=sorted(['MSysACEs','MSysObjects','MSysQueries','MSysRelationships','Items','Later']),queries=[dict(name='KeepQuery',sql='retained SQL',type=0)],user_tables=tables)

    def test_complete_classifier_and_failure_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);identity=experiment.identity
            result=dict(document_type='dao_row_delete_release_result',producer_os='posix',source_revision=self.plan['source_revision'],plan_sha256=identity(experiment.PLAN)['sha256'],phase='complete',error=None,phases={},updates=[])
            for phase in ('create','observe'):
                document=dict(document_type='dao_row_delete_release_phase',phase=phase,plan_sha256=result['plan_sha256'],error=None,retention_failures=[],mutation_started=True,environment=dict(process_bits=32,provider='DAO.DBEngine.36'),observations=[],operations=[])
                for arm in self.plan['arms']:
                    for replica in range(1,4):
                        roles=['original','control'] if phase=='create' else ['original','control','rust','control-next','rust-next']
                        for role in roles:
                            name=f"{arm['name']}-r{replica}-{role}.mdb";file=root/name;file.write_bytes(b'x')
                            document['observations'].append(dict(arm=arm['name'],replica=replica,role=role,observation=dict(file=name,before=identity(file),after=identity(file),status='pass',error=None,snapshot=self.snapshot(arm,role))))
                        for role in (['control'] if phase=='create' else ['control-next','rust-next']):document['operations'].append(dict(arm=arm['name'],replica=replica,role=role,result=dict(operation='delete' if phase=='create' else 'insert',status='complete',selected_id=arm['selected_id'])))
                        if phase=='create':result['updates'].append(dict(arm=arm['name'],replica=replica,original_before=identity(file),original_after=identity(file),rust=identity(file),locator={}))
                file=root/(phase+'.json');file.write_text(json.dumps(document));result['phases'][phase]=identity(file)
            with patch.object(experiment,'patch_check',return_value={'maps':{}}),patch.object(experiment,'shape',return_value=(None,None,{})):
                self.assertEqual(experiment.build_report(result,root,self.plan)['outcome'],'observed_accepted')
                for key,value in [('producer_os','nt'),('source_revision','wrong'),('error','failed'),('phase','create')]:
                    bad=dict(result,**{key:value});self.assertEqual(experiment.build_report(bad,root,self.plan)['outcome'],'no_outcome')
                bad=copy.deepcopy(result);bad['updates'].pop();self.assertEqual(experiment.build_report(bad,root,self.plan)['outcome'],'no_outcome')
                (root/'first-sole-r1-rust.mdb').write_bytes(b'changed');self.assertEqual(experiment.build_report(result,root,self.plan)['outcome'],'no_outcome')

    def test_exact_release_maps_payload_slack_and_page_zero(self):
        data=bytearray(24*2048);base=23*2048;root=20*2048
        data[base:base+4]=bytes.fromhex('0101ea07');data[base+8:base+12]=bytes.fromhex('0100f607');data[base+2038:base+2048]=bytes(range(10));data[root+12:root+16]=(1).to_bytes(4,'little')
        def store(page,values):
            pos=page*2048;data[pos]=1;data[pos+8:pos+10]=len(values).to_bytes(2,'little');end=2048
            for slot,value in enumerate(values):
                end-=len(value);data[pos+end:pos+end+len(value)]=value;data[pos+10+slot*2:pos+12+slot*2]=end.to_bytes(2,'little')
        empty=bytearray(133);member=bytearray(empty);member[7]=128
        store(1,[empty]);store(21,[member,member])
        locations=dict(owned=dict(page=21,row=0),available=dict(page=21,row=1));maps=dict(owned=[23],available=[23]);rows=[dict(page=23,row=0,values=[1])]
        after=bytearray(data);after[base]=9;after[base+2:base+4]=(2036).to_bytes(2,'little');after[base+10:base+12]=bytes.fromhex('00c8');after[root+12:root+16]=bytes(4)
        for location in locations.values():
            raw=data[location['page']*2048:(location['page']+1)*2048];entry=experiment.layout.catalog._row_directory(raw,location['page'])[location['row']];after[location['page']*2048+entry['start']+7]=0
        after[2048+2048-133+7]=128
        def shape(raw,_):return dict(root=20,row_count=1 if raw==data else 0,maps=locations),rows if raw==data else [],maps if raw==data else dict(owned=[],available=[])
        with patch.object(experiment,'shape',side_effect=shape):
            arm=dict(selected_id=1);receipt=dict(root=20,page=23,slot=0)
            report=experiment.patch_check(data,after,arm,receipt);self.assertEqual(report['row_count'],0)
            for offset in [1538,base+2040,base+100,base,root+12,2048+2048-133+7]:
                bad=bytearray(after);bad[offset]^=1
                with self.assertRaises(ValueError):experiment.patch_check(data,bad,arm,receipt)
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
