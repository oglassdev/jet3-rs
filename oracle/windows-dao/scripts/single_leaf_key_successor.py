#!/usr/bin/env python3
"""EXP-0185: distinct typed-setter successor; original EXP-0179 stays frozen."""
import argparse
import importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('_key_successor',ROOT/'oracle/windows-dao/scripts/single_leaf_key.py')
original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
original.PLAN=ROOT/'oracle/windows-dao/acquisition/single-leaf-key-successor.plan.json'
original.SCRIPT='oracle/windows-dao/scripts/single_leaf_key_successor.ps1'

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('preflight');p.add_argument('--images',type=Path,required=True)
    p=sub.add_parser('analyze');p.add_argument('outbox',type=Path)
    p=sub.add_parser('run');p.add_argument('--images',type=Path,required=True);p.add_argument('--run-id',required=True);p.add_argument('--shared-root',type=Path,required=True)
    for name,default in [('host','127.0.0.1'),('port','2222'),('user','jet3runner'),('identity',str(Path.home()/'.ssh/jet3-dao')),('remote-shared-root',r'\\host.lan\Data')]:p.add_argument('--'+name,default=default)
    args=parser.parse_args()
    if args.command=='preflight':original.preflight(args.images);print('Committed successor inputs/images match.')
    elif args.command=='analyze':original.analyze(args.outbox)
    else:original.dispatch(args)
