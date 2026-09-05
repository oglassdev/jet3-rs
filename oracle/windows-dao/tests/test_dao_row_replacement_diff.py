import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import dao_row_replacement_diff as a
import dao_indexed_update_diff as historical

class ReplacementTests(unittest.TestCase):
    def test_inventory_recipe_and_sidecar_inventory(self):
        cases=a.inventory()['scenarios'];self.assertEqual(len(cases),17)
        self.assertEqual(cases[:13],historical.inventory()['scenarios'])
        self.assertEqual(a.indexed.inventory()['scenarios'],cases)
        for case,count in zip(cases[13:],[0,3,3,2]):
            expected=a.recipe(case,'after');table=next(t for t in expected['tables'] if t['name']==case['request']['table']);self.assertEqual(len(table['rows']),count)
        tombstone=cases[-1];self.assertEqual(len(tombstone['tables'][1]['seed_rows']),3);self.assertEqual(len(tombstone['tables'][1]['rows']),2)
        self.assertEqual(a.recipe(tombstone,'after')['tables'][1]['rows'][1],[13,None,None,None,False])

    def test_request_identity_and_binary_conversion(self):
        case=a.inventory()['scenarios'][14]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for role in a.base.ROLES:(root/role).mkdir();(root/role/'database.mdb').write_bytes(role.encode())
            receipt=dict(scenario_id=case['id'],request=case['request'],preserved=True,locator={'root':20,'page':23,'slot':0},before_sha256=a.base.write.digest(root/'before/database.mdb'),after_sha256=a.base.write.digest(root/'after/database.mdb'))
            with patch.object(a.replacement,'patch_check') as check:
                a.check_preservation(root,case,receipt);self.assertEqual(check.call_args.args[2]['replacement'][3],b'abcdefgh'.hex());self.assertEqual(check.call_args.args[3],receipt['locator'])
                self.assertEqual(case['request']['replacement'][3],'abcdefgh')
                bad=copy.deepcopy(receipt);bad['request']['selected_id']=9
                with self.assertRaises(a.base.ValidationError):a.check_preservation(root,case,bad)
                (root/'after/database.mdb').write_bytes(b'changed')
                with self.assertRaises(a.base.ValidationError):a.check_preservation(root,case,receipt)

    def test_cli_selector_retains_frozen_indexed_runner(self):
        with patch.object(a.indexed.allocation.rows,'old_command') as run:
            a.command(Path('.'),'snapshot',['cli','snapshot','file'])
            self.assertEqual(run.call_args.args[2][-2:],['--inventory','row-replacement'])
        self.assertEqual(len(historical.inventory()['scenarios']),13)

if __name__=='__main__':unittest.main()
