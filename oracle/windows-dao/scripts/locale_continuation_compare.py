#!/usr/bin/env python3
"""Compare complete DAO continuations of both six-locale output lineages."""
import argparse
import copy
import json
from pathlib import Path
from compare_schema_structure import differences, semantics, preserve
from locale_updates_compare import setup
import schema_edit_structure as raw
from locale_keys import LOCALES


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('keys','before','continued','readback','out'):
        p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();setup(args.keys);args.out.mkdir()
    rb=json.loads(args.readback.read_text());raw.require(rb['status']=='pass','complete readback status')
    files={f['file']:f for f in rb['files']}
    raw.require(set(files)=={f'{kind}-{locale}-continued.mdb' for kind in ('candidate','native') for locale in LOCALES}
                and len(files)==len(rb['files']),'complete continuation inventory')
    results=[]
    for locale in LOCALES:
        observations={};snapshots={};preservation={};headers={};row_orders={}
        spec=dict(name=locale+'-continued',steps=[dict(request=dict(operation='update',table='Items'))])
        for kind in ('candidate','native'):
            name=f'{kind}-{locale}-continued.mdb';path=args.continued/name
            before_path=args.before/f'{kind}-{locale}-rows.mdb'
            before=raw.observe(before_path);after=raw.observe(path)
            data=path.read_bytes();prior=before_path.read_bytes()
            raw.require(files[name]['identity']==dict(size=len(data),sha256=raw.sha(data)),'readback identity')
            rows={r['values']['id']:r['values'] for r in before['tables']['Items']['rows']}
            rows[999]['code']='ContinuedÁ';rows[1000]=dict(id=1000,code='NativeNext',spare=1000);rows.pop(3)
            actual={r['values']['id']:r['values'] for r in after['tables']['Items']['rows']}
            raw.require(actual==rows,'complete continuation row model')
            header=[dict(offset=i,before=a,after=b) for i,(a,b) in enumerate(zip(prior[:2048],data[:2048])) if a!=b]
            raw.require(header == [dict(offset=1538,before=prior[1538],after=prior[1538]+3)],
                        'native header counter for three accepted operations')
            headers[kind]=header
            requests=[]
            for operation,ident in [('update',999),('delete',3)]:
                row,=[r for r in before['tables']['Items']['rows'] if r['values']['id']==ident]
                loc=row['locator']
                requests.append(dict(request=dict(operation=operation,table='Items',row=dict(page=loc['page'],slot=loc['row']))))
            preservation[kind]=preserve(before,after,data[:2048]+prior[2048:],data,dict(spec,steps=requests))
            observations[kind]=semantics(after,spec)
            snapshots[kind]=copy.deepcopy({k:v for k,v in files[name].items() if k not in ('file','identity')})
            tables=snapshots[kind]['tables']
            if len(tables)==1 and isinstance(tables[0],list):tables=tables[0]
            table,=[t for t in tables if t['name']=='Items']
            observed_rows=table['rows']
            if len(observed_rows)==1 and isinstance(observed_rows[0],list):observed_rows=observed_rows[0]
            raw.require(sorted(observed_rows,key=lambda r:r['id'])==sorted(rows.values(),key=lambda r:r['id']),
                        'complete DAO continuation row model')
            row_orders[kind]=[r['id'] for r in observed_rows]
            # Unordered table scans follow physical page placement; index traversals stay exact.
            table['rows']=sorted(observed_rows,key=lambda r:r['id'])
            (args.out/(name+'.raw.json')).write_text(json.dumps(after,indent=2,sort_keys=True)+'\n')
        raw.require(not differences(observations['candidate'],observations['native']),'complete raw continuation semantics')
        raw.require(not differences(snapshots['candidate'],snapshots['native']),'complete DAO continuation semantics')
        results.append(dict(name=locale,preservation=preservation,header_changes=headers,physical_row_orders=row_orders))
    (args.out/'REPORT.json').write_text(json.dumps(dict(status='pass',pairs=len(results),results=results),indent=2,sort_keys=True)+'\n')
    print('pass',len(results),'complete continuation pairs')

if __name__=='__main__':main()
