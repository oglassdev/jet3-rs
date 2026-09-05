import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import autoincrement_reanalysis as secondary


class SecondaryTests(unittest.TestCase):
    def test_limit_is_private_and_other_decoder_checks_remain(self):
        module = secondary.analyzer()
        self.assertEqual(secondary.original.catalog.MAX_ROWS_PER_PAGE, 64)
        constants = lambda m: {k: v for k, v in vars(m).items() if k.isupper() and isinstance(v, (str, int))}
        expected = constants(secondary.original.catalog)
        expected['MAX_ROWS_PER_PAGE'] = 256
        self.assertEqual(constants(module.catalog), expected)
        self.assertIsNot(module.catalog, secondary.original.catalog)
        # Synthetic row directory: the old count gate differs, but invalid offsets still fail.
        data = bytearray(2048)
        data[0] = 1
        data[8:10] = (169).to_bytes(2, 'little')
        with self.assertRaisesRegex(secondary.original.catalog.DecodeError, 'bound of 64'):
            secondary.original.catalog._row_directory(bytes(data), 0)
        with self.assertRaises(module.catalog.DecodeError) as error:
            module.catalog._row_directory(bytes(data), 0)
        self.assertNotIn('bound of 64', str(error.exception))

    def test_pins_refuse_changed_input_or_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = root / 'plan.json'
            source = root / 'source.py'
            source.write_text('original')
            report = root / 'report.json'
            report.write_text('{"outcome":"no_outcome"}')
            document = {'experiment_id': 'autoincrement-reanalysis',
                        'inputs': {'source.py': secondary.original.digest(source)},
                        'artifacts': {'report.json': secondary.original.identity(report)}}
            plan.write_text(json.dumps(document))
            with patch.object(secondary, 'ROOT', root), patch.object(secondary, 'PLAN', plan), \
                    patch.object(secondary.original, 'verify_inputs'), patch.object(secondary.subprocess, 'run') as run:
                run.return_value.stdout = plan.read_bytes()
                secondary.verify(root)
                report.write_text('changed')
                with self.assertRaisesRegex(ValueError, 'Artifact pin mismatch'):
                    secondary.verify(root)
                source.write_text('changed')
                with self.assertRaisesRegex(ValueError, 'Input pin mismatch'):
                    secondary.verify(root)

    def test_output_cannot_overwrite_originals(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(secondary, 'verify', return_value={}):
            outbox = Path(directory)
            original = outbox / 'report.json'
            original.write_text('original no_outcome')
            with self.assertRaisesRegex(ValueError, 'outside'):
                secondary.analyze(outbox, original)
            nested = outbox / 'nested'
            nested.mkdir()
            with self.assertRaisesRegex(ValueError, 'outside'):
                secondary.analyze(outbox, nested / 'report.json')
            self.assertFalse((nested / 'report.json').exists())
            self.assertEqual(original.read_text(), 'original no_outcome')


if __name__ == '__main__':
    unittest.main()
