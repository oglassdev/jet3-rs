import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dao_row_allocation_diff as a
import dao_row_update_diff as historical

class AllocationTests(unittest.TestCase):
    def test_inventory_and_recipe_preserve_old_cases(self):
        value=a.inventory();self.assertEqual(len(value['scenarios']),9)
        self.assertEqual(value['scenarios'][:5],historical.inventory()['scenarios'])
        for case,count in zip(value['scenarios'][5:],[1,8,3,2]):
            self.assertEqual(len(a.recipe(case,'after')['tables'][0]['rows']),count)
        bad=copy.deepcopy(value['scenarios'][-1]);bad['request']['selected_ids']=[2,2]
        with self.assertRaises(a.base.ValidationError):a.recipe(bad,'after')

    def test_snapshot_routing_is_additive(self):
        with patch.object(a.rows,'old_command',return_value='ok') as run:
            a.command(Path('.'),'snapshot',['cli','snapshot','file'])
            self.assertEqual(run.call_args.args[2],['cli','snapshot','file','--inventory','row-allocation'])
            a.command(Path('.'),'generate',['checker','generate','id'])
            self.assertEqual(run.call_args.args[2],['checker','generate','id'])
        self.assertEqual(len(historical.inventory()['scenarios']),5)

    def test_empty_eof_exact_maps_payload_and_request_binding(self):
        case=a.inventory()['scenarios'][5];before=bytearray(23*2048);definition=20*2048
        # Two records on the same table-map page, and a separate global map.
        for page in [1,21]:
            before[page*2048+10:page*2048+12]=(1915).to_bytes(2,'little')
            before[page*2048+12:page*2048+14]=(1782).to_bytes(2,'little')
        before[definition+35:definition+39]=(21<<8).to_bytes(4,'little')
        before[definition+39:definition+43]=((21<<8)|1).to_bytes(4,'little')
        offsets=[2048+1915+5+2,21*2048+1915+5+2,21*2048+1782+5+2]
        before[offsets[0]]=128
        after=bytearray(before);after[offsets[0]]=0;after[offsets[1]]=128;after[offsets[2]]=128;after[definition+12]=1
        image=bytearray(2048);image[:12]=bytes([1,1,223,7,20,0,0,0,1,0,235,7]);image[2027:]=bytes([3,88,0,0,0,160,221,255,255])+b'inserted'+bytes([17,9,1,7]);after.extend(image)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);receipt=dict(scenario_id=case['id'],request=case['request'],root=20,steps=[dict(page=23,slot=0,map_offsets=offsets)],preserved=True)
            for role,data in [('before',before),('after',after)]:
                d=root/role;d.mkdir();(d/'database.mdb').write_bytes(data);receipt[role+'_sha256']=a.base.write.digest(d/'database.mdb')
            with patch.object(a,'table_rows',return_value=(dict(root=20),[])):
                a.check_preservation(root,case,receipt)
                wrong=copy.deepcopy(receipt);wrong['steps'][0]['slot']=1
                with self.assertRaisesRegex(a.base.ValidationError,'coordinates'):a.check_preservation(root,case,wrong)
                for offset in [1538,offsets[1],len(after)-1]:
                    changed=bytearray(after);changed[offset]^=1;(root/'after/database.mdb').write_bytes(changed);receipt['after_sha256']=a.base.write.digest(root/'after/database.mdb')
                    with self.assertRaisesRegex(a.base.ValidationError,'unrelated bytes'):a.check_preservation(root,case,receipt)

if __name__=='__main__':unittest.main()
