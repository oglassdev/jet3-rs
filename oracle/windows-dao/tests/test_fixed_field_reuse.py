import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import fixed_field_reuse as experiment


def snapshot(plan,arm,updated):
    tables=[]
    for table in experiment.original.arm_tables(plan,arm,updated):
        fields=[dict({k:f[k] for k in ('name','type','size')},attributes=1 if f['fixed'] else 2) for f in table['fields']]
        tables.append(dict(name=table['name'],attributes=0,indexes=[],fields=fields,rows=table['rows']))
    return dict(version='3.0',relations=[],queries=[dict(name=plan['query']['name'],sql=plan['query']['sql'],type=0)],user_tables=tables)


class ReuseTests(unittest.TestCase):
    def test_complete_matrix_and_source_query_inventory_gates(self):
        plan=json.loads(experiment.PLAN.read_text())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir();out=root/'out';out.mkdir();plan['retained_root']=str(source);plan['retained']={}
            create=dict(plan_sha256=plan['original_plan_sha256'],error=None,observations=[])
            old=dict(phase='unix_update',plan_sha256=plan['original_plan_sha256'],source_revision=plan['original_source_revision'],updates=[])
            capture=dict(document_type='dao_fixed_field_reuse_phase',plan_sha256=experiment.digest(experiment.PLAN),error=None,retention_failures=[],mutation_started=False,environment=dict(process_bits=32,provider='DAO.DBEngine.36'),observations=[])
            result=dict(document_type='dao_fixed_field_reuse_result',plan_sha256=experiment.digest(experiment.PLAN),source_revision=plan['source_revision'],producer_os='posix',phase='complete',error=None,updates=[])
            for arm in plan['arms']:
                for replica in range(1,4):
                    ids={};new=arm['name']=='fixed-text-255'
                    for role in ('original','updated'):
                        path=out/f"{arm['name']}-r{replica}-{role}.mdb";path.write_bytes(role.encode());ids[role]=experiment.identity(path)
                        if role=='original' or not new:plan['retained'][path.name]=ids[role]
                        obs=dict(arm=arm['name'],replica=replica,role=role,observation=dict(file=path.name,before=ids[role],after=ids[role],status='pass',error=None,snapshot=snapshot(plan,arm,role=='updated')))
                        capture['observations'].append(obs)
                        if role=='original':create['observations'].append(copy.deepcopy(obs))
                    update=dict(arm=arm['name'],replica=replica,original_before=ids['original'],original_after=ids['original'],updated=ids['updated'],locator={})
                    if not new:old['updates'].append(copy.deepcopy(update))
                    result['updates'].append(dict(update,source_revision=plan['source_revision'] if new else plan['original_source_revision'],origin='new_public_update' if new else 'retained_update'))
            (source/'result.json').write_text(json.dumps(old));(source/'create.json').write_text(json.dumps(create))
            def classify():
                (out/'observe.json').write_text(json.dumps(capture));result['observe']=experiment.identity(out/'observe.json')
                with patch.object(experiment,'patch_check',return_value={}):return experiment.build_report(result,out,plan)['outcome']
            self.assertEqual(classify(),'observed_accepted')
            result['updates'][0]['source_revision']=plan['source_revision'];self.assertEqual(classify(),'no_outcome');result['updates'][0]['source_revision']=plan['original_source_revision']
            capture['observations'][1]['observation']['snapshot']['queries'][0]['sql']='changed';self.assertEqual(classify(),'no_outcome');capture['observations'][1]['observation']['snapshot']['queries'][0]['sql']=plan['query']['sql']
            capture['environment']['provider']='other';self.assertEqual(classify(),'no_outcome');capture['environment']['provider']='DAO.DBEngine.36'
            capture['observations'].pop();self.assertEqual(classify(),'no_outcome')

    def test_private_decoder_wide_shape_and_original_unchanged(self):
        columns=[dict(ordinal=0,type='Long',storage='fixed',fixed_offset=0,size=4),dict(ordinal=1,type='Text',storage='fixed',fixed_offset=4,size=255),dict(ordinal=2,type='Text',storage='variable',variable_index=0,size=20)]
        raw=bytes([3])+bytes.fromhex('02000000')+b'a'*255+b'second'+bytes([10,4,0,1,7])
        self.assertEqual(experiment.decode_row(raw,columns,'test')['values'],[2,'a'*255,'second'])
        with self.assertRaises(experiment.original.catalog.DecodeError):experiment.original.catalog._decode_row(raw,columns,'original')
        bad=bytearray(raw);bad[-3]=3
        with self.assertRaises(experiment.catalog.DecodeError):experiment.decode_row(bytes(bad),columns,'test')
        bad=bytearray(raw);bad[-5]=11
        with self.assertRaises(experiment.catalog.DecodeError):experiment.decode_row(bytes(bad),columns,'test')

    def test_input_retained_and_output_preservation_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);script=root/'script';script.write_text('pinned');source=root/'source';source.mkdir();image=source/'one.mdb';image.write_bytes(b'original')
            p=root/'plan.json';p.write_text(json.dumps(dict(inputs={'script':experiment.digest(script)},retained_root=str(source),retained={'one.mdb':experiment.identity(image)})))
            with patch.object(experiment,'ROOT',root),patch.object(experiment,'PLAN',p):
                experiment.verify_inputs()
                with self.assertRaisesRegex(ValueError,'original retained'):experiment.analyze(source/'nested')
                self.assertEqual(image.read_bytes(),b'original')
                script.write_text('changed')
                with self.assertRaisesRegex(ValueError,'Input pin'):experiment.verify_inputs()
                script.write_text('pinned');image.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'Retained identity'):experiment.verify_inputs()

if __name__=='__main__':unittest.main()
