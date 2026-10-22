# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas>=2.2,<4"]
# ///
"""Load the delivered SQL tables/views as real pandas DataFrames."""
import sqlite3
from pathlib import Path

import pandas as pd


def load_frames(database=None):
    path = Path(database) if database else Path(__file__).parent / 'vv_evidence/assurance.sqlite'
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
        names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'")]
        return {name: pd.read_sql_query(f'SELECT * FROM "{name}"', db) for name in names}


if __name__ == '__main__':
    for name, frame in load_frames().items():
        print(f'{name}: {len(frame):,} rows × {len(frame.columns)} columns')
