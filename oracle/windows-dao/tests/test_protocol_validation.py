import sys
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from protocol_validation import (  # noqa: E402
    ValidationError,
    lint_schema,
    validate_schema_value,
)


class SharedProtocolValidationTests(unittest.TestCase):
    def test_schema_lint_rejects_an_unimplemented_keyword(self):
        with self.assertRaisesRegex(ValidationError, "unsupported schema keywords"):
            lint_schema({"type": "integer", "multipleOf": 2})

    def test_maximum_and_max_items_are_enforced_in_the_main_walk(self):
        schema = {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "maxItems": 1,
                    "items": {"type": "integer", "maximum": 255},
                }
            },
            "required": ["values"],
            "additionalProperties": False,
        }
        lint_schema(schema)
        validate_schema_value({"values": [255]}, schema, schema, "$")
        with self.assertRaisesRegex(ValidationError, "above maximum"):
            validate_schema_value({"values": [256]}, schema, schema, "$")
        with self.assertRaisesRegex(ValidationError, "too many items"):
            validate_schema_value({"values": [1, 2]}, schema, schema, "$")

    def test_prefix_items_are_positional_and_items_false_closes_the_array(self):
        schema = {
            "type": "array",
            "prefixItems": [{"const": "a"}, {"type": "integer"}],
            "items": False,
        }
        lint_schema(schema)
        validate_schema_value(["a", 1], schema, schema, "$")
        validate_schema_value(["a"], schema, schema, "$")
        with self.assertRaisesRegex(ValidationError, "const"):
            validate_schema_value([1, "a"], schema, schema, "$")
        with self.assertRaisesRegex(ValidationError, "beyond prefixItems are forbidden"):
            validate_schema_value(["a", 1, 2], schema, schema, "$")
        with self.assertRaisesRegex(ValidationError, "prefixItems"):
            lint_schema({"type": "array", "items": False})
        with self.assertRaisesRegex(ValidationError, "prefixItems"):
            lint_schema({"type": "array", "prefixItems": []})

    def test_m1_uses_the_shared_public_boundary(self):
        source = (SCRIPTS / "validate_m1_protocol.py").read_text(encoding="utf-8")
        self.assertNotIn("importlib", source)
        self.assertNotIn("_V1", source)
        self.assertNotIn("_walk_schema_constraints", source)
        self.assertIn("SCHEMA_SET.validate(document)", source)


if __name__ == "__main__":
    unittest.main()
