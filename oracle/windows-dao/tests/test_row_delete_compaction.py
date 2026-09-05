import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import row_delete_compaction as experiment


class CandidateTests(unittest.TestCase):
    def setUp(self): self.plan=json.loads(experiment.PLAN.read_text())

    def snapshot(self,arm,role):
        tables=[]
        for spec in arm['tables']:
            rows=copy.deepcopy(spec['rows'])
            if spec['name']==arm['table']:
                if role!='original':rows=[r for r in rows if r[0] not in arm['selected_ids']]
                if role.endswith('-next'):rows.append(self.plan['insert'])
            tables.append(dict(name=spec['name'],attributes=0,indexes=[],fields=[dict(f,attributes=2 if f['type']==10 else 1) for f in self.plan['fields']],rows=rows))
        return dict(version='3.0',relations=[],tables=sorted(['MSysACEs','MSysObjects','MSysQueries','MSysRelationships','Items','Later']),queries=[dict(name='KeepQuery',sql='retained SQL',type=0)],user_tables=tables)

    def test_complete_classifier_and_failure_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);identity=experiment.identity
            result=dict(document_type='dao_row_delete_compaction_result',producer_os='posix',source_revision=self.plan['source_revision'],plan_sha256=identity(experiment.PLAN)['sha256'],phase='complete',error=None,phases={},updates=[])
            for phase in ('create','observe'):
                document=dict(document_type='dao_row_delete_compaction_phase',phase=phase,plan_sha256=result['plan_sha256'],error=None,retention_failures=[],mutation_started=True,environment=dict(process_bits=32,provider='DAO.DBEngine.36'),observations=[],operations=[])
                for arm in self.plan['arms']:
                    for replica in range(1,4):
                        roles=['original','control'] if phase=='create' else ['original','control','rust','control-next','rust-next']
                        for role in roles:
                            name=f"{arm['name']}-r{replica}-{role}.mdb";file=root/name;file.write_bytes(b'x')
                            document['observations'].append(dict(arm=arm['name'],replica=replica,role=role,observation=dict(file=name,before=identity(file),after=identity(file),status='pass',error=None,snapshot=self.snapshot(arm,role))))
                        for role in (['control'] if phase=='create' else ['control-next','rust-next']):document['operations'].append(dict(arm=arm['name'],replica=replica,role=role,result=dict(operation='delete' if phase=='create' else 'insert',status='complete',selected_ids=arm['selected_ids'])))
                        if phase=='create':result['updates'].append(dict(arm=arm['name'],replica=replica,original_before=identity(file),original_after=identity(file),rust=identity(file),locator={}))
                file=root/(phase+'.json');file.write_text(json.dumps(document));result['phases'][phase]=identity(file)
            with patch.object(experiment,'patch_check',return_value={'maps':{}}),patch.object(experiment,'shape',return_value=(None,None,{})):
                self.assertEqual(experiment.build_report(result,root,self.plan)['outcome'],'observed_accepted')
                for key,value in [('producer_os','nt'),('source_revision','wrong'),('error','failed'),('phase','create')]:
                    bad=dict(result,**{key:value});self.assertEqual(experiment.build_report(bad,root,self.plan)['outcome'],'no_outcome')
                bad=copy.deepcopy(result);bad['updates'].pop();self.assertEqual(experiment.build_report(bad,root,self.plan)['outcome'],'no_outcome')
                (root/'first-row-r1-rust.mdb').write_bytes(b'changed');self.assertEqual(experiment.build_report(result,root,self.plan)['outcome'],'no_outcome')

    def test_exact_patch_preserves_payload_and_page_zero(self):
        data=bytearray(24*2048);base=23*2048;root=20*2048
        data[base]=1;data[base+8:base+10]=(2).to_bytes(2,'little');data[base+2:base+4]=(2014).to_bytes(2,'little')
        data[base+10:base+14]=bytes.fromhex('f607ec07');data[base+2028:base+2048]=bytes(range(20));data[root+12:root+16]=(2).to_bytes(4,'little')
        rows=[dict(page=23,row=0,values=[1]),dict(page=23,row=1,values=[2])];maps=dict(owned=[23],available=[23]);arm=dict(selected_ids=[2])
        after=bytearray(data);after[base+2:base+4]=(2024).to_bytes(2,'little');after[base+12:base+14]=bytes.fromhex('f6c7');after[root+12:root+16]=(1).to_bytes(4,'little')
        def shape(raw,_):return (dict(root=20,row_count=2 if raw==data else 1),rows if raw==data else rows[:1],maps)
        with patch.object(experiment,'shape',side_effect=shape):
            receipt=dict(root=20,page=23,slot=1)
            self.assertTrue(experiment.patch_check(data,after,arm,[receipt])['page0_unchanged'])
            for offset in (1538,base+2028,base+100):
                bad=bytearray(after);bad[offset]^=1
                with self.assertRaisesRegex(ValueError,'preservation'):experiment.patch_check(data,bad,arm,[receipt])

    def test_repeated_middle_then_first_preserves_known_tombstones_and_slack(self):
        before=bytearray(24*2048);base=23*2048;root=20*2048
        before[base:base+4]=bytes.fromhex('0101c607');before[base+8:base+10]=(4).to_bytes(2,'little')
        before[base+10:base+18]=bytes.fromhex('f607ec07e207d807');before[root+12:root+16]=(4).to_bytes(4,'little')
        payloads=[bytes([2])+i.to_bytes(4,'little')+bytes(4)+bytes([3]) for i in range(1,5)]
        for slot,payload in enumerate(payloads):before[base+2038-slot*10:base+2048-slot*10]=payload
        after=bytearray(before);after[base+10:base+18]=bytes.fromhex('00c800c8f607ec07');after[base+2:base+4]=(2010).to_bytes(2,'little');after[root+12:root+16]=(2).to_bytes(4,'little')
        after[base+2018:base+2028]=payloads[3];after[base+2028:base+2038]=payloads[3];after[base+2038:base+2048]=payloads[2]
        maps=dict(owned=[23],available=[23])
        def shape(data,_):
            raw=data[base:base+2048];entries=experiment.layout.catalog._row_directory(raw,23)
            rows=[dict(page=23,row=e['row'],values=[int.from_bytes(raw[e['start']+1:e['start']+5],'little')]) for e in entries if not e['hidden']]
            return dict(root=20,row_count=len(rows)),rows,maps
        receipts=[dict(root=20,page=23,slot=1),dict(root=20,page=23,slot=0)]
        arm=dict(selected_ids=[2,1])
        with patch.object(experiment,'shape',side_effect=shape):
            report=experiment.patch_check(before,after,arm,receipts)
            self.assertEqual(report['row_count'],2);self.assertEqual(len(report['steps']),2)
            for offset in (base+2008,base+2018,base+10,base+100):
                bad=bytearray(after);bad[offset]^=1
                with self.assertRaises(ValueError):experiment.patch_check(before,bad,arm,receipts)
            with self.assertRaisesRegex(ValueError,'locator'):experiment.patch_check(before,after,arm,list(reversed(receipts)))

    def test_schema_rows_query_and_pins(self):
        arm=self.plan['arms'][0];value=self.snapshot(arm,'rust-next');experiment.expected(value,arm,'rust-next',self.plan)
        value['user_tables'][0]['rows'].pop()
        with self.assertRaises(ValueError):experiment.expected(value,arm,'rust-next',self.plan)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'x').write_text('changed');plan=root/'plan.json';plan.write_text(json.dumps(dict(inputs={'x':'0'*64})))
            with patch.object(experiment,'ROOT',root),patch.object(experiment,'PLAN',plan):
                with self.assertRaisesRegex(ValueError,'Input pin'):experiment.verify_inputs()


if __name__=='__main__':unittest.main()
