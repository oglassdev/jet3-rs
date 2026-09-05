import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import row_delete_reanalysis as secondary


class SecondaryTests(unittest.TestCase):
    def image(self):
        raw = bytearray(2048); raw[0] = 1; raw[8:10] = (1).to_bytes(2, 'little')
        raw[10:12] = (2040).to_bytes(2, 'little'); raw[2040:] = b'abcdefgh'
        return dict(hex=raw.hex(), directory=[dict(row=0, start=2040, end=2048, raw_hex=raw[2040:].hex(), raw_word=2040)])

    def signature(self, image, changed=None):
        checks = {name: dict(row_count=1, rows=[]) for name in ('before', 'deleted', 'inserted')}
        state = dict(image=image, owned=True, available=True, globally_free=False)
        movement = dict(tracked_data_pages=[dict(before=state, after=dict(state, image=changed or image))],
            row_count_before=1, row_count_after=1, page_count_before=24, page_count_after=24,
            global_free_added=[], global_free_removed=[])
        return secondary.signature(checks, [movement])

    def test_only_unchanged_unused_bytes_are_excluded(self):
        image = self.image(); other = copy.deepcopy(image)
        raw = bytearray.fromhex(other['hex']); raw[100] = 99; other['hex'] = raw.hex()
        self.assertEqual(self.signature(image), self.signature(other))
        self.assertNotEqual(self.signature(image), self.signature(image, other))
        raw[2] = 3; other['hex'] = raw.hex()
        self.assertNotEqual(self.signature(image), self.signature(other))
        bad = self.image(); bad['directory'][0]['start'] = 11
        with self.assertRaisesRegex(ValueError, 'boundary'): self.signature(bad)
        bad = self.image(); bad['directory'][0]['raw_hex'] = '00'
        with self.assertRaisesRegex(ValueError, 'row bytes'): self.signature(bad)

    def test_pins_and_output_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root / 'source'; source.mkdir()
            artifact = source / 'report.json'; artifact.write_text('original')
            code = root / 'code.py'; code.write_text('code')
            plan = root / 'plan.json'
            plan.write_text(json.dumps(dict(inputs={'code.py': secondary.original.identity(code)},
                artifacts={'report.json': secondary.original.identity(artifact)})))
            with patch.object(secondary, 'ROOT', root), patch.object(secondary, 'PLAN', plan):
                secondary.verify(source)
                code.write_text('changed')
                with self.assertRaisesRegex(ValueError, 'Input pin'): secondary.verify(source)
                code.write_text('code'); artifact.write_text('changed')
                with self.assertRaisesRegex(ValueError, 'Artifact pin'): secondary.verify(source)
            nested = source / 'nested'; nested.mkdir()
            for output in (artifact, nested / 'new.json'):
                with self.assertRaisesRegex(ValueError, 'outside source'): secondary.analyze(source, output)
            existing = root / 'existing.json'; existing.write_text('preserve')
            with self.assertRaisesRegex(ValueError, 'already exists'): secondary.analyze(source, existing)
            self.assertEqual(existing.read_text(), 'preserve')


if __name__ == '__main__': unittest.main()
