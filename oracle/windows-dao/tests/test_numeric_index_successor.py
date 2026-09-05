import hashlib
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import numeric_index as historical
import numeric_index_successor as successor

class SuccessorTests(unittest.TestCase):
    def test_private_plan_preserves_all_requests_images_and_classifier(self):
        old=json.loads(historical.PLAN.read_text());new=json.loads(successor.original.PLAN.read_text())
        for name in ['source_revision','images','arms','replicas','decision_rule','bounds']:
            self.assertEqual(old[name],new[name])
        self.assertNotEqual(historical.PLAN,successor.original.PLAN)
        for name in ['build_report','expected','raw_check','component']:
            self.assertEqual(getattr(historical,name).__code__.co_code,getattr(successor.original,name).__code__.co_code)
        self.assertEqual(new['inputs'][str(historical.PLAN.relative_to(historical.ROOT))],hashlib.sha256(historical.PLAN.read_bytes()).hexdigest())

if __name__=='__main__':unittest.main()
