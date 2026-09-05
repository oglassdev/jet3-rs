import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import indexed_row_candidate as a

def snapshot(arm,role):
    rows=a.rows_for(arm,role)
    return dict(version='3.0',tables=['Items','MSysACEs','MSysObjects','MSysQueries','MSysRelationships'],queries=[],relations=[],user_tables=[dict(name='Items',attributes=0,fields=[dict(name=n,type=4,size=4,attributes=1,required=False) for n in ('Id','Value')],indexes=[dict(name='ByKey',primary=arm['primary'],unique=True,required=arm['primary'],foreign=False,ignore_nulls=False,fields=[dict(name='Id',attributes=int(arm['descending']))])],rows=rows)],traversal=sorted(rows,reverse=arm['descending']),seek=[dict(query=q,row=next((r for r in rows if r[0]==q),None)) for q in arm['queries']])

class IndexedRowTests(unittest.TestCase):
    def test_finite_rows_full_traversal_and_missing_seek(self):
        plan=json.loads(a.PLAN.read_text())
        self.assertEqual([len(a.rows_for(arm,'candidate')) for arm in plan['arms']],[4,4,200,3])
        for arm in plan['arms']:
            a.expected(snapshot(arm,'candidate'),arm,'candidate')
            for defect in ('row','traversal','seek','flags'):
                value=snapshot(arm,'candidate')
                if defect=='row':value['user_tables'][0]['rows'].pop()
                elif defect=='traversal':value['traversal'].reverse()
                elif defect=='seek':value['seek'].pop()
                else:value['user_tables'][0]['indexes'][0]['unique']=False
                with self.assertRaises(ValueError):a.expected(value,arm,'candidate')

    def test_complete_capture_source_operation_identity_and_rejection_gates(self):
        plan=json.loads(a.PLAN.read_text());plan['images']={}
        result=dict(document_type='dao_indexed_row_result',plan_sha256=a.identity(a.PLAN)['sha256'],source_revision=plan['source_revision'],error=None,retention_failures=[],mutation_started=True,environment=dict(process_bits=32,provider='DAO.DBEngine.36'),pairs=[])
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)
            for arm in plan['arms']:
                for replica in range(1,4):
                    operations={r:dict(status='complete') for r in ('control','candidate-next','control-next')}
                    operations.update({r:dict(accepted=False,error=dict(message='duplicate'),numbers=[3022]) for r in ('candidate-duplicate','control-duplicate')})
                    pair=dict(arm=arm['name'],replica=replica,captures={},operations=operations);result['pairs'].append(pair)
                    for role in ['original','candidate','control-original','control','candidate-next','control-next','candidate-duplicate','control-duplicate']:
                        path=out/f"{arm['name']}-r{replica}-{role}.mdb";path.write_bytes(role.encode());ident=a.identity(path)
                        if role in ('original','candidate'):plan['images'][arm['name']+'-'+role+'.mdb']=ident
                        pair['captures'][role]=dict(file=path.name,status='pass',error=None,before=copy.deepcopy(ident),after=copy.deepcopy(ident),snapshot=snapshot(arm,role))
            with patch.object(a,'raw_check',return_value={}),patch.object(a,'patch_check',return_value={}),patch.object(a,'definition',return_value=(dict(row_count=4,physical_indexes=[dict(entry_count=5)]),[0]*4)):
                self.assertEqual(a.build_report(result,out,plan)['outcome'],'observed_accepted')
                for defect in ('source','missing','identity','accepted','numbers','poststate','retention'):
                    bad=copy.deepcopy(result);p=bad['pairs'][0]
                    if defect=='source':bad['source_revision']='wrong'
                    elif defect=='missing':p['captures'].pop('control-original')
                    elif defect=='identity':p['captures']['candidate']['after']['size']+=1
                    elif defect=='accepted':p['operations']['candidate-duplicate']['accepted']=True
                    elif defect=='numbers':p['operations']['candidate-duplicate']['numbers']=[3022,99]
                    elif defect=='poststate':p['captures']['candidate-duplicate']['snapshot']['user_tables'][0]['rows'].append([42,0])
                    else:bad['retention_failures']=['failed']
                    self.assertEqual(a.build_report(bad,out,plan)['outcome'],'no_outcome',defect)

    def test_preflight_and_analysis_use_shared_input_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'input';source.write_text('old');plan=root/'plan';plan.write_text(json.dumps(dict(inputs={'input':a.identity(source)['sha256']})))
            with patch.object(a,'ROOT',root),patch.object(a,'PLAN',plan):
                a.verify_inputs();source.write_text('changed')
                with self.assertRaisesRegex(ValueError,'pin'):a.verify_inputs()
                with self.assertRaisesRegex(ValueError,'pin'):a.analyze(root)
if __name__=='__main__':unittest.main()
