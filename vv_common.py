"""Catalog and immutable read-only access shared by campaign tooling."""
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / 'vv_evidence' / 'assurance.sqlite'


def source_hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(ROOT.glob('governance_*.py')) if p.name != 'governance_lab.py'}


def source_seal():
    return hashlib.sha256(json.dumps(source_hashes(), sort_keys=True).encode()).hexdigest()


def catalog():
    return json.loads((ROOT / 'vv_catalog.json').read_text())


def connect_readonly():
    connection = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def obligations():
    with connect_readonly() as connection:
        return [dict(row) for row in connection.execute('SELECT * FROM obligations ORDER BY id')]
