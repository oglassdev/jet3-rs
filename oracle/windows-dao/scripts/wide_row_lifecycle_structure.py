"""EXP-0257/0258 canonical wide rows and complete independent payload/map checks."""
from index_tree_mutation import require, tables
import numeric_index_mutation_structure as indexes
from allocation_lifecycle_structure import map_record

catalog = indexes.catalog
catalog.MAX_COLUMNS = 255


def layout(raw, columns):
    count = raw[0]; require(count == len(columns), 'Current candidate physical column count')
    presence_length = (count + 7) // 8; presence = raw[-presence_length:]
    variables = sum(c['storage'] == 'variable' for c in columns)
    fixed = 1 + max((c['fixed_offset'] + c['size'] for c in columns if c['storage'] == 'fixed'), default=0)
    jumps = b''; boundaries = []
    if variables:
        position = len(raw) - presence_length - 1
        require(raw[position] == variables, 'Exact stored variable count')
        jump_count = (len(raw) - 1) // 256
        end = position - jump_count - variables - 1
        require(fixed <= end and (jump_count == 0 or len(raw) - 1 > 256 * jump_count), 'Minimal jump framing')
        lows = list(reversed(raw[end:end + variables + 1])); jumps = raw[end + variables + 1:position]
        boundaries = [low + 256 * sum(j != 255 and j <= i for j in jumps) for i, low in enumerate(lows)]
        if variables == 255: boundaries[-1] = end
        require(boundaries[0] == fixed and boundaries[-1] == end and lows[-1] == end % 256 and
                all(a <= b for a,b in zip(boundaries,boundaries[1:])), 'Exact ordered full row boundaries')
        expected = [next((i for i,b in enumerate(boundaries) if b >= 256*k),255) for k in range(jump_count,0,-1)]
        require(list(jumps) == expected, 'Every canonical descending threshold jump')
    else:
        end = len(raw) - presence_length; require(end == fixed, 'Exact fixed-only body')
    values = []
    for c in columns:
        ordinal = c['ordinal']; present = bool(presence[ordinal // 8] & 1 << (ordinal % 8))
        if not present: field = None
        elif c['storage'] == 'fixed':
            start = 1 + c['fixed_offset']; field = raw[start:start + c['size']]
        else:
            i = c['variable_index']; field = raw[boundaries[i]:boundaries[i+1]]
        if not present and c['storage'] == 'variable':
            i = c['variable_index']; require(boundaries[i] == boundaries[i+1], 'Null variable occupies no bytes')
        values.append(field)
    return values, dict(length=len(raw), columns=count, variables=variables, fixed=fixed, data_end=end,
                        boundaries=boundaries, jumps=list(jumps), presence=presence.hex())


def payload(data, field, owned, reached):
    require(len(field) >= 12, 'Complete long-value header')
    word = int.from_bytes(field[:4], 'little'); length = word & 0xffffff; flags = word & 0xff000000
    require(field[8:12] == bytes(4) and length <= 4096, 'Finite payload header')
    if flags == 0x80000000:
        require(field[4:8] == bytes(4) and len(field) == length + 12, 'Exact inline payload length')
        return field[12:]
    require(flags in (0,0x40000000) and len(field) == 12, 'External payload reference')
    page,slot = int.from_bytes(field[5:8],'little'),field[4]; result = bytearray()
    while page:
        require(page in owned and (page,slot) not in reached, 'Distinct column-owned payload reference')
        reached.add((page,slot)); image = catalog._page(data,page,'payload')
        require(image[:2] == b'\x01\x01' and image[4:8] == b'LVAL', 'Payload page kind/owner')
        entry = next((e for e in catalog._row_directory(image,page) if e['row'] == slot),None)
        require(entry is not None and not entry['hidden'] and not entry['overflow'], 'Live payload row')
        fragment = image[entry['start']:entry['end']]
        if flags == 0x40000000: result.extend(fragment); break
        require(len(fragment) > 4, 'Nonempty payload fragment'); result.extend(fragment[4:])
        require(len(result) <= length, 'Payload chain length'); page,slot = int.from_bytes(fragment[1:4],'little'),fragment[0]
    require(len(result) == length, 'Complete external payload content'); return bytes(result)


def table_rows(data, table, pages):
    owned = {g['column']: set(catalog._locator_pages(data,g['owned'],'column payload')) for g in table['long_value_maps']}
    reached = {c:set() for c in owned}; result = []; storage = set(); physical = set()
    for number in pages:
        page = catalog._page(data,number,'wide row')
        for entry in catalog._row_directory(page,number):
            if entry['start'] < entry['end'] and not entry['overflow']: physical.add((number,entry['row']))
            if entry['hidden']: continue
            logical = (number,entry['row']); current = number; image = page; visited = set()
            while True:
                require((current,entry['row']) not in visited, 'No row overflow cycle'); visited.add((current,entry['row']))
                raw = image[entry['start']:entry['end']]
                if not entry['overflow']: break
                require(len(raw) == 4, 'Row overflow reference width'); current,slot = int.from_bytes(raw[1:],'little'),raw[0]
                require(current in pages, 'Owned overflow target'); image = catalog._page(data,current,'overflow')
                directory = catalog._row_directory(image,current); require(slot < len(directory), 'Overflow slot')
                entry = directory[slot]; require(entry['hidden'] and entry['start'] < entry['end'], 'Live hidden overflow row')
            require((current,entry['row']) not in storage, 'Distinct row storage'); storage.add((current,entry['row']))
            raw_values, shape = layout(raw,table['columns']); values=[]
            for c,value in zip(table['columns'],raw_values):
                ordinal=c['ordinal']
                if value is None: decoded=None
                elif c['type'] in ('Memo','LongBinary'): decoded=payload(data,value,owned[ordinal],reached[ordinal]).hex()
                else: decoded=catalog._decode_value(c,value,True)
                values.append(decoded)
            result.append(dict(page=logical[0],row=logical[1],values=values,layout=shape))
    require(storage == physical, 'Every physical data row reached exactly once')
    for ordinal,members in owned.items():
        active=set()
        for page in members:
            image=catalog._page(data,page,'owned payload');require(image[4:8]==b'LVAL','Column-owned payload page')
            for entry in catalog._row_directory(image,page):
                if entry['hidden']:require(entry['overflow'] and entry['start']==entry['end'],'Empty payload tombstone')
                else:active.add((page,entry['row']))
        require(active==reached[ordinal],'Every active payload slot reached exactly once')
    return result


def maps(data):
    definitions=tables(data); locations={'global':dict(page=1,row=0)};owners=[];available=[]
    for name,table in definitions.items():
        for role,locator in table['maps'].items(): locations[f'{name}-{role}']=locator
        owners.append(f'{name}-owned');available.append((f'{name}-available',f'{name}-owned'))
        for physical in table['physical_indexes']:
            key=f"{name}-index{physical['index']}";locations[key]=physical['map'];owners.append(key)
        for group in table['long_value_maps']:
            for role in ('owned','available'): locations[f"{name}-lval{group['column']}-{role}"]=group[role]
            key=f"{name}-lval{group['column']}";owners.append(key+'-owned');available.append((key+'-available',key+'-owned'))
    require(len({(v['page'],v['row']) for v in locations.values()})==len(locations),'Distinct map rows')
    records={};members={};bitmaps=set();owned=set()
    for role,locator in locations.items():
        records[role],members[role]=map_record(data,locator,role)
        refs={p for p in records[role]['references'] if p};require(not refs & bitmaps,'Distinct role bitmap pages');bitmaps|=refs
    for role in owners:
        require(not members[role] & (owned|members['global']),'Independent globally allocated ownership');owned|=members[role]
    for a,o in available:require(members[a]<=members[o],'Availability subset')
    metadata=bitmaps|{v['page'] for v in locations.values()}|{p for t in definitions.values() for p in t['pages']}
    require(not metadata & (owned|members['global']),'Allocated metadata is separate from data')
    return records
