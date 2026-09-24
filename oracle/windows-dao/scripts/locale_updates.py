#!/usr/bin/env python3
"""Paired six-locale row and schema edits for EXP-0309/0310."""
from __future__ import annotations
import argparse
import copy
from pathlib import Path
import relationship_forms_suite as f

LOCALES = [('general', 0x409, 1252), ('nordic', 0x41d, 1252), ('spanish', 0x40a, 1252),
           ('dutch', 0x413, 1252), ('cyrillic', 0x419, 1251), ('greek', 0x408, 1253)]


def host(raw):
    return ''.join(bytes([b]).decode('cp1252') if b not in (0x81,0x8d,0x8f,0x90,0x9d) else chr(b) for b in raw)


def literal(raw):
    return "'" + host(raw).replace("'", "''") + "'"


def plan(out):
    out.mkdir(parents=True)
    jobs, cases = dict(inputs=[], edits=[]), []
    for locale, lang, cp in LOCALES:
        codepage = 'cp' + str(cp)
        extended = bytes([0xc1]).decode(codepage)
        def native_names(request):
            if isinstance(request, str):
                return host(request.encode(codepage))
            if isinstance(request, list):
                return [native_names(x) for x in request]
            if isinstance(request, dict):
                return {k: native_names(v) for k,v in request.items()}
            return request
        ops = [f.table('Items', [f.col('id','long'),f.col('code','text',255),f.col('spare','long')]),
               f.table('P',[f.col('id','long'),f.col('code','text',63)]),
               f.table('C',[f.col('id','long'),f.col('code','text',63)]),
               f.table('Watch',[f.col('id','long'),f.col('memo','memo'),f.col('ole','long_binary')]),
               f.sql('INSERT INTO Watch (id) VALUES (1)'),
               dict(op='payload',table='Watch',key='id',id=1,column='memo',length=4200,seed=3),
               dict(op='payload',table='Watch',key='id',id=1,column='ole',length=2700,seed=7),
               f.sql('CREATE UNIQUE INDEX ux ON Items (code)'),
               f.sql('CREATE INDEX dx ON Items (code DESC)'),
               f.sql('CREATE INDEX cx ON Items (code DESC, id)'),
               f.sql('CREATE UNIQUE INDEX ux ON P (code)')]
        samples=[b'Case',b'CH',b'LL',b' a',bytes([0xc1]),bytes([0xe1]),bytes([0xdf])]
        for width in [1,3,127,128,129,254,255]:
            samples += [(f'{i:03}'.encode()+bytes([b])*(width-3))[:width] for i,b in [(100+width,0xc1),(400+width,0xe1),(700+width,0x61)]]
        # Prefixes avoid duplicate full keys; preserve expansion-heavy shortened keys.
        samples=list(dict.fromkeys(b'%02d'%i + v[:253] for i,v in enumerate(samples)))
        for i,value in enumerate(samples,1):ops.append(f.sql(f'INSERT INTO Items (id, code, spare) VALUES ({i},{literal(value)},{i})'))
        for table in ['P','C']:
            ops += [f.sql(f'INSERT INTO {table} (id, code) VALUES (1,\'Case\')'),
                    f.sql(f'INSERT INTO {table} (id, code) VALUES (2,{literal(bytes([0xc1]))})')]
        ops += [f.relation('Base','P','C',4352,[('code','code')]),
                dict(op='query',name='SavedSelect',sql='SELECT id, code FROM Items ORDER BY id')]
        jobs['inputs'].append(dict(name=locale,locale=f';LANGID=0x{lang:04x};CP={cp};COUNTRY=0',ops=ops))
        local=[]
        def add(name,steps,**kwargs):
            affected=kwargs.pop('affected_tables',None)
            case=f.case(name,locale,steps,**kwargs)
            if affected:case['affected_tables']=affected
            local.append(case)
        def schema(request,sql=None):return f.schema(request,**({'dao':{'sql':sql}} if sql else {}))
        changed=b'Changed-'+bytes([0xc1])*120
        add('rows',[
            f.mutate(dict(operation='update',table='Items',column=1,value={'text':list(changed)}),f'UPDATE Items SET code={literal(changed)} WHERE id=1',locate=dict(table='Items',id=1)),
            f.mutate(dict(operation='insert',table='Items',values=[{'long':999},{'text':list(bytes([0xc1])*255)},None]),f'INSERT INTO Items (id, code) VALUES (999,{literal(bytes([0xc1])*255)})'),
            f.mutate(dict(operation='delete',table='Items'),'DELETE FROM Items WHERE id=2',locate=dict(table='Items',id=2))])
        add('duplicate', [f.mutate(dict(operation='insert',table='Items',values=[{'long':999},{'text':list(samples[0].upper())},None]),f'INSERT INTO Items (id,code) VALUES (999,{literal(samples[0].upper())})',expect=1)],kind='residue',dao_error=3022)
        ix='ch'+extended
        add('indexes',[schema(dict(operation='create_index',table='Items',index=dict(name=ix,kind='ordinary',fields=[dict(column='spare'),dict(column='code',direction='descending')])),f'CREATE INDEX [{host(ix.encode(codepage))}] ON Items (spare,code DESC)'),
                       schema(dict(operation='rename_index',table='Items',index=ix,name='ll'+extended))])
        field='Field'+extended
        add('column',[schema(dict(operation='create_column',table='Items',column=f.col(field,'text',32))),
                      schema(dict(operation='rename_column',table='Items',column=field,name='Renamed'+extended)),
                      schema(dict(operation='set_column_properties',table='Items',column='Renamed'+extended,description='Description '+extended))])
        table='Table'+extended
        add('table',[schema(dict(operation='create_table',table=dict(name=table,columns=[f.col('id','long'),f.col(field,'text',32)],indexes=[f.primary(),dict(name=ix,kind='unique',fields=[dict(column=field)])]))),
                     schema(dict(operation='rename_table',table=table,name='Renamed'+extended))])
        add('drop-column',[schema(dict(operation='drop_column',table='Items',column='spare'))])
        add('replace-index',[schema(dict(operation='replace_index',table='Items',index='dx',replacement=dict(name=ix,kind='ordinary',fields=[dict(column='code')])),'DROP INDEX dx ON Items'),
                             ])
        # A replacement is one Rust operation and two native statements.
        local[-1]['steps'][0]['dao']={'sql':f'DROP INDEX dx ON Items'}
        local[-1]['native_extra']=[{'sql':f'CREATE INDEX [{host(ix.encode(codepage))}] ON Items (code)'}]
        add('relation', [f.create(f.rel('Relation'+extended,'P','C',['code'],['code']))])
        add('rename-related',[schema(dict(operation='rename_table',table='P',name='Parents'+extended)),
                              schema(dict(operation='rename_column',table='C',column='code',name=field))])
        add('cascade-update',[f.mutate(dict(operation='update',table='P',column=1,value={'text':list(b'New'+bytes([0xc1]))}),f'UPDATE P SET code={literal(b"New"+bytes([0xc1]))} WHERE id=2',locate=dict(table='P',id=2))],affected_tables=['C'])
        add('cascade-delete',[f.mutate(dict(operation='delete',table='P'),'DELETE FROM P WHERE id=2',locate=dict(table='P',id=2))],affected_tables=['C'])
        add('orphan',[f.mutate(dict(operation='insert',table='C',values=[{'long':999},{'text':list(b'orphan')}]),"INSERT INTO C (id,code) VALUES (999,'orphan')",expect=1)],kind='residue',dao_error=3201)
        for case in local:
            case['name']=locale+'-'+case['name'];case['code_page']=cp
            suffix=case['name'][len(locale)+1:]
            dates={'indexes':['Items'],'column':['Items'],'drop-column':['Items'],'replace-index':['Items'],
                   'table':[table,'Renamed'+extended], 'relation':['Relation'+extended,'P','C'],
                   'rename-related':['P','Parents'+extended,'C','Base']}.get(suffix,[])
            case['normalize_table_dates']=[host(name.encode(codepage)) for name in dates]
            cases.append(case)
            steps=[step.get('dao') or {'request':native_names(step['request'])} for step in case['steps']]
            jobs['edits'].append(dict(name=case['name'],input=locale,steps=steps+case.pop('native_extra',[])))
    f.write(out/'jobs.json',jobs);f.write(out/'edits-template.json',dict(cases=cases))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);args=p.parse_args();plan(args.out)
