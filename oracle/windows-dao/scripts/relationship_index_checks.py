#!/usr/bin/env python3
from __future__ import annotations

import argparse, copy, hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
import system_catalog as catalog
import numeric_index_mutation_structure as index_tree
from relationship_mutation_structure import rows as raw_rows, long_key, logical_relation, map_info
from relationship_create import leaf_entries

def req(condition, message):
    if not condition: raise AssertionError(message)

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def identity(path): return {'size': path.stat().st_size, 'sha256': digest(path)}
def n(record): return record['name']['value']

def normalized_snapshot(snapshot):
    value = copy.deepcopy(snapshot)
    for table in value.get('tables', []):
        for prop in table['properties']:
            if n(prop) in ('DateCreated', 'LastUpdated'): prop['value'] = '<timestamp>'
    return value

def normalized_raw_tables(tables):
    value=copy.deepcopy(tables)
    for table in value.values():
        for row in table['rows']: row.pop('raw_sha256',None)
    return value

def expected_value(field, value, raw=False):
    if value is None or field['type'] == 4: return value
    payload = value.encode('cp1252')
    if raw: return value if field['type'] == 10 else payload
    return payload.hex()

def expected_rows(table, raw=False):
    return [{field['name']: expected_value(field, row.get(field['name']), raw)
             for field in table['fields']} for row in table['rows']]

def compact_error(error):
    if error is None: return None
    return {key: error.get(key) for key in ('message', 'numbers', 'hresult', 'type')}

def component(value, descending):
    raw = b'\0' if value is None else b'\x7f' + ((value & 0xffffffff) ^ 0x80000000).to_bytes(4, 'big')
    return bytes(byte ^ 255 for byte in raw) if descending else raw

def observe_raw(path, case, *, include_row_bytes=False, system_inventory=None):
    data = path.read_bytes(); analysis = catalog.analyze_checkpoint(data)
    named = {table['name']: table for table in analysis['tables'].values()}
    result = {'identity': identity(path), 'page_count': len(data)//2048,
              'page0_relationship_byte': data[1538], 'free_pages': analysis['free_pages'],
              'tables': {}, 'relationship_rows': [], 'relationship_objects': [],
              'relationship_aces': [], 'maps': {}}
    by_spec = {table['name']: table for table in case['tables']}
    for name, table in named.items():
        definition = table['definition']
        compact = {'root': definition['root'], 'columns': definition['columns'],
                   'logical_indexes': definition['logical_indexes'],
                   'physical_indexes': [], 'maps': definition['maps'],
                   'long_value_maps': definition['long_value_maps'], 'rows': []}
        primary = ('Id' if name in by_spec and any(field['name']=='Id' for field in by_spec[name]['fields'])
                   else by_spec[name]['fields'][0]['name'] if name in by_spec else None)
        decoded = (raw_rows(data, table, primary_column=primary)
                   if name in by_spec else [])
        for row in decoded:
            compact['rows'].append({'locator': row['locator'], 'values': {
                key: (value.hex() if isinstance(value, bytes) else value) for key, value in row['values'].items()},
                'storage': row['storage'], 'descriptors': row['descriptors'],
                'raw_sha256': hashlib.sha256(bytes.fromhex(row['raw_hex'])).hexdigest()})
            if include_row_bytes:
                compact['rows'][-1]['raw_hex'] = row['raw_hex']
        for physical in definition['physical_indexes']:
            if name not in by_spec:
                compact['physical_indexes'].append({
                    'index': physical['index'], 'root': physical['root'], 'flags': physical['flags'],
                    'keys': physical['keys'], 'first_word': int.from_bytes(bytes.fromhex(physical['prefix_hex']), 'little'),
                    'second_word': physical['entry_count'], 'map': physical['map'],
                    'mapped_pages': sorted(catalog._locator_pages(data, physical['map'], f'{name} index map'))})
                continue
            fields = []
            for key in physical['keys']:
                column = definition['columns'][key['column']]
                req(column['type'] == 'Long', f'{path.name} indexed non-Long {name}')
                fields.append((4, key['direction'] == 0))
            nodes, entries = index_tree.tree(data, physical['root'], definition['root'], fields)
            expected = []
            for row in decoded:
                if physical['flags'] & 2 and any(row['values'][definition['columns'][item['column']]['name']] is None for item in physical['keys']):
                    continue
                key = b''.join(component(row['values'][definition['columns'][item['column']]['name']], item['direction'] == 0)
                               for item in physical['keys'])
                expected.append(key + row['locator']['page'].to_bytes(3, 'big') + bytes([row['locator']['row']]))
            req(entries == sorted(expected), f'{path.name} {name} physical {physical["index"]} keys')
            members = set(catalog._locator_pages(data, physical['map'], f'{name} index map'))
            req({node['page'] for node in nodes} <= members, f'{path.name} {name} index map membership')
            compact['physical_indexes'].append({
                'index': physical['index'], 'root': physical['root'], 'flags': physical['flags'],
                'keys': physical['keys'], 'first_word': int.from_bytes(bytes.fromhex(physical['prefix_hex']), 'little'),
                'second_word': physical['entry_count'], 'map': physical['map'],
                'mapped_pages': sorted(members), 'nodes': nodes, 'entries_hex': [entry.hex() for entry in entries]})
        result['tables'][name] = compact
    for system_name in ('MSysRelationships', 'MSysObjects', 'MSysACEs'):
        rows = catalog._generic_rows(analysis['system_rows'][system_name], analysis)
        if system_name == 'MSysRelationships': result['relationship_rows'] = rows
        elif system_name == 'MSysObjects':
            result['relationship_objects'] = [{key:row[key] for key in ('Id','ParentId','Name','Type','Flags','Owner')}
                                              for row in rows if row['Type'] == 8]
        else:
            ids = {row['Id'] for row in result['relationship_objects']}
            result['relationship_aces'] = [row for row in rows if row['ObjectId'] in ids]
    if system_inventory is not None:
        result['relationship_system_indexes'] = system_inventory(path)['MSysRelationships']['indexes']
    else:
        rel_table = named['MSysRelationships']; raw_rel_rows = raw_rows(data, rel_table, primary_column='szRelationship')
        rel_locators = {(row['locator']['page'], row['locator']['row']) for row in raw_rel_rows}
        result['relationship_system_indexes'] = []
        for physical in rel_table['definition']['physical_indexes']:
            leaf = leaf_entries(data, physical['root'])
            req({(entry['row_page'], entry['row']) for entry in leaf['entries']} == rel_locators,
                f'{path.name} relationship system-index locator coverage')
            result['relationship_system_indexes'].append({'index': physical['index'], 'root': physical['root'],
                'flags': physical['flags'], 'first_word': int.from_bytes(bytes.fromhex(physical['prefix_hex']), 'little'),
                'second_word': physical['entry_count'], 'entries': leaf['entries']})
    maps = {'global': map_info(data, {'page':1,'row':0}, 'global')}
    for name, table in named.items():
        definition = table['definition']
        for role, locator in definition['maps'].items(): maps[f'{name}/table/{role}'] = map_info(data, locator, f'{name}-{role}')
        for physical in definition['physical_indexes']:
            maps[f'{name}/index/{physical["index"]}'] = map_info(data, physical['map'], f'{name}-index')
        for group in definition['long_value_maps']:
            for role in ('owned','available'):
                maps[f'{name}/lval/{group["column"]}/{role}'] = map_info(data, group[role], f'{name}-lval')
    locators = [(record['locator']['page'],record['locator']['row']) for record,_ in maps.values()]
    references = [page for record,_ in maps.values() for page in record['references'] if page]
    req(len(locators)==len(set(locators)) and len(references)==len(set(references)), f'{path.name} distinct map records')
    claimed={}; free=set(analysis['free_pages']); metadata={0,1}
    for name,table in named.items():
        definition=table['definition']; owned=set(maps[f'{name}/table/owned'][1]); available=set(maps[f'{name}/table/available'][1])
        req(available<=owned and not owned&free, f'{path.name} {name} data allocation'); metadata.update(definition['pages'])
        groups=[(f'{name}/table',owned)]
        for physical in definition['physical_indexes']:
            members=set(maps[f'{name}/index/{physical["index"]}'][1]); groups.append((f'{name}/index/{physical["index"]}',members)); req(not members&free,f'{path.name} live index allocation')
        for group in definition['long_value_maps']:
            key=f'{name}/lval/{group["column"]}';members=set(maps[key+'/owned'][1]);groups.append((key,members));req(set(maps[key+'/available'][1])<=members and not members&free,f'{path.name} LVAL allocation')
        for role,members in groups:
            for page in members: req(page not in claimed,f'{path.name} duplicate page owner {page}');claimed[page]=role
    for record,_ in maps.values(): metadata.add(record['locator']['page']);metadata.update(p for p in record['references'] if p)
    req(not metadata&free and not metadata&set(claimed), f'{path.name} metadata allocation separation')
    result['maps']={key:{'record':value[0],'members':value[1]} for key,value in maps.items()}
    return result

def evaluate_case(case, result, mdb_path, *, include_row_bytes=False, system_inventory=None):
    req(result['status']=='pass' and result['error'] is None, f'{mdb_path.name} worker status')
    req(result['capture']['before']==result['capture']['after']==identity(mdb_path), f'{mdb_path.name} read-only identity')
    snapshot=result['capture']['snapshot']; captured={n(table):table for table in snapshot['tables']}
    successful_specs=[spec for spec,attempt in zip(case['relations'],result['relations']) if attempt['error'] is None]
    if result['table_error'] is None:
        req(set(captured)=={table['name'] for table in case['tables']}, f'{mdb_path.name} DAO table inventory')
        for spec in case['tables']:
            actual=captured[spec['name']]
            req([(n(field),field['type'],field['size'],field['required'],field['allow_zero_length']) for field in actual['fields']]
                ==[(field['name'],field['type'],field['size'],field['required'],field['allow_zero_length'] if field['type'] in (10,12) else False) for field in spec['fields']],f'{mdb_path.name} {spec["name"]} fields')
            req(actual['rows']==expected_rows(spec),f'{mdb_path.name} {spec["name"]} DAO rows')
            expected_indexes=[(index['name'],index['primary'],index['unique'],index['required'],False,index['ignore_nulls'],((index['field'],1 if index['direction']=='desc' else 0),)) for index in spec['indexes']]
            expected_indexes += [(relation['name'],False,False,False,True,False,((relation['child_field'],0),)) for relation in successful_specs if relation['child']==spec['name']]
            actual_indexes=[(n(index),index['primary'],index['unique'],index['required'],index['foreign'],index['ignore_nulls'],tuple((n(field),field['attributes']) for field in index['fields'])) for index in actual['indexes']]
            req(sorted(actual_indexes)==sorted(expected_indexes),f'{mdb_path.name} {spec["name"]} index inventory')
            for index in actual['indexes']:
                read=actual['index_reads'][n(index)];req(read['error'] is None,f'{mdb_path.name} {spec["name"]}/{n(index)} read')
                traversal_expected=[row for row in expected_rows(spec) if not index['ignore_nulls'] or row[read['field']] is not None]
                req(len(read['traversal'])==len(traversal_expected) and sorted(read['traversal'],key=lambda x:json.dumps(x,sort_keys=True))==sorted(traversal_expected,key=lambda x:json.dumps(x,sort_keys=True)),f'{mdb_path.name} traversal complete')
                expected_queries=[]
                for row in read['traversal']:
                    value=row[read['field']]
                    if value not in expected_queries: expected_queries.append(value)
                expected_queries.append(2147483000)
                req([seek['query'] for seek in read['seek']]==expected_queries,f'{mdb_path.name} Seek inventory')
                matches={json.dumps(row,sort_keys=True) for row in expected_rows(spec)}
                for seek in read['seek']:
                    match=[row for row in expected_rows(spec) if row[read['field']]==seek['query']]
                    req(seek['no_match']==(not match) and ((seek['row'] is None)==(not match)),f'{mdb_path.name} Seek presence')
                    if match: req(seek['row'] in match and json.dumps(seek['row'],sort_keys=True) in matches,f'{mdb_path.name} Seek row')
    successes=[entry['name'] for entry in result['relations'] if entry['error'] is None]
    req([n(relation) for relation in snapshot['relations']]==successes,f'{mdb_path.name} relation API inventory')
    raw=observe_raw(mdb_path,case,include_row_bytes=include_row_bytes,system_inventory=system_inventory)
    if result['table_error'] is None:
        for spec in case['tables']:
            req([{key:(value.hex() if isinstance(value,bytes) else value) for key,value in row.items()} for row in expected_rows(spec,True)]
                ==[row['values'] for row in raw['tables'][spec['name']]['rows']],f'{mdb_path.name} {spec["name"]} raw rows')
    expected_relation_rows=[{'szRelationship':relation['name'],'grbit':0,'ccolumn':1,'icolumn':0,
        'szObject':relation['child'],'szColumn':relation['child_field'],'szReferencedObject':relation['parent'],'szReferencedColumn':relation['parent_field']} for relation in successful_specs]
    canon=lambda value:json.dumps(value,sort_keys=True,separators=(',',':'))
    req(sorted(map(canon,raw['relationship_rows']))==sorted(map(canon,expected_relation_rows)),f'{mdb_path.name} raw relation rows')
    req(sorted(row['Name'] for row in raw['relationship_objects'])==sorted(successes),f'{mdb_path.name} relationship objects')
    ordered_objects=sorted(raw['relationship_objects'],key=lambda row:row['Id'])
    req([(row['Id'],row['ParentId'],row['Name'],row['Type'],row['Flags'],row['Owner']) for row in ordered_objects]
        ==[(-2147483648+position,251658243,relation['name'],8,0,'0301') for position,relation in enumerate(successful_specs)],f'{mdb_path.name} relationship objects')
    expected_aces=[(object_row['Id'],sid,mask,False) for object_row in ordered_objects for sid,mask in (('0301',983294),('0201',1048575))]
    req(sorted((row['ObjectId'],row['SID'],row['ACM'],row['FInheritable']) for row in raw['relationship_aces'])==sorted(expected_aces),f'{mdb_path.name} relationship ACEs')
    for spec in case['tables']:
        logical=raw['tables'][spec['name']]['logical_indexes'];expected_names={index['name'] for index in spec['indexes']}
        expected_names.update(relation['name'] for relation in successful_specs if relation['child']==spec['name'])
        actual_public={item['name'] for item in logical if not item['name'].startswith('.r')}
        req(actual_public==expected_names,f'{mdb_path.name} {spec["name"]} raw logical inventory')
        req(sum(item['class']==2 and item['name'].startswith('.r') for item in logical)==sum(relation['parent']==spec['name'] for relation in successful_specs),f'{mdb_path.name} {spec["name"]} hidden parent inventory')
    for relation in snapshot['relations']:
        spec=next(item for item in case['relations'] if item['name']==n(relation)); parent=raw['tables'][spec['parent']];child=raw['tables'][spec['child']]
        cr=[logical_relation(item['raw_hex'])|{'name':item['name']} for item in child['logical_indexes'] if item['class']==2 and item['name']==spec['name']]
        req(len(cr)==1,f'{mdb_path.name} named foreign record count {spec["name"]}');c=cr[0]
        # Multiple relations may share a table pair, and self-relations share one root.
        # Select the parent alias using the exact reciprocal selector/ordinal cross-link.
        pr=[logical_relation(item['raw_hex'])|{'name':item['name']} for item in parent['logical_indexes']
            if item['class']==2 and item['name'].startswith('.r')
            and logical_relation(item['raw_hex'])['related_root']==child['root']
            and logical_relation(item['raw_hex'])['selector']==c['relation_ordinal']
            and logical_relation(item['raw_hex'])['relation_ordinal']==c['selector']]
        req(len(pr)==1,f'{mdb_path.name} reciprocal parent record count {spec["name"]}');p=pr[0]
        req(p['side']==1 and c['side']==2 and p['context_hex']==c['context_hex']=='0000',f'{mdb_path.name} reciprocal roles')
        req(p['selector']==c['relation_ordinal'] and p['relation_ordinal']==c['selector'],f'{mdb_path.name} reciprocal selectors')
        req(parent['physical_indexes'][p['physical_index']]['keys'][0]['column']==next(i for i,f in enumerate(next(t for t in case['tables'] if t['name']==spec['parent'])['fields']) if f['name']==spec['parent_field']),f'{mdb_path.name} parent physical selection')
        req(child['physical_indexes'][c['physical_index']]['keys'][0]['column']==next(i for i,f in enumerate(next(t for t in case['tables'] if t['name']==spec['child'])['fields']) if f['name']==spec['child_field']),f'{mdb_path.name} child physical selection')
    return {'case':case['id'],'replica':result['replica'],'table_error':compact_error(result['table_error']),
            'relation_outcomes':[{'name':item['name'],'error':compact_error(item['error'])} for item in result['relations']],
            'environment':result['environment'],'snapshot':snapshot,'raw':raw}

