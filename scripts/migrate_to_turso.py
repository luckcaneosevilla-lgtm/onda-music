"""Copy a local IVOX SQLite file into the Turso database from .env (rows already there are kept).

Usage: python scripts/migrate_to_turso.py [path/to/onda.sqlite]
"""
from pathlib import Path
import sqlite3
import sys
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
import server as onda
if not onda.TURSO:sys.exit('Define TURSO_DATABASE_URL y TURSO_AUTH_TOKEN en .env.')
source=Path(sys.argv[1]) if len(sys.argv)>1 else onda.DATA/'onda.sqlite'
onda.init()
local=sqlite3.connect(source)
# Creation order is dependency order, so foreign keys always find their parents.
tables=[t for (t,) in local.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='audio_url_cache' ORDER BY rowid")]
for table in tables:
    rows=local.execute(f'SELECT * FROM "{table}"').fetchall()
    if not rows:continue
    marks=','.join('?'*len(rows[0]))
    for start in range(0,len(rows),200):
        with onda.db() as c:
            for row in rows[start:start+200]:c.execute(f'INSERT OR IGNORE INTO "{table}" VALUES({marks})',row)
    print(f'{table}: {len(rows)} filas')
print('Migración completada.')
