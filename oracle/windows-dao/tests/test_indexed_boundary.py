"""Full new analyzer path against deterministic public images; no DAO."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import indexed_boundary as a


def snapshot(arm, role):
    ids = list(range(arm['count']))
    if role != 'original' and arm['name'] in ('space', 'eof'):
        ids.append(arm['id'])
    rows = [a.values(k) for k in sorted(ids)]
    tables = []
    for name, fields, data in [('Items', [('Id',4,4),('Name',10,80),('Price',5,8),('Active',1,1)], rows), ('Notes', [('Id',4,4),('Body',12,0)], [[7,'n'*4096],[8,None]])]:
        tables.append(dict(name=name, attributes=0, fields=[dict(name=n,type=t,size=s) for n,t,s in fields], indexes=[], rows=data))
    tables[0]['indexes'] = [dict(name='ById',primary=True,unique=True,required=True,foreign=False,ignore_nulls=False,fields=[dict(name='Id',attributes=0)])]
    return dict(version='3.0', tables=sorted(['Items','Notes','MSysACEs','MSysObjects','MSysQueries','MSysRelationships']), queries=[], relations=[], user_tables=tables, traversal=copy.deepcopy(rows), seek=[dict(query=k,row=next((r for r in rows if r[0]==k),None)) for k in range(-1,202)])


class BoundaryTests(unittest.TestCase):
    def test_full_report_and_failure_matrix(self):
        plan = json.loads(a.PLAN.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            images = Path(tmp)/'images'
            subprocess.run(['cargo','run','--quiet','-p','jet3','--example','indexed_boundary_candidate','--',str(images)],cwd=a.ROOT,check=True)
            result = dict(document_type='dao_indexed_boundary_result',plan_sha256=a.identity(a.PLAN)['sha256'],environment=dict(process_bits=32,provider='DAO.DBEngine.36'),error=None,retention_failures=[],mutation_started=True,captures={},operations=dict(space=dict(status='inserted'),eof=dict(status='inserted'),duplicate=dict(status='duplicate',numbers=[3022])))
            for arm in plan['arms']:
                shutil.copyfile(images/f"{arm['name']}-candidate.mdb", images/f"{arm['name']}-control.mdb")
                for role in ('original','candidate','control'):
                    name = f"{arm['name']}-{role}.mdb"
                    pin = a.identity(images/name)
                    if role != 'control': self.assertEqual(pin,plan['images'][name])
                    result['captures'][name] = dict(before=pin,after=pin,snapshot=snapshot(arm,role))
            report = a.build_report(result,images,plan)
            self.assertEqual(report['outcome'],'observed_accepted',report['reasons'])
            for defect in ('currency','boolean','memo','seek','capture','operation','retention'):
                bad = copy.deepcopy(result)
                s = bad['captures']['eof-candidate.mdb']['snapshot']
                if defect == 'currency': s['user_tables'][0]['rows'][1][2] = -12.3456
                elif defect == 'boolean': s['user_tables'][0]['rows'][1][3] = 1
                elif defect == 'memo': s['user_tables'][1]['rows'][0][1] = 'changed'
                elif defect == 'seek': s['seek'].pop()
                elif defect == 'capture': bad['captures'].pop('split-control.mdb')
                elif defect == 'operation': bad['operations']['duplicate']['numbers'] = []
                else: bad['retention_failures'] = ['lost']
                self.assertEqual(a.build_report(bad,images,plan)['outcome'],'no_outcome',defect)
            arm = plan['arms'][1]
            before = (images/'eof-original.mdb').read_bytes()
            after = (images/'eof-candidate.mdb').read_bytes()
            table, _ = a.definition(before)
            memo_offset = before.index(b'n'*4096) if b'n'*4096 in before else before.index(b'n'*100)
            for offset in (table['physical_indexes'][0]['root']*2048+250, memo_offset, len(before)+100):
                bad = bytearray(after); bad[offset] ^= 1
                with self.assertRaises(ValueError): a.patch_check(before,bad,arm)


if __name__ == '__main__': unittest.main()
