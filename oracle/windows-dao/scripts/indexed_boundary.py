#!/usr/bin/env python3
"""One frozen insertion-only boundary matrix; no acquisition retries."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import field_update as common
import multi_level_index_structure as structure

ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'oracle/windows-dao/acquisition/indexed-boundary.plan.json'
SCRIPT = 'oracle/windows-dao/scripts/indexed_boundary.ps1'
identity, canonical = common.identity, common.canonical
catalog = structure.catalog


def require(value, message):
    if not value:
        raise ValueError(message)


def definition(data):
    catalog_table, _, objects = catalog._discover_catalog(data)
    name, ident = [catalog._ordinal(catalog_table, k) for k in ('Name', 'Id')]
    roots = [r['values'][ident] for r in objects if r['values'][name] == 'Items']
    require(len(roots) == 1, 'Items root')
    table = catalog._definition(data, roots[0])
    pages, lval = catalog._table_pages(data, table)
    require(not lval and len(table['physical_indexes']) == len(table['logical_indexes']) == 1, 'One scalar index')
    return table, catalog._table_rows(data, table, pages)


def values(key):
    return [key, 'x' * 80, None if key % 2 == 0 else '-12.3456', key % 2 != 0]


def patch_check(before, after, arm):
    table, rows = definition(before)
    require(len(rows) == table['row_count'] == arm['count'], 'Original count')
    if arm['name'] in ('duplicate', 'split'):
        require(before == after, 'Refusal changed bytes')
        return dict(refusal=arm['name'], preserved=True)
    key = arm['id']
    # EXP-0060/0061 fixed fields, variable text footer and Boolean presence.
    encoded = bytes([4]) + key.to_bytes(4, 'little', signed=True) + (-123456).to_bytes(8, 'little', signed=True) + b'x'*80 + bytes([93, 13, 1, 15])
    require(key % 2 == 1, 'Finite present Currency insertion')
    expected = bytearray(before)
    pages = sorted(catalog._locator_pages(before, table['maps']['owned'], 'owned'))
    eof = arm['name'] == 'eof'
    free_pages = []
    for page in pages:
        raw = catalog._page(before, page, 'Items')
        directory = catalog._row_directory(raw, page)
        require(directory and all(not e['hidden'] and not e['overflow'] for e in directory), 'Ordinary populated data')
        free = directory[-1]['start'] - 10 - 2*len(directory)
        require(free == int.from_bytes(raw[2:4], 'little'), 'Free count')
        if free >= 2*(len(encoded)+2): free_pages.append(page)
        if eof: require(free < len(encoded)+2, 'Physical boundary required')
    if eof:
        page, slot = len(before)//2048, 0
        require(len(after) == len(before)+2048, 'One appended page')
        for role, locator, set_value in [('global', dict(page=1,row=0),False), ('owned',table['maps']['owned'],True), ('available',table['maps']['available'],True)]:
            raw = catalog._locator_row(before, locator, role)
            relative = page-int.from_bytes(raw[1:5], 'little')
            require(raw[0] == 0 and 0 <= relative < 8*(len(raw)-5), 'Inline map coverage')
            entry = catalog._row_directory(catalog._page(before,locator['page'],role),locator['page'])[locator['row']]
            offset = locator['page']*2048+entry['start']+5+relative//8
            mask = 1 << (relative%8)
            require(bool(before[offset]&mask) != set_value, 'Original EOF map bit')
            expected[offset] = expected[offset]|mask if set_value else expected[offset]&~mask
        image = bytearray(2048)
        image[:2] = b'\x01\x01'; image[2:4] = (2036-len(encoded)).to_bytes(2,'little')
        image[4:8] = table['root'].to_bytes(4,'little'); image[8:10] = b'\x01\x00'
        image[10:12] = (2048-len(encoded)).to_bytes(2,'little'); image[-len(encoded):] = encoded
        expected.extend(image)
    else:
        require(len(free_pages) == 1 and len(before) == len(after), 'One existing candidate')
        page = free_pages[0]; base = page*2048
        raw = before[base:base+2048]; directory = catalog._row_directory(raw,page)
        slot = len(directory); high = directory[-1]['start']; low = high-len(encoded)
        expected[base+low:base+high] = encoded
        expected[base+10+slot*2:base+12+slot*2] = low.to_bytes(2,'little')
        expected[base+8:base+10] = (slot+1).to_bytes(2,'little')
        expected[base+2:base+4] = (low-12-slot*2).to_bytes(2,'little')
    count = len(rows)+1; root = table['root']*2048
    for offset in (12,47): expected[root+offset:root+offset+4] = count.to_bytes(4,'little')
    records = [(r['values'][0],r['page'],r['row']) for r in rows] + [(key,page,slot)]
    records = sorted(b'\x7f'+(k^0x80000000).to_bytes(4,'big')+p.to_bytes(3,'big')+bytes([s]) for k,p,s in records)
    leaf = table['physical_indexes'][0]['root']*2048
    require(before[leaf:leaf+2] == b'\x04\x01' and before[leaf+8:leaf+22] == bytes(14), 'Isolated uncompressed root leaf')
    expected[leaf+2:leaf+4] = (1800-count*9).to_bytes(2,'little')
    bitmap = bytearray(226)
    for n in range(1,count+1): bitmap[n*9//8] |= 1 << (n*9%8)
    expected[leaf+22:leaf+248] = bitmap; expected[leaf+248:leaf+248+count*9] = b''.join(records)
    require(expected == after, 'Exact data/allocation/count/leaf patch and unrelated preservation')
    return dict(page=page,slot=slot,count=count,eof=eof,unrelated_bytes_preserved=True)


def expected(snapshot, arm, role):
    value = copy.deepcopy(snapshot)
    rows = [values(k) for k in range(arm['count'])]
    if role != 'original' and arm['name'] in ('space','eof'): rows.append(values(arm['id']))
    rows.sort()
    require(value['version'] == '3.0' and value['queries'] == value['relations'] == [] and value['tables'] == sorted(['Items','Notes','MSysACEs','MSysObjects','MSysQueries','MSysRelationships']), 'Database inventory')
    require([t['name'] for t in value['user_tables']] == ['Items','Notes'], 'User inventory')
    items, notes = value['user_tables']
    items['rows'].sort(); notes['rows'].sort()
    for row in items['rows'] + value['traversal'] + [s['row'] for s in value['seek'] if s['row'] is not None]:
        require(len(row) == 4 and type(row[0]) is int and type(row[1]) is str and (row[2] is None or type(row[2]) is str) and type(row[3]) is bool, 'Typed Items serialization')
    require(items['rows'] == rows and notes['rows'] == [[7,'n'*4096],[8,None]], 'Complete typed rows and Memo')
    for table, fields in [(items,[('Id',4,4),('Name',10,80),('Price',5,8),('Active',1,1)]),(notes,[('Id',4,4),('Body',12,0)])]:
        require(table['attributes'] == 0 and [(f['name'],f['type'],f['size']) for f in table['fields']] == fields, 'Exact schema')
    require(notes['indexes'] == [] and items['indexes'] == [dict(name='ById',primary=True,unique=True,required=True,foreign=False,ignore_nulls=False,fields=[dict(name='Id',attributes=0)])], 'Exact index metadata')
    require(value['traversal'] == rows and value['seek'] == [dict(query=k,row=next((r for r in rows if r[0]==k),None)) for k in range(-1,202)], 'Complete traversal and present/missing Seek')
    return value


def raw_check(data, arm, role):
    table, rows = definition(data)
    require([(c['name'], c['type'], c['size']) for c in table['columns']] == [('Id','Long',4),('Name','Text',80),('Price','Currency',8),('Active','Boolean',1)], 'Raw schema')
    ids = list(range(arm['count']))
    if role != 'original' and arm['name'] in ('space','eof'): ids.append(arm['id'])
    expected_rows = [[k, 'x'*80, None if k%2 == 0 else dict(raw_hex=(-123456).to_bytes(8,'little',signed=True).hex()), k%2 != 0] for k in sorted(ids)]
    require(sorted((r['values'] for r in rows), key=lambda r:r[0]) == expected_rows, 'Complete raw rows')
    physical = table['physical_indexes'][0]
    require(physical['flags'] == 9 and physical['keys'] == [dict(column=0,direction=1)], 'Raw index metadata')
    nodes, entries = structure.tree(data, physical['root'], table['root'])
    require(len(nodes) == 1 and not nodes[0]['children'] and (role == 'control' or nodes[0]['prefix'] == 0), 'Single root leaf')
    require(structure.map_pages(data, physical['map'], 'index') == {physical['root']}, 'Isolated index map')
    expected_entries = sorted(b'\x7f'+(r['values'][0]^0x80000000).to_bytes(4,'big')+r['page'].to_bytes(3,'big')+bytes([r['row']]) for r in rows)
    require(entries == expected_entries and len(entries) == len(set(ids)) == table['row_count'] == physical['entry_count'], 'Key/locator/count bijection')
    pages, _ = catalog._table_pages(data, table)
    require(structure.map_pages(data, table['maps']['available'], 'available') <= set(pages), 'Available ownership')
    return dict(count=len(entries), nodes=nodes, data_pages=pages)


def verify_inputs():
    plan = json.loads(PLAN.read_text())
    for name, sha in plan['inputs'].items(): require(identity(ROOT/name)['sha256'] == sha, 'Input pin: '+name)
    return plan


def build_report(result, outbox, plan):
    observations, reasons = [], []
    try:
        require(result['document_type'] == 'dao_indexed_boundary_result' and result['plan_sha256'] == identity(PLAN)['sha256'] and result['environment'] == dict(process_bits=32,provider='DAO.DBEngine.36') and result['error'] is None and result['retention_failures'] == [] and result['mutation_started'] is True, 'Acquisition failure')
        require(set(result['captures']) == {f"{a['name']}-{r}.mdb" for a in plan['arms'] for r in ('original','candidate','control')}, 'Complete captures')
        require(set(result['operations']) == {'space','eof','duplicate'}, 'Operations inventory')
        for name in ('space','eof'): require(result['operations'][name] == dict(status='inserted'), 'Native insertion')
        duplicate = result['operations']['duplicate']
        require(duplicate['status'] == 'duplicate' and 3022 in duplicate['numbers'], 'Native duplicate rejection')
        for arm in plan['arms']:
            snapshots = {}; raw_counts = {}
            for role in ('original','candidate','control'):
                name = f"{arm['name']}-{role}.mdb"; capture = result['captures'][name]; path = outbox/name
                require(capture['before'] == capture['after'] == identity(path), 'Read-only identity')
                if role != 'control': require(identity(path) == plan['images'][name], 'Pinned public image')
                snapshots[role] = expected(capture['snapshot'],arm,role)
                table, rows = definition(path.read_bytes())
                require(table['row_count'] == table['physical_indexes'][0]['entry_count'] == len(rows), 'Fresh insertion count correlation')
                raw_counts[role] = raw_check(path.read_bytes(), arm, role)

            require(snapshots['candidate'] == snapshots['control'], 'Full native control comparison')
            metadata = []
            for snapshot in snapshots.values():
                value = copy.deepcopy(snapshot); value.pop('traversal'); value.pop('seek'); value['user_tables'][0].pop('rows'); metadata.append(value)
            require(all(m == metadata[0] for m in metadata), 'Unrelated metadata/Notes preservation')
            patch = patch_check((outbox/f"{arm['name']}-original.mdb").read_bytes(),(outbox/f"{arm['name']}-candidate.mdb").read_bytes(),arm)
            observations.append(dict(arm=arm['name'],patch=patch,raw=raw_counts))
    except (ValueError,KeyError,TypeError,OSError,catalog.DecodeError) as error: reasons.append(str(error))
    return dict(document_type='dao_indexed_boundary_report',outcome='no_outcome' if reasons else 'observed_accepted',plan_sha256=identity(PLAN)['sha256'],reasons=reasons,observations=observations,development_only=True,compatibility_claim=False,support_matrix_movement=False)


def preflight(images):
    plan = verify_inputs()
    require(subprocess.check_output(['git','show',f'HEAD:{PLAN.relative_to(ROOT)}'],cwd=ROOT) == PLAN.read_bytes(), 'Plan not committed')
    for name,pin in plan['images'].items(): require(identity(images/name) == pin, 'Image pin: '+name)
    for arm in plan['arms']: patch_check((images/f"{arm['name']}-original.mdb").read_bytes(),(images/f"{arm['name']}-candidate.mdb").read_bytes(),arm)
    return plan


def analyze(outbox):
    plan = verify_inputs(); report = build_report(json.loads((outbox/'result.json').read_text(encoding='utf-8-sig')),outbox,plan)
    report['result_sha256'] = identity(outbox/'result.json')['sha256']; (outbox/'report.json').write_text(canonical(report)+'\n'); print(report['outcome'])


def dispatch(args):
    plan=preflight(args.images);require(re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id),'Run id');shared=args.shared_root.resolve();inbox=shared/'inbox'/args.run_id;outbox=shared/'outbox'/args.run_id;require(not inbox.exists() and not outbox.exists(),'Used run; no retry');inbox.mkdir(parents=True)
    for name in plan['images']:shutil.copyfile(args.images/name,inbox/name)
    shutil.copyfile(ROOT/SCRIPT,inbox/'script.ps1');shutil.copyfile(ROOT/'oracle/windows-dao/scripts/field_update.ps1',inbox/'field_update.ps1');shutil.copyfile(PLAN,inbox/PLAN.name)
    spec=importlib.util.spec_from_file_location('transport',ROOT/'scripts/windows-dao-ps.py');transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
    command=['ssh','-p',args.port,'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','IdentitiesOnly=yes','-i',args.identity,f'{args.user}@{args.host}','powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',transport.encoded(transport.guest_script(args.remote_shared_root,args.run_id,'script.ps1'))]
    done=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=900);outbox.mkdir(exist_ok=True);(outbox/'ssh.txt').write_bytes(done.stdout+done.stderr)
    require((outbox/'result.json').exists(),'Missing result; no retry');analyze(outbox);require(done.returncode==0,'Guest failed; no retry')

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('preflight');c.add_argument('--images',type=Path,required=True);c=sub.add_parser('analyze');c.add_argument('outbox',type=Path)
    c=sub.add_parser('run');c.add_argument('--images',type=Path,required=True);c.add_argument('--run-id',required=True);c.add_argument('--shared-root',type=Path,required=True)
    for n,d in [('host','127.0.0.1'),('port','2222'),('user','jet3runner'),('identity',str(Path.home()/'.ssh/jet3-dao')),('remote-shared-root',r'\\host.lan\Data')]:c.add_argument('--'+n,default=d)
    a=p.parse_args()
    if a.command=='preflight':preflight(a.images);print('Committed inputs/images match.')
    elif a.command=='analyze':analyze(a.outbox)
    else:dispatch(a)
if __name__=='__main__':main()
