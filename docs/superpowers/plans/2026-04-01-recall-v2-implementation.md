# Recall Plugin v2.0.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the recall plugin from single-session JSON storage to a multi-session, cross-project, SQLite-backed recall system with PostCompact nudging and hybrid tagging.

**Architecture:** SQLite (stdlib `sqlite3`) replaces `index.json` as the storage backend. Three hooks (`UserPromptSubmit`, `PostCompact`, `SessionEnd`) feed data into a single `recall.db`. All query scripts read from the DB via a shared `db.py` module. Auto-tagging uses TF-based keyword extraction with no external dependencies.

**Tech Stack:** Python 3.6+ stdlib only (`sqlite3`, `json`, `os`, `sys`, `re`, `datetime`, `pathlib`, `collections`, `unittest`)

**Spec:** `docs/superpowers/specs/2026-04-01-recall-v2-design.md`

---

### Task 1: Create `scripts/db.py` — Schema, Connection, and Core CRUD

The foundation. Every other module depends on this. We build the SQLite layer with WAL mode, schema creation, and basic insert/query operations.

**Files:**
- Create: `scripts/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing tests for schema creation and WAL mode**

```python
# tests/test_db.py
#!/usr/bin/env python3
"""Tests for the database module."""

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from db import get_connection, DB_PATH


class TestGetConnection(unittest.TestCase):
    """Tests for get_connection and schema creation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_database_and_schema(self):
        """Test that get_connection creates the DB with all tables."""
        conn = get_connection(self.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        self.assertIn('sessions', tables)
        self.assertIn('exchanges', tables)
        self.assertIn('tags', tables)
        conn.close()

    def test_creates_fts5_virtual_table(self):
        """Test that FTS5 virtual table is created."""
        conn = get_connection(self.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='exchanges_fts'"
        )
        self.assertIsNotNone(cursor.fetchone())
        conn.close()

    def test_wal_mode_enabled(self):
        """Test that WAL journal mode is active."""
        conn = get_connection(self.db_path)
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        self.assertEqual(mode, 'wal')
        conn.close()

    def test_busy_timeout_set(self):
        """Test that busy timeout is configured."""
        conn = get_connection(self.db_path)
        cursor = conn.execute("PRAGMA busy_timeout")
        timeout = cursor.fetchone()[0]
        self.assertEqual(timeout, 5000)
        conn.close()

    def test_idempotent_creation(self):
        """Test that calling get_connection twice doesn't error."""
        conn1 = get_connection(self.db_path)
        conn1.close()
        conn2 = get_connection(self.db_path)
        cursor = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        self.assertGreater(len(tables), 0)
        conn2.close()


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Implement `scripts/db.py` — connection and schema**

```python
# scripts/db.py
#!/usr/bin/env python3
"""SQLite database layer for the recall plugin.

Single point of contact for all database operations.
All other modules import from here — none touch the DB file directly.
"""

import sqlite3
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple

DB_DIR = Path.home() / '.claude' / 'context-recall'
DB_PATH = DB_DIR / 'recall.db'

DB_BUSY_TIMEOUT_MS = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    project_path    TEXT NOT NULL,
    project_hash    TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    exchange_count  INTEGER DEFAULT 0,
    transcript_path TEXT,
    byte_offset     INTEGER DEFAULT 0,
    metadata        TEXT
);

CREATE TABLE IF NOT EXISTS exchanges (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    idx             INTEGER NOT NULL,
    timestamp       TEXT NOT NULL,
    preview         TEXT NOT NULL,
    user_text       TEXT,
    assistant_text  TEXT,
    UNIQUE(session_id, idx)
);

CREATE TABLE IF NOT EXISTS tags (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tag             TEXT NOT NULL,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    exchange_idx    INTEGER,
    source          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(tag, session_id, exchange_idx)
);

CREATE INDEX IF NOT EXISTS idx_exchanges_session ON exchanges(session_id);
CREATE INDEX IF NOT EXISTS idx_tags_session ON tags(session_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_hash);
"""

_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS exchanges_fts USING fts5(
    user_text,
    assistant_text,
    preview,
    content=exchanges,
    content_rowid=id
);
"""


def get_connection(db_path: Path = None) -> sqlite3.Connection:
    """Get a database connection with WAL mode and schema initialized.

    Args:
        db_path: Override path for testing. Defaults to DB_PATH.

    Returns:
        sqlite3.Connection with row_factory=sqlite3.Row
    """
    if db_path is None:
        db_path = DB_PATH

    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
    conn.executescript(_SCHEMA)
    # FTS5 requires separate creation (can't be inside executescript with IF NOT EXISTS reliably)
    try:
        conn.executescript(_FTS_SCHEMA)
    except sqlite3.OperationalError:
        pass  # Already exists
    conn.commit()
    return conn
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_db.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Add tests for session and exchange CRUD**

Append to `tests/test_db.py`:

```python
from db import (
    insert_session, get_session, list_sessions,
    insert_exchanges, get_exchanges, update_session_offset,
    end_session,
)


class TestSessionCRUD(unittest.TestCase):
    """Tests for session insert/query operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.conn = get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_insert_and_get_session(self):
        """Test inserting and retrieving a session."""
        insert_session(self.conn, 'sess-1', '/path/to/project', 'hash123',
                       '2026-01-05T09:00:00Z', '/path/transcript.jsonl')
        session = get_session(self.conn, 'sess-1')
        self.assertIsNotNone(session)
        self.assertEqual(session['project_path'], '/path/to/project')
        self.assertEqual(session['project_hash'], 'hash123')

    def test_get_nonexistent_session(self):
        """Test retrieving a session that doesn't exist."""
        session = get_session(self.conn, 'nonexistent')
        self.assertIsNone(session)

    def test_list_sessions_by_project(self):
        """Test listing sessions filtered by project hash."""
        insert_session(self.conn, 's1', '/proj/a', 'hash-a', '2026-01-05T09:00:00Z')
        insert_session(self.conn, 's2', '/proj/a', 'hash-a', '2026-01-06T09:00:00Z')
        insert_session(self.conn, 's3', '/proj/b', 'hash-b', '2026-01-07T09:00:00Z')
        sessions = list_sessions(self.conn, project_hash='hash-a')
        self.assertEqual(len(sessions), 2)

    def test_list_all_sessions(self):
        """Test listing all sessions across projects."""
        insert_session(self.conn, 's1', '/proj/a', 'hash-a', '2026-01-05T09:00:00Z')
        insert_session(self.conn, 's2', '/proj/b', 'hash-b', '2026-01-06T09:00:00Z')
        sessions = list_sessions(self.conn)
        self.assertEqual(len(sessions), 2)

    def test_end_session(self):
        """Test marking a session as ended."""
        insert_session(self.conn, 'sess-1', '/proj', 'hash', '2026-01-05T09:00:00Z')
        end_session(self.conn, 'sess-1', '2026-01-05T17:00:00Z')
        session = get_session(self.conn, 'sess-1')
        self.assertEqual(session['ended_at'], '2026-01-05T17:00:00Z')

    def test_update_session_offset(self):
        """Test updating byte offset and exchange count."""
        insert_session(self.conn, 'sess-1', '/proj', 'hash', '2026-01-05T09:00:00Z')
        update_session_offset(self.conn, 'sess-1', byte_offset=1024, exchange_count=5)
        session = get_session(self.conn, 'sess-1')
        self.assertEqual(session['byte_offset'], 1024)
        self.assertEqual(session['exchange_count'], 5)


class TestExchangeCRUD(unittest.TestCase):
    """Tests for exchange insert/query operations."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-1', '/proj', 'hash', '2026-01-05T09:00:00Z')

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_insert_and_get_exchanges(self):
        """Test inserting and retrieving exchanges."""
        exchanges = [
            {'idx': 1, 'timestamp': '2026-01-05T09:00:00Z', 'preview': 'Hello',
             'user_text': 'Hello world', 'assistant_text': 'Hi there'},
            {'idx': 2, 'timestamp': '2026-01-05T09:01:00Z', 'preview': 'Question',
             'user_text': 'How are you?', 'assistant_text': 'I am well'},
        ]
        insert_exchanges(self.conn, 'sess-1', exchanges)
        result = get_exchanges(self.conn, 'sess-1')
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['user_text'], 'Hello world')

    def test_get_exchanges_with_limit(self):
        """Test retrieving last N exchanges."""
        exchanges = [
            {'idx': i, 'timestamp': f'2026-01-05T09:{i:02d}:00Z',
             'preview': f'Ex {i}', 'user_text': f'Q{i}', 'assistant_text': f'A{i}'}
            for i in range(1, 11)
        ]
        insert_exchanges(self.conn, 'sess-1', exchanges)
        result = get_exchanges(self.conn, 'sess-1', last_n=3)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]['idx'], 8)  # 8, 9, 10

    def test_get_exchanges_empty_session(self):
        """Test retrieving from session with no exchanges."""
        result = get_exchanges(self.conn, 'sess-1')
        self.assertEqual(len(result), 0)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 6: Run tests to verify the new ones fail**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_db.py::TestSessionCRUD -v`
Expected: FAIL — `ImportError: cannot import name 'insert_session'`

- [ ] **Step 7: Implement session and exchange CRUD in `scripts/db.py`**

Append to `scripts/db.py`:

```python
def insert_session(conn: sqlite3.Connection, session_id: str, project_path: str,
                   project_hash: str, started_at: str,
                   transcript_path: str = None) -> None:
    """Insert a new session row. No-op if session_id already exists."""
    conn.execute(
        """INSERT OR IGNORE INTO sessions
           (session_id, project_path, project_hash, started_at, transcript_path)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, project_path, project_hash, started_at, transcript_path)
    )
    conn.commit()


def get_session(conn: sqlite3.Connection, session_id: str) -> Optional[Dict]:
    """Get a session by ID. Returns dict or None."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def list_sessions(conn: sqlite3.Connection, project_hash: str = None,
                  project_path_contains: str = None) -> List[Dict]:
    """List sessions, optionally filtered by project.

    Args:
        project_hash: Exact match on project hash.
        project_path_contains: Substring match on project path (for --project flag).
    """
    if project_hash:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE project_hash = ? ORDER BY started_at DESC",
            (project_hash,)
        ).fetchall()
    elif project_path_contains:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE project_path LIKE ? ORDER BY started_at DESC",
            (f'%{project_path_contains}%',)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def end_session(conn: sqlite3.Connection, session_id: str, ended_at: str) -> None:
    """Mark a session as ended."""
    conn.execute(
        "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
        (ended_at, session_id)
    )
    conn.commit()


def update_session_offset(conn: sqlite3.Connection, session_id: str,
                          byte_offset: int, exchange_count: int) -> None:
    """Update a session's byte offset and exchange count."""
    conn.execute(
        "UPDATE sessions SET byte_offset = ?, exchange_count = ? WHERE session_id = ?",
        (byte_offset, exchange_count, session_id)
    )
    conn.commit()


def insert_exchanges(conn: sqlite3.Connection, session_id: str,
                     exchanges: List[Dict]) -> None:
    """Insert exchanges and update FTS5 index."""
    for ex in exchanges:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO exchanges
               (session_id, idx, timestamp, preview, user_text, assistant_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, ex['idx'], ex['timestamp'], ex['preview'],
             ex.get('user_text', ''), ex.get('assistant_text', ''))
        )
        if cursor.rowcount > 0:
            # Sync FTS5 index
            row_id = cursor.lastrowid
            conn.execute(
                """INSERT INTO exchanges_fts(rowid, user_text, assistant_text, preview)
                   VALUES (?, ?, ?, ?)""",
                (row_id, ex.get('user_text', ''), ex.get('assistant_text', ''),
                 ex['preview'])
            )
    conn.commit()


def get_exchanges(conn: sqlite3.Connection, session_id: str,
                  last_n: int = None) -> List[Dict]:
    """Get exchanges for a session, optionally limited to last N."""
    if last_n:
        rows = conn.execute(
            """SELECT * FROM exchanges WHERE session_id = ?
               ORDER BY idx DESC LIMIT ?""",
            (session_id, last_n)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    else:
        rows = conn.execute(
            "SELECT * FROM exchanges WHERE session_id = ? ORDER BY idx",
            (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 8: Run all db tests**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_db.py -v`
Expected: All 14 tests PASS

- [ ] **Step 9: Add tests for FTS5 search and cross-session/cross-project search**

Append to `tests/test_db.py`:

```python
from db import search_exchanges_fts, search_exchanges_global


class TestFTS5Search(unittest.TestCase):
    """Tests for full-text search via FTS5."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-1', '/proj/a', 'hash-a', '2026-01-05T09:00:00Z')
        exchanges = [
            {'idx': 1, 'timestamp': '2026-01-05T09:00:00Z', 'preview': 'warp divergence',
             'user_text': 'the warp divergence in the reduction kernel is bad',
             'assistant_text': 'Try using shared memory to reduce divergence'},
            {'idx': 2, 'timestamp': '2026-01-05T09:01:00Z', 'preview': 'occupancy issue',
             'user_text': 'occupancy is only 45 percent',
             'assistant_text': 'Check your register pressure and shared memory usage'},
            {'idx': 3, 'timestamp': '2026-01-05T09:02:00Z', 'preview': 'matmul kernel',
             'user_text': 'the matmul kernel needs tiling',
             'assistant_text': 'Use 16x16 tiles for the shared memory approach'},
        ]
        insert_exchanges(self.conn, 'sess-1', exchanges)

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_search_finds_match_in_user_text(self):
        """Test FTS5 finds matches in user text."""
        results = search_exchanges_fts(self.conn, 'divergence', session_id='sess-1')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['idx'], 1)

    def test_search_finds_match_in_assistant_text(self):
        """Test FTS5 finds matches in assistant text."""
        results = search_exchanges_fts(self.conn, 'register', session_id='sess-1')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['idx'], 2)

    def test_search_no_results(self):
        """Test FTS5 returns empty for no matches."""
        results = search_exchanges_fts(self.conn, 'nonexistent', session_id='sess-1')
        self.assertEqual(len(results), 0)

    def test_search_multiple_matches(self):
        """Test FTS5 returns multiple matches."""
        results = search_exchanges_fts(self.conn, 'shared memory', session_id='sess-1')
        self.assertGreaterEqual(len(results), 2)


class TestCrossSessionSearch(unittest.TestCase):
    """Tests for cross-session and cross-project search."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.conn = get_connection(self.db_path)

        # Project A, session 1
        insert_session(self.conn, 's1', '/proj/triton-metal', 'hash-a', '2026-01-05T09:00:00Z')
        insert_exchanges(self.conn, 's1', [
            {'idx': 1, 'timestamp': '2026-01-05T09:00:00Z', 'preview': 'MSL codegen',
             'user_text': 'MSL codegen for the reduction',
             'assistant_text': 'Use threadgroup memory for the reduction'},
        ])

        # Project A, session 2 (after /clear)
        insert_session(self.conn, 's2', '/proj/triton-metal', 'hash-a', '2026-01-06T09:00:00Z')
        insert_exchanges(self.conn, 's2', [
            {'idx': 1, 'timestamp': '2026-01-06T09:00:00Z', 'preview': 'Metal dispatch',
             'user_text': 'Metal dispatch groups config',
             'assistant_text': 'Set threadgroup size to 256'},
        ])

        # Project B
        insert_session(self.conn, 's3', '/proj/cuda-kernels', 'hash-b', '2026-01-07T09:00:00Z')
        insert_exchanges(self.conn, 's3', [
            {'idx': 1, 'timestamp': '2026-01-07T09:00:00Z', 'preview': 'CUDA reduction',
             'user_text': 'CUDA reduction kernel optimization',
             'assistant_text': 'Use warp shuffle for the final reduction stage'},
        ])

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_search_across_project_sessions(self):
        """Test searching across all sessions in a project."""
        results = search_exchanges_fts(self.conn, 'reduction', project_hash='hash-a')
        self.assertEqual(len(results), 1)  # Only project A's session 1

    def test_search_global(self):
        """Test searching across all projects."""
        results = search_exchanges_global(self.conn, 'reduction')
        self.assertGreaterEqual(len(results), 2)  # Both projects have 'reduction'

    def test_search_by_project_path_substring(self):
        """Test searching with project path substring."""
        sessions = list_sessions(self.conn, project_path_contains='triton')
        self.assertEqual(len(sessions), 2)  # Both triton-metal sessions

    def test_clear_survival(self):
        """Test that /clear doesn't lose data — both sessions exist."""
        s1 = get_session(self.conn, 's1')
        s2 = get_session(self.conn, 's2')
        self.assertIsNotNone(s1)
        self.assertIsNotNone(s2)
        # Both sessions' exchanges are intact
        ex1 = get_exchanges(self.conn, 's1')
        ex2 = get_exchanges(self.conn, 's2')
        self.assertEqual(len(ex1), 1)
        self.assertEqual(len(ex2), 1)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 10: Run to verify new tests fail**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_db.py::TestFTS5Search -v`
Expected: FAIL — `ImportError: cannot import name 'search_exchanges_fts'`

- [ ] **Step 11: Implement FTS5 search and global search in `scripts/db.py`**

Append to `scripts/db.py`:

```python
def search_exchanges_fts(conn: sqlite3.Connection, query: str,
                         session_id: str = None,
                         project_hash: str = None,
                         limit: int = 10) -> List[Dict]:
    """Full-text search across exchanges using FTS5.

    Args:
        query: Search terms.
        session_id: Limit to a specific session.
        project_hash: Limit to sessions in a specific project.
        limit: Max results to return.
    """
    # FTS5 query — escape special characters
    fts_query = '"' + query.replace('"', '""') + '"'

    if session_id:
        rows = conn.execute(
            """SELECT e.* FROM exchanges e
               JOIN exchanges_fts fts ON e.id = fts.rowid
               WHERE exchanges_fts MATCH ? AND e.session_id = ?
               ORDER BY e.timestamp DESC LIMIT ?""",
            (fts_query, session_id, limit)
        ).fetchall()
    elif project_hash:
        rows = conn.execute(
            """SELECT e.* FROM exchanges e
               JOIN exchanges_fts fts ON e.id = fts.rowid
               JOIN sessions s ON e.session_id = s.session_id
               WHERE exchanges_fts MATCH ? AND s.project_hash = ?
               ORDER BY e.timestamp DESC LIMIT ?""",
            (fts_query, project_hash, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT e.* FROM exchanges e
               JOIN exchanges_fts fts ON e.id = fts.rowid
               WHERE exchanges_fts MATCH ?
               ORDER BY e.timestamp DESC LIMIT ?""",
            (fts_query, limit)
        ).fetchall()

    return [dict(r) for r in rows]


def search_exchanges_global(conn: sqlite3.Connection, query: str,
                            limit: int = 20) -> List[Dict]:
    """Search across ALL sessions and projects.

    Returns exchange dicts enriched with session/project info.
    """
    fts_query = '"' + query.replace('"', '""') + '"'
    rows = conn.execute(
        """SELECT e.*, s.project_path, s.started_at as session_started
           FROM exchanges e
           JOIN exchanges_fts fts ON e.id = fts.rowid
           JOIN sessions s ON e.session_id = s.session_id
           WHERE exchanges_fts MATCH ?
           ORDER BY e.timestamp DESC LIMIT ?""",
        (fts_query, limit)
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 12: Run all db tests**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_db.py -v`
Expected: All 22 tests PASS

- [ ] **Step 13: Add tests for prune, stats, and export**

Append to `tests/test_db.py`:

```python
from db import prune_session, prune_before_date, get_stats, export_session_json


class TestMaintenanceOps(unittest.TestCase):
    """Tests for prune, stats, and export."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 's1', '/proj/a', 'hash-a', '2026-01-05T09:00:00Z')
        insert_session(self.conn, 's2', '/proj/a', 'hash-a', '2026-02-10T09:00:00Z')
        insert_exchanges(self.conn, 's1', [
            {'idx': 1, 'timestamp': '2026-01-05T09:00:00Z', 'preview': 'Ex1',
             'user_text': 'Hello', 'assistant_text': 'Hi'},
        ])
        insert_exchanges(self.conn, 's2', [
            {'idx': 1, 'timestamp': '2026-02-10T09:00:00Z', 'preview': 'Ex1',
             'user_text': 'New session', 'assistant_text': 'Welcome back'},
        ])

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_prune_session(self):
        """Test deleting a specific session and its exchanges."""
        prune_session(self.conn, 's1')
        self.assertIsNone(get_session(self.conn, 's1'))
        self.assertEqual(len(get_exchanges(self.conn, 's1')), 0)
        # s2 untouched
        self.assertIsNotNone(get_session(self.conn, 's2'))

    def test_prune_before_date(self):
        """Test deleting sessions before a date."""
        pruned = prune_before_date(self.conn, '2026-02-01')
        self.assertEqual(pruned, 1)  # s1 deleted
        self.assertIsNone(get_session(self.conn, 's1'))
        self.assertIsNotNone(get_session(self.conn, 's2'))

    def test_get_stats(self):
        """Test getting storage statistics."""
        stats = get_stats(self.conn, self.db_path)
        self.assertEqual(stats['total_sessions'], 2)
        self.assertEqual(stats['total_exchanges'], 2)
        self.assertIn('db_size_bytes', stats)
        self.assertIn('projects', stats)

    def test_export_session_json(self):
        """Test exporting a session to JSON dict."""
        data = export_session_json(self.conn, 's1')
        self.assertEqual(data['session_id'], 's1')
        self.assertEqual(len(data['exchanges']), 1)
        self.assertEqual(data['exchanges'][0]['user_text'], 'Hello')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 14: Implement maintenance operations in `scripts/db.py`**

Append to `scripts/db.py`:

```python
def prune_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Delete a session and all its exchanges and tags."""
    # Delete FTS entries first
    conn.execute(
        """DELETE FROM exchanges_fts WHERE rowid IN
           (SELECT id FROM exchanges WHERE session_id = ?)""",
        (session_id,)
    )
    conn.execute("DELETE FROM tags WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM exchanges WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()


def prune_before_date(conn: sqlite3.Connection, before_date: str) -> int:
    """Delete all sessions started before a date. Returns count deleted."""
    rows = conn.execute(
        "SELECT session_id FROM sessions WHERE started_at < ?", (before_date,)
    ).fetchall()
    for row in rows:
        prune_session(conn, row['session_id'])
    return len(rows)


def get_stats(conn: sqlite3.Connection, db_path: Path = None) -> Dict:
    """Get storage statistics."""
    if db_path is None:
        db_path = DB_PATH

    total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_exchanges = conn.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0]
    total_tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

    projects = conn.execute(
        """SELECT project_path, COUNT(DISTINCT session_id) as session_count,
                  SUM(exchange_count) as exchange_total
           FROM sessions GROUP BY project_hash"""
    ).fetchall()

    db_size = os.path.getsize(str(db_path)) if db_path.exists() else 0

    return {
        'total_sessions': total_sessions,
        'total_exchanges': total_exchanges,
        'total_tags': total_tags,
        'db_size_bytes': db_size,
        'projects': [dict(p) for p in projects],
    }


def export_session_json(conn: sqlite3.Connection, session_id: str) -> Dict:
    """Export a session and its exchanges as a JSON-serializable dict."""
    session = get_session(conn, session_id)
    if not session:
        return {}
    exchanges = get_exchanges(conn, session_id)
    tags = conn.execute(
        "SELECT tag, exchange_idx, source, created_at FROM tags WHERE session_id = ?",
        (session_id,)
    ).fetchall()
    return {
        **session,
        'exchanges': exchanges,
        'tags': [dict(t) for t in tags],
    }
```

- [ ] **Step 15: Run full db test suite**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_db.py -v`
Expected: All 26 tests PASS

- [ ] **Step 16: Commit**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git add scripts/db.py tests/test_db.py
git commit -m "feat: add SQLite database layer with schema, CRUD, FTS5 search, and maintenance ops"
```

---

### Task 2: Create `scripts/auto_tagger.py` — Hybrid Tagging System

Term-frequency extraction, technical term heuristic, and DB integration for auto/manual tags.

**Files:**
- Create: `scripts/auto_tagger.py`
- Create: `tests/test_auto_tagger.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_auto_tagger.py
#!/usr/bin/env python3
"""Tests for the auto-tagger module."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from auto_tagger import (
    tokenize, extract_terms, select_tags, is_technical_term,
    STOPWORDS, GENERIC_PROGRAMMING_TERMS,
)


class TestTokenize(unittest.TestCase):
    """Tests for tokenize function."""

    def test_basic_tokenization(self):
        tokens = tokenize("Hello world, this is a test")
        self.assertIn('hello', tokens)
        self.assertIn('world', tokens)
        self.assertNotIn(',', tokens)

    def test_filters_short_tokens(self):
        tokens = tokenize("I am a developer in SF")
        self.assertNotIn('i', tokens)
        self.assertNotIn('am', tokens)
        self.assertNotIn('a', tokens)
        self.assertNotIn('in', tokens)
        self.assertNotIn('sf', tokens)
        self.assertIn('developer', tokens)

    def test_preserves_hyphens_and_underscores(self):
        tokens = tokenize("warp-divergence and shared_memory are important")
        self.assertIn('warp-divergence', tokens)
        self.assertIn('shared_memory', tokens)


class TestIsTechnicalTerm(unittest.TestCase):
    """Tests for the technical term heuristic."""

    def test_terms_with_special_chars(self):
        self.assertTrue(is_technical_term('warp-divergence'))
        self.assertTrue(is_technical_term('shared_memory'))
        self.assertTrue(is_technical_term('fp16'))
        self.assertTrue(is_technical_term('sm_90'))

    def test_generic_terms_rejected(self):
        self.assertFalse(is_technical_term('function'))
        self.assertFalse(is_technical_term('variable'))
        self.assertFalse(is_technical_term('error'))

    def test_common_stopwords_rejected(self):
        self.assertFalse(is_technical_term('the'))
        self.assertFalse(is_technical_term('should'))


class TestExtractTerms(unittest.TestCase):
    """Tests for extract_terms function."""

    def test_counts_term_frequency(self):
        texts = [
            "warp divergence in the reduction kernel",
            "the warp divergence is causing performance issues",
            "fixing warp divergence with shared memory",
        ]
        counts = extract_terms(texts)
        self.assertGreaterEqual(counts.get('warp', 0), 3)
        self.assertGreaterEqual(counts.get('divergence', 0), 3)

    def test_empty_texts(self):
        counts = extract_terms([])
        self.assertEqual(len(counts), 0)


class TestSelectTags(unittest.TestCase):
    """Tests for select_tags function."""

    def test_selects_frequent_technical_terms(self):
        counts = {'warp-divergence': 5, 'shared_memory': 4, 'the': 100, 'reduction': 3}
        tags = select_tags(counts)
        self.assertIn('warp-divergence', tags)
        self.assertIn('shared_memory', tags)
        self.assertIn('reduction', tags)
        self.assertNotIn('the', tags)

    def test_respects_max_limit(self):
        counts = {f'term-{i}': 10 for i in range(20)}
        tags = select_tags(counts, max_tags=10)
        self.assertLessEqual(len(tags), 10)

    def test_respects_min_frequency(self):
        counts = {'warp-divergence': 5, 'rare-term': 1}
        tags = select_tags(counts, min_frequency=3)
        self.assertIn('warp-divergence', tags)
        self.assertNotIn('rare-term', tags)

    def test_empty_counts(self):
        tags = select_tags({})
        self.assertEqual(len(tags), 0)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_auto_tagger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'auto_tagger'`

- [ ] **Step 3: Implement `scripts/auto_tagger.py`**

```python
# scripts/auto_tagger.py
#!/usr/bin/env python3
"""Auto-tagging via term-frequency extraction.

Extracts technical terms from exchange text using simple TF analysis.
No external dependencies — uses only stdlib.
"""

import re
from collections import Counter
from typing import List, Dict, Set

MAX_AUTO_TAGS_PER_SESSION = 10
AUTO_TAG_MIN_FREQUENCY = 3

STOPWORDS = frozenset({
    'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have', 'i', 'it',
    'for', 'not', 'on', 'with', 'he', 'as', 'you', 'do', 'at', 'this',
    'but', 'his', 'by', 'from', 'they', 'we', 'say', 'her', 'she', 'or',
    'an', 'will', 'my', 'one', 'all', 'would', 'there', 'their', 'what',
    'so', 'up', 'out', 'if', 'about', 'who', 'get', 'which', 'go', 'me',
    'when', 'make', 'can', 'like', 'time', 'no', 'just', 'him', 'know',
    'take', 'people', 'into', 'year', 'your', 'good', 'some', 'could',
    'them', 'see', 'other', 'than', 'then', 'now', 'look', 'only', 'come',
    'its', 'over', 'think', 'also', 'back', 'after', 'use', 'two', 'how',
    'our', 'work', 'first', 'well', 'way', 'even', 'new', 'want', 'because',
    'any', 'these', 'give', 'day', 'most', 'us', 'is', 'are', 'was', 'were',
    'been', 'being', 'has', 'had', 'having', 'does', 'did', 'doing', 'am',
    'should', 'would', 'could', 'might', 'must', 'shall', 'may', 'need',
    'let', 'here', 'right', 'still', 'too', 'own', 'such', 'much', 'very',
    'sure', 'thing', 'yeah', 'yes', 'okay', 'got', 'going', 'actually',
    'really', 'maybe', 'probably', 'looks', 'seems', 'try', 'using', 'used',
    'something', 'maybe', 'please', 'thanks', 'thank', 'great', 'nice',
    'that', 'those', 'what', 'where', 'when', 'while', 'each', 'every',
    'both', 'few', 'more', 'many', 'same', 'different', 'another',
})

GENERIC_PROGRAMMING_TERMS = frozenset({
    'function', 'method', 'class', 'variable', 'error', 'file', 'code',
    'line', 'return', 'value', 'type', 'string', 'number', 'list', 'array',
    'object', 'import', 'module', 'package', 'test', 'run', 'print',
    'output', 'input', 'data', 'result', 'issue', 'problem', 'fix',
    'change', 'update', 'add', 'remove', 'delete', 'create', 'read',
    'write', 'check', 'set', 'call', 'pass', 'name', 'path', 'dir',
    'directory', 'index', 'key', 'param', 'parameter', 'arg', 'argument',
    'config', 'option', 'flag', 'true', 'false', 'null', 'none',
    'example', 'default', 'current', 'next', 'last', 'end', 'start',
    'begin', 'loop', 'command', 'script', 'build', 'install',
})

# Pattern to split on whitespace and punctuation, but preserve - and _ within words
_TOKEN_PATTERN = re.compile(r'[a-z0-9][a-z0-9_-]*[a-z0-9]|[a-z0-9]{3,}', re.IGNORECASE)


def tokenize(text: str) -> List[str]:
    """Tokenize text into lowercase terms, filtering short tokens."""
    tokens = _TOKEN_PATTERN.findall(text.lower())
    return [t for t in tokens if len(t) >= 3]


def is_technical_term(term: str) -> bool:
    """Check if a term is likely technical (not generic/stopword)."""
    if term in STOPWORDS:
        return False
    if term in GENERIC_PROGRAMMING_TERMS:
        return False
    # Terms with special chars are likely technical
    if '-' in term or '_' in term or any(c.isdigit() for c in term):
        return True
    # Remaining terms pass if not in filter lists
    return True


def extract_terms(texts: List[str]) -> Dict[str, int]:
    """Extract term frequencies from a list of texts.

    Args:
        texts: List of text strings to analyze.

    Returns:
        Dict mapping term -> frequency count.
    """
    counter = Counter()
    for text in texts:
        tokens = tokenize(text)
        counter.update(tokens)
    # Filter stopwords
    return {term: count for term, count in counter.items()
            if term not in STOPWORDS}


def select_tags(term_counts: Dict[str, int],
                max_tags: int = MAX_AUTO_TAGS_PER_SESSION,
                min_frequency: int = AUTO_TAG_MIN_FREQUENCY) -> List[str]:
    """Select the best auto-tags from term frequency counts.

    Args:
        term_counts: Dict of term -> frequency.
        max_tags: Maximum number of tags to return.
        min_frequency: Minimum frequency threshold.

    Returns:
        List of tag strings, sorted by frequency (descending).
    """
    candidates = [
        (term, count) for term, count in term_counts.items()
        if count >= min_frequency and is_technical_term(term)
    ]
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [term for term, _ in candidates[:max_tags]]


def get_session_texts(exchanges: List[Dict]) -> List[str]:
    """Extract all text from exchanges for term analysis."""
    texts = []
    for ex in exchanges:
        if ex.get('user_text'):
            texts.append(ex['user_text'])
        if ex.get('assistant_text'):
            texts.append(ex['assistant_text'])
    return texts


def compute_auto_tags(exchanges: List[Dict],
                      max_tags: int = MAX_AUTO_TAGS_PER_SESSION,
                      min_frequency: int = AUTO_TAG_MIN_FREQUENCY) -> List[str]:
    """Compute auto-tags for a set of exchanges.

    This is the main entry point. Call with all session exchanges
    to get the current auto-tags.
    """
    texts = get_session_texts(exchanges)
    counts = extract_terms(texts)
    return select_tags(counts, max_tags, min_frequency)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_auto_tagger.py -v`
Expected: All 11 tests PASS

- [ ] **Step 5: Add integration test with DB**

Append to `tests/test_auto_tagger.py`:

```python
from db import get_connection, insert_session, insert_exchanges, get_exchanges
from auto_tagger import compute_auto_tags


class TestComputeAutoTags(unittest.TestCase):
    """Integration test: compute tags from DB exchanges."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-1', '/proj', 'hash', '2026-01-05T09:00:00Z')

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_compute_tags_from_kernel_session(self):
        """Test auto-tagging a realistic kernel optimization session."""
        exchanges = [
            {'idx': i, 'timestamp': f'2026-01-05T{9+i}:00:00Z', 'preview': f'ex{i}',
             'user_text': text, 'assistant_text': resp}
            for i, (text, resp) in enumerate([
                ("the warp divergence in this reduction kernel is killing perf",
                 "warp divergence happens when threads take different branches"),
                ("how do I fix warp divergence in the reduction",
                 "use warp shuffle intrinsics to avoid the divergence"),
                ("shared memory approach for the reduction kernel",
                 "allocate shared memory and use it for the reduction"),
                ("what about occupancy with all this shared memory",
                 "check occupancy calculator, shared memory limits blocks"),
            ], start=1)
        ]
        insert_exchanges(self.conn, 'sess-1', exchanges)
        db_exchanges = get_exchanges(self.conn, 'sess-1')
        tags = compute_auto_tags(db_exchanges)
        # Should find kernel-related terms
        tag_set = set(tags)
        self.assertTrue(
            tag_set & {'warp', 'divergence', 'reduction', 'shared', 'occupancy', 'kernel'},
            f"Expected kernel terms in tags, got: {tags}"
        )


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 6: Run full auto_tagger tests**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_auto_tagger.py -v`
Expected: All 12 tests PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git add scripts/auto_tagger.py tests/test_auto_tagger.py
git commit -m "feat: add auto-tagger with TF extraction and technical term heuristic"
```

---

### Task 3: Create `hooks/prompt_submit.py` — Rewrite the Main Hook

Replace `save_context_snapshot.py` with SQLite-backed incremental indexing + auto-tagging.

**Files:**
- Create: `hooks/prompt_submit.py`
- Create: `tests/test_prompt_submit.py`
- Remove reference: `hooks/save_context_snapshot.py` (kept until migration complete)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_prompt_submit.py
#!/usr/bin/env python3
"""Tests for the prompt_submit hook."""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from prompt_submit import build_new_exchanges, run_hook
from db import get_connection, get_session, get_exchanges


class TestBuildNewExchanges(unittest.TestCase):
    """Tests for exchange building (carried from v1)."""

    def test_builds_exchanges(self):
        messages = [
            {'role': 'user', 'text': 'Question 1', 'timestamp': '2026-01-05T09:00:00Z'},
            {'role': 'assistant', 'text': 'Answer 1', 'timestamp': '2026-01-05T09:00:05Z'},
        ]
        exchanges = build_new_exchanges(messages, start_idx=1)
        self.assertEqual(len(exchanges), 1)
        self.assertEqual(exchanges[0]['idx'], 1)
        self.assertIn('preview', exchanges[0])
        self.assertIn('user_text', exchanges[0])
        self.assertIn('assistant_text', exchanges[0])

    def test_empty_messages(self):
        self.assertEqual(len(build_new_exchanges([], 1)), 0)

    def test_unpaired_skipped(self):
        messages = [
            {'role': 'user', 'text': 'Q1', 'timestamp': ''},
            {'role': 'user', 'text': 'Q2', 'timestamp': ''},
            {'role': 'assistant', 'text': 'A2', 'timestamp': ''},
        ]
        exchanges = build_new_exchanges(messages, 1)
        self.assertEqual(len(exchanges), 1)

    def test_custom_start_idx(self):
        messages = [
            {'role': 'user', 'text': 'Q', 'timestamp': ''},
            {'role': 'assistant', 'text': 'A', 'timestamp': ''},
        ]
        exchanges = build_new_exchanges(messages, start_idx=10)
        self.assertEqual(exchanges[0]['idx'], 10)


class TestRunHook(unittest.TestCase):
    """Integration tests for the hook's run_hook function."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_file = Path(self.temp_dir) / 'transcript.jsonl'
        self.db_path = Path(self.temp_dir) / '.claude' / 'context-recall' / 'recall.db'

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_transcript(self, exchanges):
        """Write test transcript file."""
        lines = []
        for user_text, assistant_text, ts in exchanges:
            lines.append(json.dumps({
                'type': 'user',
                'message': {'content': [{'type': 'text', 'text': user_text}]},
                'timestamp': ts
            }))
            lines.append(json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': assistant_text}]},
                'timestamp': ts
            }))
        with open(self.transcript_file, 'w') as f:
            f.write('\n'.join(lines))

    def test_creates_session_and_exchanges(self):
        """Test that hook creates session row and exchanges."""
        self._write_transcript([
            ('Hello', 'Hi there', '2026-01-05T09:00:00Z'),
            ('How are you?', 'Good', '2026-01-05T09:01:00Z'),
        ])
        input_data = {
            'session_id': 'test-sess',
            'transcript_path': str(self.transcript_file),
            'user_prompt': 'Hello',
            'project_path': '/proj/test',
            'project_hash': 'hash-test',
        }
        result = run_hook(input_data, db_path=self.db_path)
        self.assertIsInstance(result, dict)

        conn = get_connection(self.db_path)
        session = get_session(conn, 'test-sess')
        self.assertIsNotNone(session)
        self.assertEqual(session['exchange_count'], 2)
        exchanges = get_exchanges(conn, 'test-sess')
        self.assertEqual(len(exchanges), 2)
        conn.close()

    def test_incremental_update(self):
        """Test that subsequent calls only add new exchanges."""
        self._write_transcript([
            ('Hello', 'Hi', '2026-01-05T09:00:00Z'),
        ])
        input_data = {
            'session_id': 'test-sess',
            'transcript_path': str(self.transcript_file),
            'user_prompt': 'Hello',
            'project_path': '/proj/test',
            'project_hash': 'hash-test',
        }

        # First call
        run_hook(input_data, db_path=self.db_path)

        # Add more to transcript
        with open(self.transcript_file, 'a') as f:
            f.write('\n' + json.dumps({
                'type': 'user',
                'message': {'content': [{'type': 'text', 'text': 'Follow up'}]},
                'timestamp': '2026-01-05T09:02:00Z'
            }))
            f.write('\n' + json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': 'Response'}]},
                'timestamp': '2026-01-05T09:02:05Z'
            }))

        # Second call
        run_hook(input_data, db_path=self.db_path)

        conn = get_connection(self.db_path)
        exchanges = get_exchanges(conn, 'test-sess')
        self.assertEqual(len(exchanges), 2)
        conn.close()

    def test_new_session_preserves_old(self):
        """Test that a new session_id doesn't delete old data (/clear survival)."""
        self._write_transcript([('Hello', 'Hi', '2026-01-05T09:00:00Z')])
        input_data = {
            'session_id': 'sess-1',
            'transcript_path': str(self.transcript_file),
            'user_prompt': 'Hello',
            'project_path': '/proj',
            'project_hash': 'hash',
        }
        run_hook(input_data, db_path=self.db_path)

        # New session (simulates /clear)
        input_data['session_id'] = 'sess-2'
        run_hook(input_data, db_path=self.db_path)

        conn = get_connection(self.db_path)
        self.assertIsNotNone(get_session(conn, 'sess-1'))
        self.assertIsNotNone(get_session(conn, 'sess-2'))
        conn.close()

    def test_recall_command_logs(self):
        """Test that /recall triggers logging."""
        self._write_transcript([('Hello', 'Hi', '2026-01-05T09:00:00Z')])
        input_data = {
            'session_id': 'test-sess',
            'transcript_path': str(self.transcript_file),
            'user_prompt': '/recall',
            'project_path': '/proj',
            'project_hash': 'hash',
        }
        result = run_hook(input_data, db_path=self.db_path)
        self.assertIn('systemMessage', result)
        self.assertIn('recall', result['systemMessage'].lower())


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_prompt_submit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'prompt_submit'`

- [ ] **Step 3: Implement `hooks/prompt_submit.py`**

```python
# hooks/prompt_submit.py
#!/usr/bin/env python3
"""UserPromptSubmit hook — incremental indexing with SQLite + auto-tagging.

Replaces save_context_snapshot.py from v1.0.1.
Runs on every user prompt. Reads stdin JSON, writes to recall.db.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from utils import extract_text_content, make_preview, truncate_text, MAX_CHARS_PER_MESSAGE
from db import (
    get_connection, insert_session, get_session, insert_exchanges,
    update_session_offset, get_exchanges, DB_PATH,
)
from auto_tagger import compute_auto_tags

LOG_FILE = Path.home() / '.claude' / 'recall-events.log'

# --- Migration support ---
INDEX_DIR = Path.home() / '.claude' / 'context-recall'
LEGACY_INDEX_FILE = INDEX_DIR / 'index.json'


def parse_transcript_from_offset(
    transcript_path: str, byte_offset: int = 0
) -> Tuple[List[Dict[str, Any]], int]:
    """Parse transcript file starting from byte offset.

    Returns (messages, new_byte_offset).
    """
    messages = []
    new_offset = byte_offset

    if not transcript_path or not os.path.exists(transcript_path):
        return messages, new_offset

    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            if byte_offset > 0:
                f.seek(byte_offset)
            for line in f:
                line_stripped = line.strip()
                if not line_stripped:
                    continue
                try:
                    entry = json.loads(line_stripped)
                    role = entry.get('type', '') or entry.get('role', '')
                    if role not in ('user', 'assistant'):
                        message_obj = entry.get('message', {})
                        role = message_obj.get('role', '')
                    if role in ('user', 'assistant'):
                        message_obj = entry.get('message', {})
                        text = extract_text_content(message_obj)
                        timestamp = entry.get('timestamp', '')
                        if text:
                            messages.append({
                                'role': role, 'text': text, 'timestamp': timestamp
                            })
                except json.JSONDecodeError:
                    continue
            new_offset = f.tell()
    except Exception:
        pass

    return messages, new_offset


def build_new_exchanges(messages: List[Dict], start_idx: int = 1) -> List[Dict]:
    """Build exchanges from message pairs."""
    exchanges = []
    i = 0
    idx = start_idx
    while i < len(messages):
        if messages[i]['role'] == 'user':
            if i + 1 < len(messages) and messages[i + 1]['role'] == 'assistant':
                user_msg = messages[i]
                asst_msg = messages[i + 1]
                exchanges.append({
                    'idx': idx,
                    'preview': make_preview(user_msg['text']),
                    'timestamp': user_msg.get('timestamp', ''),
                    'user_text': truncate_text(user_msg['text'], MAX_CHARS_PER_MESSAGE),
                    'assistant_text': truncate_text(asst_msg['text'], MAX_CHARS_PER_MESSAGE),
                })
                idx += 1
                i += 2
            else:
                i += 1
        else:
            i += 1
    return exchanges


def migrate_from_json(conn, legacy_path: Path = None):
    """One-time migration from v1.0.1 index.json to SQLite."""
    if legacy_path is None:
        legacy_path = LEGACY_INDEX_FILE
    if not legacy_path.exists():
        return

    try:
        with open(legacy_path, 'r', encoding='utf-8') as f:
            index = json.load(f)

        session_id = index.get('session_id', 'migrated-unknown')
        insert_session(
            conn, session_id,
            project_path=index.get('transcript_path', 'unknown'),
            project_hash='migrated',
            started_at=index.get('session_start', ''),
            transcript_path=index.get('transcript_path', ''),
        )

        exchanges = index.get('exchanges', [])
        if exchanges:
            insert_exchanges(conn, session_id, exchanges)
            update_session_offset(
                conn, session_id,
                byte_offset=index.get('_byte_offset', 0),
                exchange_count=len(exchanges),
            )

        # Rename to .migrated
        migrated_path = legacy_path.with_suffix('.json.migrated')
        legacy_path.rename(migrated_path)
        print("[context-recall] Migrated index.json to SQLite", file=sys.stderr)
    except Exception as e:
        print(f"[context-recall] Migration warning: {e}", file=sys.stderr)


def log_recall_event(session_id: str, exchange_count: int) -> None:
    """Log recall event for observability."""
    timestamp = datetime.now(timezone.utc).isoformat()
    log_entry = f"{timestamp} | session={session_id} | exchanges={exchange_count} | CONTEXT_RECALL_TRIGGERED\n"
    print(f"[context-recall] Context recall triggered at exchange #{exchange_count}", file=sys.stderr)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    except Exception:
        pass


def _store_auto_tags(conn, session_id: str, exchanges: List[Dict]) -> None:
    """Compute and store auto-tags for the session."""
    tags = compute_auto_tags(exchanges)
    now = datetime.now(timezone.utc).isoformat()
    for tag in tags:
        conn.execute(
            """INSERT OR IGNORE INTO tags (tag, session_id, exchange_idx, source, created_at)
               VALUES (?, ?, NULL, 'auto', ?)""",
            (tag, session_id, now)
        )
    conn.commit()


def run_hook(input_data: Dict, db_path: Path = None) -> Dict:
    """Core hook logic, separated from stdin/stdout for testability.

    Args:
        input_data: Parsed JSON from stdin.
        db_path: Override DB path for testing.

    Returns:
        Dict to print as JSON to stdout.
    """
    session_id = input_data.get('session_id', 'unknown')
    transcript_path = input_data.get('transcript_path', '')
    user_prompt = input_data.get('user_prompt', '')
    project_path = input_data.get('project_path', transcript_path)
    project_hash = input_data.get('project_hash', 'default')

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection(db_path)

    # One-time migration
    if db_path:
        legacy = db_path.parent / 'index.json'
    else:
        legacy = LEGACY_INDEX_FILE
    migrate_from_json(conn, legacy)

    # Ensure session exists
    session = get_session(conn, session_id)
    if not session:
        insert_session(conn, session_id, project_path, project_hash, now, transcript_path)
        session = get_session(conn, session_id)

    # Incremental parse
    byte_offset = session['byte_offset'] or 0
    transcript_size = os.path.getsize(transcript_path) if transcript_path and os.path.exists(transcript_path) else 0

    if transcript_size > byte_offset:
        new_messages, new_offset = parse_transcript_from_offset(transcript_path, byte_offset)
        if new_messages:
            start_idx = (session['exchange_count'] or 0) + 1
            new_exchanges = build_new_exchanges(new_messages, start_idx)
            if new_exchanges:
                insert_exchanges(conn, session_id, new_exchanges)
                total = (session['exchange_count'] or 0) + len(new_exchanges)
                update_session_offset(conn, session_id, new_offset, total)

                # Auto-tag with all exchanges
                all_exchanges = get_exchanges(conn, session_id)
                _store_auto_tags(conn, session_id, all_exchanges)
            else:
                update_session_offset(conn, session_id, new_offset, session['exchange_count'] or 0)
        else:
            update_session_offset(conn, session_id, transcript_size, session['exchange_count'] or 0)

    # Check for /recall
    if user_prompt.strip().lower().startswith('/recall'):
        updated = get_session(conn, session_id)
        count = updated['exchange_count'] or 0
        log_recall_event(session_id, count)
        conn.close()
        return {"systemMessage": f"[Observability] Context recall logged at exchange #{count}"}

    conn.close()
    return {}


def main():
    """Main entry point — reads stdin, runs hook, writes stdout."""
    try:
        input_data = json.load(sys.stdin)
        result = run_hook(input_data)
        print(json.dumps(result), file=sys.stdout)
    except Exception as e:
        print(json.dumps({
            "systemMessage": f"[context-recall] Hook error (non-blocking): {str(e)}"
        }), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_prompt_submit.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git add hooks/prompt_submit.py tests/test_prompt_submit.py
git commit -m "feat: add SQLite-backed prompt_submit hook with migration and auto-tagging"
```

---

### Task 4: Create `hooks/post_compact.py` and `hooks/session_end.py`

The two new hooks: PostCompact nudge and SessionEnd finalization.

**Files:**
- Create: `hooks/post_compact.py`
- Create: `hooks/session_end.py`
- Create: `tests/test_post_compact.py`
- Create: `tests/test_session_end.py`

- [ ] **Step 1: Write failing tests for post_compact**

```python
# tests/test_post_compact.py
#!/usr/bin/env python3
"""Tests for the PostCompact hook."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from post_compact import build_nudge_message, run_hook
from db import get_connection, insert_session, insert_exchanges


class TestBuildNudgeMessage(unittest.TestCase):

    def test_formats_nudge_with_previews(self):
        msg = build_nudge_message(
            session_exchange_count=47,
            project_exchange_count=82,
            recent_previews=['checking the MSL output...', 'occupancy after fix...'],
            tags=['shared-memory', 'warp-divergence'],
        )
        self.assertIn('47', msg)
        self.assertIn('82', msg)
        self.assertIn('shared-memory', msg)
        self.assertIn('/recall', msg)
        self.assertLessEqual(len(msg), 600)  # allow some margin over 500

    def test_handles_empty_tags(self):
        msg = build_nudge_message(10, 10, ['preview'], [])
        self.assertIn('10', msg)
        self.assertNotIn('Recent topics', msg)

    def test_handles_empty_previews(self):
        msg = build_nudge_message(0, 0, [], [])
        self.assertIn('/recall', msg)


class TestRunHook(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-1', '/proj/test', 'hash-t', '2026-01-05T09:00:00Z')
        insert_exchanges(self.conn, 'sess-1', [
            {'idx': i, 'timestamp': f'2026-01-05T09:{i:02d}:00Z',
             'preview': f'Exchange {i} preview', 'user_text': f'Q{i}', 'assistant_text': f'A{i}'}
            for i in range(1, 11)
        ])
        from db import update_session_offset
        update_session_offset(self.conn, 'sess-1', 0, 10)
        self.conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_system_message(self):
        result = run_hook({'session_id': 'sess-1'}, db_path=self.db_path)
        self.assertIn('systemMessage', result)
        self.assertIn('10', result['systemMessage'])
        self.assertIn('/recall', result['systemMessage'])

    def test_unknown_session_returns_empty(self):
        result = run_hook({'session_id': 'nonexistent'}, db_path=self.db_path)
        self.assertEqual(result, {})


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Write failing tests for session_end**

```python
# tests/test_session_end.py
#!/usr/bin/env python3
"""Tests for the SessionEnd hook."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from session_end import run_hook
from db import get_connection, insert_session, get_session


class TestRunHook(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        conn = get_connection(self.db_path)
        insert_session(conn, 'sess-1', '/proj', 'hash', '2026-01-05T09:00:00Z')
        conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_marks_session_ended(self):
        result = run_hook({'session_id': 'sess-1'}, db_path=self.db_path)
        self.assertEqual(result, {})
        conn = get_connection(self.db_path)
        session = get_session(conn, 'sess-1')
        self.assertIsNotNone(session['ended_at'])
        conn.close()

    def test_unknown_session_noop(self):
        result = run_hook({'session_id': 'nonexistent'}, db_path=self.db_path)
        self.assertEqual(result, {})


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 3: Run both to confirm failure**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_post_compact.py tests/test_session_end.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Implement `hooks/post_compact.py`**

```python
# hooks/post_compact.py
#!/usr/bin/env python3
"""PostCompact hook — nudge Claude after context compaction.

Injects a system message with session stats, recent previews, and tags.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from db import get_connection, get_session, get_exchanges, DB_PATH

NUDGE_PREVIEW_COUNT = 5
NUDGE_MAX_CHARS = 500


def build_nudge_message(
    session_exchange_count: int,
    project_exchange_count: int,
    recent_previews: List[str],
    tags: List[str],
) -> str:
    """Build the nudge system message."""
    lines = [f"[Context Compacted] This session has {session_exchange_count} exchanges indexed."]
    lines.append(f"{project_exchange_count} total exchanges across this project's history.")

    if tags:
        lines.append(f"Recent topics: {', '.join(tags)}")

    if recent_previews:
        lines.append("Last exchanges:")
        for preview in recent_previews:
            lines.append(f'  - "{preview}"')

    lines.append("Use /recall to recover full conversation context.")
    return '\n'.join(lines)


def run_hook(input_data: Dict, db_path: Path = None) -> Dict:
    """Core hook logic."""
    session_id = input_data.get('session_id', '')
    if not session_id:
        return {}

    conn = get_connection(db_path)
    session = get_session(conn, session_id)
    if not session:
        conn.close()
        return {}

    session_count = session['exchange_count'] or 0

    # Get project total
    project_hash = session['project_hash']
    row = conn.execute(
        "SELECT SUM(exchange_count) FROM sessions WHERE project_hash = ?",
        (project_hash,)
    ).fetchone()
    project_count = row[0] or 0 if row else 0

    # Last N previews
    exchanges = get_exchanges(conn, session_id, last_n=NUDGE_PREVIEW_COUNT)
    previews = [ex['preview'] for ex in exchanges]

    # Top tags
    tag_rows = conn.execute(
        "SELECT tag FROM tags WHERE session_id = ? AND source = 'auto' ORDER BY created_at DESC LIMIT 5",
        (session_id,)
    ).fetchall()
    tags = [r['tag'] for r in tag_rows]

    conn.close()

    msg = build_nudge_message(session_count, project_count, previews, tags)
    return {"systemMessage": msg}


def main():
    try:
        input_data = json.load(sys.stdin)
        result = run_hook(input_data)
        print(json.dumps(result), file=sys.stdout)
    except Exception as e:
        print(json.dumps({
            "systemMessage": f"[context-recall] PostCompact error: {str(e)}"
        }), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
```

- [ ] **Step 5: Implement `hooks/session_end.py`**

```python
# hooks/session_end.py
#!/usr/bin/env python3
"""SessionEnd hook — marks session as ended."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from db import get_connection, get_session, end_session, DB_PATH


def run_hook(input_data: Dict, db_path: Path = None) -> Dict:
    """Core hook logic."""
    session_id = input_data.get('session_id', '')
    if not session_id:
        return {}

    conn = get_connection(db_path)
    session = get_session(conn, session_id)
    if session:
        now = datetime.now(timezone.utc).isoformat()
        end_session(conn, session_id, now)
    conn.close()
    return {}


def main():
    try:
        input_data = json.load(sys.stdin)
        result = run_hook(input_data)
        print(json.dumps(result), file=sys.stdout)
    except Exception:
        print(json.dumps({}), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
```

- [ ] **Step 6: Run both test files**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_post_compact.py tests/test_session_end.py -v`
Expected: All 7 tests PASS

- [ ] **Step 7: Commit**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git add hooks/post_compact.py hooks/session_end.py tests/test_post_compact.py tests/test_session_end.py
git commit -m "feat: add PostCompact nudge and SessionEnd hooks"
```

---

### Task 5: Create `scripts/manage_tags.py` and `scripts/manage_sessions.py`

The new management scripts for session listing, pruning, export, stats, and tagging commands.

**Files:**
- Create: `scripts/manage_tags.py`
- Create: `scripts/manage_sessions.py`
- Create: `tests/test_manage_tags.py`
- Create: `tests/test_manage_sessions.py`

- [ ] **Step 1: Write failing tests for manage_tags**

```python
# tests/test_manage_tags.py
#!/usr/bin/env python3
"""Tests for the tag management script."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from manage_tags import add_tag, list_tags, search_by_tag
from db import get_connection, insert_session, insert_exchanges


class TestAddTag(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-1', '/proj', 'hash', '2026-01-05T09:00:00Z')

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_add_session_tag(self):
        add_tag(self.conn, 'kernel-opt', 'sess-1')
        tags = list_tags(self.conn, project_hash='hash')
        self.assertIn('kernel-opt', [t['tag'] for t in tags])

    def test_add_exchange_tag(self):
        insert_exchanges(self.conn, 'sess-1', [
            {'idx': 1, 'timestamp': '2026-01-05T09:00:00Z', 'preview': 'test',
             'user_text': 'Q', 'assistant_text': 'A'}
        ])
        add_tag(self.conn, 'important', 'sess-1', exchange_idx=1)
        tags = list_tags(self.conn, project_hash='hash')
        tag = next(t for t in tags if t['tag'] == 'important')
        self.assertEqual(tag['exchange_idx'], 1)

    def test_duplicate_tag_ignored(self):
        add_tag(self.conn, 'kernel-opt', 'sess-1')
        add_tag(self.conn, 'kernel-opt', 'sess-1')  # Should not error
        tags = list_tags(self.conn, project_hash='hash')
        count = sum(1 for t in tags if t['tag'] == 'kernel-opt')
        self.assertEqual(count, 1)


class TestSearchByTag(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-1', '/proj/a', 'hash-a', '2026-01-05T09:00:00Z')
        insert_session(self.conn, 'sess-2', '/proj/b', 'hash-b', '2026-01-06T09:00:00Z')
        insert_exchanges(self.conn, 'sess-1', [
            {'idx': 1, 'timestamp': '2026-01-05T09:00:00Z', 'preview': 'warp',
             'user_text': 'warp divergence', 'assistant_text': 'fix it'}
        ])
        insert_exchanges(self.conn, 'sess-2', [
            {'idx': 1, 'timestamp': '2026-01-06T09:00:00Z', 'preview': 'cuda',
             'user_text': 'cuda kernel', 'assistant_text': 'optimize it'}
        ])
        add_tag(self.conn, 'kernel-opt', 'sess-1')
        add_tag(self.conn, 'kernel-opt', 'sess-2')

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_search_finds_tagged_sessions(self):
        results = search_by_tag(self.conn, 'kernel-opt')
        self.assertEqual(len(results), 2)

    def test_search_no_results(self):
        results = search_by_tag(self.conn, 'nonexistent')
        self.assertEqual(len(results), 0)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Write failing tests for manage_sessions**

```python
# tests/test_manage_sessions.py
#!/usr/bin/env python3
"""Tests for the session management script."""

import json
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from manage_sessions import format_session_list, format_stats, format_export
from db import (
    get_connection, insert_session, insert_exchanges,
    update_session_offset, list_sessions, get_stats, export_session_json,
)


class TestFormatSessionList(unittest.TestCase):

    def test_formats_sessions(self):
        sessions = [
            {'session_id': 's1', 'project_path': '/proj/a', 'started_at': '2026-01-05T09:00:00Z',
             'ended_at': None, 'exchange_count': 15},
            {'session_id': 's2', 'project_path': '/proj/a', 'started_at': '2026-01-06T09:00:00Z',
             'ended_at': '2026-01-06T17:00:00Z', 'exchange_count': 30},
        ]
        output = format_session_list(sessions, tags_by_session={})
        self.assertIn('s1', output)
        self.assertIn('s2', output)
        self.assertIn('15', output)

    def test_empty_sessions(self):
        output = format_session_list([], {})
        self.assertIn('No sessions', output)


class TestFormatStats(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 's1', '/proj/a', 'hash-a', '2026-01-05T09:00:00Z')
        insert_exchanges(self.conn, 's1', [
            {'idx': 1, 'timestamp': '2026-01-05T09:00:00Z', 'preview': 'test',
             'user_text': 'Q', 'assistant_text': 'A'}
        ])
        update_session_offset(self.conn, 's1', 0, 1)

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_formats_stats(self):
        stats = get_stats(self.conn, self.db_path)
        output = format_stats(stats)
        self.assertIn('1', output)  # 1 session
        self.assertIn('/proj/a', output)


class TestFormatExport(unittest.TestCase):

    def test_formats_json_export(self):
        data = {
            'session_id': 's1', 'project_path': '/proj',
            'exchanges': [{'idx': 1, 'user_text': 'Q', 'assistant_text': 'A'}],
            'tags': [],
        }
        output = format_export(data)
        parsed = json.loads(output)
        self.assertEqual(parsed['session_id'], 's1')


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 3: Implement `scripts/manage_tags.py`**

```python
# scripts/manage_tags.py
#!/usr/bin/env python3
"""Tag management for the recall plugin.

Handles: tag, untag, list tags, search by tag.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from db import get_connection, DB_PATH


def add_tag(conn, tag: str, session_id: str, exchange_idx: int = None) -> None:
    """Add a manual tag to a session or exchange."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO tags (tag, session_id, exchange_idx, source, created_at)
           VALUES (?, ?, ?, 'manual', ?)""",
        (tag, session_id, exchange_idx, now)
    )
    conn.commit()


def list_tags(conn, project_hash: str = None) -> List[Dict]:
    """List all tags, optionally filtered by project."""
    if project_hash:
        rows = conn.execute(
            """SELECT t.* FROM tags t
               JOIN sessions s ON t.session_id = s.session_id
               WHERE s.project_hash = ?
               ORDER BY t.tag""",
            (project_hash,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tags ORDER BY tag").fetchall()
    return [dict(r) for r in rows]


def search_by_tag(conn, tag: str) -> List[Dict]:
    """Find sessions/exchanges with a specific tag."""
    rows = conn.execute(
        """SELECT t.*, s.project_path, s.started_at as session_started
           FROM tags t
           JOIN sessions s ON t.session_id = s.session_id
           WHERE t.tag = ?
           ORDER BY t.created_at DESC""",
        (tag,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_tags_by_session(conn, session_ids: List[str]) -> Dict[str, List[str]]:
    """Get top tags for multiple sessions (for display alongside session list)."""
    result = {}
    for sid in session_ids:
        rows = conn.execute(
            "SELECT DISTINCT tag FROM tags WHERE session_id = ? LIMIT 5",
            (sid,)
        ).fetchall()
        result[sid] = [r['tag'] for r in rows]
    return result


def format_tag_list(tags: List[Dict]) -> str:
    """Format tags for display."""
    if not tags:
        return "*No tags found.*"

    # Group by tag name
    by_tag = {}
    for t in tags:
        name = t['tag']
        if name not in by_tag:
            by_tag[name] = {'count': 0, 'source': t['source']}
        by_tag[name]['count'] += 1

    lines = ["**Tags:**\n"]
    for tag_name, info in sorted(by_tag.items()):
        source = "auto" if info['source'] == 'auto' else "manual"
        lines.append(f"- **{tag_name}** ({info['count']} uses, {source})")
    return '\n'.join(lines)


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: manage_tags.py <add|list|search> [args]")
        return

    conn = get_connection()
    cmd = args[0].lower()

    if cmd == 'add' and len(args) >= 3:
        tag_name = args[1]
        session_id = args[2]
        exchange_idx = int(args[3]) if len(args) > 3 else None
        add_tag(conn, tag_name, session_id, exchange_idx)
        print(f"*Tagged session with '{tag_name}'*")

    elif cmd == 'list':
        project_hash = args[1] if len(args) > 1 else None
        tags = list_tags(conn, project_hash)
        print(format_tag_list(tags))

    elif cmd == 'search' and len(args) >= 2:
        results = search_by_tag(conn, args[1])
        if results:
            print(f"**Sessions tagged '{args[1]}':** ({len(results)} matches)\n")
            for r in results:
                print(f"- {r['project_path']} — Session {r['session_id'][:8]}... ({r['session_started']})")
        else:
            print(f"*No sessions found with tag '{args[1]}'*")

    else:
        print("Usage: manage_tags.py <add|list|search> [args]")

    conn.close()


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Implement `scripts/manage_sessions.py`**

```python
# scripts/manage_sessions.py
#!/usr/bin/env python3
"""Session management for the recall plugin.

Handles: list sessions, prune, export, stats.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent))

from db import (
    get_connection, list_sessions, prune_session, prune_before_date,
    get_stats, export_session_json, get_exchanges, DB_PATH,
)
from manage_tags import get_tags_by_session
from utils import format_date, format_short_date


def format_session_list(sessions: List[Dict], tags_by_session: Dict[str, List[str]]) -> str:
    """Format session list for display."""
    if not sessions:
        return "*No sessions found.*"

    lines = [f"**Sessions:** ({len(sessions)} total)\n"]
    for s in sessions:
        sid = s['session_id'][:8] + '...'
        started = format_date(s.get('started_at', ''))
        count = s.get('exchange_count', 0)
        status = "ended" if s.get('ended_at') else "active"
        tags = tags_by_session.get(s['session_id'], [])
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        project = s.get('project_path', 'unknown').split('/')[-1]
        lines.append(f"- **{sid}** ({project}) — {started} — {count} exchanges ({status}){tag_str}")
    return '\n'.join(lines)


def format_stats(stats: Dict) -> str:
    """Format storage statistics for display."""
    lines = ["**Recall Storage Stats:**\n"]
    lines.append(f"- Total sessions: {stats['total_sessions']}")
    lines.append(f"- Total exchanges: {stats['total_exchanges']}")
    lines.append(f"- Total tags: {stats['total_tags']}")

    db_kb = stats['db_size_bytes'] / 1024
    if db_kb > 1024:
        lines.append(f"- Database size: {db_kb / 1024:.1f} MB")
    else:
        lines.append(f"- Database size: {db_kb:.1f} KB")

    if stats['projects']:
        lines.append("\n**By project:**")
        for p in stats['projects']:
            lines.append(f"- {p['project_path']} — {p['session_count']} sessions, {p['exchange_total'] or 0} exchanges")

    if db_kb > 10240:  # > 10 MB
        lines.append("\n*Storage is getting large. Consider `/recall prune --before DATE` to clean up old sessions.*")

    return '\n'.join(lines)


def format_export(data: Dict) -> str:
    """Format session export as JSON string."""
    return json.dumps(data, indent=2)


def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: manage_sessions.py <list|prune|export|stats> [args]")
        return

    conn = get_connection()
    cmd = args[0].lower()

    if cmd == 'list':
        if '--all' in args:
            sessions = list_sessions(conn)
        elif '--project' in args:
            idx = args.index('--project')
            if idx + 1 < len(args):
                sessions = list_sessions(conn, project_path_contains=args[idx + 1])
            else:
                print("*Missing project name after --project*")
                conn.close()
                return
        else:
            # Default: current project (passed as arg)
            project_hash = args[1] if len(args) > 1 and not args[1].startswith('-') else None
            sessions = list_sessions(conn, project_hash=project_hash)

        session_ids = [s['session_id'] for s in sessions]
        tags = get_tags_by_session(conn, session_ids)
        print(format_session_list(sessions, tags))

    elif cmd == 'prune':
        if '--session' in args:
            idx = args.index('--session')
            if idx + 1 < len(args):
                sid = args[idx + 1]
                prune_session(conn, sid)
                print(f"*Pruned session {sid[:8]}...*")
            else:
                print("*Missing session ID after --session*")
        elif '--before' in args:
            idx = args.index('--before')
            if idx + 1 < len(args):
                count = prune_before_date(conn, args[idx + 1])
                print(f"*Pruned {count} session(s) before {args[idx + 1]}*")
            else:
                print("*Missing date after --before*")
        else:
            print("Usage: manage_sessions.py prune --session <id> | --before <date>")

    elif cmd == 'export':
        if '--session' in args:
            idx = args.index('--session')
            if idx + 1 < len(args):
                data = export_session_json(conn, args[idx + 1])
                if data:
                    print(format_export(data))
                else:
                    print(f"*Session not found: {args[idx + 1]}*")
            else:
                print("*Missing session ID after --session*")
        else:
            print("Usage: manage_sessions.py export --session <id>")

    elif cmd == 'stats':
        stats = get_stats(conn)
        print(format_stats(stats))

    else:
        print("Usage: manage_sessions.py <list|prune|export|stats> [args]")

    conn.close()


if __name__ == '__main__':
    main()
```

- [ ] **Step 5: Run both test files**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_manage_tags.py tests/test_manage_sessions.py -v`
Expected: All 11 tests PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git add scripts/manage_tags.py scripts/manage_sessions.py tests/test_manage_tags.py tests/test_manage_sessions.py
git commit -m "feat: add session management and tag management scripts"
```

---

### Task 6: Update `scripts/fetch_exchanges.py` and `scripts/show_index.py` for DB

Port the existing query scripts from JSON to SQLite, add --all/--global/--project/--tag flags.

**Files:**
- Modify: `scripts/fetch_exchanges.py`
- Modify: `scripts/show_index.py`
- Modify: `tests/test_fetch_exchanges.py`
- Modify: `tests/test_show_index.py`

- [ ] **Step 1: Rewrite `scripts/fetch_exchanges.py` to use DB**

Replace the entire file. The key changes: import from `db` instead of loading JSON, add `--all`, `--global`, `--project`, `--tag` argument parsing.

The new `fetch_exchanges.py` should:
- Import `get_connection`, `get_exchanges`, `search_exchanges_fts`, `search_exchanges_global`, `get_session` from `db`
- Keep the same `format_exchanges()` function (it operates on dicts, same shape)
- Keep `parse_last_n()` — now calls `get_exchanges(conn, session_id, last_n=n)`
- For `search` — call `search_exchanges_fts()` with appropriate scope
- For `around` — use `find_exchanges_by_time()` from utils on DB-fetched exchanges
- Parse `--all`, `--global`, `--project <name>`, `--tag <name>` from args
- Accept session_id as env var `RECALL_SESSION_ID` or first non-flag argument

The full implementation follows the existing structure but swaps the data source. Write this as a complete file replacement.

- [ ] **Step 2: Rewrite `scripts/show_index.py` to use DB**

Same approach — swap `load_index()` for DB queries. Keep all formatting functions. Add `--session <id>` flag for browsing past sessions.

- [ ] **Step 3: Update `tests/test_fetch_exchanges.py` to use DB fixtures**

Replace `load_index` mocks with real DB inserts via `get_connection()` in `setUp()`. Assert same output behavior.

- [ ] **Step 4: Update `tests/test_show_index.py` to use DB fixtures**

Same approach as fetch_exchanges tests.

- [ ] **Step 5: Run updated tests**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_fetch_exchanges.py tests/test_show_index.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git add scripts/fetch_exchanges.py scripts/show_index.py tests/test_fetch_exchanges.py tests/test_show_index.py
git commit -m "refactor: port fetch_exchanges and show_index from JSON to SQLite"
```

---

### Task 7: Update `scripts/utils.py` — Remove JSON I/O

Slim down utils.py by removing `load_index()`, `save_index()`, and the `INDEX_FILE`/`INDEX_DIR` constants that are now in `db.py`.

**Files:**
- Modify: `scripts/utils.py`
- Modify: `tests/test_utils.py`

- [ ] **Step 1: Remove `load_index`, `save_index`, `INDEX_FILE`, `INDEX_DIR` from utils.py**

Keep `LOG_FILE` (still used by hooks). Remove `parse_transcript_messages` and `build_exchanges_from_messages` (now in `prompt_submit.py` and `db.py`).

- [ ] **Step 2: Update `tests/test_utils.py` — remove tests for deleted functions**

Remove `TestBuildExchangesFromMessages` class. Remove imports of deleted symbols. All remaining tests should still pass since the formatting/parsing functions are unchanged.

- [ ] **Step 3: Run utils tests**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/test_utils.py -v`
Expected: All remaining tests PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git add scripts/utils.py tests/test_utils.py
git commit -m "refactor: slim utils.py — remove JSON I/O, move to db.py"
```

---

### Task 8: Update `hooks/hooks.json`, `commands/recall.md`, and `.claude-plugin/plugin.json`

Wire everything together — update hook config, extend the command definition, bump version.

**Files:**
- Modify: `hooks/hooks.json`
- Modify: `commands/recall.md`
- Modify: `.claude-plugin/plugin.json`

- [ ] **Step 1: Update `hooks/hooks.json`**

Replace with the new three-hook config:

```json
{
  "description": "Recall hooks - indexes conversations, nudges after compaction, finalizes sessions",
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/prompt_submit.py",
            "timeout": 10
          }
        ]
      }
    ],
    "PostCompact": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/post_compact.py",
            "timeout": 5
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/session_end.py",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Update `commands/recall.md`**

Extend the command to handle all new subcommands. Add `sessions`, `session`, `tag`, `tags`, `stats`, `prune`, `export` routing alongside existing `last`, `around`, `search`. Pass `$ARGUMENTS` through to the appropriate script.

Key additions to the "Check for Quick Commands" section:
- `sessions` → `manage_sessions.py list`
- `sessions --all` → `manage_sessions.py list --all`
- `session <id> last10` → `fetch_exchanges.py --session <id> last10`
- `tag <name>` → `manage_tags.py add <name> $SESSION_ID`
- `tags` → `manage_tags.py list`
- `stats` → `manage_sessions.py stats`
- `prune --session <id>` → `manage_sessions.py prune --session <id>`
- `prune --before <date>` → `manage_sessions.py prune --before <date>`
- `export --session <id> --json` → `manage_sessions.py export --session <id>`
- `search <keyword> --all` → `fetch_exchanges.py search <keyword> --all`
- `search <keyword> --global` → `fetch_exchanges.py search <keyword> --global`
- `search <keyword> --project <name>` → `fetch_exchanges.py search <keyword> --project <name>`
- `search --tag <name>` → `manage_tags.py search <name>`

- [ ] **Step 3: Update `.claude-plugin/plugin.json`**

```json
{
  "name": "recall",
  "version": "2.0.0",
  "description": "Cross-session, cross-project conversation recall with SQLite storage, auto-tagging, and compaction recovery",
  "author": {
    "name": "bledden"
  }
}
```

- [ ] **Step 4: Commit**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git add hooks/hooks.json commands/recall.md .claude-plugin/plugin.json
git commit -m "feat: wire up v2.0.0 — new hooks, extended commands, version bump"
```

---

### Task 9: Clean Up Legacy Files and Write Integration Tests

Remove deprecated files and write end-to-end integration tests covering the full v2 lifecycle.

**Files:**
- Remove: `hooks/save_context_snapshot.py`
- Remove: `scripts/extract_context.py`
- Remove: `tests/test_save_context_snapshot.py`
- Remove: `tests/test_extract_context.py`
- Modify: `tests/integration_test.py`

- [ ] **Step 1: Delete legacy files**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git rm hooks/save_context_snapshot.py scripts/extract_context.py tests/test_save_context_snapshot.py tests/test_extract_context.py
```

- [ ] **Step 2: Rewrite `tests/integration_test.py`**

```python
# tests/integration_test.py
#!/usr/bin/env python3
"""Integration tests for the recall plugin v2.0.0.

Tests the full lifecycle: session creation, exchanges, /clear survival,
cross-session search, migration, auto-tagging, and PostCompact nudge.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from prompt_submit import run_hook as prompt_hook, build_new_exchanges
from post_compact import run_hook as compact_hook
from session_end import run_hook as end_hook
from db import (
    get_connection, get_session, get_exchanges,
    search_exchanges_fts, search_exchanges_global, get_stats,
)
from manage_tags import add_tag, search_by_tag
from auto_tagger import compute_auto_tags


class TestFullLifecycle(unittest.TestCase):
    """End-to-end test: create session, add exchanges, /clear, cross-session search."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / '.claude' / 'context-recall' / 'recall.db'
        self.transcript = Path(self.temp_dir) / 'transcript.jsonl'

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_transcript(self, pairs):
        with open(self.transcript, 'w') as f:
            for user, asst, ts in pairs:
                f.write(json.dumps({
                    'type': 'user',
                    'message': {'content': [{'type': 'text', 'text': user}]},
                    'timestamp': ts
                }) + '\n')
                f.write(json.dumps({
                    'type': 'assistant',
                    'message': {'content': [{'type': 'text', 'text': asst}]},
                    'timestamp': ts
                }) + '\n')

    def test_full_lifecycle(self):
        # Session 1 — kernel work
        self._write_transcript([
            ('warp divergence in reduction', 'use shared memory', '2026-01-05T09:00:00Z'),
            ('occupancy is low', 'reduce register pressure', '2026-01-05T09:01:00Z'),
        ])
        prompt_hook({
            'session_id': 'sess-1', 'transcript_path': str(self.transcript),
            'user_prompt': 'hello', 'project_path': '/proj/kernels', 'project_hash': 'hash-k',
        }, db_path=self.db_path)

        conn = get_connection(self.db_path)
        self.assertEqual(get_session(conn, 'sess-1')['exchange_count'], 2)

        # /clear — new session, same project
        self._write_transcript([
            ('flash attention tiling', 'use 16x16 tiles', '2026-01-06T09:00:00Z'),
        ])
        prompt_hook({
            'session_id': 'sess-2', 'transcript_path': str(self.transcript),
            'user_prompt': 'hello', 'project_path': '/proj/kernels', 'project_hash': 'hash-k',
        }, db_path=self.db_path)

        # Both sessions exist (/clear survival)
        self.assertIsNotNone(get_session(conn, 'sess-1'))
        self.assertIsNotNone(get_session(conn, 'sess-2'))

        # Cross-session search within project
        results = search_exchanges_fts(conn, 'reduction', project_hash='hash-k')
        self.assertGreaterEqual(len(results), 1)

        # Manual tag + cross-project search
        add_tag(conn, 'kernel-opt', 'sess-1')
        add_tag(conn, 'kernel-opt', 'sess-2')
        tagged = search_by_tag(conn, 'kernel-opt')
        self.assertEqual(len(tagged), 2)

        # PostCompact nudge
        result = compact_hook({'session_id': 'sess-1'}, db_path=self.db_path)
        self.assertIn('systemMessage', result)
        self.assertIn('2', result['systemMessage'])

        # SessionEnd
        end_hook({'session_id': 'sess-1'}, db_path=self.db_path)
        self.assertIsNotNone(get_session(conn, 'sess-1')['ended_at'])

        # Stats
        stats = get_stats(conn, self.db_path)
        self.assertEqual(stats['total_sessions'], 2)
        self.assertEqual(stats['total_exchanges'], 3)

        conn.close()


class TestMigrationFromV1(unittest.TestCase):
    """Test automatic migration from v1.0.1 index.json."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.recall_dir = Path(self.temp_dir) / '.claude' / 'context-recall'
        self.recall_dir.mkdir(parents=True)
        self.db_path = self.recall_dir / 'recall.db'
        self.transcript = Path(self.temp_dir) / 'transcript.jsonl'

        # Write a v1 index.json
        v1_index = {
            'session_id': 'legacy-sess',
            'session_start': '2026-01-01T10:00:00Z',
            'updated_at': '2026-01-01T12:00:00Z',
            'total_exchanges': 2,
            'transcript_path': str(self.transcript),
            '_byte_offset': 500,
            'exchanges': [
                {'idx': 1, 'preview': 'First exchange', 'timestamp': '2026-01-01T10:00:00Z',
                 'user_text': 'Hello from v1', 'assistant_text': 'Hi from v1 assistant'},
                {'idx': 2, 'preview': 'Second exchange', 'timestamp': '2026-01-01T11:00:00Z',
                 'user_text': 'Question from v1', 'assistant_text': 'Answer from v1'},
            ]
        }
        with open(self.recall_dir / 'index.json', 'w') as f:
            json.dump(v1_index, f)

        # Write a minimal transcript for the new session
        with open(self.transcript, 'w') as f:
            f.write(json.dumps({
                'type': 'user',
                'message': {'content': [{'type': 'text', 'text': 'New prompt'}]},
                'timestamp': '2026-01-02T09:00:00Z'
            }) + '\n')
            f.write(json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': 'New response'}]},
                'timestamp': '2026-01-02T09:00:05Z'
            }) + '\n')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_migration_preserves_data(self):
        # Trigger migration by running prompt_submit with a new session
        prompt_hook({
            'session_id': 'new-sess',
            'transcript_path': str(self.transcript),
            'user_prompt': 'test',
            'project_path': '/proj',
            'project_hash': 'hash',
        }, db_path=self.db_path)

        conn = get_connection(self.db_path)

        # Legacy session was migrated
        legacy = get_session(conn, 'legacy-sess')
        self.assertIsNotNone(legacy)
        legacy_exchanges = get_exchanges(conn, 'legacy-sess')
        self.assertEqual(len(legacy_exchanges), 2)
        self.assertEqual(legacy_exchanges[0]['user_text'], 'Hello from v1')

        # New session also exists
        self.assertIsNotNone(get_session(conn, 'new-sess'))

        # index.json was renamed
        self.assertFalse((self.recall_dir / 'index.json').exists())
        self.assertTrue((self.recall_dir / 'index.json.migrated').exists())

        # Cross-session search finds both
        results = search_exchanges_global(conn, 'from v1')
        self.assertGreaterEqual(len(results), 1)

        conn.close()


class TestCrossProjectSearch(unittest.TestCase):
    """Test searching across different projects."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.transcript = Path(self.temp_dir) / 'transcript.jsonl'
        with open(self.transcript, 'w') as f:
            f.write(json.dumps({
                'type': 'user',
                'message': {'content': [{'type': 'text', 'text': 'reduction kernel optimization'}]},
                'timestamp': '2026-01-05T09:00:00Z'
            }) + '\n')
            f.write(json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': 'use warp shuffle for reduction'}]},
                'timestamp': '2026-01-05T09:00:05Z'
            }) + '\n')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_global_search_across_projects(self):
        # Project A
        prompt_hook({
            'session_id': 'proj-a-s1', 'transcript_path': str(self.transcript),
            'user_prompt': 'test', 'project_path': '/proj/triton-metal', 'project_hash': 'hash-a',
        }, db_path=self.db_path)

        # Project B (same transcript for simplicity)
        prompt_hook({
            'session_id': 'proj-b-s1', 'transcript_path': str(self.transcript),
            'user_prompt': 'test', 'project_path': '/proj/cuda-kernels', 'project_hash': 'hash-b',
        }, db_path=self.db_path)

        conn = get_connection(self.db_path)
        results = search_exchanges_global(conn, 'reduction')
        self.assertGreaterEqual(len(results), 2)

        # Verify results come from different projects
        project_paths = {r['project_path'] for r in results}
        self.assertIn('/proj/triton-metal', project_paths)
        self.assertIn('/proj/cuda-kernels', project_paths)
        conn.close()


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 3: Run integration tests**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/integration_test.py -v`
Expected: All 3 tests PASS

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/bledden/Documents/claude-recall-plugin && python3 -m pytest tests/ -v`
Expected: All tests PASS (target: 80+ tests)

- [ ] **Step 5: Commit**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git rm hooks/save_context_snapshot.py scripts/extract_context.py tests/test_save_context_snapshot.py tests/test_extract_context.py
git add tests/integration_test.py
git commit -m "feat: v2.0.0 complete — remove legacy files, add integration tests"
```

---

### Task 10: Update README.md

Document all new features, commands, migration path, and updated installation instructions.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README with v2.0.0 changes**

Key sections to add/update:
- Version bump to 2.0.0 in header
- New features section: /clear survival, cross-session search, cross-project search, PostCompact nudge, auto-tagging, manual tagging
- Full command reference with all new subcommands
- Migration section: automatic from v1.0.1, rollback instructions
- Updated data storage section: SQLite instead of JSON
- Updated known limitations
- Changelog entry for v2.0.0

- [ ] **Step 2: Commit**

```bash
cd /Users/bledden/Documents/claude-recall-plugin
git add README.md
git commit -m "docs: update README for v2.0.0 — SQLite, cross-session, tagging, compaction"
```
