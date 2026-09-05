#!/usr/bin/env python3
"""EXP-0205: corrected Rust candidates from unchanged EXP-0197 originals/controls."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[3]
OLD_PLAN=ROOT/'oracle/windows-dao/acquisition/row-update-candidate.plan.json'
PLAN=ROOT/'oracle/windows-dao/acquisition/row-update-successor.plan.json'
spec=importlib.util.spec_from_file_location('_successor_candidate',ROOT/'oracle/windows-dao/scripts/row_update_candidate.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
base.PLAN=PLAN
original_entries=base.entries
identity=base.identity


def entries(document,phase,plan):
    # Original create receipts retain their original plan identity. Fresh observe
    # receipts bind the successor. No retained receipt or consumed input is edited.
    current=base.PLAN
    try:
        base.PLAN=OLD_PLAN if phase=='create' else PLAN
        return original_entries(document,phase,plan)
    finally:base.PLAN=current


base.entries=entries


def verify():
    plan=base.verify_inputs();root=Path(plan['retained_root'])
    for name,wanted in plan['retained'].items():
        if identity(root/name)!=wanted:raise ValueError('Retained input mismatch: '+name)
    report=json.loads((root/'report.json').read_text());result=json.loads((root/'result.json').read_text())
    if report['outcome']!='no_outcome' or result['updates'] or result['phase']!='unix_update':raise ValueError('Original outcome identity/state')
    captures=entries(json.loads((root/'create.json').read_text(encoding='utf-8-sig')),'create',plan)
    for arm in plan['arms']:
        for replica in range(1,4):
            for role in ('original','control'):
                item=captures[arm['name'],replica,role]
                if identity(root/item['file'])!=item['after']:raise ValueError('Original retained capture mismatch')
                base.expected(item['snapshot'],arm,role,plan)
    return plan


def preflight():
    plan=verify();base.preflight();return plan


def analyze(outbox):
    plan=verify();result=json.loads((outbox/'result.json').read_text())
    report=base.build_report(result,outbox,plan)
    for update in result.get('updates',[]):
        name=f"{update['arm']}-r{update['replica']}-rust.mdb"
        if update['rust']!=plan['candidates'].get(name):
            report['outcome']='no_outcome';report['reasons'].append('Pinned candidate identity differs: '+name)
    report.update(experiment='EXP-0205',outcome_provenance='EXP-0206',original_outcome='EXP-0198',original_report_sha256=plan['retained']['report.json']['sha256'],result_sha256=identity(outbox/'result.json')['sha256'])
    (outbox/'report.json').write_text(base.canonical(report)+'\n');print(report['outcome'])


def dispatch(args):
    plan=preflight();retained=Path(plan['retained_root']);shared=args.shared_root.resolve()
    if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-z0-9-]{1,24}',args.run_id):raise ValueError('Invalid run id')
    run_id=args.run_id+'-observe';outbox=shared/'outbox'/args.run_id;inbox=shared/'inbox'/run_id;guest=shared/'outbox'/run_id
    if any(p.exists() for p in (outbox,inbox,guest)):raise ValueError('Used run; no retry/resume')
    subprocess.run(['cargo','build','-p','jet3','--example','row_update_candidate'],cwd=ROOT,check=True)
    spec=importlib.util.spec_from_file_location('_successor_transport',ROOT/'scripts/windows-dao-ps.py');transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
    outbox.mkdir(parents=True)
    result=dict(document_type='dao_row_update_candidate_result',producer_os='posix',source_revision=plan['source_revision'],acquisition_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT).decode().strip(),plan_sha256=identity(PLAN)['sha256'],phase='copy_retained_inputs',error=None,phases={},updates=[])
    try:
        shutil.copyfile(retained/'create.json',outbox/'create.json');result['phases']['create']=identity(outbox/'create.json')
        for arm in plan['arms']:
            for replica in range(1,4):
                prefix=f"{arm['name']}-r{replica}"
                for role in ('original','control'):
                    name=prefix+'-'+role+'.mdb';shutil.copyfile(retained/name,outbox/name)
                    if identity(outbox/name)!=plan['retained'][name]:raise ValueError('Copied retained identity')
                original=outbox/(prefix+'-original.mdb');rust=outbox/(prefix+'-rust.mdb');before=identity(original)
                result['phase']='unix_update/'+prefix
                command=['cargo','run','--quiet','-p','jet3','--example','row_update_candidate','--',str(original),str(rust),arm['table'],str(arm['selected_id']),arm['name']]
                done=subprocess.run(command,cwd=ROOT,capture_output=True,timeout=120);(outbox/(prefix+'-unix.txt')).write_bytes(done.stdout+done.stderr)
                if done.returncode:raise ValueError('Public row update failed: '+prefix)
                if identity(rust)!=plan['candidates'][rust.name]:raise ValueError('Pinned candidate identity mismatch: '+prefix)
                receipt=json.loads(done.stdout);base.patch_check(original.read_bytes(),rust.read_bytes(),arm,receipt)
                if identity(original)!=before:raise ValueError('Unix original changed')
                result['updates'].append(dict(arm=arm['name'],replica=replica,original_before=before,original_after=identity(original),rust=identity(rust),locator=receipt))
        verify();result['phase']='observe';inbox.mkdir(parents=True)
        # The frozen producer reads this conventional filename; its actual plan
        # bytes/hash are the committed successor, and phase is observe exclusively.
        shutil.copyfile(PLAN,inbox/OLD_PLAN.name);shutil.copyfile(ROOT/base.SCRIPT,inbox/'script.ps1');shutil.copyfile(ROOT/'oracle/windows-dao/scripts/field_update.ps1',inbox/'field_update.ps1');(inbox/'phase.txt').write_text('observe')
        for path in outbox.glob('*.mdb'):shutil.copyfile(path,inbox/path.name)
        command=['ssh','-p',args.port,'-o','BatchMode=yes','-o','ConnectTimeout=15','-o','IdentitiesOnly=yes','-i',args.identity,f'{args.user}@{args.host}','powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',transport.encoded(transport.guest_script(args.remote_shared_root,run_id,'script.ps1'))]
        done=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,timeout=300);(outbox/'observe-ssh.txt').write_bytes(done.stdout+done.stderr)
        shutil.copyfile(guest/'result.json',outbox/'observe.json');result['phases']['observe']=identity(outbox/'observe.json')
        for path in guest.glob('*.mdb'):
            destination=outbox/path.name
            if destination.exists() and identity(destination)!=identity(path):raise ValueError('Transferred original/control/Rust image changed')
            if not destination.exists():shutil.copyfile(path,destination)
        entries(json.loads((outbox/'observe.json').read_text(encoding='utf-8-sig')),'observe',plan)
        if done.returncode:raise ValueError('Guest observe phase failed')
        verify();result['phase']='complete'
    except (OSError,ValueError,subprocess.SubprocessError) as error:result['error']=type(error).__name__+': '+str(error)
    finally:(outbox/'result.json').write_text(base.canonical(result)+'\n')
    analyze(outbox)


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('preflight');report=commands.add_parser('analyze');report.add_argument('outbox',type=Path)
    run=commands.add_parser('run');run.add_argument('--run-id',required=True);run.add_argument('--shared-root',type=Path,required=True)
    for name,default in [('host','127.0.0.1'),('port','2222'),('user','jet3runner'),('identity',str(Path.home()/'.ssh/jet3-dao')),('remote-shared-root',r'\\host.lan\Data')]:run.add_argument('--'+name,default=default)
    args=parser.parse_args()
    if args.command=='preflight':preflight();print('Committed successor and retained inputs match.')
    elif args.command=='analyze':analyze(args.outbox)
    else:dispatch(args)


if __name__=='__main__':main()
