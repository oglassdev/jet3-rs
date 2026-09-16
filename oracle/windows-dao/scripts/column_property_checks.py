"""Decode retained column-property payloads for DAO comparisons."""


import system_catalog as catalog

PAGE = 2048


def require(value, message):
    if not value:
        raise ValueError(message)


def map_record(data: bytes, locator: dict, label: str):
    raw = catalog._locator_row(data, locator, label)
    members, references = set(), []

    def add_bits(bitmap, base):
        for offset, byte in enumerate(bitmap):
            for bit in range(8):
                if byte & (1 << bit):
                    members.add(base + 8 * offset + bit)

    if raw[0] == 0:
        require(len(raw) >= 5, label + ": short inline map")
        add_bits(raw[5:], int.from_bytes(raw[1:5], "little"))
    else:
        require(raw[0] == 1 and len(raw) == 133, label + ": bad indirect map")
        references = [int.from_bytes(raw[o : o + 4], "little") for o in range(1, 133, 4)]
        active = [p for p in references if p]
        require(references[: len(active)] == active and len(active) == len(set(active)), label + ": bad references")
        for slot, page in enumerate(references):
            if page:
                image = catalog._page(data, page, label)
                require(image[:4] == b"\x05\x01\x00\x00", label + ": bad bitmap page")
                add_bits(image[4:], 16352 * slot)
    within = {p for p in members if p < len(data) // PAGE}
    return {"raw_hex": raw.hex(), "members": sorted(within), "references": references}, within


def row_layout(raw: bytes, columns: list[dict]):
    count = len(columns)
    require(raw and raw[0] == count, "row column count")
    presence_len = (count + 7) // 8
    presence = raw[-presence_len:]
    fixed = 1 + max(
        (c["fixed_offset"] + c["size"] for c in columns if c["storage"] == "fixed"),
        default=0,
    )
    variable_count = sum(c["storage"] == "variable" for c in columns)
    boundaries = []
    if variable_count:
        position = len(raw) - presence_len - 1
        require(raw[position] == variable_count, "row variable count")
        jump_count = (len(raw) - 1) // 256
        end = position - jump_count - variable_count - 1
        lows = list(reversed(raw[end : end + variable_count + 1]))
        jumps = raw[end + variable_count + 1 : position]
        boundaries = [low + 256 * sum(j != 255 and j <= i for j in jumps) for i, low in enumerate(lows)]
        if variable_count == 255:
            boundaries[-1] = end
        require(boundaries[0] == fixed and boundaries[-1] == end, "row boundaries")
        require(all(a <= b for a, b in zip(boundaries, boundaries[1:])), "ordered row boundaries")
    else:
        require(len(raw) - presence_len == fixed, "fixed row length")
    result = []
    for column in columns:
        ordinal = column["ordinal"]
        present = bool(presence[ordinal // 8] & (1 << (ordinal % 8)))
        if not present:
            field = None
        elif column["storage"] == "fixed":
            start = 1 + column["fixed_offset"]
            field = raw[start : start + column["size"]]
        else:
            index = column["variable_index"]
            field = raw[boundaries[index] : boundaries[index + 1]]
        if not present and column["storage"] == "variable":
            index = column["variable_index"]
            require(boundaries[index] == boundaries[index + 1], "Null variable consumes bytes")
        result.append((present, field))
    return result


def long_payload(data: bytes, field: bytes, owned: set[int], reached: set[tuple[int, int]]):
    require(len(field) >= 12 and field[8:12] == bytes(4), "long-value header")
    word = int.from_bytes(field[:4], "little")
    length, flags = word & 0xFFFFFF, word & 0xFF000000
    header = {"length": length, "flags": flags, "raw_hex": field.hex(), "chain": []}
    if flags == 0x80000000:
        require(field[4:8] == bytes(4) and len(field) == length + 12, "inline long value")
        return field[12:], header
    require(flags in (0, 0x40000000) and len(field) == 12, "external long value")
    page, slot, value = int.from_bytes(field[5:8], "little"), field[4], bytearray()
    while page:
        require(page in owned and (page, slot) not in reached, "owned distinct long-value reference")
        reached.add((page, slot)); header["chain"].append([page, slot])
        image = catalog._page(data, page, "long value")
        require(image[:2] == b"\x01\x01" and image[4:8] == b"LVAL", "long-value page kind")
        entries = catalog._row_directory(image, page)
        require(slot < len(entries), "long-value slot")
        entry = entries[slot]
        require(not entry["hidden"] and not entry["overflow"] and entry["start"] < entry["end"], "live long-value slot")
        fragment = image[entry["start"] : entry["end"]]
        if flags == 0x40000000:
            value.extend(fragment); break
        require(len(fragment) > 4, "nonempty chained fragment")
        value.extend(fragment[4:]); slot, page = fragment[0], int.from_bytes(fragment[1:4], "little")
    require(len(value) == length, "complete long-value content")
    return bytes(value), header


def property_payload(data: bytes, table_name: str):
    definition, pages, _ = catalog._discover_catalog(data)
    name, lvprop = (catalog._ordinal(definition, n) for n in ("Name", "LvProp"))
    group = next(g for g in definition["long_value_maps"] if g["column"] == lvprop)
    _, owned = map_record(data, group["owned"], "catalog LvProp owned")
    for page in pages:
        image = catalog._page(data, page, "catalog")
        for entry in catalog._row_directory(image, page):
            if entry["hidden"]:
                continue
            fields = row_layout(image[entry["start"] : entry["end"]], definition["columns"])
            name_field = fields[name][1]
            if name_field is not None and name_field.decode("cp1252") == table_name:
                require(fields[lvprop][0], "table has LvProp")
                reached = set()
                payload, header = long_payload(data, fields[lvprop][1], owned, reached)
                return payload, header
    raise ValueError("missing property payload for " + table_name)

