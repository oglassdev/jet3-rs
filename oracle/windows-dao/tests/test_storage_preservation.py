"""Preservation comparisons must reject changes even when row values still match."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import storage_preservation as suite
import numeric_index_mutation as numeric


class PreservationTests(unittest.TestCase):
    def test_query_parameters_properties_and_sql_are_compared(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "sample.mdb"
            image.write_bytes(b"image identity")
            snapshot = dict(identity=suite.identity(image), queries=[dict(sql="SELECT Id FROM Items",
                            parameters=[dict(name="Id", type=4)], properties=[dict(name="Tag", value="sentinel")])],
                            tables=[[dict(name="Items", rows=[[dict(Id=1)]])]])
            suite.compare_snapshot(snapshot, snapshot, [dict(Id=1)], image)
            changes = [lambda q: q.update(sql="SELECT 1"),
                       lambda q: q["parameters"][0].update(type=10),
                       lambda q: q["properties"][0].update(value="changed")]
            for change in changes:
                changed = copy.deepcopy(snapshot)
                change(changed["queries"][0])
                with self.subTest(change=change), self.assertRaisesRegex(AssertionError, "QueryDefs"):
                    suite.compare_snapshot(changed, snapshot, [dict(Id=1)], image)
            image.write_bytes(b"changed image")
            with self.assertRaisesRegex(AssertionError, "identity"):
                suite.compare_snapshot(snapshot, snapshot, [dict(Id=1)], image)

    def test_surviving_descriptor_changes_are_rejected(self):
        row = dict(values=dict(Id=1, Memo="aabb"), descriptors=dict(Memo="descriptor"),
                   locator=dict(page=3,row=0), storage=dict(page=3,row=0), raw_hex="aabb")
        before = dict(tables=dict(Items=dict(rows=[row])))
        after = copy.deepcopy(before)
        after["tables"]["Items"]["rows"][0]["descriptors"]["Memo"] = "different"
        with self.assertRaisesRegex(AssertionError, "unassigned row/descriptor"):
            suite.surviving_rows(before, after, [])
        self.assertEqual(suite.surviving_rows(before, after, [dict(id=1,operation="replace")]), [])

    def test_unrelated_page_and_map_changes_are_rejected(self):
        before = dict(tables=dict(MSysQueries=dict(definition=dict(pages=[1]))),
                      maps={"MSysQueries/table/owned":dict(members=[2],record=dict(references=[]))})
        data = bytes(6144)
        self.assertEqual(suite.preserve(before,before,data,data), [1,2])
        changed = bytearray(data); changed[4096] = 1
        with self.assertRaisesRegex(AssertionError, "unrelated page"):
            suite.preserve(before,before,data,bytes(changed))
        after = copy.deepcopy(before)
        after["maps"]["MSysQueries/table/owned"]["members"] = []
        with self.assertRaisesRegex(AssertionError, "unrelated map"):
            suite.preserve(before,after,data,data)

    def test_snapshot_normalization_retains_counters_and_index_order(self):
        before = dict(file="a",identity={},database_properties=[dict(name="Name",result=dict(value="a"))],
                      relations=[[]],queries=[dict(date_created=1)],
                      tables=[[dict(rows=[[dict(Id=2),dict(Id=1)]],indexes=[dict(traversal=[2,1],count=3)])]])
        equivalent = copy.deepcopy(before)
        equivalent["file"]="b";equivalent["database_properties"][0]["result"]["value"]="b"
        equivalent["tables"][0][0]["rows"][0].reverse()
        self.assertEqual(suite.normalized(before),suite.normalized(equivalent))
        for key,value in (("count",4),("traversal",[1,2])):
            changed = copy.deepcopy(before);changed["tables"][0][0]["indexes"][0][key]=value
            self.assertNotEqual(suite.normalized(before),suite.normalized(changed))

    def test_long_key_boundary_retains_the_prefix_and_suffix_checksum(self):
        short = b"\x7f" + b"\x60" * 253 + b"\x00"
        self.assertEqual(numeric.shorten_key(short), short)
        long = b"\x7f" + b"\x60" * 254 + b"\x00"
        encoded = numeric.shorten_key(long)
        self.assertEqual(len(encoded),255)
        self.assertEqual(encoded[:253],long[:253])
        self.assertEqual(encoded[-2:],bytes.fromhex("4061"))


if __name__ == "__main__":
    unittest.main()
