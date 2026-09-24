"""Registry suites: a Rust example writes candidate images, DAO replays the same recipe on
the VM, and the suite module compares both, independently decoding every image.

dao.py calls `run` for specs with a "registry" key: {"registry": module, "example": name}.
A suite module provides:

    SCRIPT                                  its PowerShell producer (dot-sources Common.ps1, Rows.ps1)
    EXTRA                                   optional extra guest files
    prepare(images, revision, spec, stdout) checks the candidates and writes the manifest
    aggregate(outbox)                       optional; combines per-case worker results
    evaluate(images, outbox) -> dict        compares; `status` is 'accepted' on a match
    prepare_continue(images, outbox, output, generator, revision)
                                            optional second round on native DAO outputs

Every stage keeps its inputs, logs, VM outbox and comparison under the suite directory.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import subprocess
import traceback

import dao
from registry import common


class Stage(RuntimeError):
    pass


class Run:
    def __init__(self, name: str, spec: dict, out: Path, context):
        self.name, self.spec, self.out, self.context = name, spec, out, context
        self.module = importlib.import_module(f'registry.{spec["registry"]}')
        self.generator = dao.ROOT / 'target/debug/examples' / spec['example']
        source = context.source
        self.revision = source['revision'] + ('+dirty' if source['dirty'] else '')
        self.report = {'suite': name, 'source': source, 'provenance': spec.get('provenance', []),
                       'registry': spec['registry'], 'example': spec['example'], 'stages': [], 'outcome': 'failed'}

    def stage(self, name: str, function):
        record = {'name': name, 'status': 'failed'}
        self.report['stages'].append(record)
        try:
            detail = function()
        except Exception as error:
            record['error'] = f'{type(error).__name__}: {error}'
            (self.out / f'{name}.traceback.txt').write_text(traceback.format_exc())
            raise Stage(record['error']) from error
        record['status'] = 'pass'
        if isinstance(detail, dict):
            record.update(detail)
        return detail

    def command(self, arguments, label: str) -> str:
        done = subprocess.run([str(a) for a in arguments], cwd=dao.ROOT, capture_output=True, text=True)
        (self.out / f'{label}.stdout.log').write_text(done.stdout)
        (self.out / f'{label}.stderr.log').write_text(done.stderr)
        if done.returncode:
            tail = done.stderr.strip().splitlines()[-1:] or ['']
            raise RuntimeError(f'{label} exited {done.returncode}: {tail[0][-300:]}')
        return done.stdout

    def dao_round(self, label: str, images: Path, retain: Path) -> None:
        """One VM job over `images`, then aggregation and comparison. A failed job is still compared."""
        files = [common.REGISTRY / 'Rows.ps1', *getattr(self.module, 'EXTRA', []),
                 *sorted(p for p in images.iterdir() if p.is_file())]
        vm_error = None
        try:
            self.stage(label, lambda: self.context.vm.run(self.module.SCRIPT, files, f'{self.name}-{label}', retain) and None)
        except Stage as error:
            vm_error = error

        def compare():
            if hasattr(self.module, 'aggregate'):
                self.module.aggregate(retain)
            comparison = self.module.evaluate(images, retain)
            path = dao.new_file(self.out / f'{label}.comparison.json')
            dao.write_json(path, comparison)
            cases = {c['name']: c.get('status') for c in comparison.get('cases', []) if 'name' in c}
            if comparison.get('status') != 'accepted':
                raise RuntimeError(f'{comparison.get("error")} (cases {cases}; see {path.name})')
            return {'comparison': path.name, 'cases': cases}

        self.stage(f'{label}-compare', compare)
        if vm_error:
            raise vm_error

    def run(self) -> dict:
        images = self.out / 'images'
        example = self.spec['example']
        dao.write_json(self.out / 'spec.json', self.spec)
        try:
            self.stage('build', lambda: self.command(['cargo', 'build', '--locked', '-p', 'jet3', '--example', example], 'build') and None)
            stdout = self.stage('generate', lambda: self.command([self.generator, images], 'generate'))
            self.stage('prepare', lambda: self.module.prepare(images, self.revision, self.spec, stdout) and None)
            self.dao_round('dao', images, self.out / 'dao')
            if hasattr(self.module, 'prepare_continue'):
                continued = self.out / 'continuation' / 'images'
                self.stage('continuation-prepare', lambda: self.module.prepare_continue(
                    images, self.out / 'dao', continued, self.generator, self.revision) and None)
                self.dao_round('continuation', continued, self.out / 'continuation' / 'dao')
            self.report['outcome'] = 'matched'
        except Stage:
            pass
        finally:
            dao.write_json(dao.new_file(self.out / 'report.json'), self.report)
        return self.report


def run(name: str, spec: dict, out: Path, context) -> dict:
    return Run(name, spec, out, context).run()
