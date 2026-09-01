from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "oracle" / "windows-dao" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import system_catalog as catalog  # noqa: E402


PAGE = catalog.PAGE_BYTES
PLAN_SHA256 = "b" * 64
SYSTEM = -2147483648
TABLES_ID = 0x0F000001
DATABASES_ID = 0x0F000002
RELATIONSHIPS_ID = 0x0F000003
CONTAINERS_PARENT = 0x0F000000
MSYSDB_ID = 0x10000000
ALPHA_ROOT = 11
CONTEXT = bytes.fromhex("0904e404")

# (name, physical type, class byte, declared size)
OBJECTS_COLUMNS = [
    ("Id", 4, 0x13, 4),
    ("ParentId", 4, 0x13, 4),
    ("Name", 10, 0x12, 255),
    ("Type", 3, 0x13, 2),
    ("DateCreate", 8, 0x13, 8),
    ("DateUpdate", 8, 0x13, 8),
    ("Owner", 9, 0x32, 255),
    ("Flags", 4, 0x13, 4),
    ("LvProp", 11, 0x12, 0),
]
ACES_COLUMNS = [
    ("ObjectId", 4, 0x13, 4),
    ("SID", 9, 0x32, 255),
    ("ACM", 4, 0x13, 4),
    ("FInheritable", 1, 0x13, 1),
]
QUERIES_COLUMNS = [("ObjectId", 4, 0x13, 4), ("Name1", 10, 0x12, 255)]
RELATIONSHIPS_COLUMNS = [("szRelationship", 10, 0x12, 255), ("grbit", 4, 0x13, 4)]
ALPHA_COLUMNS = [("Id", 4, 0x03, 4)]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def le16(value: int) -> bytes:
    return value.to_bytes(2, "little", signed=value < 0)


def le32(value: int) -> bytes:
    return value.to_bytes(4, "little", signed=value < 0)


def layout(spec: list[tuple[str, int, int, int]]) -> list[dict]:
    columns = []
    next_fixed = 0
    variables = 0
    for ordinal, (name, type_code, class_byte, size) in enumerate(spec):
        column = {
            "class": class_byte,
            "name": name,
            "ordinal": ordinal,
            "size": size,
            "type": catalog.PHYSICAL_TYPES[type_code],
            "type_code": type_code,
        }
        if class_byte & 0x07 == 2:
            column["storage"] = "variable"
            column["variable_index"] = variables
            column["fixed_offset"] = None
            variables += 1
        else:
            column["storage"] = "fixed"
            column["variable_index"] = 0
            column["fixed_offset"] = next_fixed
            if type_code != 1:
                next_fixed += size
        columns.append(column)
    return columns


def encode_row(columns: list[dict], values: list) -> bytes:
    fixed_boundary, variable_count = catalog._row_layout(columns)
    fixed = bytearray(fixed_boundary - 1)
    present = 0
    variable_data = b""
    boundaries = [fixed_boundary]
    for column, value in zip(columns, values):
        if column["type"] == "Boolean":
            if value:
                present |= 1 << column["ordinal"]
            continue
        if value is None:
            if column["storage"] == "variable":
                boundaries.append(fixed_boundary + len(variable_data))
            continue
        present |= 1 << column["ordinal"]
        if column["type"] == "Long":
            raw = le32(value)
        elif column["type"] == "Integer":
            raw = le16(value)
        elif column["type"] == "Byte":
            raw = bytes([value])
        elif column["type"] == "Date":
            raw = struct.pack("<d", value)
        elif column["type"] == "Text":
            raw = value.encode("cp1252")
        else:
            raw = value
        if column["storage"] == "fixed":
            start = column["fixed_offset"]
            fixed[start : start + column["size"]] = raw
        else:
            variable_data += raw
            boundaries.append(fixed_boundary + len(variable_data))
    row = bytes([len(columns)]) + bytes(fixed) + variable_data
    if variable_count:
        row += bytes(reversed(boundaries)) + bytes([variable_count])
    return row + present.to_bytes((len(columns) + 7) // 8, "little")


def data_page(owner: int | bytes, rows: list[bytes]) -> bytearray:
    image = bytearray(PAGE)
    image[0] = 1
    image[4:8] = owner if isinstance(owner, bytes) else le32(owner)
    image[8:10] = le16(len(rows))
    end = PAGE
    for index, row in enumerate(rows):
        start = end - len(row)
        image[start:end] = row
        image[10 + 2 * index : 12 + 2 * index] = le16(start)
        end = start
    return image


def map_record(pages: set[int]) -> bytes:
    bitmap = bytearray(16)
    for page in pages:
        bitmap[page // 8] |= 1 << (page % 8)
    return b"\x00" + le32(0) + bytes(bitmap)


def definition(
    root: int,
    spec: list[tuple[str, int, int, int]],
    *,
    owned: tuple[int, int],
    available: tuple[int, int],
    row_count: int,
    marker: int = 0x53,
    constant: int = 0,
    indexes: list[dict] | None = None,
) -> bytearray:
    columns = layout(spec)
    indexes = indexes or []
    body = bytearray(43)
    body[0:4] = catalog.DEFINITION_PREFIX
    body[12:16] = le32(row_count)
    body[20] = marker
    body[21:23] = le16(len(columns))
    body[23:25] = le16(sum(1 for column in columns if column["storage"] == "variable"))
    body[25:27] = le16(len(columns))
    body[27:29] = le16(len(indexes))
    body[31:33] = le16(len(indexes))
    body[35:39] = le32(owned[1] | owned[0] << 8)
    body[39:43] = le32(available[1] | available[0] << 8)
    for index in indexes:
        body += bytes(4) + le32(index["entry_count"])
    for column in columns:
        record = bytearray(18)
        record[0] = column["type_code"]
        record[1:3] = le16(column["ordinal"])
        record[3:5] = le16(column["variable_index"])
        record[7:9] = le16(constant)
        record[9:13] = CONTEXT
        record[13] = column["class"]
        record[14:16] = le16(column["fixed_offset"] or 0)
        record[16:18] = le16(column["size"])
        body += record
    for column in columns:
        raw = column["name"].encode("cp1252")
        body += bytes([len(raw)]) + raw
    for index in indexes:
        record = bytearray(39)
        for slot in range(10):
            if slot < len(index["keys"]):
                column, direction = index["keys"][slot]
                record[3 * slot : 3 * slot + 2] = le16(column)
                record[3 * slot + 2] = direction
            else:
                record[3 * slot : 3 * slot + 2] = b"\xff\xff"
        record[30] = index["map"][1]
        record[31:34] = index["map"][0].to_bytes(3, "little")
        record[34:38] = le32(index["root"])
        record[38] = index["flags"]
        body += record
    for position, index in enumerate(indexes):
        record = bytearray(20)
        record[0:4] = le32(position)
        record[4:8] = le32(position)
        record[9:13] = b"\xff\xff\xff\xff"
        record[17:19] = b"\x04\x04"
        record[19] = index["class"]
        body += record
    for index in indexes:
        raw = index["name"].encode("cp1252")
        body += bytes([len(raw)]) + raw
    body += b"\xff\xff"
    body[8:12] = le32(len(body))
    image = bytearray(PAGE)
    image[: len(body)] = body
    return image


def build_image(replica: int, with_alpha: bool, *, marker: int = 0x53, stray: bool = False) -> bytes:
    stamp = 46000.0 + replica / 1000
    objects = [
        (TABLES_ID, CONTAINERS_PARENT, "Tables", 3, SYSTEM),
        (DATABASES_ID, CONTAINERS_PARENT, "Databases", 3, SYSTEM),
        (RELATIONSHIPS_ID, CONTAINERS_PARENT, "Relationships", 3, SYSTEM),
        (MSYSDB_ID, DATABASES_ID, "MSysDb", 2, SYSTEM),
        (2, TABLES_ID, "MSysObjects", 1, SYSTEM),
        (3, TABLES_ID, "MSysACEs", 1, SYSTEM),
        (4, TABLES_ID, "MSysQueries", 1, SYSTEM),
        (5, TABLES_ID, "MSysRelationships", 1, SYSTEM),
    ]
    if with_alpha:
        objects.append((ALPHA_ROOT, TABLES_ID, "Alpha", 1, 0))
    object_columns = layout(OBJECTS_COLUMNS)
    object_rows = [
        encode_row(
            object_columns,
            [ident, parent, name, kind, stamp, stamp, b"\x03\x01", flags, b"\x2b" + bytes(11) if name == "Alpha" else None],
        )
        for ident, parent, name, kind, flags in objects
    ]
    ace_columns = layout(ACES_COLUMNS)
    ace_rows = [encode_row(ace_columns, [ident, b"\x03\x01", 393216, ident == TABLES_ID]) for ident, *_ in objects]
    count = len(objects)
    pages = [bytearray(PAGE) for _ in range(11)]
    pages[0][1538] = 1 if with_alpha else 0
    pages[1] = data_page(1, [map_record(set()), bytes(20)])
    pages[2] = definition(
        2,
        OBJECTS_COLUMNS,
        owned=(6, 0),
        available=(6, 1),
        row_count=count,
        marker=marker,
        indexes=[{"keys": [(0, 1)], "map": (6, 2), "root": 7, "flags": 1, "entry_count": count, "name": "Id", "class": 1}],
    )
    pages[3] = definition(3, ACES_COLUMNS, owned=(6, 3), available=(6, 4), row_count=count)
    pages[4] = definition(4, QUERIES_COLUMNS, owned=(6, 5), available=(6, 6), row_count=0)
    pages[5] = definition(5, RELATIONSHIPS_COLUMNS, owned=(6, 7), available=(6, 8), row_count=0)
    pages[6] = data_page(
        0,
        [
            map_record({8}),
            map_record({8}),
            map_record({7}),
            map_record({9}),
            map_record({9}),
            map_record(set()),
            map_record(set()),
            map_record(set()),
            map_record(set()),
        ],
    )
    pages[7][0] = 4
    pages[7][4:8] = le32(2)
    pages[8] = data_page(2, object_rows)
    pages[9] = data_page(3, ace_rows)
    pages[10] = data_page(0, [bytes(20)])
    if stray:
        pages[10][100] = 0x5A
    if with_alpha:
        pages.append(
            definition(ALPHA_ROOT, ALPHA_COLUMNS, owned=(12, 0), available=(12, 1), row_count=0, marker=0x4E, constant=1)
        )
        pages.append(data_page(0, [map_record(set()), map_record(set())]))
    return b"".join(bytes(page) for page in pages)


def dao_metadata(replica: int, with_alpha: bool, *, extra_table: str | None = None) -> dict:
    stamp = 46000.0 + replica / 1000
    system_names = ["MSysACEs", "MSysObjects", "MSysQueries", "MSysRelationships"]
    tabledefs = [
        {"name": name, "attributes": SYSTEM, "date_created": None, "last_updated": None, "error": "refused"}
        for name in system_names
    ]
    documents = [{"name": name, "owner": "admin", "error": None} for name in system_names]
    if with_alpha:
        tabledefs.append({"name": "Alpha", "attributes": 0, "date_created": stamp, "last_updated": stamp, "error": None})
        documents.append({"name": "Alpha", "owner": "admin", "error": None})
    if extra_table:
        tabledefs.append({"name": extra_table, "attributes": 0, "date_created": None, "last_updated": None, "error": None})
    return {
        "tabledefs": tabledefs,
        "containers": [
            {"name": "Databases", "owner": "admin", "error": None, "documents": [{"name": "MSysDb", "owner": "admin", "error": None}]},
            {"name": "Relationships", "owner": "admin", "error": None, "documents": []},
            {"name": "Tables", "owner": "admin", "error": None, "documents": documents},
        ],
        "querydefs": [],
        "relations": [],
        "properties": [{"name": "Version", "type": 10, "value": "3.0", "error": None}],
    }


def write_checkpoint(root: Path, replica: int, name: str, data: bytes, dao: dict) -> dict:
    database = f"system-catalog-r{replica}-{name}.mdb"
    (root / database).write_bytes(data)
    return {
        "name": name,
        "database": database,
        "size": len(data),
        "sha256": digest(data),
        "sha256_after_metadata": digest(data),
        "dao": dao,
    }


def synthetic_document(root: Path, *, markers: dict[int, int] | None = None, stray: bool = False) -> dict:
    replicas = []
    for replica in (1, 2, 3):
        marker = (markers or {}).get(replica, 0x53)
        checkpoints = []
        for name in catalog.CHECKPOINT_NAMES:
            with_alpha = name != "empty"
            image = build_image(replica, with_alpha, marker=marker, stray=stray and with_alpha)
            checkpoints.append(write_checkpoint(root, replica, name, image, dao_metadata(replica, with_alpha)))
        replicas.append({"replica": replica, "status": "pass", "error": None, "checkpoints": checkpoints})
    return {
        "document_type": catalog.DOCUMENT_TYPE,
        "development_only": True,
        "plan_sha256": PLAN_SHA256,
        "run_id": "synthetic",
        "status": "pass",
        "replicas": replicas,
    }


def write_job(root: Path, document: dict) -> Path:
    path = root / "system-catalog-job-result.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


class SystemCatalogTests(unittest.TestCase):
    def evaluate(self, root: Path, document: dict) -> dict:
        return catalog.evaluate(write_job(root, document), PLAN_SHA256, root / "report.json")

    def assert_rejected(self, root: Path, document: dict, pattern: str) -> None:
        with self.assertRaisesRegex(catalog.AnalysisError, pattern):
            self.evaluate(root, document)
        self.assertFalse((root / "report.json").exists())

    def test_accepts_and_emits_deterministic_canonical_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            job = write_job(root, document)
            first = root / "first.json"
            second = root / "second.json"
            report = catalog.evaluate(job, PLAN_SHA256, first)
            catalog.evaluate(job, PLAN_SHA256, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.read_bytes(), catalog.canonical_bytes(report))
        self.assertEqual(report["status"], "accepted")
        self.assertEqual(report["document_type"], "system_catalog_report")
        self.assertFalse(report["compatibility_claim"])
        self.assertFalse(report["support_movement"])
        questions = report["questions"]
        self.assertEqual({name: question["status"] for name, question in questions.items()}, {name: "answered" for name in catalog.QUESTION_NAMES})
        tables = questions["Q1"]["tables"]
        self.assertEqual([table["name"] for table in tables], ["MSysObjects", "MSysACEs", "MSysQueries", "MSysRelationships"])
        self.assertEqual([column["name"] for column in tables[0]["columns"]], [name for name, *_ in OBJECTS_COLUMNS])
        self.assertEqual(tables[0]["marker"], 0x53)
        self.assertEqual(tables[0]["row_counts"], {"empty": [8, 8, 8], **{name: [9, 9, 9] for name in catalog.CHECKPOINT_NAMES[1:]}})
        self.assertEqual(tables[0]["physical_indexes"][0]["entry_counts"]["table1"], [9, 9, 9])
        self.assertEqual(tables[0]["physical_indexes"][0]["keys"], [{"column": 0, "direction": 1}])
        roles = {page["page"]: page["role"] for page in questions["Q2"]["checkpoints"]["table1"]["pages"]}
        self.assertEqual(roles[7], "index_root")
        self.assertEqual(roles[8], "data")
        self.assertEqual(roles[10], "unassigned")
        self.assertEqual(roles[11], "definition_root")
        self.assertEqual(roles[12], "map_rows")
        self.assertEqual(questions["Q2"]["checkpoints"]["empty"]["unassigned_pages"], [10])
        rows = questions["Q3"]["checkpoints"]["table1"]
        self.assertEqual(len(rows["msys_objects"]["rows"]), 9)
        self.assertEqual(rows["msys_objects"]["correlations"]["tabledef:Alpha"], {"date_created_match": True, "last_updated_match": True, "matches": 1, "parent_matches_container": None, "row_id": ALPHA_ROOT})
        self.assertEqual(rows["msys_objects"]["rows"][8]["present_variable_columns"], ["Name", "Owner", "LvProp"])
        self.assertEqual(rows["class_observations"]["user_table"], [{"flags": 0, "parent_id": TABLES_ID, "type": 1}])
        self.assertEqual(rows["MSysACEs"]["rows"][0], {"ACM": 393216, "FInheritable": True, "ObjectId": TABLES_ID, "SID": "0301"})
        self.assertFalse(rows["MSysACEs"]["rows"][1]["FInheritable"])
        self.assertEqual(report["replicas"][0]["dao_errors"], ["refused"])

    def test_q4_attributes_row_insertion_and_reports_stray_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = self.evaluate(root, synthetic_document(root, stray=True))
        q4 = report["questions"]["Q4"]
        self.assertEqual(q4["status"], "answered")
        transition = q4["transitions"][0]
        self.assertEqual((transition["from"], transition["to"]), ("empty", "table1"))
        self.assertEqual(transition["unattributed_count"], 1)
        attributions = {(item["page"], item["attribution"], item["owner"]) for item in transition["ranges"]}
        self.assertIn((0, "page0_counter", None), attributions)
        self.assertIn((2, "definition_row_count", "table 2 MSysObjects"), attributions)
        self.assertIn((2, "index_entry_count", "table 2 MSysObjects index 0"), attributions)
        self.assertIn((3, "definition_row_count", "table 3 MSysACEs"), attributions)
        self.assertIn((8, "row_directory", "table 2 MSysObjects"), attributions)
        self.assertIn((8, "row_bytes", "table 2 MSysObjects row 8"), attributions)
        self.assertIn((9, "row_bytes", "table 3 MSysACEs row 8"), attributions)
        self.assertIn((10, "unattributed", None), attributions)
        self.assertIn((11, "appended_page", "definition_root: table 11 Alpha"), attributions)
        self.assertIn((12, "appended_page", "map_rows: table 11 Alpha"), attributions)
        stray = next(item for item in transition["ranges"] if item["attribution"] == "unattributed")
        self.assertEqual((stray["start"], stray["end"]), (10 * PAGE + 100, 10 * PAGE + 101))
        self.assertEqual(q4["transitions"][1]["ranges"], [])

    def test_marker_disagreement_is_q1_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = self.evaluate(root, synthetic_document(root, markers={2: 0x4E}))
        self.assertEqual(report["status"], "no_outcome")
        q1 = report["questions"]["Q1"]
        self.assertEqual(q1["status"], "no_outcome")
        self.assertIn("replica 2 checkpoint empty table 2 differs at definition.marker", q1["reason"])
        self.assertEqual(report["questions"]["Q2"]["status"], "answered")

    def test_unmatched_dao_name_is_q3_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            for replica in document["replicas"]:
                replica["checkpoints"][0]["dao"] = dao_metadata(replica["replica"], False, extra_table="Ghost")
            report = self.evaluate(root, document)
        q3 = report["questions"]["Q3"]
        self.assertEqual(q3["status"], "no_outcome")
        self.assertEqual(q3["reason"], "replica 1 checkpoint empty tabledef:Ghost matched 0 rows")
        self.assertEqual(q3["checkpoints"]["empty"]["msys_objects"]["correlations"]["tabledef:Ghost"]["matches"], 0)

    def test_failed_replica_with_checkpoint_prefix_is_no_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            failed = document["replicas"][1]
            failed["status"] = "fail"
            failed["error"] = "stopped after table1"
            failed["checkpoints"] = failed["checkpoints"][:2]
            report = self.evaluate(root, document)
        self.assertEqual(report["status"], "no_outcome")
        self.assertEqual(report["checkpoints_compared"], ["empty", "table1"])
        for question in report["questions"].values():
            self.assertEqual(question["status"], "no_outcome")
            self.assertEqual(question["reason"], "replica 2 failed: stopped after table1")
        self.assertEqual(len(report["questions"]["Q1"]["tables"]), 4)
        self.assertEqual(report["replicas"][1]["status"], "fail")

    def test_metadata_reopen_repair_is_recorded_not_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["replicas"][2]["checkpoints"][1]["sha256_after_metadata"] = "c" * 64
            report = self.evaluate(root, document)
        self.assertEqual(report["status"], "accepted")
        self.assertTrue(report["replicas"][2]["checkpoints"][1]["metadata_open_repaired"])
        self.assertFalse(report["replicas"][2]["checkpoints"][0]["metadata_open_repaired"])

    def test_rejects_wrong_plan_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = write_job(root, synthetic_document(root))
            with self.assertRaisesRegex(catalog.AnalysisError, "plan digest differs"):
                catalog.evaluate(job, "d" * 64, root / "report.json")
            self.assertFalse((root / "report.json").exists())

    def test_rejects_wrong_file_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["replicas"][0]["checkpoints"][3]["sha256"] = "e" * 64
            self.assert_rejected(root, document, "digest differs from metadata")

    def test_rejects_oversize_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            checkpoint = document["replicas"][0]["checkpoints"][0]
            data = (root / checkpoint["database"]).read_bytes() + bytes(PAGE * 54)
            (root / checkpoint["database"]).write_bytes(data)
            checkpoint["size"] = len(data)
            checkpoint["sha256"] = checkpoint["sha256_after_metadata"] = digest(data)
            self.assert_rejected(root, document, r"size must be an integer between")

    def test_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            text = json.dumps(document)
            path = root / "system-catalog-job-result.json"
            path.write_text(text[:-1] + ', "status": "pass"}', encoding="utf-8")
            with self.assertRaisesRegex(catalog.AnalysisError, "duplicate JSON field"):
                catalog.evaluate(path, PLAN_SHA256, root / "report.json")

    def test_rejects_malformed_checkpoint_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            checkpoints = document["replicas"][1]["checkpoints"]
            checkpoints[0], checkpoints[1] = checkpoints[1], checkpoints[0]
            self.assert_rejected(root, document, "out of preregistered order")

    def test_rejects_passing_replica_without_every_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            document = synthetic_document(root)
            document["replicas"][0]["checkpoints"] = document["replicas"][0]["checkpoints"][:3]
            self.assert_rejected(root, document, "passed without every checkpoint")

    def test_cli_reports_rejection_on_stderr_without_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job = write_job(root, synthetic_document(root))
            output = root / "cli-report.json"
            self.assertEqual(catalog.main(["--expected-plan-sha256", PLAN_SHA256, "--output", str(output), str(job)]), 0)
            self.assertTrue(output.exists())
            with contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(catalog.main(["--expected-plan-sha256", "f" * 64, "--output", str(root / "none.json"), str(job)]), 1)
            self.assertTrue(stderr.getvalue().startswith("REJECTED: "))
            self.assertFalse((root / "none.json").exists())

    def test_decode_row_matches_exp_0060_mixed_control(self) -> None:
        columns = layout([("Number", 4, 0x03, 4), ("Small", 2, 0x03, 1), ("Text", 10, 0x02, 50)])
        row = bytes.fromhex("0340302010" "2a" "6d69786564" "0b06" "01" "07")
        decoded = catalog._decode_row(row, columns, "control")
        self.assertEqual(decoded["values"], [0x10203040, 0x2A, "mixed"])
        self.assertEqual(decoded["present"], [True, True, True])
        self.assertEqual(encode_row(columns, [0x10203040, 0x2A, "mixed"]), row)

    def test_decode_row_rejects_nonzero_unused_presence_bits(self) -> None:
        columns = layout(ACES_COLUMNS)
        row = bytearray(encode_row(columns, [2, b"\x03\x01", 5, False]))
        row[-1] |= 0x80
        with self.assertRaisesRegex(catalog.DecodeError, "unused presence bits"):
            catalog._decode_row(bytes(row), columns, "row")

    def test_definition_rejects_key_slot_hole(self) -> None:
        image = definition(
            2,
            OBJECTS_COLUMNS,
            owned=(6, 0),
            available=(6, 1),
            row_count=0,
            indexes=[{"keys": [(0, 1)], "map": (6, 2), "root": 7, "flags": 1, "entry_count": 0, "name": "Id", "class": 1}],
        )
        offset = 43 + 8 + 18 * len(OBJECTS_COLUMNS) + sum(1 + len(name) for name, *_ in OBJECTS_COLUMNS)
        image[offset : offset + 2] = b"\xff\xff"
        image[offset + 3 : offset + 5] = le16(1)
        data = bytes(PAGE * 2) + bytes(image)
        with self.assertRaisesRegex(catalog.DecodeError, "hole in its key slots"):
            catalog._definition(data, 2)


if __name__ == "__main__":
    unittest.main()
