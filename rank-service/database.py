"""Local SQLite and managed PostgreSQL use the same application queries."""
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = Path(os.environ.get('DATA_DIR', str(ROOT/'data'))).resolve()
DATABASE_URL = os.environ.get('DATABASE_URL','')
POSTGRES = bool(DATABASE_URL)
if POSTGRES:
    import psycopg
    from psycopg.rows import dict_row
    IntegrityError = psycopg.IntegrityError
else:
    IntegrityError = sqlite3.IntegrityError

class Database:
    def __init__(self):
        if POSTGRES:
            self.raw = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=15)
        else:
            STORE.mkdir(parents=True,exist_ok=True)
            self.raw = sqlite3.connect(STORE/'rank.sqlite3',timeout=20)
            self.raw.row_factory = sqlite3.Row
            self.raw.execute('PRAGMA foreign_keys=ON')
    def execute(self,sql,args=()):
        if POSTGRES:
            sql = sql.replace('?', '%s').replace('BEGIN IMMEDIATE','BEGIN')
        return self.raw.execute(sql,args)
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()
    def __enter__(self): return self
    def __exit__(self,typ,value,tb):
        try:
            if typ: self.rollback()
            else: self.commit()
        finally: self.close()

def connect(): return Database()

def init_db():
    STORE.mkdir(parents=True,exist_ok=True)
    with connect() as c:
        if POSTGRES:
            # Serialise schema creation across concurrent web and worker starts.
            c.execute('SELECT pg_advisory_xact_lock(73461680)')
        else:
            c.execute('PRAGMA journal_mode=WAL')
        primary = 'BIGSERIAL PRIMARY KEY' if POSTGRES else 'INTEGER PRIMARY KEY'
        schema = f'''
        CREATE TABLE IF NOT EXISTS users(id {primary}, email TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
          recovery_hash TEXT NOT NULL DEFAULT '', created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
          csrf TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);
        CREATE TABLE IF NOT EXISTS academies(user_id BIGINT PRIMARY KEY REFERENCES users(id), config TEXT NOT NULL,
          share_token TEXT UNIQUE NOT NULL, sharing INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, user_id BIGINT REFERENCES users(id),
          status TEXT NOT NULL, created TEXT NOT NULL, finished TEXT, config TEXT NOT NULL,
          results TEXT NOT NULL DEFAULT '[]', log TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '');
        CREATE UNIQUE INDEX IF NOT EXISTS one_active ON runs(user_id) WHERE status IN ('queued','running');
        CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY, user_id BIGINT REFERENCES users(id), run_id TEXT REFERENCES runs(id),
          created TEXT NOT NULL, snapshot TEXT NOT NULL, share_token TEXT UNIQUE NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
          UNIQUE(user_id,run_id));
        CREATE TABLE IF NOT EXISTS attempts(key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires DOUBLE PRECISION NOT NULL);
        CREATE TABLE IF NOT EXISTS leases(name TEXT PRIMARY KEY, owner TEXT NOT NULL, expires DOUBLE PRECISION NOT NULL);
        '''
        for statement in schema.split(';'):
            if statement.strip(): c.execute(statement)
        if not POSTGRES:
            columns = {r['name'] for r in c.execute('PRAGMA table_info(users)').fetchall()}
            if 'recovery_hash' not in columns:
                c.execute("ALTER TABLE users ADD COLUMN recovery_hash TEXT NOT NULL DEFAULT ''")

def lock_account(c,uid):
    # PostgreSQL row locks / SQLite writer lock make quota and enqueue atomic.
    if not POSTGRES: c.execute('BEGIN IMMEDIATE')
    c.execute('SELECT id FROM users WHERE id=?'+(' FOR UPDATE' if POSTGRES else ''),(uid,))
