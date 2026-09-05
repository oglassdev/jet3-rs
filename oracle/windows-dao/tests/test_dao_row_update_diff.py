import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dao_row_update_diff as rows
import dao_update_diff as historical


def images(root,scenario):
    request=scenario['request'];values=next(t['rows'] for t in scenario['tables'] if t['name']=='Items')
    before=bytearray(24*2048);page=23*2048;definition=20*2048;end=2048
    for slot,value in enumerate(values):
        data=rows.encoded(value);start=end-len(data);before[page+start:page+end]=data;before[page+10+2*slot:page+12+2*slot]=start.to_bytes(2,'little');end=start
    before[definition+12:definition+16]=(3).to_bytes(4,'little');before[page+8:page+10]=(3).to_bytes(2,'little');free=end-16;before[page+2:page+4]=free.to_bytes(2,'little')
    expected=bytearray(before);changes=[]
    def put(offset,value):
        changes.append(dict(offset=offset,before=list(expected[offset:offset+len(value)]),after=list(value)));expected[offset:offset+len(value)]=value
    if request['kind']=='insert':
        data=rows.encoded(request['row']);start=page+end-len(data);length=len(data);slot=3
        put(start,data);put(page+16,(start-page).to_bytes(2,'little'));put(page+8,(4).to_bytes(2,'little'));put(page+2,(free-length-2).to_bytes(2,'little'));put(definition+12,(4).to_bytes(4,'little'))
    else:
        start=page+end;length=len(rows.encoded(values[-1]));slot=2
        put(page+14,(0xc000|end+length).to_bytes(2,'little'));put(page+2,(free+length).to_bytes(2,'little'));put(definition+12,(2).to_bytes(4,'little'))
    receipt=dict(scenario_id=scenario['id'],request=request,root=20,page=23,slot=slot,row_offset=start,row_length=length,patches=changes,preserved=True)
    for role,data in [('before',before),('after',expected)]:
        folder=root/role;folder.mkdir();(folder/'database.mdb').write_bytes(data);receipt[role+'_sha256']=rows.base.write.digest(folder/'database.mdb')
    return receipt


class RowUpdateTests(unittest.TestCase):
    def test_inventory_adapter_preserves_historical_membership(self):
        self.assertEqual(len(rows.inventory()['scenarios']),5)
        self.assertEqual(len(historical.inventory()['scenarios']),3)
        self.assertIsNot(rows.base,historical)
        for scenario in rows.inventory()['scenarios'][3:]:
            expected=rows.recipe(scenario,'after');table=next(t for t in expected['tables'] if t['name']=='Items')
            self.assertEqual(len(table['rows']),4 if scenario['request']['kind']=='insert' else 2)

    def test_exact_row_patch_and_unrelated_bytes(self):
        for scenario in rows.inventory()['scenarios'][3:]:
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);receipt=images(root,scenario);rows.check_preservation(root,scenario,receipt)
                for key,value in [('slot',99),('root',21),('row_length',1),('request',{})]:
                    bad=copy.deepcopy(receipt);bad[key]=value
                    with self.assertRaises(rows.base.ValidationError):rows.check_preservation(root,scenario,bad)
                path=root/'after/database.mdb';data=bytearray(path.read_bytes());data[1538]^=1;path.write_bytes(data);receipt['after_sha256']=rows.base.write.digest(path)
                with self.assertRaisesRegex(rows.base.ValidationError,'unrelated byte'):rows.check_preservation(root,scenario,receipt)

    def test_snapshot_command_selects_five_case_inventory(self):
        with patch.object(rows,'old_command',return_value='done') as run:
            self.assertEqual(rows.command(Path('.'),'snapshot',['cli','snapshot','file']),'done')
            self.assertEqual(run.call_args.args[2],['cli','snapshot','file','--inventory','row-update'])
            rows.command(Path('.'),'generate',['checker','generate','case'])
            self.assertEqual(run.call_args.args[2],['checker','generate','case'])

    def test_unreviewed_plan_gate_fails(self):
        with self.assertRaises((OSError,rows.base.ValidationError)):
            rows.base.validate_plan('0'*64)


if __name__=='__main__':unittest.main()
