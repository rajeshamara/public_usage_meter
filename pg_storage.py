"""PostgreSQL persistence, explicit target checks, and durable outage queue.
UI reads memory; only collector thread does network writes. No SQLite runtime writes.
"""
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import threading
import time
import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parent

def settings():
    """Read a local target description; passwords are never read from this file."""
    cfg = json.loads((ROOT/'db_config.json').read_text(encoding='utf-8'))
    if not isinstance(cfg, dict) or cfg.get('database') != 'usage_meter':
        raise RuntimeError('Database target guard rejected configuration')
    if 'password' in cfg or 'shared_config' in cfg:
        raise RuntimeError('Use USAGE_METER_DB_PASSWORD; credential files are not supported')
    for field in ('host', 'user'):
        value = cfg.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f'Database configuration requires a nonempty {field}')
    for field in ('port', 'expected_server_port'):
        value = cfg.get(field)
        if type(value) is not int or not 1 <= value <= 65535:
            raise RuntimeError(f'Database configuration requires a valid integer {field}')
    try:
        address = cfg['expected_server_address']
        if not isinstance(address, str):
            raise ValueError('Expected a string IP address')
        cfg['expected_server_address'] = str(ipaddress.ip_address(address))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError('Set expected_server_address to the approved server IP address') from exc
    cfg.setdefault('sslmode', 'prefer')
    if cfg['sslmode'] not in ('disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'):
        raise RuntimeError('Database configuration contains an invalid sslmode')
    if 'sslrootcert' in cfg and (not isinstance(cfg['sslrootcert'], str) or not cfg['sslrootcert'].strip()):
        raise RuntimeError('sslrootcert must be a certificate file path')
    return cfg

def database_password():
    password = os.environ.get('USAGE_METER_DB_PASSWORD')
    if not password:
        raise RuntimeError('Set USAGE_METER_DB_PASSWORD in the process environment')
    return password

def connect():
    cfg = settings()
    tls = {'sslmode': cfg['sslmode']}
    if 'sslrootcert' in cfg:
        tls['sslrootcert'] = cfg['sslrootcert']
    c = psycopg.connect(host=cfg['host'], port=cfg['port'], dbname=cfg['database'],
        user=cfg['user'], password=database_password(),
        connect_timeout=5, application_name='usage_meter',
        options='-c statement_timeout=10000 -c lock_timeout=5000', autocommit=True, **tls)
    try:
        identity = c.execute('SELECT current_database(), current_user, host(inet_server_addr()), inet_server_port()').fetchone()
        expected = (cfg['database'], cfg['user'], cfg['expected_server_address'], cfg['expected_server_port'])
        actual = (identity[0], identity[1], str(ipaddress.ip_address(identity[2])), identity[3])
        if actual != expected:
            raise RuntimeError('Connected database identity did not match approved target')
    except Exception:
        c.close()
        raise
    return c

def put_snapshot(c, key, stamp, kind, payload):
    c.execute('INSERT INTO public.snapshots(source_key,timestamp,observed_at,kind,payload) VALUES(%s,%s,to_timestamp(%s),%s,%s) ON CONFLICT(source_key) DO NOTHING', (key,stamp,stamp,kind,Jsonb(payload)))

def put_sample(c, key, stamp, used, resets):
    c.execute('INSERT INTO public.samples(source_key,timestamp,observed_at,used,resets,resets_at) VALUES(%s,%s,to_timestamp(%s),%s,%s,to_timestamp(%s)) ON CONFLICT(source_key) DO NOTHING', (key,stamp,stamp,used,resets,resets))

class Store:
    def __init__(self, unused_path=None):
        self.folder = ROOT/'data'
        self.pending = self.folder/'postgres_outbox'
        self.pending.mkdir(parents=True,exist_ok=True)
        self.cache_file = self.folder/'postgres_cache.json'
        self.lock = threading.RLock()
        self.cache = {'latest':{}, 'rows':[]}
        self.storage_error = None
        self.connection = None
        if self.cache_file.exists():
            self.cache = json.loads(self.cache_file.read_text())
        try:
            self.connection = connect()
            self.flush()
            if self.connection is None:
                raise RuntimeError('Pending upload failed')
            for kind in ('limits','activity'):
                row = self.connection.execute('SELECT timestamp,payload FROM public.snapshots WHERE kind=%s ORDER BY timestamp DESC,id DESC LIMIT 1',(kind,)).fetchone()
                if row:self.cache['latest'][kind] = list(row)
            self.cache['rows'] = [list(row) for row in self.connection.execute('SELECT timestamp,used,resets FROM public.samples ORDER BY timestamp,id').fetchall()]
            self._persist_cache()
            self.flush()
        except Exception:
            self.storage_error = 'PostgreSQL unavailable; readings queued locally'
            self._disconnect()

    def _persist_cache(self):
        temp = self.cache_file.with_suffix('.tmp')
        temp.write_text(json.dumps(self.cache),encoding='utf-8')
        temp.replace(self.cache_file)

    def _disconnect(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    def save(self, kind, payload):
        stamp = time.time()
        event = {'timestamp':stamp,'kind':kind,'payload':payload}
        body=json.dumps(event,sort_keys=True)
        key=hashlib.sha256(body.encode()).hexdigest()
        event['key']=key
        temp=self.pending/(key+'.tmp')
        temp.write_text(json.dumps(event),encoding='utf-8')
        temp.replace(self.pending/(key+'.json'))
        with self.lock:
            self.cache['latest'][kind]=[stamp,payload]
            if kind=='limits':
                w=self._weekly(payload)
                if w:self.cache['rows'].append([stamp,w['usedPercent'],w['resetsAt']])
            self._persist_cache()
        self.flush()

    @staticmethod
    def _weekly(payload):
        buckets=payload.get('rateLimitsByLimitId') or {'codex':payload.get('rateLimits')}
        ordered=sorted(buckets,key=lambda k:k!='codex')
        for key in ordered:
            for kind in ('primary','secondary'):
                w=(buckets[key] or {}).get(kind)
                if w and w.get('windowDurationMins')==10080 and w.get('usedPercent') is not None and w.get('resetsAt') is not None:return w
        return None

    def flush(self):
        try:
            if self.connection is None:self.connection=connect()
            for path in sorted(self.pending.glob('*.json')):
                event=json.loads(path.read_text())
                with self.connection.transaction():
                    put_snapshot(self.connection,event['key'],event['timestamp'],event['kind'],event['payload'])
                    w=self._weekly(event['payload']) if event['kind']=='limits' else None
                    if w:put_sample(self.connection,event['key'],event['timestamp'],w['usedPercent'],w['resetsAt'])
                # Only remove this utility's acknowledged queue file after a successful commit.
                path.unlink()
            self.storage_error=None
        except Exception:
            self.storage_error='PostgreSQL unavailable; readings queued locally'
            self._disconnect()

    def rows(self,reset):
        with self.lock:return [tuple(r) for r in self.cache['rows'] if abs(r[2]-reset)<=60]

    def latest(self,kind):
        with self.lock:return self.cache['latest'].get(kind)

    def close(self):
        self._disconnect()
