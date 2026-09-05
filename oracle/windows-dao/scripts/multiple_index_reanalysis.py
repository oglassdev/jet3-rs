#!/usr/bin/env python3
"""EXP-0201: read-only secondary comparison of a finite failed-insert count residue."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import multiple_index as frozen
ROOT=Path(__file__).resolve().parents[3]
PLAN=ROOT/'oracle/windows-dao/acquisition/multiple-index-reanalysis.plan.json'
identity,canonical,require=frozen.identity,frozen.canonical,frozen.require

def verify_inputs():
    plan=json.loads(PLAN.read_text())
    for name,sha in plan['inputs'].items():require(identity(ROOT/name)['sha256']==sha,'Secondary input pin: '+name)
    frozen.verify_inputs()
    return plan

def verify_artifacts(plan):
    source=Path(plan['source_directory']).resolve()
    for name,expected in plan['artifacts'].items():require(identity(source/name)==expected,'Retained artifact pin: '+name)
    return source

def preflight():
    plan=verify_inputs();require(subprocess.check_output(['git','show',f'HEAD:{PLAN.relative_to(ROOT)}'],cwd=ROOT)==PLAN.read_bytes(),'Uncommitted secondary plan');verify_artifacts(plan);return plan

def checked_raw(data,arm,overrides):
    sha=hashlib.sha256(data).hexdigest();override=overrides.get(sha)
    if override is None:return frozen.raw_check(data,arm)
    require(arm['name']=='three-long','Count residue arm')
    table=frozen.catalog._definition(data,override['table_root']);index=table['physical_indexes'][0]
    require(index['entry_count']==override['stored_count']==202 and index['entry_count_offset']==override['offset'],'Exact stored count/offset')
    # Only four in-memory prefix bytes change for the original complete validator.
    # The original file and the observed count are retained unchanged in the report.
    normalized=bytearray(data);offset=index['entry_count_offset']
    require(normalized[offset:offset+4]==(202).to_bytes(4,'little'),'Prefix byte binding')
    normalized[offset:offset+4]=(201).to_bytes(4,'little')
    observed=frozen.raw_check(bytes(normalized),arm)
    require(observed['indexes'][0]['name']=='ZPrimary' and observed['indexes'][0]['entries']==observed['indexes'][0]['distinct']==201,'Primary complete inventory')
    observed['count_residue']=dict(index='ZPrimary',offset=offset,stored_count=202,actual_entries=201,actual_distinct=201,normalization='four private in-memory prefix bytes only')
    return observed

def add_override(overrides,sha,residue):
    if sha in overrides:
        fields=('table_root','offset','stored_count','actual_distinct')
        require(all(overrides[sha][field]==residue[field] for field in fields),'Conflicting residue image metadata')
    else:overrides[sha]=residue

def compare(plan,source):
    result=json.loads((source/'result.json').read_text());overrides={}
    require(len(plan['count_residues'])==6,'Six declared residue images')
    pairs={(p['arm'],p['replica']):p for p in result['pairs']}
    wanted={('three-long',r,role+'-duplicate-secondary') for r in range(1,4) for role in ('candidate','control')}
    actual=set()
    for residue in plan['count_residues']:
        key=(residue['arm'],residue['replica'],residue['role']);actual.add(key)
        pair=pairs[key[:2]];operation=pair['probes'][key[2]]
        require(operation['accepted'] is False and operation['error'] is not None and 3022 in operation['numbers'],'Exact secondary duplicate rejection')
        path=source/residue['file'];require(identity(path)==plan['artifacts'][residue['file']],'Residue image pin')
        require(pair['captures'][key[2]]['after']==identity(path),'Residue capture binding')
        sha=identity(path)['sha256'];add_override(overrides,sha,residue)
    require(actual==wanted,'Residue role inventory')
    # Private module preserves the original acquisition plan/source and all gates.
    spec=importlib.util.spec_from_file_location('_multiple_secondary',ROOT/'oracle/windows-dao/scripts/multiple_index.py')
    original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
    original.raw_check=lambda data,arm:checked_raw(data,arm,overrides)
    original_plan=frozen.verify_inputs();comparison=original.build_report(result,source,original_plan)
    return comparison

def build_report(plan,source):
    try:comparison=compare(plan,source)
    except (ValueError,KeyError,TypeError,OSError,frozen.catalog.DecodeError) as error:
        comparison=dict(outcome='no_outcome',reasons=[str(error)],observations=[])
    return dict(document_type='dao_multiple_index_secondary_report',plan_sha256=identity(PLAN)['sha256'],original_plan_sha256=identity(frozen.PLAN)['sha256'],original_result=identity(source/'result.json'),original_report=identity(source/'report.json'),outcome=comparison['outcome'],reasons=comparison['reasons'],observations=comparison['observations'],count_residue_scope=plan['count_residues'],post_acquisition_secondary=True,original_outcome='no_outcome',development_only=True,compatibility_claim=False,support_matrix_movement=False)

def analyze(output):
    plan=preflight();source=verify_artifacts(plan);output=output.resolve()
    require(not output.is_relative_to(source.parent),'Output must be outside original outbox tree')
    require(not output.exists(),'Output already exists')
    report=build_report(plan,source);verify_artifacts(plan)
    with output.open('x') as file:file.write(canonical(report)+'\n')
    print(report['outcome'])

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True);sub.add_parser('preflight');p=sub.add_parser('analyze');p.add_argument('output',type=Path);args=parser.parse_args()
    if args.command=='preflight':preflight();print('Committed secondary inputs and retained artifacts match.')
    else:analyze(args.output)
if __name__=='__main__':main()
