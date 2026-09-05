import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import eof_insert_candidate as experiment


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
            result=dict(document_type='dao_eof_insert_candidate_result',producer_os='posix',source_revision=self.plan['source_revision'],plan_sha256=identity(experiment.PLAN)['sha256'],phase='complete',error=None,phases={},updates=[])
            for phase in ('create','observe'):
                document=dict(document_type='dao_eof_insert_candidate_phase',phase=phase,plan_sha256=result['plan_sha256'],error=None,retention_failures=[],mutation_started=True,environment=dict(process_bits=32,provider='DAO.DBEngine.36'),observations=[],operations=[])
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
                (root/'empty-table-r1-rust.mdb').write_bytes(b'changed');self.assertEqual(experiment.build_report(result,root,self.plan)['outcome'],'no_outcome')

    def test_exact_eof_maps_page_and_prefix_preservation(self):
        arm=copy.deepcopy(self.plan['arms'][0]);arm['insert']=[88,-8800,'new']
        before=bytearray(24*2048);locators=dict(owned=dict(page=21,row=0),available=dict(page=21,row=1))
        def store(page,records):
            base=page*2048;before[base]=1;before[base+8:base+10]=len(records).to_bytes(2,'little');end=2048
            for slot,raw in enumerate(records):
                end-=len(raw);before[base+end:base+end+len(raw)]=raw;before[base+10+2*slot:base+12+2*slot]=end.to_bytes(2,'little')
        zero=bytearray(133);free=bytearray(zero);free[8]=1
        store(1,[free]);store(21,[zero,zero]);after=bytearray(before)
        after[20*2048+12:20*2048+16]=(1).to_bytes(4,'little')
        for locator in locators.values():
            image=before[locator['page']*2048:(locator['page']+1)*2048];entry=experiment.layout.catalog._row_directory(image,locator['page'])[locator['row']]
            after[locator['page']*2048+entry['start']+8]|=1
        after[2048+2048-133+8]&=254
        raw=bytes.fromhex('0358000000a0ddffff6e65770c090107');page=bytearray(2048);page[:2]=bytes([1,1]);page[2:4]=(2036-len(raw)).to_bytes(2,'little');page[4:8]=(20).to_bytes(4,'little');page[8:10]=(1).to_bytes(2,'little');page[10:12]=(2048-len(raw)).to_bytes(2,'little');page[-len(raw):]=raw;after.extend(page)
        def shape(data,_):
            return (dict(root=20,row_count=0 if data==before else 1,maps=locators),[] if data==before else [dict(values=arm['insert'])],dict(owned=[] if data==before else [24],available=[] if data==before else [24]))
        with patch.object(experiment,'shape',side_effect=shape):
            receipt=dict(root=20,page=24,slot=0);self.assertTrue(experiment.patch_check(before,after,arm,receipt)['page0_unchanged'])
            for offset in (1538,20*2048+12,21*2048+100,24*2048+100):
                bad=bytearray(after);bad[offset]^=1
                with self.assertRaises(ValueError):experiment.patch_check(before,bad,arm,receipt)
            with self.assertRaises(ValueError):experiment.patch_check(before,after,arm,dict(receipt,page=23))

    def test_schema_rows_query_and_pins(self):
        arm=self.plan['arms'][0];value=self.snapshot(arm,'rust-next');experiment.expected(value,arm,'rust-next',self.plan)
        value['user_tables'][0]['rows'].pop()
        with self.assertRaises(ValueError):experiment.expected(value,arm,'rust-next',self.plan)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'x').write_text('changed');plan=root/'plan.json';plan.write_text(json.dumps(dict(inputs={'x':'0'*64})))
            with patch.object(experiment,'ROOT',root),patch.object(experiment,'PLAN',plan):
                with self.assertRaisesRegex(ValueError,'Input pin'):experiment.verify_inputs()


if __name__=='__main__':unittest.main()
