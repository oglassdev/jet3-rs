"""Rust candidate images built from ordered jet3-cli requests.

A recipe is a list of images, built in order into one directory:

    {"file": "b.mdb", "from": "a.mdb", "steps": [step, ...]}

`from` copies an earlier image of the directory (or any path) before the steps run. A step is
one jet3-cli command on the image:

    {"command": "create" | "mutate" | "schema",   default "mutate"
     "request": {...},
     "limits": {"work-units": 0},                   --max-work-units 0
     "locate": {"table": "Items", "id": 3},         sets request["row"] to the row whose Id is 3
                                                    (in this image, or in "image" of the directory)
     "refused": "duplicate unique key"}             must fail with this text and leave the file unchanged

or {"edit": function}, which maps the file's bytes to new bytes (to damage a refusal input).

`build` stops at the first unexpected outcome and returns each step's parsed stdout (or
refusal message) by image. Requests and outputs are kept under `logs`.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess

import dao
import structure

# The registry runner points this at the run's frozen jet3-cli.
CLI = dao.ROOT / 'target/debug/jet3-cli'


class RecipeError(RuntimeError):
    pass


def locate(data: bytes, table: str, id: int, column: str = 'Id') -> dict:
    """The page/slot of the one live row whose `column` equals `id`."""
    rows = structure.rows(data, structure.tables(data)[table])
    matches = [r['locator'] for r in rows if r['values'][column] == id]
    if len(matches) != 1:
        raise RecipeError(f'expected one {table} row with {column} {id}, found {len(matches)}')
    return {'page': matches[0]['page'], 'slot': matches[0]['row']}


def run_step(cli: Path, target: Path, step: dict, log: Path) -> dict:
    if 'edit' in step:
        target.write_bytes(step['edit'](target.read_bytes()))
        return {'edited': True}
    request = copy.deepcopy(step['request'])
    if 'locate' in step:
        where = dict(step['locate'])
        source = target.parent / where.pop('image', target.name)
        try:
            request['row'] = locate(source.read_bytes(), **where)
        except RecipeError as error:
            raise RecipeError(f'{source.name}: {error}') from None
    log.with_suffix('.request.json').write_text(json.dumps(request, separators=(',', ':')) + '\n')
    command = step.get('command', 'mutate')
    arguments = [str(cli), command, target.name, '--input', str(log.with_suffix('.request.json').resolve())]
    for name, value in step.get('limits', {}).items():
        arguments += [f'--max-{name}', str(value)]
    before = target.read_bytes() if 'refused' in step and command != 'create' else None
    done = subprocess.run(arguments, cwd=target.parent, capture_output=True, text=True)
    record = {'command': command, 'returncode': done.returncode, 'stdout': done.stdout, 'stderr': done.stderr}
    log.with_suffix('.result.json').write_text(json.dumps(record, indent=1) + '\n')
    if 'refused' not in step:
        if done.returncode:
            raise RecipeError(f'{target.name}: {command} exited {done.returncode}: {done.stderr.strip()[-300:]}')
        return json.loads(done.stdout)
    message = json.loads(done.stderr)['message'] if done.returncode == 1 else ''
    if step['refused'] not in message:
        raise RecipeError(f'{target.name}: expected refusal {step["refused"]!r}, got {done.returncode}: {done.stderr.strip()[-300:]}')
    if command == 'create' and target.exists():
        raise RecipeError(f'{target.name}: refused creation published a file')
    if command != 'create' and target.read_bytes() != before:
        raise RecipeError(f'{target.name}: refused {command} changed the file')
    return {'refused': message}


def build(images: list[dict], out: Path, logs: Path, cli: Path | None = None) -> dict[str, list]:
    """Builds `images` into `out`; returns each image's step results by file name."""
    cli = Path(cli or CLI).resolve()
    out.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    results = {}
    for image in images:
        target = out / image['file']
        if 'from' in image:
            shutil.copyfile(out / image['from'] if isinstance(image['from'], str) else image['from'], target)
        stem = Path(image['file']).stem
        results[image['file']] = [run_step(cli, target, step, logs / f'{stem}-{n:03d}')
                                  for n, step in enumerate(image.get('steps', []))]
    return results
