#!/usr/bin/env python3
"""Compare complete raw schema observations and preserve unrelated objects."""
import argparse, copy, json
from pathlib import Path
from schema_edit_structure import require, sha


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def differences(left, right, path=''):
    if type(left) != type(right): return [(path, left, right)]
    if isinstance(left, dict):
        if set(left) != set(right): return [(path + '/keys', sorted(left), sorted(right))]
        return sum((differences(left[k], right[k], path + '/' + str(k)) for k in left), [])
    if isinstance(left, list):
        if len(left) != len(right): return [(path + '/length', len(left), len(right))]
        return sum((differences(a, b, path + '/' + str(i)) for i, (a, b) in enumerate(zip(left, right))), [])
    return [] if left == right else [(path, left, right)]



def definition_bytes(d, refusal_counts=False):
    body = bytearray.fromhex(d['body_hex'])
    # Pointers are checked through complete role inventories and tree traversals.
    body[4:8] = bytes(4)
    body[35:43] = bytes(8)
    offset = 43 + 8 * len(d['physical_indexes'])
    for column in d['columns']:
        if column['storage'] == 'variable': body[offset+14:offset+16] = bytes(2)
        offset += 18
    for column in d['columns']: offset += 1 + len(column['name'].encode('cp1252'))
    for index in d['physical_indexes']:
        for slot in range(10):
            start = offset + slot * 3
            if body[start:start+2] == b'\xff\xff': body[start+2] = 0
        body[offset+30:offset+38] = bytes(8)
        offset += 39
    offset += 20 * len(d['logical_indexes'])
    for index in d['logical_indexes']: offset += 1 + len(index['name'].encode('cp1252'))
    for group in d['long_value_maps']:
        body[offset+2:offset+10] = bytes(8); offset += 10
    require(offset+2 == len(body), 'complete logical definition coverage')
    if refusal_counts:
        body[12:16] = bytes(4)
        for i in range(len(d['physical_indexes'])): body[47+8*i:51+8*i] = bytes(4)
    return body.hex()

def semantics(raw, spec):
    tables = {}
    dates = set(spec.get('normalize_table_dates', []))
    for step in spec['steps']:
        request = step['request']
        if 'relationship' in request: dates.add(request['relationship']['name'])
        if request['operation'] in ('drop_relationship', 'replace_relationship'): dates.add(request['name'])
    for name, table in raw['tables'].items():
        d = table['definition']; rows = copy.deepcopy([r['values'] for r in table['rows']])
        if name == 'MSysObjects':
            for r in rows:
                if r['Name'] in dates:
                    r['DateCreate'] = r['DateUpdate'] = '<mutated object date>'
        logical = []
        for i in d['logical_indexes']:
            logical.append({k: i[k] for k in ('name', 'selector', 'physical_index', 'class', 'raw_hex')})
        indexes = []
        for i in table['indexes']:
            indexes.append({k: i[k] for k in ('index', 'keys', 'flags', 'prefix_hex', 'entry_count', 'live_distinct')} |
                           {'keys_hex': [bytes.fromhex(e)[:-4].hex() for e in i['entries_hex']]})
        columns = copy.deepcopy(d['columns'])
        for c in columns:
            if c['storage'] == 'variable':
                b = bytearray.fromhex(c['raw_hex']); b[14:16] = bytes(2); c['raw_hex'] = b.hex()
        tables[name] = {'definition_bytes': definition_bytes(d, spec['name'] == 'relationship-orphan-refusal' and name in ('MSysObjects','MSysACEs')), 'columns': columns, 'storage_high_water': d['storage_high_water'],
                        'variable_high_water': d['variable_high_water'], 'marker': d['marker'],
                        'header_unknown_hex': d['header_unknown_hex'], 'row_count': d['row_count'],
                        'logical_indexes': logical, 'indexes': indexes,
                        'rows': sorted(rows, key=canonical),
                        'payload_columns': sorted(g['storage_id'] for g in d['long_value_maps'])}
    return tables



PLACEMENT_ROLES = {
    'column-add-memo': {'Target/lval/AddedMemo/owned', 'Target/lval/AddedMemo/available'},
    'index-add-composite': {'Target/index/3/owned'},
    'index-drop-reappend': {'Target/index/2/owned'},
    'index-replace': {'Target/index/2/owned'},
    'relationship-add': {'AltChild/index/1/owned'},
    'relationship-child-logical-zero': {'ZeroChild/index/0/owned'},
    'relationship-parent-logical-zero': {'GapChild/index/1/owned'},
    'relationship-composite-add': {'MSysRelationships/table/owned', 'MSysRelationships/table/available', 'Child/index/1/owned'},
    'relationship-composite-replace': {'MSysRelationships/table/owned', 'MSysRelationships/table/available', 'Child/index/1/owned'},
    'relationship-replace-cascade': {'MSysRelationships/table/owned', 'MSysRelationships/table/available', 'Child/index/1/owned'},
    'relationship-self-add': {'MSysRelationships/table/owned', 'MSysRelationships/table/available', 'Node/index/2/owned'},
    'table-create-insert': {'CreatedTable/table/owned', 'CreatedTable/table/available', 'CreatedTable/index/0/owned'},
}


def affected(before, spec):
    tables, objects, relations = set(), set(), set()
    relrows = [r['values'] for r in before['tables']['MSysRelationships']['rows']]
    for step in spec['steps']:
        request = step['request']; kind = request['operation']
        if 'table' in request:
            table = request['table']; table = table['name'] if isinstance(table, dict) else table
            tables.add(table); objects.add(table)
        if kind == 'rename_table': tables.add(request['name']); objects.add(request['name'])
        if 'relationship' in request:
            rel = request['relationship']; relations.add(rel['name']); objects.add(rel['name'])
            tables.update((rel['parent']['table'], rel['child']['table']))
        if kind in ('drop_relationship', 'replace_relationship'):
            relations.add(request['name']); objects.add(request['name'])
        for rel in relrows:
            selected = rel['szRelationship'] in relations
            if kind == 'drop_table' and table in (rel['szObject'], rel['szReferencedObject']):
                selected = True; relations.add(rel['szRelationship']); objects.add(rel['szRelationship'])
            if selected: tables.update((rel['szObject'], rel['szReferencedObject']))
            if kind in ('rename_table', 'rename_column') and table in (rel['szObject'], rel['szReferencedObject']):
                relations.add(rel['szRelationship'])
    objects.update(tables)
    return tables, objects, relations


def preserve(before, after, before_bytes, after_bytes, spec):
    require(before_bytes[:2048] == after_bytes[:2048], 'complete original database header')
    tables, objects, relations = affected(before, spec)
    preserved_pages, preserved_rows, preserved_objects = set(), [], []
    for name, old in before['tables'].items():
        if name.startswith('MSys') or name in tables: continue
        require(name in after['tables'] and old == after['tables'][name], 'unrelated complete table: '+name)
        pages = set(old['definition']['pages'])
        for role, m in before['maps'].items():
            if role.startswith(name+'/'):
                require(m == after['maps'][role], 'unrelated map: '+role)
                pages.update(m['members']); pages.update(p for p in m['record']['references'] if p)
        for page in pages:
            require(before_bytes[page*2048:(page+1)*2048] == after_bytes[page*2048:(page+1)*2048], 'unrelated page: '+str(page))
        preserved_pages.update(pages)
    old_catalog = {r['values']['Id']: r for r in before['tables']['MSysObjects']['rows']}
    new_catalog = {r['values']['Id']: r for r in after['tables']['MSysObjects']['rows']}
    affected_ids = {i for i, r in old_catalog.items() if r['values']['Name'] in objects}
    for ident, row in old_catalog.items():
        if ident not in affected_ids:
            require(ident in new_catalog and row == new_catalog[ident], 'complete unrelated catalog row: '+row['values']['Name'])
            preserved_objects.append(row['values']['Name'])
        elif ident in new_catalog and not any(s['request']['operation'] == 'replace_relationship' and s['request']['name'] == row['values']['Name'] for s in spec['steps']):
            for field in ('DateCreate','DateUpdate'):
                require(row['values'][field] == new_catalog[ident]['values'][field], 'existing object timestamp: '+row['values']['Name'])
    for name in ('MSysQueries', 'MSysACEs', 'MSysRelationships'):
        old = before['tables'][name]; new = after['tables'][name]
        if name == 'MSysQueries':
            require(old == new, 'complete saved-query storage/definition')
            query_pages = set(old['definition']['pages'])
            for role,record in before['maps'].items():
                if role.startswith('MSysQueries/'):
                    require(record == after['maps'][role], 'saved-query map record')
                    query_pages.update(record['members'])
                    query_pages.update(p for p in record['record']['references'] if p)
            for page in query_pages:
                require(before_bytes[page*2048:(page+1)*2048] == after_bytes[page*2048:(page+1)*2048], 'complete saved-query owned page')
            preserved_pages.update(query_pages)
        def unselected(r):
            if name == 'MSysACEs': return r['values']['ObjectId'] not in affected_ids
            if name == 'MSysRelationships': return r['values']['szRelationship'] not in relations
            return True
        for row in old['rows']:
            if unselected(row):
                require(row in new['rows'], 'unrelated raw system row: '+name)
                preserved_rows.append({'table': name, 'locator': row['locator'], 'sha256': sha(bytes.fromhex(row['raw_hex']))})
    # Schema-only edits retain the existing row bodies, including deleted fields.
    renames = {s['request']['table']: s['request']['name'] for s in spec['steps'] if s['request']['operation'] == 'rename_table'}
    rewritten = set()
    auto_tables = set()
    for step in spec['steps']:
        r = step['request']
        if r['operation'] in ('update','replace','delete'): rewritten.add((r['table'],r['row']['page'],r['row']['slot']))
        if r['operation'] == 'create_column' and r['column']['type'] == 'auto_increment': auto_tables.add(r['table'])
    for name, old in before['tables'].items():
        if name.startswith('MSys') or name in auto_tables: continue
        new = after['tables'].get(renames.get(name,name))
        if new is None: continue
        indexed = {(r['locator']['page'],r['locator']['row']): r for r in new['rows']}
        for row in old['rows']:
            key = (row['locator']['page'],row['locator']['row'])
            if (name,*key) in rewritten: continue
            require(key in indexed and row['raw_hex'] == indexed[key]['raw_hex'], 'surviving row bytes: '+name)
    # Surviving payload columns retain complete descriptor storage and allocation.
    for role, record in before['maps'].items():
        if '/lval/' not in role or role.startswith('MSys'): continue
        if role not in after['maps']: continue
        require(record == after['maps'][role], 'surviving payload allocation: '+role)
        for page in record['members']:
            require(before_bytes[page*2048:(page+1)*2048] == after_bytes[page*2048:(page+1)*2048], 'surviving payload page')
    return {'unrelated_pages': sorted(preserved_pages), 'unrelated_catalog_objects': sorted(preserved_objects),
            'unrelated_system_rows': preserved_rows, 'affected_tables': sorted(tables)}


def compare(before, candidate, native, before_bytes, candidate_bytes, native_bytes, spec):
    refused = any(step.get('expected_returncode',0) != 0 for step in spec['steps'])
    if refused: require(before_bytes == candidate_bytes, 'whole-file refusal equality')
    left, right = semantics(candidate,spec), semantics(native,spec)
    residue = []
    if spec['name'] == 'relationship-orphan-refusal':
        require(len(native_bytes) == len(before_bytes), 'native refusal size')
        permitted = {1538}
        for name in ('MSysObjects','MSysACEs'):
            d = before['tables'][name]['definition']; permitted.add(d['row_count_offset'])
            permitted.update(i['entry_count_offset'] for i in d['physical_indexes'])
        residue = [{'offset':i,'before':a,'native':b} for i,(a,b) in enumerate(zip(before_bytes,native_bytes)) if a!=b]
        require(len(residue)==6 and {x['offset'] for x in residue}==permitted, 'exact native refusal residue positions')
        for x in residue: require(x['native']-x['before']==(2 if x['offset'] in (1538,before['tables']['MSysACEs']['definition']['row_count_offset']) else 1), 'exact native refusal residue values')
        for name in ('MSysObjects','MSysACEs'):
            right[name]['row_count'] = left[name]['row_count']
            for a,b in zip(left[name]['indexes'],right[name]['indexes']): b['entry_count']=a['entry_count']
    diff = differences(left,right)
    require(not diff, 'raw semantic difference: '+str([(p,str(a)[:80],str(b)[:80]) for p,a,b in diff[:3]]))
    require(set(candidate['maps']) == set(native['maps']), 'complete map role inventory')
    placements = []
    for role, a in candidate['maps'].items():
        b = native['maps'][role]
        if a == b: continue
        require(role=='global' or role in PLACEMENT_ROLES.get(spec['name'],set()), 'unlisted placement: '+role)
        for key in ('kind','length','start','references'):
            require(a['record'][key]==b['record'][key], 'map framing: '+role+'/'+key)
        if role!='global': require(len(a['members'])==len(b['members']), 'same complete role capacity: '+role)
        placements.append({'role':role,'candidate':a,'native':b})
    preservation = preserve(before,candidate,before_bytes,candidate_bytes,spec)
    unused = []
    for name, table in candidate['tables'].items():
        for a,b in zip(table['definition']['columns'],native['tables'][name]['definition']['columns']):
            if a['raw_hex']!=b['raw_hex']:
                require(a['storage']==b['storage']=='variable', 'only unused variable fixed-offset bytes differ')
                aa,bb=bytes.fromhex(a['raw_hex']),bytes.fromhex(b['raw_hex'])
                require(aa[:14]+aa[16:]==bb[:14]+bb[16:], 'all meaningful column bytes exact')
                unused.append({'table':name,'column':a['name'],'candidate':aa[14:16].hex(),'native':bb[14:16].hex()})
    unused_slots = []
    for name,table in candidate['tables'].items():
        for a,b in zip(table['indexes'],native['tables'][name]['indexes']):
            aa,bb=bytes.fromhex(a['raw_hex']),bytes.fromhex(b['raw_hex'])
            for slot in range(10):
                offset=slot*3
                if aa[offset:offset+2]==bb[offset:offset+2]==b'\xff\xff' and aa[offset+2]!=bb[offset+2]:
                    unused_slots.append({'table':name,'index':a['index'],'slot':slot,'candidate':aa[offset+2],'native':bb[offset+2]})
    return {'name':spec['name'],'status':'pass','refused_input_exact':refused,'preservation':preservation,
            'unused_variable_fixed_offsets':unused,'unused_index_slot_directions':unused_slots,'native_refusal_residue':residue,'placement_differences':placements,
            'normalized_dates':spec.get('normalize_table_dates',[])}


def main():
    p=argparse.ArgumentParser();p.add_argument('--prepared',type=Path,required=True);p.add_argument('--raw',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();manifest=json.loads((args.prepared/'manifest.json').read_text());results=[];failures=[]
    for spec in manifest['cases']:
        name=spec['name']
        inputs={kind:json.loads((args.raw/(kind+'-'+name+'.json')).read_text()) for kind in ('input','candidate','native')}
        images=[Path(spec['input']).read_bytes(),(args.prepared/('candidate-'+name+'.mdb')).read_bytes(),(args.prepared/('native-'+name+'.mdb')).read_bytes()]
        try:
            for kind,data in zip(('input','candidate','native'),images):
                require(inputs[kind]['identity']=={'size':len(data),'sha256':sha(data)}, 'raw observation input identity')
            results.append(compare(inputs['input'],inputs['candidate'],inputs['native'],*images,spec))
        except Exception as error: failures.append({'name':name,'error':str(error)})
    report={'status':'fail' if failures else 'pass','results':results,'failures':failures}
    args.out.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'status':report['status'],'passed':len(results),'failures':failures},indent=2))
    if failures: raise SystemExit(1)

if __name__=='__main__': main()
