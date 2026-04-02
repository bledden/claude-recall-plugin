#!/usr/bin/env python3
"""SQLite database layer for Claude Context Recall plugin v2.

Provides schema creation, WAL-mode connections, session/exchange CRUD,
FTS5 full-text search, and maintenance operations.

All functions accept a sqlite3.Connection and are safe for concurrent
reads via WAL mode with a 5-second busy timeout.
"""

import os
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_DIR = Path.home() / '.claude' / 'context-recall'
DB_PATH = DB_DIR / 'recall.db'
DB_BUSY_TIMEOUT_MS = 5000

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

CREATE VIRTUAL TABLE IF NOT EXISTS exchanges_fts USING fts5(
    user_text, assistant_text, preview,
    content=exchanges, content_rowid=id
);

CREATE INDEX IF NOT EXISTS idx_exchanges_session ON exchanges(session_id);
CREATE INDEX IF NOT EXISTS idx_tags_session ON tags(session_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_hash);
"""

# ---------------------------------------------------------------------------
# Connection & schema
# ---------------------------------------------------------------------------

def get_connection(db_path=None):
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
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout={}".format(DB_BUSY_TIMEOUT_MS))
    conn.executescript(_SCHEMA_SQL)
    return conn

# ---------------------------------------------------------------------------
# Session CRUD
# ---------------------------------------------------------------------------

def insert_session(conn, session_id, project_path, project_hash, started_at,
                   transcript_path=None):
    """Insert a new session (INSERT OR IGNORE)."""
    conn.execute(
        "INSERT OR IGNORE INTO sessions "
        "(session_id, project_path, project_hash, started_at, transcript_path) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, project_path, project_hash, started_at, transcript_path),
    )
    conn.commit()


def get_session(conn, session_id):
    """Return a session as a dict, or None if not found."""
    cur = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


def list_sessions(conn, project_hash=None, project_path_contains=None):
    """List sessions, optionally filtered, ordered by started_at DESC."""
    sql = "SELECT * FROM sessions WHERE 1=1"
    params = []
    if project_hash is not None:
        sql += " AND project_hash = ?"
        params.append(project_hash)
    if project_path_contains is not None:
        sql += " AND project_path LIKE ?"
        params.append('%' + project_path_contains + '%')
    sql += " ORDER BY started_at DESC"
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def end_session(conn, session_id, ended_at):
    """Mark a session as ended."""
    conn.execute(
        "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
        (ended_at, session_id),
    )
    conn.commit()


def update_session_offset(conn, session_id, byte_offset, exchange_count):
    """Update the incremental-read offset and exchange count for a session."""
    conn.execute(
        "UPDATE sessions SET byte_offset = ?, exchange_count = ? WHERE session_id = ?",
        (byte_offset, exchange_count, session_id),
    )
    conn.commit()

# ---------------------------------------------------------------------------
# Exchange CRUD
# ---------------------------------------------------------------------------

def insert_exchanges(conn, session_id, exchanges):
    """Insert a batch of exchanges and sync the FTS5 index.

    Args:
        conn: SQLite connection.
        session_id: Parent session ID.
        exchanges: List of dicts with keys: idx, timestamp, preview,
                   user_text, assistant_text.
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
    conn.commit()

    # Add newly inserted rows to the FTS5 index
    if new_rowids:
        _insert_fts_rows(conn, new_rowids)


def _insert_fts_rows(conn, rowids):
    """Insert specific exchange rows into the FTS5 index by rowid."""
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
    conn.commit()


def _delete_fts_rows(conn, session_id):
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


def get_exchanges(conn, session_id, last_n=None):
    """Get exchanges for a session, ordered by idx.

    Args:
        conn: SQLite connection.
        session_id: Session to query.
        last_n: If set, return only the last N exchanges.

    Returns:
        List of dicts.
    """
    if last_n is not None:
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

def search_exchanges_fts(conn, query, session_id=None, project_hash=None, limit=10):
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
    # Quote the query for phrase-safe matching
    safe_query = '"' + query.replace('"', '""') + '"'

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


def search_exchanges_global(conn, query, limit=20):
    """Search across ALL sessions/projects, enriching results with session info.

    Returns:
        List of dicts — exchange fields plus project_path and session_started.
    """
    safe_query = '"' + query.replace('"', '""') + '"'

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

def prune_session(conn, session_id):
    """Delete a session, its exchanges (including FTS entries), and tags."""
    # Remove FTS entries BEFORE deleting exchanges (needs column values)
    _delete_fts_rows(conn, session_id)

    conn.execute("DELETE FROM tags WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM exchanges WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()


def prune_before_date(conn, before_date):
    """Delete all sessions started before the given ISO date string.

    Returns:
        Number of sessions deleted.
    """
    cur = conn.execute(
        "SELECT session_id FROM sessions WHERE started_at < ?", (before_date,)
    )
    session_ids = [row['session_id'] for row in cur.fetchall()]
    for sid in session_ids:
        prune_session(conn, sid)
    return len(session_ids)


def get_stats(conn, db_path=None):
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

    cur = conn.execute("SELECT DISTINCT project_path FROM sessions ORDER BY project_path")
    projects = [row['project_path'] for row in cur.fetchall()]

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


def export_session_json(conn, session_id):
    """Export a full session (session row + exchanges + tags) as a dict.

    Returns:
        Dict with keys 'session', 'exchanges', 'tags'.  Returns None-valued
        session if the session_id does not exist.
    """
    session = get_session(conn, session_id)

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

    return {
        'session': session,
        'exchanges': exchanges,
        'tags': tags,
    }
