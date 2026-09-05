import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import multiple_index_reanalysis as s

class SecondaryTests(unittest.TestCase):
    def test_only_pinned_four_byte_count_is_normalized_and_reported(self):
        data=bytes(16)+(202).to_bytes(4,'little')+bytes(20);original=bytes(data)
        override=dict(table_root=20,offset=16,stored_count=202)
        seen=[]
        def validate(value,arm):
            seen.append(value);self.assertEqual(value[:16]+value[20:],data[:16]+data[20:]);self.assertEqual(value[16:20],(201).to_bytes(4,'little'))
            return dict(indexes=[dict(name='ZPrimary',entries=201,distinct=201)])
        definition=dict(physical_indexes=[dict(entry_count=202,entry_count_offset=16)])
        with patch.object(s.frozen.catalog,'_definition',return_value=definition),patch.object(s.frozen,'raw_check',side_effect=validate):
            report=s.checked_raw(data,dict(name='three-long'),{s.hashlib.sha256(data).hexdigest():override})
            self.assertEqual(report['count_residue']['stored_count'],202);self.assertEqual(data,original);self.assertEqual(len(seen),1)
            with self.assertRaisesRegex(ValueError,'arm'):s.checked_raw(data,dict(name='other'),{s.hashlib.sha256(data).hexdigest():override})
        with patch.object(s.frozen,'raw_check',return_value={'unchanged':True}) as old:
            self.assertEqual(s.checked_raw(data,{},{}),{'unchanged':True});old.assert_called_once_with(data,{})

    def test_identical_replica_images_share_only_consistent_normalization(self):
        values={};first=dict(table_root=20,offset=40999,stored_count=202,actual_distinct=201,replica=1)
        s.add_override(values,'same',first);s.add_override(values,'same',dict(first,replica=2))
        self.assertEqual(len(values),1)
        with self.assertRaisesRegex(ValueError,'Conflicting'):s.add_override(values,'same',dict(first,offset=41000))

    def test_native_rejection_gate_and_exact_residue_inventory(self):
        plan=json.loads(s.PLAN.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'result.json').write_text(json.dumps(dict(pairs=[dict(arm='three-long',replica=1,probes={'candidate-duplicate-secondary':dict(accepted=True,error=None,numbers=[])})])))
            with self.assertRaisesRegex(ValueError,'rejection'):s.compare(plan,root)
            short=dict(plan,count_residues=plan['count_residues'][:-1])
            with self.assertRaisesRegex(ValueError,'Six'):s.compare(short,root)

    def test_pins_and_output_preserve_original_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'outbox'/'original';source.mkdir(parents=True);file=source/'result.json';file.write_bytes(b'original')
            plan=dict(source_directory=str(source),artifacts={'result.json':s.identity(file)})
            s.verify_artifacts(plan)
            with patch.object(s,'preflight',return_value=plan):
                with self.assertRaisesRegex(ValueError,'outside'):s.analyze(source/'nested'/'report.json')
            self.assertEqual(file.read_bytes(),b'original');file.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'artifact'):s.verify_artifacts(plan)

if __name__=='__main__':unittest.main()
