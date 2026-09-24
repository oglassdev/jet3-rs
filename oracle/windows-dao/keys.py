"""Independent index-key models derived from native DAO observations.

Scalar components follow EXP-0062/0148/0150 and EXP-0243..0246, General CP1252 Text
follows EXP-0248, key shortening follows EXP-0245/0248, and the six single-byte
locale collations follow EXP-0309.
"""

from __future__ import annotations

import math
import struct

SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 4, 7: 8, 8: 8, 9: 255, 10: 255, 15: 16}

# EXP-0248: General CP1252 primary weights (hex) and explicit secondary nibbles
# (`:a,b`) per byte 0x00..0xff; `-` marks the five bytes undefined in CP1252.
GENERAL_TEXT = """
    10 10 10 10 10 10 10 10 10 11 10 10 10 10 10 10
    10 10 10 10 10 10 10 10 10 10 10 10 10 10 10 10
    11 12 13 14 15 16 17 18 19 1a 1b 1c 1d 1e 1f 20
    56 57 58 59 5a 5b 5c 5d 5e 5f 21 22 23 24 25 26
    27 60 61 62 64 66 67 68 69 6a 6b 6c 6d 6f 70 72
    73 74 75 76 77 78 7a 7b 7c 7d 7e 28 29 2a 2b 2c
    2d 60 61 62 64 66 67 68 69 6a 6b 6c 6d 6f 70 72
    73 74 75 76 77 78 7a 7b 7c 7d 7e 2e 2f 30 31 10
    10 - 18 32 13 33 34 35 36 37 76:10 18 7266 - 10 -
    - 18 18 13 13 38 1e 1e 39 3a 76:10 18 7266 - 10 7d:6
    11 3b 3c 3d 3e 3f 40 41 42 43 44 13 45 1e 46 47
    48 49 58 59 4a 4b 4c 4d 4e 57 4f 13 50 51 52 53
    60:3 60:4 60:5 60:7 60:6 60:8 6066 62:9 66:3 66:4 66:5 66:6 6a:3 6a:4 6a:5 6a:6
    65 70:7 72:3 72:4 72:5 72:7 72:6 54 81 78:3 78:4 78:5 78:6 7d:4 7f 7676
    60:3 60:4 60:5 60:7 60:6 60:8 6066 62:9 66:3 66:4 66:5 66:6 6a:3 6a:4 6a:5 6a:6
    65 70:7 72:3 72:4 72:5 72:7 72:6 55 81 78:3 78:4 78:5 78:6 7d:4 7f 7d:6
"""
ACCENTED = {96, 98, 102, 106, 112, 114, 118, 120, 125}


def _general_mapping():
    mapping = {}
    for byte, entry in enumerate(GENERAL_TEXT.split()):
        if entry == '-':
            continue
        primary, _, accents = entry.partition(':')
        mapping[byte] = (bytes.fromhex(primary), [int(a) for a in accents.split(',')] if accents else [])
    return mapping


MAPPING = _general_mapping()


class ModelError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise ModelError(message)


def _secondary(primary, secondary):
    while secondary and secondary[-1] == 2:
        secondary.pop()
    nibbles = [0, *secondary, 0]
    if len(nibbles) % 2:
        nibbles.append(0)
    return b'\x7f' + bytes(primary) + bytes(a * 16 + b for a, b in zip(nibbles[::2], nibbles[1::2]))


def general_text(raw: bytes, descending: bool) -> bytes:
    primary, secondary = bytearray(), []
    for byte in raw.rstrip(b' '):
        part, accents = MAPPING[byte]
        primary.extend(part)
        secondary.extend(accents or [2 for weight in part if weight in ACCENTED])
    data = _secondary(primary, secondary)
    return bytes(b ^ 255 for b in data) if descending else data


def component(value, kind: int, descending: bool, text=general_text) -> bytes:
    """One directed key component; Text values are raw bytes, Binary/GUID hex strings."""
    mask = 255 if descending else 0
    if value is None:
        _require(kind != 1, 'Boolean null is outside the admitted input')
        return bytes([0 ^ mask])
    if kind == 10:
        return text(value, descending)
    if kind in (9, 15):
        raw = bytes.fromhex(value)
        _require((0 < len(raw) <= 255) if kind == 9 else len(raw) == 16, 'Binary/GUID length')
        result = bytearray([127 ^ mask])
        for offset in range(0, len(raw), 8):
            chunk = raw[offset:offset + 8]
            result.extend(b ^ mask for b in chunk + bytes(8 - len(chunk)))
            result.append(9 if offset + 8 < len(raw) else len(chunk) ^ mask)
        return bytes(result)
    if kind == 1:
        _require(type(value) is bool, 'Boolean model type')
        result = b'\x7f' + bytes([0 if value else 255])
    else:
        size = SIZES[kind]
        if kind in (6, 7, 8):
            _require(math.isfinite(value), 'Excluded floating value')
            bits = int.from_bytes(struct.pack('>f' if kind == 6 else '>d', value), 'big')
            sign = 1 << (size * 8 - 1)
            bits = (bits ^ ((1 << (size * 8)) - 1)) if bits & sign else bits ^ sign
        else:
            _require(type(value) is int, 'Integer or scaled Currency model type')
            bits = int.from_bytes(value.to_bytes(size, 'big', signed=kind != 2), 'big')
            if kind != 2:
                bits ^= 1 << (size * 8 - 1)
        result = b'\x7f' + bits.to_bytes(size, 'big')
    return bytes(b ^ mask for b in result)


def shorten(encoded: bytes) -> bytes:
    """EXP-0245/0248: keys over 255 bytes keep 253 bytes and a CRC of the rest."""
    if len(encoded) > 255:
        state = 0
        for byte in encoded[253:]:
            for _ in range(8):
                state = ((state << 1) ^ (0x8005 if state & 0x8000 else 0)) & 65535
            state ^= byte
        encoded = encoded[:253] + state.to_bytes(2, 'little')
    return encoded


class LocaleModel:
    """EXP-0309 weights read from native single-byte key captures.

    `captures` maps each locale to {'keys': {value hex: native key hex}} and must
    contain every single byte and the two-byte value 0x2068.
    """

    CONTEXTS = {'0904e404': 'general', '1d04e404': 'nordic', '0a04e404': 'spanish',
                '1304e404': 'dutch', '1904e304': 'cyrillic', '0804e504': 'greek'}

    def __init__(self, captures):
        self.tables = {name: self._weights(captures[name]['keys']) for name in self.CONTEXTS.values()}

    @staticmethod
    def _weights(keys):
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
            _require(len(accents) <= 1, 'one secondary weight per byte')
            if accents:
                secondary[byte] = accents[0]
        accented = {primary[b] for b in range(256) if secondary[b] != 2}
        return primary, secondary, accented

    def encode(self, name: str, value: bytes) -> bytes:
        primary, secondary, accented = self.tables[name]
        accented = accented | ({0x70} if name == 'spanish' else set())
        value = value.rstrip(b' ')
        weights, accents, index = bytearray(), [], 0
        while index < len(value):
            pair = value[index:index + 2].lower()
            if name == 'spanish' and pair in (b'ch', b'll'):
                weights.append(0x63 if pair == b'ch' else 0x6e)
                if pair == b'ch':
                    accents.append(2)
                index += 2
                continue
            byte = value[index]
            index += 1
            for weight in primary[byte].to_bytes(2, 'big'):
                if weight:
                    weights.append(weight)
                    if weight in accented:
                        accents.append(secondary[byte])
        return _secondary(weights, accents)

    def text(self, context_hex: str):
        name = self.CONTEXTS[context_hex]

        def encode(raw, descending):
            data = self.encode(name, raw)
            return bytes(b ^ 255 for b in data) if descending else data
        return encode
