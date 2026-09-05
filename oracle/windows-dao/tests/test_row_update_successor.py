import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import row_update_successor as s

class SuccessorTests(unittest.TestCase):
    def test_phase_plan_identity_is_scoped_and_restored(self):
        with patch.object(s,'original_entries',side_effect=lambda *args: str(s.base.PLAN)):
            self.assertEqual(s.entries({},'create',{}),str(s.OLD_PLAN))
            self.assertEqual(s.entries({},'observe',{}),str(s.PLAN))
        with patch.object(s,'original_entries',side_effect=ValueError('failure')):
            with self.assertRaises(ValueError):s.entries({},'create',{})
        self.assertEqual(s.base.PLAN,s.PLAN)

    def test_retained_identity_drift_refuses_before_receipts(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);f=root/'original.mdb';f.write_bytes(b'original')
            plan=dict(retained_root=temp,retained={f.name:s.identity(f)})
            f.write_bytes(b'changed')
            with patch.object(s.base,'verify_inputs',return_value=plan):
                with self.assertRaisesRegex(ValueError,'Retained input mismatch'):s.verify()

    def test_candidate_pin_gate_preserves_original_failure_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp);candidate=dict(size=1,sha256='pinned');plan=dict(retained={'report.json':{'sha256':'original-failure'}},candidates={'grow-first-r1-rust.mdb':candidate})
            result={'updates':[dict(arm='grow-first',replica=1,rust=candidate)]};(out/'result.json').write_text(json.dumps(result))
            report=dict(outcome='observed_accepted',reasons=[])
            with patch.object(s,'verify',return_value=plan),patch.object(s.base,'build_report',side_effect=lambda *args:copy.deepcopy(report)):
                s.analyze(out);value=json.loads((out/'report.json').read_text());self.assertEqual(value['outcome'],'observed_accepted');self.assertEqual(value['original_report_sha256'],'original-failure')
                result['updates'][0]['rust']={'size':1,'sha256':'different'};(out/'result.json').write_text(json.dumps(result));s.analyze(out)
                self.assertEqual(json.loads((out/'report.json').read_text())['outcome'],'no_outcome')

if __name__=='__main__':unittest.main()
