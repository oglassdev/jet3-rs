#!/usr/bin/env python3
"""golden-check recipe corpus: every registry suite's Rust candidates plus the recipes of
archived DAO candidates (`scripts/golden_recipes`), each built by `jet3-cli` into `<out>/<name>/`.

`golden_corpus.py --cli BIN --out DIR [NAME...]`

A recipe that stops early (an expected failure is part of the corpus) records its error in
`<out>/<name>/error.txt`.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from golden_recipes import RECIPES  # noqa: E402
import dao  # noqa: E402
import recipes  # noqa: E402


def suites():
    """Registry suites whose modules describe their Rust candidates."""
    for name in dao.suite_names():
        spec = dao.read_json(dao.SUITES / f'{name}.json')
        module = importlib.import_module(f'registry.{spec["registry"]}') if 'registry' in spec else None
        if module and hasattr(module, 'candidates'):
            yield name, lambda module=module, spec=spec: module.candidates(spec)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--cli', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('names', nargs='*')
    args = parser.parse_args(argv)
    every = dict(RECIPES)
    if not args.names or set(args.names) - set(RECIPES):
        every.update(suites())
    names = args.names or list(every)
    unknown = set(names) - set(every)
    if unknown:
        raise SystemExit('unknown recipes: ' + ', '.join(sorted(unknown)))
    cli = args.cli.resolve()
    recipes.CLI = cli

    def build(name):
        try:
            recipes.build(every[name](), args.out / name, args.out / 'requests' / name, cli)
            return 0
        except recipes.RecipeError as error:
            (args.out / name / 'error.txt').write_text(str(error) + '\n')
            return 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        failed = sum(pool.map(build, names))
    print(f'{len(names)} recipes, {failed} stopped early')
    return 0


if __name__ == '__main__':
    sys.exit(main())
