#!/usr/bin/env python3
"""Independent EXP-0309 key model and Rust weight-table generator from DAO captures."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

LOCALES = {'general': 1252, 'nordic': 1252, 'spanish': 1252, 'dutch': 1252,
           'cyrillic': 1251, 'greek': 1253}
CONTEXTS = dict(zip(('0904e404','1d04e404','0a04e404','1304e404','1904e304','0804e504'), LOCALES))


def weights(keys):
    primary, secondary = [0] * 256, [2] * 256
    for byte in range(256):
        key = bytes.fromhex(keys[bytes([byte]).hex()])[1:]
        values = []
        for value in key:
            if value < 16:
                break
            values.append(value)
        if byte == 32:
            values = [bytes.fromhex(keys['2068'])[1]]
        primary[byte] = int.from_bytes(bytes(values), 'big')
        suffix = key[len(values):] if byte != 32 else b'\0'
        accents = [n for value in suffix for n in (value >> 4, value & 15) if n]
        assert len(accents) <= 1
        if accents:
            secondary[byte] = accents[0]
    accented = {primary[b] for b in range(256) if secondary[b] != 2}
    return primary, secondary, accented


class Model:
    def __init__(self, captures):
        self.tables = {name: weights(captures[name]['keys']) for name in LOCALES}

    def encode(self, name, value):
        primary, secondary, accented = self.tables[name]
        accented = accented | ({0x70} if name == 'spanish' else set())
        value = value.rstrip(b' ')
        result, accents, index = bytearray(b'\x7f'), [], 0
        while index < len(value):
            pair = value[index:index + 2].lower()
            if name == 'spanish' and pair in (b'ch', b'll'):
                result.append(0x63 if pair == b'ch' else 0x6e)
                if pair == b'ch':
                    accents.append(2)
                index += 2
                continue
            byte = value[index]
            index += 1
            for weight in primary[byte].to_bytes(2, 'big'):
                if weight:
                    result.append(weight)
                    if weight in accented:
                        accents.append(secondary[byte])
        while accents and accents[-1] == 2:
            accents.pop()
        nibbles = [0, *accents, 0]
        if len(nibbles) % 2:
            nibbles.append(0)
        result.extend(a * 16 + b for a, b in zip(nibbles[::2], nibbles[1::2]))
        return bytes(result)

    def rust(self):
        output = ['//! EXP-0309 weights extracted from complete native single-byte and context inventories.',
                  'use super::Weights;', '']
        for name, cp in LOCALES.items():
            if name == 'general':
                continue
            primary, secondary, accented = self.tables[name]
            accented = accented | ({0x70} if name == 'spanish' else set())
            masks, costs, expansions = [0] * 256, [0] * 256, []
            for byte in range(256):
                try:
                    bytes([byte]).decode('cp' + str(cp))
                except UnicodeDecodeError:
                    continue
                values = [v for v in primary[byte].to_bytes(2, 'big') if v]
                if len(values) == 2 and values not in expansions:
                    expansions.append(values)
                for weight in values:
                    costs[weight] = 1
                    if weight in accented:
                        masks[weight] |= 1 << secondary[byte]
            if name == 'spanish':
                costs[0x63] = costs[0x6e] = 2
                masks[0x63] = 1 << 2
            output.append(f'pub(super) const {name.upper()}: Weights = Weights {{')
            for field, values, width in [('primary', primary, 4), ('secondary', secondary, 1),
                                         ('accents', masks, 4), ('source_cost', costs, 1)]:
                output.append(f'    {field}: [' + ', '.join(f'0x{v:0{width}x}' for v in values) + '],')
            output.append('    expansions: &[' + ', '.join(f'[0x{a:02x}, 0x{b:02x}]' for a,b in expansions) + '],')
            output.extend(['};', ''])
        return '\n'.join(output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--keys', type=Path, required=True)
    p.add_argument('--rust', type=Path)
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    data = json.loads(args.keys.read_text())
    model = Model(data)
    import numeric_index_mutation as numeric
    results = []
    for name in LOCALES:
        failures = []
        for value, key in data[name]['keys'].items():
            actual = numeric.shorten_key(model.encode(name, bytes.fromhex(value))).hex()
            if actual != key:
                failures.append(dict(input=value, native=key, actual=actual))
        results.append(dict(name=name, keys=len(data[name]['keys']), failures=failures))
    args.report.write_text(json.dumps(results, indent=2) + '\n')
    if any(r['failures'] for r in results):
        raise SystemExit('Native key model mismatch; see report')
    if args.rust:
        args.rust.write_text(model.rust())
    print('matched', sum(r['keys'] for r in results), 'native keys')

if __name__ == '__main__':
    main()
