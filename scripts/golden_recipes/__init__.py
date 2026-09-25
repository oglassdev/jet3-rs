"""jet3-cli recipes of archived DAO candidates for the golden-check corpus.

Each recipe returns the images `oracle/windows-dao/recipes.py` builds; `RECIPES` maps
recipe names to them. `creation` holds creation recipes and `mutation` mutation recipes.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'oracle/windows-dao'))

RECIPES = {}


def recipe(function):
    RECIPES[function.__name__.replace('_', '-')] = function
    return function


def create(file, *tables, **extra):
    """One image created from `tables` (and relationship/relationships in `extra`)."""
    return {'file': file, 'steps': [{'command': 'create', 'request': {'tables': list(tables), **extra}}]}


def table(name, columns, rows=(), indexes=(), **extra):
    return {'name': name, 'columns': list(columns), 'rows': list(rows), 'indexes': list(indexes), **extra}


def col(name, kind, **extra):
    return {'name': name, 'type': kind, **extra}


def index(name, kind, *columns, **extra):
    """`columns` are names; a leading '-' makes the field descending."""
    fields = [{'column': c[1:], 'direction': 'descending'} if c.startswith('-') else {'column': c} for c in columns]
    return {'name': name, 'kind': kind, 'fields': fields, **extra}


def insert(table_name, values, **extra):
    return {'request': {'operation': 'insert', 'table': table_name, 'values': values}, **extra}


def update(table_name, id, column, value, **extra):
    request = {'operation': 'update', 'table': table_name, 'column': column, 'value': value}
    return {'request': request, 'locate': {'table': table_name, 'id': id}, **extra}


def replace(table_name, id, values, **extra):
    request = {'operation': 'replace', 'table': table_name, 'values': values}
    return {'request': request, 'locate': {'table': table_name, 'id': id}, **extra}


def delete(table_name, id, **extra):
    return {'request': {'operation': 'delete', 'table': table_name}, 'locate': {'table': table_name, 'id': id}, **extra}


# Retained native row-overflow captures (a DAO outbox directory); the recipe is skipped without them.
ROW_OVERFLOW_CAPTURES = os.environ.get('JET3_ROW_OVERFLOW_CAPTURES')

from . import creation, mutation  # noqa: E402,F401  (registers the recipes)
