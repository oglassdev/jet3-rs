import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import single_leaf_key as historical
import single_leaf_key_successor as successor

class SuccessorTests(unittest.TestCase):
    def test_private_plan_binding_preserves_source_images_requests_and_gates(self):
        old=json.loads(historical.PLAN.read_text());new=json.loads(successor.original.PLAN.read_text())
        for name in ['source_revision','images','receipts','arms','replicas','decision_rule','bounds']:self.assertEqual(old[name],new[name])
        self.assertNotEqual(historical.PLAN,successor.original.PLAN)
        self.assertEqual(historical.build_report.__code__.co_code,successor.original.build_report.__code__.co_code)
        self.assertEqual(historical.patch_check.__code__.co_code,successor.original.patch_check.__code__.co_code)
        successor.original.verify_inputs()

if __name__=='__main__':unittest.main()
