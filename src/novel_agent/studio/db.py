from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid4().hex


def encode(x):
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, genre TEXT NOT NULL, premise TEXT NOT NULL,
 style TEXT NOT NULL, target_chapters INTEGER, chapter_words INTEGER, revision INTEGER DEFAULT 0,
 outline TEXT DEFAULT '', logline TEXT DEFAULT '', is_demo INTEGER DEFAULT 0,
 archived INTEGER DEFAULT 0, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS volumes (
 project_id TEXT REFERENCES projects(id), number INTEGER, title TEXT, outline TEXT DEFAULT '',
 PRIMARY KEY(project_id,number));
CREATE TABLE IF NOT EXISTS memory (
 project_id TEXT REFERENCES projects(id), name TEXT, content TEXT, revision INTEGER DEFAULT 0,
 PRIMARY KEY(project_id,name));
CREATE TABLE IF NOT EXISTS initial_memory (
 project_id TEXT PRIMARY KEY, content TEXT);
CREATE TABLE IF NOT EXISTS chapters (
 id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), volume INTEGER, number INTEGER,
 title TEXT NOT NULL, content TEXT DEFAULT '', canonical TEXT DEFAULT '', draft_revision INTEGER DEFAULT 0,
 canonical_revision INTEGER DEFAULT 0, status TEXT DEFAULT 'planned', stale INTEGER DEFAULT 0,
 plan TEXT DEFAULT '{}', review TEXT DEFAULT '{}', proposal TEXT DEFAULT '{}', context TEXT DEFAULT '{}',
 summary TEXT DEFAULT '', reviewed_hash TEXT DEFAULT '', reviewed_project_revision INTEGER DEFAULT -1,
 updated TEXT, UNIQUE(project_id,volume,number));
CREATE TABLE IF NOT EXISTS versions (
 id TEXT PRIMARY KEY, chapter_id TEXT REFERENCES chapters(id), version INTEGER, content TEXT, title TEXT,
 memory_snapshot TEXT, facts_snapshot TEXT, summary TEXT, plan TEXT, note TEXT, created TEXT,
 UNIQUE(chapter_id,version));
CREATE TABLE IF NOT EXISTS facts (
 id TEXT PRIMARY KEY, project_id TEXT, chapter_id TEXT, version INTEGER,
 subject TEXT, predicate TEXT, value TEXT, kind TEXT, status TEXT, quote TEXT, related TEXT,
 position INTEGER, stale INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS chunks (
 id TEXT PRIMARY KEY, project_id TEXT, chapter_id TEXT, version INTEGER, position INTEGER,
 content TEXT, stale INTEGER DEFAULT 0);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED, project_id UNINDEXED, tokens);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, project_id TEXT, chapter_id TEXT, kind TEXT, config TEXT,
 status TEXT DEFAULT 'queued', node TEXT DEFAULT '', error TEXT DEFAULT '',
 result TEXT DEFAULT '{}', cancel_requested INTEGER DEFAULT 0, created TEXT, updated TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(project_id)
 WHERE status IN ('queued','running','awaiting_approval','interrupted');
CREATE TABLE IF NOT EXISTS run_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT REFERENCES runs(id), type TEXT,
 data TEXT, created TEXT);
CREATE INDEX IF NOT EXISTS event_cursor ON run_events(run_id,seq);
CREATE TABLE IF NOT EXISTS usage (
 id TEXT PRIMARY KEY, run_id TEXT, role TEXT, model TEXT, input_tokens INTEGER DEFAULT 0,
 output_tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0, reserved REAL DEFAULT 0,
 state TEXT DEFAULT 'reserved', duration REAL DEFAULT 0, request_id TEXT DEFAULT '',
 price TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS experiments (
 id TEXT PRIMARY KEY, name TEXT, config TEXT, result TEXT, status TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS blind_pairs (
 id TEXT PRIMARY KEY, experiment_id TEXT, a TEXT, b TEXT, labels TEXT, preference TEXT,
 reason TEXT, voted_at TEXT);
CREATE TABLE IF NOT EXISTS audit (
 id TEXT PRIMARY KEY, project_id TEXT, action TEXT, detail TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS run_commits (
 run_id TEXT PRIMARY KEY REFERENCES runs(id), project_id TEXT, chapter_id TEXT,
 version INTEGER, created TEXT);
CREATE TABLE IF NOT EXISTS story_events (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 chapter_id TEXT NOT NULL REFERENCES chapters(id), version INTEGER NOT NULL,
 position INTEGER NOT NULL, sequence INTEGER NOT NULL, event TEXT NOT NULL,
 labels TEXT NOT NULL DEFAULT '{}', stale INTEGER NOT NULL DEFAULT 0, created TEXT,
 UNIQUE(project_id,chapter_id,version,sequence));
CREATE INDEX IF NOT EXISTS story_events_active
 ON story_events(project_id,stale,position,sequence);
CREATE TABLE IF NOT EXISTS imported_story_events (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 chapter_id TEXT NOT NULL REFERENCES chapters(id), source_project_id TEXT NOT NULL,
 source_event TEXT NOT NULL, proposal TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', resolved_version INTEGER, created TEXT);
CREATE INDEX IF NOT EXISTS imported_story_events_pending
 ON imported_story_events(project_id,chapter_id,status);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    @contextmanager
    def transaction(self):
        with self.lock, self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            yield c

    def all(self, sql, args=()):
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql, args=(), required=True):
        rows = self.all(sql, args)
        if not rows and required:
            raise LookupError("记录不存在")
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.transaction() as c:
            c.execute(sql, args)

    def event(self, run_id: str, kind: str, data: dict):
        with self.transaction() as c:
            cur = c.execute(
                "INSERT INTO run_events(run_id,type,data,created) VALUES(?,?,?,?)",
                (run_id, kind, encode(data), now()),
            )
            return cur.lastrowid

    def audit(self, project_id, action, detail):
        self.execute(
            "INSERT INTO audit VALUES(?,?,?,?,?)",
            (uid(), project_id, action, encode(detail), now()),
        )

    def usage_summary(self):
        return self.one(
            "SELECT COALESCE(SUM(cost),0) AS spent, COALESCE(SUM(reserved),0) AS reserved, COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens, COUNT(*) AS calls FROM usage"
        )
