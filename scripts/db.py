#!/usr/bin/env python3
"""SQLite database layer for Claude Context Recall plugin v2.

Provides schema creation, WAL-mode connections, session/exchange CRUD,
FTS5 full-text search, and maintenance operations.

All functions accept a sqlite3.Connection and are safe for concurrent
reads via WAL mode with a 5-second busy timeout.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_DIR = Path.home() / '.claude' / 'context-recall'
DB_PATH = DB_DIR / 'recall.db'
DB_BUSY_TIMEOUT_MS = 5000

# Current on-disk schema version, tracked via SQLite's PRAGMA user_version.
# Bump this and add a branch in _apply_migrations() whenever the schema changes
# (e.g. the v3 vector/tier tables would be version 3).
SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# Schema SQL
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
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

-- IMPORTANT: The FTS5 table uses external content (no triggers).
-- All exchange inserts/deletes MUST go through insert_exchanges() / _delete_fts_rows().
-- Direct modifications to the exchanges table will corrupt the FTS index.
CREATE VIRTUAL TABLE IF NOT EXISTS exchanges_fts USING fts5(
    user_text, assistant_text, preview,
    content=exchanges, content_rowid=id
);

-- Note: exchanges(session_id, idx) is already covered by the UNIQUE constraint.
CREATE INDEX IF NOT EXISTS idx_tags_session ON tags(session_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_hash);

CREATE TABLE IF NOT EXISTS highlights (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    summary         TEXT NOT NULL,
    exchange_idx    INTEGER,
    tags            TEXT NOT NULL,
    source          TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(session_id, summary)
);

CREATE TABLE IF NOT EXISTS connections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    watcher_session TEXT NOT NULL REFERENCES sessions(session_id),
    target_session  TEXT NOT NULL REFERENCES sessions(session_id),
    topic           TEXT NOT NULL,
    check_mode      TEXT NOT NULL DEFAULT 'explicit',
    check_counter   INTEGER DEFAULT 0,
    check_interval  INTEGER DEFAULT 7,
    last_checked_at TEXT,
    delivery_mode   TEXT NOT NULL DEFAULT 'silent',
    created_at      TEXT NOT NULL,
    UNIQUE(watcher_session, target_session)
);

CREATE INDEX IF NOT EXISTS idx_highlights_session ON highlights(session_id);
CREATE INDEX IF NOT EXISTS idx_highlights_created ON highlights(created_at);
CREATE INDEX IF NOT EXISTS idx_connections_watcher ON connections(watcher_session);
CREATE INDEX IF NOT EXISTS idx_connections_target ON connections(target_session);
"""

# ---------------------------------------------------------------------------
# Connection & schema
# ---------------------------------------------------------------------------

def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Return a WAL-mode SQLite connection with row_factory=sqlite3.Row.

    Creates the database directory and schema if they do not exist.

    Args:
        db_path: Override path for the database file.  Defaults to DB_PATH.

    Returns:
        sqlite3.Connection configured with WAL mode and busy timeout.
    """
    if db_path is None:
        db_path = str(DB_PATH)
    else:
        db_path = str(db_path)

    # Ensure parent directory exists
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, mode=0o700, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout={}".format(DB_BUSY_TIMEOUT_MS))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")

    # Only run schema DDL if tables don't exist yet (avoids parsing 15 DDL
    # statements on every connection — saves ~2ms per prompt)
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sessions'"
    ).fetchone()
    if row is None:
        conn.executescript(_SCHEMA_SQL)

    _apply_migrations(conn)

    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Bring the database up to SCHEMA_VERSION, stamping PRAGMA user_version.

    A single PRAGMA read on each connection (cheap); only the first connection
    to an un-versioned or out-of-date store performs a write. Future schema
    changes add a ``if current < N: _migrate_to_vN(conn)`` branch here. The
    current (v2) schema is created in full by ``_SCHEMA_SQL``, so bootstrapping
    a fresh or pre-versioning DB only needs to stamp the version.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return
    # (future) per-version migrations would run here, e.g.:
    #   if current < 3:
    #       conn.executescript(_MIGRATION_V3_VECTORS)
    conn.execute("PRAGMA user_version = {}".format(SCHEMA_VERSION))
    conn.commit()

# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def insert_session(conn: sqlite3.Connection, session_id: str, project_path: str,
                   project_hash: str, started_at: str,
                   transcript_path: Optional[str] = None,
                   commit: bool = True) -> None:
    """Insert a new session (INSERT OR IGNORE)."""
    conn.execute(
        "INSERT OR IGNORE INTO sessions "
        "(session_id, project_path, project_hash, started_at, transcript_path) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, project_path, project_hash, started_at, transcript_path),
    )
    if commit:
        conn.commit()


def get_session(conn: sqlite3.Connection, session_id: str) -> Optional[Dict]:
    """Return a session as a dict, or None if not found."""
    cur = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


def list_sessions(conn: sqlite3.Connection, project_hash: Optional[str] = None,
                  project_path_contains: Optional[str] = None) -> List[Dict]:
    """List sessions, optionally filtered, ordered by started_at DESC."""
    sql = "SELECT * FROM sessions WHERE 1=1"
    params = []
    if project_hash is not None:
        sql += " AND project_hash = ?"
        params.append(project_hash)
    if project_path_contains is not None:
        escaped = (project_path_contains
                   .replace('\\', '\\\\')
                   .replace('%', '\\%')
                   .replace('_', '\\_'))
        sql += " AND project_path LIKE ? ESCAPE '\\'"
        params.append('%' + escaped + '%')
    sql += " ORDER BY started_at DESC"
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def end_session(conn: sqlite3.Connection, session_id: str, ended_at: str) -> None:
    """Mark a session as ended."""
    conn.execute(
        "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
        (ended_at, session_id),
    )
    conn.commit()


def update_session_offset(conn: sqlite3.Connection, session_id: str,
                          byte_offset: int, exchange_count: int,
                          commit: bool = True) -> None:
    """Update the incremental-read offset and exchange count for a session."""
    conn.execute(
        "UPDATE sessions SET byte_offset = ?, exchange_count = ? WHERE session_id = ?",
        (byte_offset, exchange_count, session_id),
    )
    if commit:
        conn.commit()

# ---------------------------------------------------------------------------
# Exchange CRUD
# ---------------------------------------------------------------------------

def insert_exchanges(conn: sqlite3.Connection, session_id: str,
                     exchanges: List[Dict[str, Any]],
                     commit: bool = True) -> None:
    """Insert a batch of exchanges and sync the FTS5 index.

    Args:
        conn: SQLite connection.
        session_id: Parent session ID.
        exchanges: List of dicts with keys: idx, timestamp, preview,
                   user_text, assistant_text.
        commit: If True (default), commit after insertion.
    """
    new_rowids = []
    for ex in exchanges:
        cur = conn.execute(
            "INSERT OR IGNORE INTO exchanges "
            "(session_id, idx, timestamp, preview, user_text, assistant_text) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                session_id,
                ex['idx'],
                ex['timestamp'],
                ex['preview'],
                ex.get('user_text'),
                ex.get('assistant_text'),
            ),
        )
        if cur.rowcount > 0:
            new_rowids.append(cur.lastrowid)

    # Add newly inserted rows to the FTS5 index within the same transaction
    if new_rowids:
        _insert_fts_rows(conn, new_rowids)

    if commit:
        conn.commit()


def _insert_fts_rows(conn: sqlite3.Connection, rowids: List[int]) -> None:
    """Insert specific exchange rows into the FTS5 index by rowid.

    Does NOT commit — the caller is responsible for committing the transaction.
    """
    placeholders = ','.join('?' for _ in rowids)
    rows = conn.execute(
        "SELECT id, user_text, assistant_text, preview FROM exchanges "
        "WHERE id IN ({})".format(placeholders),
        rowids,
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO exchanges_fts(rowid, user_text, assistant_text, preview) "
            "VALUES(?, ?, ?, ?)",
            (row['id'], row['user_text'], row['assistant_text'], row['preview']),
        )


def _delete_fts_rows(conn: sqlite3.Connection, session_id: str) -> None:
    """Delete FTS5 entries for all exchanges in a session.

    Must be called BEFORE deleting the exchanges from the content table,
    since the delete command needs the original column values to match.
    """
    rows = conn.execute(
        "SELECT id, user_text, assistant_text, preview FROM exchanges "
        "WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO exchanges_fts(exchanges_fts, rowid, user_text, assistant_text, preview) "
            "VALUES('delete', ?, ?, ?, ?)",
            (row['id'], row['user_text'], row['assistant_text'], row['preview']),
        )


def get_exchange_count(conn: sqlite3.Connection, session_id: str) -> int:
    """Return the number of exchanges for a session via COUNT(*)."""
    row = conn.execute(
        "SELECT COUNT(*) FROM exchanges WHERE session_id = ?", (session_id,)
    ).fetchone()
    return row[0] if row else 0


def get_exchanges(conn: sqlite3.Connection, session_id: str,
                  last_n: Optional[int] = None) -> List[Dict]:
    """Get exchanges for a session, ordered by idx.

    Args:
        conn: SQLite connection.
        session_id: Session to query.
        last_n: If set, return only the last N exchanges.

    Returns:
        List of dicts.
    """
    if last_n is not None and last_n > 0:
        cur = conn.execute(
            "SELECT * FROM exchanges WHERE session_id = ? "
            "ORDER BY idx DESC LIMIT ?",
            (session_id, last_n),
        )
        rows = [dict(r) for r in cur.fetchall()]
        rows.reverse()
        return rows
    else:
        cur = conn.execute(
            "SELECT * FROM exchanges WHERE session_id = ? ORDER BY idx",
            (session_id,),
        )
        return [dict(r) for r in cur.fetchall()]

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _build_fts_query(query: str) -> Optional[str]:
    """Build an FTS5 query from user input.

    Returns None if the query is empty after stripping (callers should
    short-circuit to an empty result list in that case).

    If the query is wrapped in double quotes, treat it as an exact phrase match.
    Otherwise, split into individual terms and AND them together so that
    "warp divergence" matches text containing both words in any order/position.
    """
    query = query.strip()
    if not query:
        return None

    if query.startswith('"') and query.endswith('"') and len(query) > 2:
        # User explicitly wants phrase match — pass through, escape internal quotes
        inner = query[1:-1].replace('"', '""')
        return '"' + inner + '"'

    # Split into terms, quote each individually, AND them together
    terms = query.split()
    if not terms:
        return None
    if len(terms) == 1:
        # Single term — quote it for safety
        return '"' + terms[0].replace('"', '""') + '"'

    # Multiple terms: "term1" AND "term2" AND ...
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    return ' AND '.join(quoted)


def search_exchanges_fts(conn: sqlite3.Connection, query: str,
                         session_id: Optional[str] = None,
                         project_hash: Optional[str] = None,
                         limit: int = 10) -> List[Dict]:
    """Full-text search over exchanges via FTS5.

    Args:
        conn: SQLite connection.
        query: Search query string.
        session_id: Optional — restrict to one session.
        project_hash: Optional — restrict to sessions with this project hash.
        limit: Max results.

    Returns:
        List of exchange dicts matching the query.
    """
    safe_query = _build_fts_query(query)
    if safe_query is None:
        return []

    sql = (
        "SELECT e.* FROM exchanges e "
        "JOIN exchanges_fts fts ON e.id = fts.rowid "
    )
    wheres = ["exchanges_fts MATCH ?"]
    params = [safe_query]

    if session_id is not None:
        wheres.append("e.session_id = ?")
        params.append(session_id)

    if project_hash is not None:
        wheres.append(
            "e.session_id IN (SELECT session_id FROM sessions WHERE project_hash = ?)"
        )
        params.append(project_hash)

    sql += " WHERE " + " AND ".join(wheres)
    sql += " LIMIT ?"
    params.append(limit)

    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def search_exchanges_global(conn: sqlite3.Connection, query: str,
                            limit: int = 20) -> List[Dict]:
    """Search across ALL sessions/projects, enriching results with session info.

    Returns:
        List of dicts — exchange fields plus project_path and session_started.
    """
    safe_query = _build_fts_query(query)
    if safe_query is None:
        return []

    sql = (
        "SELECT e.*, s.project_path, s.started_at AS session_started "
        "FROM exchanges e "
        "JOIN exchanges_fts fts ON e.id = fts.rowid "
        "JOIN sessions s ON e.session_id = s.session_id "
        "WHERE exchanges_fts MATCH ? "
        "LIMIT ?"
    )
    cur = conn.execute(sql, (safe_query, limit))
    return [dict(r) for r in cur.fetchall()]

# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def _prune_session_no_commit(conn: sqlite3.Connection, session_id: str) -> None:
    """Delete a session's data without committing. Caller owns the transaction."""
    _delete_fts_rows(conn, session_id)
    conn.execute("DELETE FROM tags WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM highlights WHERE session_id = ?", (session_id,))
    conn.execute(
        "DELETE FROM connections WHERE watcher_session = ? OR target_session = ?",
        (session_id, session_id)
    )
    conn.execute("DELETE FROM exchanges WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


def prune_session(conn: sqlite3.Connection, session_id: str) -> None:
    """Delete a session, its exchanges (including FTS entries), tags, highlights, and connections."""
    _prune_session_no_commit(conn, session_id)
    conn.commit()


def prune_before_date(conn: sqlite3.Connection, before_date: str) -> int:
    """Delete all sessions started before the given ISO date string. Atomic.

    Returns:
        Number of sessions deleted.
    """
    cur = conn.execute(
        "SELECT session_id FROM sessions WHERE started_at < ?", (before_date,)
    )
    session_ids = [row['session_id'] for row in cur.fetchall()]
    for sid in session_ids:
        _prune_session_no_commit(conn, sid)
    conn.commit()
    return len(session_ids)


def get_stats(conn: sqlite3.Connection, db_path: Optional[Path] = None) -> Dict:
    """Return summary statistics about the database.

    Args:
        conn: SQLite connection.
        db_path: Path to database file (for file size). Defaults to DB_PATH.

    Returns:
        Dict with total_sessions, total_exchanges, total_tags, db_size_bytes,
        and projects (list of unique project_path values).
    """
    if db_path is None:
        db_path = str(DB_PATH)

    total_sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_exchanges = conn.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0]
    total_tags = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]

    cur = conn.execute(
        "SELECT project_path, COUNT(*) AS session_count, SUM(exchange_count) AS exchange_total "
        "FROM sessions GROUP BY project_path ORDER BY project_path"
    )
    projects = [
        {
            'project_path': row['project_path'],
            'session_count': row['session_count'],
            'exchange_total': row['exchange_total'] or 0,
        }
        for row in cur.fetchall()
    ]

    try:
        db_size_bytes = os.path.getsize(db_path)
    except OSError:
        db_size_bytes = 0

    return {
        'total_sessions': total_sessions,
        'total_exchanges': total_exchanges,
        'total_tags': total_tags,
        'db_size_bytes': db_size_bytes,
        'projects': projects,
    }


def export_session_json(conn: sqlite3.Connection, session_id: str) -> Dict:
    """Export a full session (session row + exchanges + tags) as a flat dict.

    Returns:
        Flat dict with session fields spread at top level plus 'exchanges' and
        'tags' keys.  Session fields will be None-valued if session_id does not
        exist.
    """
    session = get_session(conn, session_id) or {}

    cur = conn.execute(
        "SELECT * FROM exchanges WHERE session_id = ? ORDER BY idx",
        (session_id,),
    )
    exchanges = [dict(r) for r in cur.fetchall()]

    cur = conn.execute(
        "SELECT * FROM tags WHERE session_id = ? ORDER BY id",
        (session_id,),
    )
    tags = [dict(r) for r in cur.fetchall()]

    return {**session, 'exchanges': exchanges, 'tags': tags}

# ---------------------------------------------------------------------------
# Tag CRUD
# ---------------------------------------------------------------------------

def insert_tag(conn: sqlite3.Connection, tag: str, session_id: str,
               exchange_idx: Optional[int] = None, source: str = 'manual',
               commit: bool = True) -> None:
    """Insert a tag. INSERT OR IGNORE for idempotency."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO tags (tag, session_id, exchange_idx, source, created_at) VALUES (?, ?, ?, ?, ?)",
        (tag, session_id, exchange_idx, source, now)
    )
    if commit:
        conn.commit()


def get_tags(conn: sqlite3.Connection, session_id: Optional[str] = None,
             project_hash: Optional[str] = None) -> List[Dict]:
    """Get tags, optionally filtered by session or project."""
    if session_id:
        rows = conn.execute(
            "SELECT * FROM tags WHERE session_id = ? ORDER BY tag",
            (session_id,)
        ).fetchall()
    elif project_hash:
        rows = conn.execute(
            "SELECT t.* FROM tags t JOIN sessions s ON t.session_id = s.session_id "
            "WHERE s.project_hash = ? ORDER BY t.tag",
            (project_hash,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tags ORDER BY tag").fetchall()
    return [dict(r) for r in rows]

# ---------------------------------------------------------------------------
# Highlight CRUD
# ---------------------------------------------------------------------------

def insert_highlight(conn: sqlite3.Connection, session_id: str, summary: str,
                     tags: str, source: str,
                     exchange_idx: Optional[int] = None,
                     commit: bool = True) -> None:
    """Insert a highlight. INSERT OR IGNORE for dedup."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO highlights "
        "(session_id, summary, exchange_idx, tags, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, summary, exchange_idx, tags, source, now),
    )
    if commit:
        conn.commit()


def get_highlights(conn: sqlite3.Connection, session_id: str,
                   since: Optional[str] = None,
                   limit: int = 20) -> List[Dict]:
    """Get highlights for a session, optionally filtered by created_at > since."""
    sql = "SELECT * FROM highlights WHERE session_id = ?"
    params: List[Any] = [session_id]
    if since is not None:
        sql += " AND created_at > ?"
        params.append(since)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def get_highlights_for_connections(conn: sqlite3.Connection,
                                   watcher_session: str) -> List[Dict]:
    """Get unchecked highlights across all connections for a watcher.

    For each connection, fetches highlights from the target session where
    created_at > connection.last_checked_at (or all if never checked).
    Returns highlights enriched with the connection's topic.
    """
    connections = get_connections(conn, watcher_session)
    results: List[Dict] = []
    for c in connections:
        sql = "SELECT * FROM highlights WHERE session_id = ?"
        params: List[Any] = [c['target_session']]
        if c['last_checked_at'] is not None:
            sql += " AND created_at > ?"
            params.append(c['last_checked_at'])
        sql += " ORDER BY created_at ASC"
        rows = conn.execute(sql, params).fetchall()
        for row in rows:
            enriched = dict(row)
            enriched['connection_topic'] = c['topic']
            results.append(enriched)
    return results

# ---------------------------------------------------------------------------
# Connection CRUD
# ---------------------------------------------------------------------------

def insert_connection(conn: sqlite3.Connection, watcher_session: str,
                      target_session: str, topic: str,
                      check_mode: str = 'explicit',
                      delivery_mode: str = 'silent') -> None:
    """Create a connection. INSERT OR IGNORE."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO connections "
        "(watcher_session, target_session, topic, check_mode, delivery_mode, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (watcher_session, target_session, topic, check_mode, delivery_mode, now),
    )
    conn.commit()


def get_connections(conn: sqlite3.Connection,
                    watcher_session: str) -> List[Dict]:
    """Get all connections for a watcher session."""
    cur = conn.execute(
        "SELECT * FROM connections WHERE watcher_session = ? ORDER BY created_at",
        (watcher_session,),
    )
    return [dict(r) for r in cur.fetchall()]


def update_connection_check(conn: sqlite3.Connection, connection_id: int,
                            check_counter: int, check_interval: int,
                            last_checked_at: str,
                            commit: bool = True) -> None:
    """Update a connection's check state (counter, interval, last_checked)."""
    conn.execute(
        "UPDATE connections "
        "SET check_counter = ?, check_interval = ?, last_checked_at = ? "
        "WHERE id = ?",
        (check_counter, check_interval, last_checked_at, connection_id),
    )
    if commit:
        conn.commit()


def delete_connection(conn: sqlite3.Connection, watcher_session: str,
                      target_session: str) -> None:
    """Delete a connection."""
    conn.execute(
        "DELETE FROM connections WHERE watcher_session = ? AND target_session = ?",
        (watcher_session, target_session),
    )
    conn.commit()

# ---------------------------------------------------------------------------
# Session config (stored in sessions.metadata JSON blob)
# ---------------------------------------------------------------------------

def get_session_config(conn: sqlite3.Connection, session_id: str,
                       key: str) -> Any:
    """Read a config value from sessions.metadata JSON. Returns None if not set."""
    row = conn.execute(
        "SELECT metadata FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None or row['metadata'] is None:
        return None
    try:
        data = json.loads(row['metadata'])
    except (json.JSONDecodeError, TypeError):
        return None
    return data.get(key)


def set_session_config(conn: sqlite3.Connection, session_id: str,
                       key: str, value: Any) -> None:
    """Write a config value to sessions.metadata JSON. Merges with existing."""
    row = conn.execute(
        "SELECT metadata FROM sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        return
    try:
        data = json.loads(row['metadata']) if row['metadata'] else {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    data[key] = value
    conn.execute(
        "UPDATE sessions SET metadata = ? WHERE session_id = ?",
        (json.dumps(data), session_id),
    )
    conn.commit()
