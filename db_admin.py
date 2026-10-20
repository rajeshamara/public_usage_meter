"""Saved, target-guarded schema/migration commands. No credentials printed.
Run through database.bat. SQLite import preserves epochs, payloads, and source identity.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
import subprocess
import os
import shutil
from psycopg import sql
from pg_storage import ROOT, connect, settings, database_password, put_snapshot, put_sample

def migrate():
    source=ROOT/'data/usage.sqlite3'
    backup=ROOT/'data'/('usage_before_postgres_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S')+'.sqlite3')
    old=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
    with sqlite3.connect(backup) as target:old.backup(target)
    snapshots=old.execute('SELECT id,timestamp,kind,payload FROM snapshots ORDER BY id').fetchall()
    samples=old.execute('SELECT rowid,timestamp,used,resets FROM samples ORDER BY rowid').fetchall()
    old.close()
    with connect() as c:
        with c.transaction():
            c.execute((ROOT/'sql/001_schema.sql').read_text())
            for ident,stamp,kind,raw in snapshots:
                key=f'sqlite:original:snapshots:{ident}'
                put_snapshot(c,key,stamp,kind,json.loads(raw))
                assert c.execute('SELECT timestamp,kind,payload FROM public.snapshots WHERE source_key=%s',(key,)).fetchone()==(stamp,kind,json.loads(raw)), 'Snapshot mismatch'
            for ident,stamp,used,resets in samples:
                key=f'sqlite:original:samples:{ident}'
                put_sample(c,key,stamp,used,resets)
                assert c.execute('SELECT timestamp,used,resets FROM public.samples WHERE source_key=%s',(key,)).fetchone()==(stamp,used,resets), 'Sample mismatch'
    print(f'Migrated and verified {len(snapshots)} snapshots and {len(samples)} samples.')
    print('SQLite backup:',backup)

def psql_file(path,readonly=False):
    path=Path(path).resolve()
    if not path.is_relative_to(ROOT/'sql') or path.suffix!='.sql':
        raise RuntimeError('SQL file must be inside usage_meter/sql')
    cfg=settings()
    with connect() as c:
        print('Verified target:',c.execute('SELECT current_database(),inet_server_addr(),inet_server_port()').fetchone())
    env=os.environ.copy()
    env['PGPASSWORD']=database_password()
    env['PGCONNECT_TIMEOUT']='5'
    env['PGOPTIONS']='-c statement_timeout=30000 -c lock_timeout=5000'+(' -c default_transaction_read_only=on' if readonly else '')
    env['PGSSLMODE']=cfg['sslmode']
    if 'sslrootcert' in cfg:env['PGSSLROOTCERT']=cfg['sslrootcert']
    exe=os.environ.get('USAGE_METER_PSQL') or shutil.which('psql')
    if not exe and os.name == 'nt':
        candidate=Path(r'C:\Program Files\PostgreSQL\18\bin\psql.exe')
        if candidate.is_file():exe=str(candidate)
    if not exe:
        raise RuntimeError('Install the PostgreSQL client or set USAGE_METER_PSQL to its executable')
    # Check the identity again on the exact psql connection that executes the file.
    # A mismatch divides by zero; ON_ERROR_STOP aborts the transaction before -f.
    guard = sql.SQL(
        'SELECT 1 / CASE WHEN current_database() = {database} AND current_user = {user} '
        'AND inet_server_addr() = {address}::inet AND inet_server_port() = {port} '
        'THEN 1 ELSE 0 END AS usage_meter_target_verified'
    ).format(database=sql.Literal(cfg['database']), user=sql.Literal(cfg['user']),
             address=sql.Literal(cfg['expected_server_address']),
             port=sql.Literal(cfg['expected_server_port'])).as_string()
    subprocess.run([exe,'-X','-w','-v','ON_ERROR_STOP=1','--single-transaction','-h',cfg['host'],'-p',str(cfg['port']),'-U',cfg['user'],'-d',cfg['database'],'-c',guard,'-f',str(path)],env=env,check=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['migrate','sql','query'])
    p.add_argument('file',nargs='?')
    a=p.parse_args()
    if a.action=='migrate':migrate()
    else:psql_file(a.file,readonly=a.action=='query')
