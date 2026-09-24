#!/usr/bin/env python3
"""DAO oracle runner: suite specs, local Windows VM transport, run layout and reports.

    dao.py list
    dao.py run SUITE... --out DIR          prepare, run DAO, observe and compare
    dao.py compare RUN_DIR/SUITE           re-evaluate retained outputs (new report file)
    dao.py ps SCRIPT.ps1 [--with FILE]...  run one ad-hoc script under x86 DAO

Each run writes to a new directory. Every stage keeps its inputs, VM outboxes and logs;
failed stages and comparisons are reported, never discarded.
"""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ORACLE = Path(__file__).resolve().parent
ROOT = ORACLE.parents[1]
SUITES = ORACLE / 'suites'
sys.path.insert(0, str(ORACLE))
sys.path.insert(1, str(SUITES))

import compare  # noqa: E402
import generate  # noqa: E402
import keys  # noqa: E402
import structure  # noqa: E402

# The accepted x86 DAO 3.6 provider of every recorded differential run (EXP-0290 onward).
PROVIDER = {'provider': 'DAO.DBEngine.36', 'version': '3.6', 'bits': 32,
            'dll_sha256': '4cc28a5be8dc7425a4c4c1ef275ca392f18be35d70232e777dce6d9f3b4d79ac'}


# --- Files ---------------------------------------------------------------------------

def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(path: Path) -> dict:
    data = Path(path).read_bytes()
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def write_json(path: Path, value) -> None:
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def new_file(path: Path) -> Path:
    """`path`, or the first free `stem-N` sibling, so reports are never overwritten."""
    candidate, number = path, 1
    while candidate.exists():
        number += 1
        candidate = path.with_name(f'{path.stem}-{number}{path.suffix}')
    return candidate


# --- VM transport --------------------------------------------------------------------

class VmError(RuntimeError):
    pass


class Vm:
    """Stages files in the shared inbox and runs one script under x86 PowerShell over SSH."""

    def __init__(self, shared_root, host='127.0.0.1', port='2222', user='jet3runner',
                 key=str(Path.home() / '.ssh/jet3-dao'), remote_root=r'\\host.lan\Data', timeout=3600):
        self.shared = Path(shared_root).expanduser().resolve()
        self.host, self.port, self.user, self.key = host, str(port), user, key
        self.remote_root, self.timeout = remote_root, timeout

    @classmethod
    def from_args(cls, args):
        if not args.shared_root:
            raise SystemExit('--shared-root or JET3_WINDOWS_SHARED_ROOT is required')
        return cls(args.shared_root, args.host, args.port, args.user, args.identity, args.remote_shared_root, args.timeout)

    def guest_script(self, run_id: str, script: str) -> str:
        config = {'inbox': ntpath.join(self.remote_root, 'inbox', run_id),
                  'outbox': ntpath.join(self.remote_root, 'outbox', run_id), 'script': script, 'run_id': run_id}
        blob = base64.b64encode(json.dumps(config).encode()).decode()
        return (
            "$ErrorActionPreference='Stop';"
            f"$c=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{blob}'))|ConvertFrom-Json;"
            "$work=Join-Path $env:LOCALAPPDATA ('jet3-rs-dev\\ps\\'+$c.run_id);"
            "New-Item -ItemType Directory -Force -Path $work | Out-Null;"
            "New-Item -ItemType Directory -Force -Path $c.outbox | Out-Null;"
            "Copy-Item -Path (Join-Path $c.inbox '*') -Destination $work -Recurse -Force;"
            "$env:JET3_WORK=$work;$env:JET3_OUTBOX=$c.outbox;"
            "$winps=Join-Path $env:WINDIR 'SysWOW64\\WindowsPowerShell\\v1.0\\powershell.exe';"
            "$log=Join-Path $work 'log.txt';"
            "$ErrorActionPreference='Continue';"
            "& $winps -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $work $c.script) *> $log;"
            "$code=$LASTEXITCODE;$ErrorActionPreference='Stop';"
            "Copy-Item $log (Join-Path $c.outbox 'log.txt') -Force;"
            "Set-Content -Path (Join-Path $c.outbox 'exit.txt') -Value $code;"
            "Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue;"
            "exit $code"
        )

    def run(self, script: Path, files, label: str, retain: Path | None = None) -> Path:
        """Runs `script` with Common.ps1 and `files`; returns the outbox (copied to `retain`)."""
        slug = re.sub(r'[^a-z0-9-]+', '-', label.lower()).strip('-')[:40]
        run_id = f'{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{slug}-{uuid.uuid4().hex[:6]}'
        inbox, outbox = self.shared / 'inbox' / run_id, self.shared / 'outbox' / run_id
        inbox.mkdir(parents=True)
        # copyfile, not copy2: copied SELinux labels would deny the container access.
        for path in [ORACLE / 'Common.ps1', Path(script), *files]:
            shutil.copyfile(path, inbox / Path(path).name)
        command = ['ssh', '-p', self.port, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'IdentitiesOnly=yes',
                   '-i', self.key, f'{self.user}@{self.host}', 'powershell.exe', '-NoProfile', '-NonInteractive',
                   '-EncodedCommand', base64.b64encode(self.guest_script(run_id, Path(script).name).encode('utf-16-le')).decode()]
        try:
            done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=self.timeout)
            code, transport = done.returncode, (done.stdout + done.stderr).decode('utf-8', 'replace')
        except subprocess.TimeoutExpired:
            code, transport = 124, f'exceeded {self.timeout} seconds'
        if retain is not None:
            retain.mkdir(parents=True, exist_ok=True)
            (retain / 'run-id.txt').write_text(run_id + '\n')
            (retain / 'transport.log').write_text(transport)
            if outbox.is_dir():
                shutil.copytree(outbox, retain, dirs_exist_ok=True)
        if code:
            raise VmError(f'{label}: guest exit {code} (run {run_id}); see {retain or outbox}/log.txt')
        return retain or outbox


def check_environment(environment: dict) -> None:
    mismatched = {k: environment.get(k) for k, v in PROVIDER.items() if environment.get(k) != v}
    if mismatched:
        raise VmError(f'provider differs from the accepted x86 DAO 3.6: {mismatched}')


# --- Specs ---------------------------------------------------------------------------

def load_spec(name: str) -> dict:
    spec = read_json(SUITES / f'{name}.json')
    if 'generate' in spec:
        generated = getattr(generate, spec['generate'])(**spec.get('generate_args', {}))
        spec = {**generated, **{k: v for k, v in spec.items() if k not in ('generate', 'generate_args')}}
    return expand(spec)


def expand(spec: dict) -> dict:
    """Resolves input inheritance and replicas into concrete inputs, creations and cases."""
    spec = copy.deepcopy(spec)
    raw_inputs = spec.get('inputs', {})

    def ops(name, seen=()):
        entry = raw_inputs[name]
        if name in seen:
            raise ValueError(f'input inheritance cycle at {name}')
        base = ops(entry['extends'], (*seen, name)) if 'extends' in entry else []
        return base + entry.get('ops', [])

    inputs = {}
    for name, entry in raw_inputs.items():
        resolved = {k: v for k, v in entry.items() if k not in ('extends', 'ops')}
        if 'ops' in entry or 'extends' in entry:
            resolved['ops'] = ops(name)
        inputs[name] = resolved
    replicas = spec.get('replicas')
    cases, creations = spec.get('cases', []), spec.get('creations', [])
    if replicas:
        inputs = {f'{n}-r{r}': dict(v) for n, v in inputs.items() for r in replicas}
        cases = [dict(c, name=f'{c["name"]}-r{r}', input=f'{c["input"]}-r{r}') for c in cases for r in replicas]
    creation_replicas = spec.get('creation_replicas')
    expanded = []
    for creation in creations:
        for replica in creation_replicas or [None]:
            suffix = f'-r{replica}' if replica else ''
            expanded.append(dict(creation, name=creation['name'] + suffix, candidate=creation['name']))
    inputs = {name: entry for name, entry in inputs.items() if not entry.get('template')}
    spec.update(inputs=inputs, cases=cases, creations=expanded, continuations=spec.get('continuations', []))
    for case in spec['cases']:
        for step in case['steps']:
            step.setdefault('command', 'schema')
        case['native_steps'] = len(case_native_ops(case))
    return spec


def native_ops(step: dict, code_page: int | None = None) -> list[dict]:
    """DAO operations equivalent to one Rust step: explicit `native` ops, or the request itself
    (in host names for a `code_page` database)."""
    if 'native' in step:
        native = step['native']
        return [op if 'op' in op else {'op': 'sql', 'text': op['sql']} for op in (native if isinstance(native, list) else [native])]
    request = step['request'] if code_page is None else compare.host_names(step['request'], code_page)
    return [{'op': 'request', 'command': step['command'], 'request': request}]


def case_native_ops(case: dict) -> list[dict]:
    return [op for step in case['steps'] for op in native_ops(step, case.get('code_page'))] + case.get('native_extra', [])


# --- Suite run -----------------------------------------------------------------------

class Stage(RuntimeError):
    pass


class SuiteRun:
    def __init__(self, name: str, spec: dict, out: Path, context):
        self.name, self.spec, self.out, self.context = name, spec, out, context
        self.report = {'suite': name, 'source': context.source, 'provenance': spec.get('provenance', []),
                       'stages': [], 'pairs': [], 'outcome': 'failed'}
        self.native_result = {}
        self.observations = {}
        self.candidate_failures = {}

    # Paths
    def native_dir(self):
        return self.out / 'native'

    def candidate_dir(self):
        return self.out / 'candidates'

    def input_path(self, name: str) -> Path:
        entry = self.spec['inputs'][name]
        if 'external' in entry:
            return self.external(entry)
        if 'from' in entry:
            return self.image(entry['from'])
        return self.native_dir() / f'input-{name}.mdb'

    def external(self, entry: dict) -> Path:
        """A retained file under the archive root, checked against its recorded SHA-256."""
        path = self.context.archive / entry['external'] if self.context.archive else None
        if path is None or not path.is_file():
            raise Stage(f'external file needs --archive with {entry["external"]}')
        if digest(path) != entry['sha256']:
            raise Stage(f'external file {entry["external"]} differs from its recorded SHA-256')
        return path

    def image(self, file: str) -> Path:
        for directory in (self.candidate_dir(), self.native_dir(), self.out / 'continued'):
            if (directory / file).is_file():
                return directory / file
        raise Stage(f'missing image {file}')

    def stage(self, name, function):
        record = {'name': name, 'status': 'failed'}
        self.report['stages'].append(record)
        try:
            detail = function()
            record['status'] = 'pass'
            if detail:
                record.update(detail)
        except (Stage, VmError, subprocess.SubprocessError, OSError, ValueError, KeyError) as error:
            record['error'] = f'{type(error).__name__}: {error}'
            raise Stage(record['error']) from error

    # Rust candidates
    def cli(self, arguments, log: Path, request=None):
        if request is not None:
            write_json(log.with_suffix('.request.json'), request)
            arguments = [*arguments, '--input', str(log.with_suffix('.request.json'))]
        done = subprocess.run([str(self.context.cli), *map(str, arguments)], capture_output=True, text=True)
        write_json(log.with_suffix('.result.json'), {'arguments': list(map(str, arguments)), 'returncode': done.returncode,
                                                     'stdout': done.stdout, 'stderr': done.stderr})
        return done

    def locate(self, path: Path, where: dict) -> dict:
        table = structure.tables(path.read_bytes())[where['table']]
        matches = [r for r in structure.rows(path.read_bytes(), table)
                   if {k.lower(): v for k, v in r['values'].items()}.get('id') == where['id']]
        if len(matches) != 1:
            raise Stage(f'{path.name}: expected one {where["table"]} row with id {where["id"]}')
        return {'page': matches[0]['locator']['page'], 'slot': matches[0]['locator']['row']}

    def apply_steps(self, target: Path, case: dict, logs: Path) -> list[dict]:
        events = []
        for number, step in enumerate(case['steps'], 1):
            request = copy.deepcopy(step['request'])
            if 'locate' in step:
                request['row'] = self.locate(target, step['locate'])
                step['request']['row'] = request['row']
            before = target.read_bytes()
            done = self.cli([step['command'], target], logs / f'{case["name"]}-{number:02d}', request)
            expected = int(step.get('expected_returncode', 0))
            events.append({'step': number, 'returncode': done.returncode})
            if done.returncode != expected:
                raise Stage(f'{case["name"]} step {number}: jet3-cli returned {done.returncode}, expected {expected}: {done.stderr[-300:]}')
            if expected and target.read_bytes() != before:
                raise Stage(f'{case["name"]} step {number}: refused operation changed the candidate')
        validation = self.cli(['validate', target, '--code-page', case.get('code_page', 1252)], logs / f'{case["name"]}-validate')
        if validation.returncode != int(case.get('validation_returncode', 0)):
            raise Stage(f'{case["name"]}: validate returned {validation.returncode}: {validation.stderr[-300:]}')
        return events

    def creation_candidates(self):
        directory = self.candidate_dir()
        (directory / 'logs').mkdir(parents=True, exist_ok=True)
        built = set()
        for creation in self.spec['creations']:
            if creation['candidate'] in built:
                continue
            built.add(creation['candidate'])
            target = directory / f'candidate-{creation["candidate"]}.mdb'
            done = self.cli(['create', target], directory / 'logs' / f'create-{creation["candidate"]}', creation['request'])
            if done.returncode:
                raise Stage(f'create {creation["candidate"]}: {done.stderr[-300:]}')
        return {'candidates': len(built)}

    def edit_candidates(self):
        directory = self.candidate_dir()
        (directory / 'logs').mkdir(parents=True, exist_ok=True)
        for case in self.spec['cases']:
            source = self.input_path(case['input'])
            target = directory / f'candidate-{case["name"]}.mdb'
            shutil.copyfile(source, target)
            try:
                self.apply_steps(target, case, directory / 'logs')
            except Stage as error:
                # The pair fails; the other cases still run.
                self.candidate_failures[case['name']] = str(error)
            if case.get('native') is False:
                # No native counterpart: DAO would accept or ignore what Rust refuses.
                shutil.copyfile(source, directory / f'native-{case["name"]}.mdb')
        write_json(directory / 'failures.json', self.candidate_failures)
        return {'candidates': len(self.spec['cases']), 'failed': sorted(self.candidate_failures)}

    # Native DAO
    def native_job(self):
        job, staged = {'inputs': [], 'edits': []}, {}
        for name, entry in self.spec['inputs'].items():
            if 'ops' in entry:
                job['inputs'].append({'name': name, 'file': f'input-{name}.mdb', 'locale': entry.get('locale', 'general'),
                                      'ops': entry['ops']})
        for creation in self.spec['creations']:
            native = creation.get('native', {'request': creation['request']})
            ops = native['ops'] if 'ops' in native else [{'op': 'table', 'table': t} for t in native['request']['tables']] + \
                [{'op': 'relationship', 'relationship': r} for r in native['request'].get('relationships', [])]
            job['inputs'].append({'name': creation['name'], 'file': f'native-{creation["name"]}.mdb',
                                  'locale': creation.get('locale', 'general'), 'ops': ops})
        built = {item['file'] for item in job['inputs']}
        for case in self.spec['cases']:
            steps = case_native_ops(case)
            if case.get('native') is False:
                continue
            entry = self.spec['inputs'][case['input']]
            source = f'input-{case["input"]}.mdb'
            if entry.get('from') in built:
                source = entry['from']  # built earlier in this job
            elif 'ops' not in entry:
                staged[source] = self.input_path(case['input'])
            job['edits'].append({'name': case['name'], 'source': source, 'file': f'native-{case["name"]}.mdb', 'steps': steps})
        return job, staged

    def native(self):
        job, staged = self.native_job()
        directory = self.native_dir()
        directory.mkdir(parents=True, exist_ok=True)
        write_json(directory / 'native.json', job)
        stage_dir = directory / 'staged'
        stage_dir.mkdir(exist_ok=True)
        files = [directory / 'native.json']
        for name, path in staged.items():
            shutil.copyfile(path, stage_dir / name)
            files.append(stage_dir / name)
        self.context.vm.run(ORACLE / 'Native.ps1', files, f'{self.name}-native', directory)
        self.native_result = read_json(directory / 'native-result.json')
        check_environment(self.native_result['environment'])
        failed = [i['name'] for i in self.native_result['inputs'] if i['failure']]
        if failed:
            raise Stage(f'native inputs failed: {failed}')
        return {'inputs': len(job['inputs']), 'edits': len(job['edits'])}

    # Native continuations of both lineages
    def continue_lineages(self):
        directory = self.out / 'continued'
        staged = directory / 'staged'
        staged.mkdir(parents=True)
        job, files = {'edits': []}, []
        for continuation in self.spec['continuations']:
            for side in ('candidate', 'native'):
                source = f'{side}-{continuation["of"]}.mdb'
                shutil.copyfile(self.image(source), staged / source)
                files.append(staged / source)
                job['edits'].append({'name': f'{side}-{continuation["name"]}', 'source': source,
                                     'file': f'{side}-{continuation["name"]}.mdb', 'steps': continuation['steps']})
        write_json(directory / 'native.json', job)
        self.context.vm.run(ORACLE / 'Native.ps1', [directory / 'native.json', *files], f'{self.name}-continue', directory)
        check_environment(read_json(directory / 'native-result.json')['environment'])
        return {'edits': len(job['edits'])}

    # DAO observation
    def observe_files(self) -> list[Path]:
        files = []
        for creation in self.spec['creations']:
            files += [self.candidate_dir() / f'candidate-{creation["candidate"]}.mdb', self.native_dir() / f'native-{creation["name"]}.mdb']
        for case in self.spec['cases']:
            if self.readback_kinds(case) and case['name'] not in self.candidate_failures:
                files += [self.candidate_dir() / f'candidate-{case["name"]}.mdb', self.image(f'native-{case["name"]}.mdb')]
        for continuation in self.spec['continuations']:
            files += [self.out / 'continued' / f'{side}-{continuation["name"]}.mdb' for side in ('candidate', 'native')]
        if self.spec['checks'].get('reader'):
            files += [self.input_path(name) for name, entry in self.spec['inputs'].items() if 'ops' in entry]
        return sorted(set(files), key=lambda p: p.name)

    def readback_kinds(self, case) -> bool:
        return case.get('kind', 'accepted') in self.spec['checks'].get('readback', [])

    def observe(self):
        files = self.observe_files()
        batches = max(1, min(self.context.parallel, len(files)))
        groups = [files[i::batches] for i in range(batches)]
        directory = self.out / 'observe'

        def batch(number):
            return self.context.vm.run(ORACLE / 'Observe.ps1', groups[number], f'{self.name}-observe-{number}',
                                       directory / f'batch-{number}')

        with ThreadPoolExecutor(max_workers=batches) as pool:
            outboxes = list(pool.map(batch, range(batches)))
        environment = None
        for outbox in outboxes:
            result = read_json(outbox / 'observe-result.json')
            check_environment(result['environment'])
            if environment not in (None, result['environment']):
                raise Stage('observer batches ran under different providers')
            environment = result['environment']
            for item in result['files']:
                self.observations[item['file']] = item
        if set(self.observations) != {p.name for p in files}:
            raise Stage('observation inventory differs from the requested images')
        self.report['environment'] = environment
        return {'files': len(files), 'batches': batches}

    def load_retained(self):
        self.native_result = read_json(self.native_dir() / 'native-result.json') if (self.native_dir() / 'native-result.json').exists() else {}
        failures = self.candidate_dir() / 'failures.json'
        self.candidate_failures = read_json(failures) if failures.exists() else {}
        for result in sorted((self.out / 'observe').glob('batch-*/observe-result.json')):
            for item in read_json(result)['files']:
                self.observations[item['file']] = item

    # Comparison
    def text_model(self):
        if 'locale_keys' not in self.spec:
            return structure.general_text_model
        return keys.LocaleModel(read_json(self.external(self.spec['locale_keys']))).text

    def compare_all(self):
        checks = self.spec['checks']
        text_model = self.text_model()
        native_edits = {e['name']: e for e in self.native_result.get('edits', [])}
        native_inputs = {i['name']: i for i in self.native_result.get('inputs', [])}
        raw_dir = self.out / 'compare'
        raw_dir.mkdir(exist_ok=True)
        pairs = []
        for creation in self.spec['creations']:
            pair = {'name': creation['name'], 'kind': 'creation', 'checks': {}}
            try:
                candidate = self.candidate_dir() / f'candidate-{creation["candidate"]}.mdb'
                native = self.native_dir() / f'native-{creation["name"]}.mdb'
                images = {'candidate': candidate.read_bytes(), 'native': native.read_bytes()}
                images['input'] = images['candidate']
                failure = native_inputs[creation['name']]['failure']
                self.check(pair, 'outcome', lambda: compare.require(failure is None, f'native creation failed: {failure}'))
                case = {'name': creation['name'], 'steps': [], 'dates': creation.get('dates', [])}
                self.check(pair, 'readback', lambda: compare.readback(case, self.observations[candidate.name],
                                                                        self.observations[native.name], images))
                for check in creation.get('checks', []):
                    self.check(pair, check, lambda: compare.CREATION_CHECKS[check](self.spec, creation, candidate, native))
            except Exception as error:
                pair['checks']['setup'] = {'status': 'fail', 'error': str(error)}
            pairs.append(pair)
        for case in self.spec['cases']:
            case = compare.host_case(case)
            kind = case.get('kind', 'accepted')
            pair = {'name': case['name'], 'kind': kind, 'checks': {}}
            if case['name'] in self.candidate_failures:
                pair['checks']['candidate'] = {'status': 'fail', 'error': self.candidate_failures[case['name']]}
                pairs.append(pair)
                continue
            try:
                paths = {'input': self.input_path(case['input']), 'candidate': self.candidate_dir() / f'candidate-{case["name"]}.mdb',
                         'native': self.image(f'native-{case["name"]}.mdb')}
                images = {k: p.read_bytes() for k, p in paths.items()}
                if kind in checks.get('outcome', []):
                    self.check(pair, 'outcome', lambda: compare.outcome(case, native_edits.get(case['name']), images))
                if 'native_residue' in case:
                    def residue():
                        symbolic = any('table' in entry for entry in case['native_residue'])
                        raw_input = structure.observe(paths['input'], None, text_model) if symbolic else None
                        return compare.native_residue(case, images['input'], images['native'], raw_input)
                    self.check(pair, 'residue', residue)
                if kind in checks.get('readback', []):
                    self.check(pair, 'readback', lambda: compare.readback(
                        case, self.observations[paths['candidate'].name], self.observations[paths['native'].name], images))
                if kind in checks.get('structure', []) and not case.get('skip_structure'):
                    def raw():
                        deltas = case.get('native_count_deltas')
                        raws = {k: structure.observe(p, deltas if k == 'native' else None, text_model) for k, p in paths.items()}
                        for k, value in raws.items():
                            write_json(raw_dir / f'{k}-{case["name"]}.json', value)
                        return compare.raw_structure(case, raws, images)
                    self.check(pair, 'structure', raw)
                if kind in checks.get('lvprop', []):
                    self.check(pair, 'lvprop', lambda: compare.lvprop(case.get('dates', []), images['candidate'], images['native']))
            except Exception as error:
                pair['checks']['setup'] = {'status': 'fail', 'error': str(error)}
            pairs.append(pair)
        continued = {}
        if self.spec['continuations']:
            continued = {e['name']: e for e in read_json(self.out / 'continued' / 'native-result.json')['edits']}
        for continuation in self.spec['continuations']:
            pairs.append(self.compare_continuation(continuation, continued, text_model))
        if checks.get('reader'):
            for name, item in sorted(self.observations.items()):
                pair = {'name': 'reader/' + name, 'kind': 'reader', 'checks': {}}
                self.check(pair, 'reader', lambda: compare.reader(self.context.cli, self.image(name), item))
                pairs.append(pair)
        for pair in pairs:
            pair['status'] = 'pass' if all(c['status'] == 'pass' for c in pair['checks'].values()) else 'fail'
        self.report['pairs'] = pairs
        return self.summarize()

    def compare_continuation(self, continuation, continued, text_model):
        name = continuation['name']
        pair = {'name': name, 'kind': 'continuation', 'checks': {}}
        try:
            before = {side: self.image(f'{side}-{continuation["of"]}.mdb') for side in ('candidate', 'native')}
            after = {side: self.out / 'continued' / f'{side}-{name}.mdb' for side in ('candidate', 'native')}
            edits = [continued[f'{side}-{name}'] for side in ('candidate', 'native')]
            self.check(pair, 'outcome', lambda: compare.require(
                all(len(e['steps']) == len(continuation['steps']) and all(s['ok'] for s in e['steps']) for e in edits),
                'native continuation writes completed on both lineages'))

            def readback():
                case = {'name': name, 'steps': [], 'unordered_rows': True}
                images = {side: after[side].read_bytes() for side in after}
                observed = {side: self.observations[after[side].name] for side in after}
                detail = compare.readback(case, observed['candidate'], observed['native'], images)
                raws = {side: structure.observe(before[side], None, text_model) for side in before}
                orders = {}
                for side in after:
                    expected = compare.row_model(continuation['rows'], [r['values'] for r in
                                                 raws[side]['tables'][continuation['rows']['table']]['rows']])
                    orders[side] = compare.dao_rows(continuation, observed[side], expected)
                return {**detail, 'physical_row_orders': orders}
            self.check(pair, 'readback', readback)

            def raw():
                raws = {side: (structure.observe(before[side], None, text_model), structure.observe(after[side], None, text_model))
                        for side in before}
                images = {side: (before[side].read_bytes(), after[side].read_bytes()) for side in before}
                return compare.continuation(continuation, raws, images)
            self.check(pair, 'structure', raw)
        except Exception as error:
            pair['checks']['setup'] = {'status': 'fail', 'error': str(error)}
        return pair

    @staticmethod
    def check(pair, name, function):
        try:
            pair['checks'][name] = {'status': 'pass', 'detail': function()}
        except Exception as error:
            pair['checks'][name] = {'status': 'fail', 'error': f'{type(error).__name__}: {error}'}

    def summarize(self):
        expected = self.spec.get('expected_failures', {})
        failed = {p['name'] for p in self.report['pairs'] if p['status'] == 'fail'}
        counts = {}
        for pair in self.report['pairs']:
            key = f'{pair["kind"]}/{pair["status"]}'
            counts[key] = counts.get(key, 0) + 1
        unexpected = sorted(failed - set(expected))
        unexpected_passes = sorted(set(expected) - failed)
        self.report.update(counts=counts, recorded_failures=expected, unexpected_failures=unexpected,
                           unexpected_passes=unexpected_passes)
        if unexpected or unexpected_passes:
            raise Stage(f'{len(unexpected)} unexpected failures, {len(unexpected_passes)} recorded failures now pass')
        return {'pairs': len(self.report['pairs'])}

    def run(self):
        write_json(self.out / 'spec.json', self.spec)
        try:
            if self.spec['creations']:
                self.stage('creation-candidates', self.creation_candidates)
            self.stage('native', self.native)
            if self.spec['cases']:
                self.stage('candidates', self.edit_candidates)
            if self.spec['continuations']:
                self.stage('continue', self.continue_lineages)
            self.stage('observe', self.observe)
            self.stage('compare', self.compare_all)
            self.report['outcome'] = 'matched'
        except Stage:
            pass
        finally:
            write_json(new_file(self.out / 'report.json'), self.report)
        return self.report


# --- Commands ------------------------------------------------------------------------

class Context:
    def __init__(self, args, vm=None):
        self.vm = vm
        self.cli = Path(args.cli).resolve() if getattr(args, 'cli', None) else ROOT / 'target/debug/jet3-cli'
        archive = getattr(args, 'archive', None)
        if not archive and getattr(args, 'shared_root', None):
            archive = Path(args.shared_root) / 'checks'
        self.archive = Path(archive).expanduser().resolve() if archive else None
        self.parallel = getattr(args, 'parallel', 4)
        self.source = source_state()


def source_state() -> dict:
    def git(*arguments):
        return subprocess.run(['git', *arguments], cwd=ROOT, capture_output=True, text=True).stdout
    return {'revision': git('rev-parse', 'HEAD').strip(), 'dirty': bool(git('status', '--porcelain').strip())}


def suite_names():
    return sorted(p.stem for p in SUITES.glob('*.json'))


def build_cli(out: Path) -> Path:
    done = subprocess.run(['cargo', 'build', '--locked', '-p', 'jet3-cli'], cwd=ROOT, capture_output=True, text=True)
    (out / 'build.log').write_text(done.stdout + done.stderr)
    if done.returncode:
        raise SystemExit(f'cargo build failed; see {out / "build.log"}')
    cli = out / 'jet3-cli'
    shutil.copyfile(ROOT / 'target/debug/jet3-cli', cli)
    cli.chmod(0o755)
    return cli


def command_run(args):
    names = args.suites or suite_names()
    unknown = set(names) - set(suite_names())
    if unknown:
        raise SystemExit('unknown suites: ' + ', '.join(sorted(unknown)))
    args.out.mkdir(parents=True, exist_ok=False)
    if not args.cli:
        args.cli = build_cli(args.out)
    context = Context(args, Vm.from_args(args))
    results = []
    for name in names:
        print(f'{name}: running', flush=True)
        out = args.out / name
        out.mkdir()
        raw = read_json(SUITES / f'{name}.json')
        context.vm.timeout = max(args.timeout, raw.get('timeout', 0))
        try:
            if 'registry' in raw:
                import registry
                report = registry.run(name, raw, out, context)
            elif 'module' in raw:
                # Lifecycle suites keep their own staging in suites/<module>.py.
                report = importlib.import_module(raw['module']).run(name, raw, out, context)
            else:
                report = SuiteRun(name, load_spec(name), out, context).run()
        except Exception as error:  # a harness bug in one suite must not end the others
            report = {'suite': name, 'outcome': 'failed', 'stages': [], 'error': f'{type(error).__name__}: {error}'}
            write_json(new_file(out / 'report.json'), report)
        results.append({'suite': name, 'outcome': report['outcome'], 'counts': report.get('counts'),
                        'stages': [(s['name'], s['status']) for s in report['stages']]})
        print(f'{name}: {report["outcome"]} {report.get("counts", {})}', flush=True)
    write_json(args.out / 'summary.json', {'source': context.source, 'cli': identity(context.cli), 'suites': results})
    return int(any(r['outcome'] != 'matched' for r in results))


def command_compare(args):
    directory = args.run.resolve()
    spec = read_json(directory / 'spec.json')
    if not args.cli and (directory.parent / 'jet3-cli').exists():
        args.cli = directory.parent / 'jet3-cli'
    run = SuiteRun(directory.name, spec, directory, Context(args))
    run.load_retained()
    run.report['stages'].append({'name': 'retained', 'status': 'pass'})
    try:
        run.stage('compare', run.compare_all)
        run.report['outcome'] = 'matched'
    except Stage:
        pass
    path = new_file(directory / 'report.json')
    write_json(path, run.report)
    print(path, run.report['outcome'], run.report.get('counts'))
    return int(run.report['outcome'] != 'matched')


def command_ps(args):
    vm = Vm.from_args(args)
    out = args.out.resolve() if args.out else None
    try:
        outbox = vm.run(args.script, args.extra, args.script.stem, out)
        code = 0
    except VmError as error:
        print(error, file=sys.stderr)
        outbox, code = out, 1
    log = (outbox or Path()) / 'log.txt'
    if log.is_file():
        data = log.read_bytes()
        sys.stdout.write(data.decode('utf-16') if data.startswith((b'\xff\xfe', b'\xfe\xff')) else data.decode('utf-8', 'replace'))
    print(f'outbox: {outbox}', file=sys.stderr)
    return code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('list')
    run = commands.add_parser('run')
    run.add_argument('suites', nargs='*')
    run.add_argument('--out', type=Path, required=True)
    rerun = commands.add_parser('compare')
    rerun.add_argument('run', type=Path)
    ps = commands.add_parser('ps')
    ps.add_argument('script', type=Path)
    ps.add_argument('--with', dest='extra', action='append', default=[], type=Path)
    ps.add_argument('--out', type=Path, help='retain the outbox here')
    for command in (run, rerun):
        command.add_argument('--cli', type=Path, help='jet3-cli binary (default: build and freeze one per run)')
        command.add_argument('--archive', default=os.environ.get('JET3_DAO_ARCHIVE'), help='root of retained external inputs')
    for command in (run, ps):
        command.add_argument('--timeout', type=int, default=3600, help='seconds per VM job')
        command.add_argument('--shared-root', default=os.environ.get('JET3_WINDOWS_SHARED_ROOT'))
        for option, default in [('host', '127.0.0.1'), ('port', '2222'), ('user', 'jet3runner'),
                                ('identity', str(Path.home() / '.ssh/jet3-dao')), ('remote-shared-root', r'\\host.lan\Data')]:
            command.add_argument('--' + option, default=os.environ.get('JET3_WINDOWS_' + option.upper().replace('-', '_'), default))
    run.add_argument('--parallel', type=int, default=4, help='concurrent observer jobs')
    args = parser.parse_args(argv)
    if args.command == 'list':
        for name in suite_names():
            print(name, '-', read_json(SUITES / f'{name}.json').get('description', ''))
        return 0
    return {'run': command_run, 'compare': command_compare, 'ps': command_ps}[args.command](args)


if __name__ == '__main__':
    sys.exit(main())
