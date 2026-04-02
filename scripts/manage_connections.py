#!/usr/bin/env python3
"""Connection management for Claude Context Recall plugin.

Provides functions to connect sessions, display inbox highlights, and
configure per-session behavior. Wraps connection CRUD from db.py.

CLI usage:
    manage_connections.py connect <watcher> <target> "topic"
    manage_connections.py connect-latest <watcher> <project_hash> "topic"
    manage_connections.py disconnect <watcher> <target>
    manage_connections.py inbox <watcher>
    manage_connections.py config <session> <key> <value>
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from db import (get_connection, get_session, list_sessions, insert_connection,
                get_connections, delete_connection, update_connection_check,
                get_highlights_for_connections, get_session_config, set_session_config)
from utils import format_timestamp

# ---------------------------------------------------------------------------
# Connect / Disconnect
# ---------------------------------------------------------------------------

def connect(conn, watcher_session: str, target_session: str, topic: str,
            check_mode: str = 'explicit', delivery_mode: str = 'silent') -> str:
    """Connect watcher_session to target_session, watching for highlights about topic.

    Args:
        conn: SQLite connection.
        watcher_session: The session that will receive updates.
        target_session: The session being watched.
        topic: Description of what to watch for.
        check_mode: How highlights are delivered — 'explicit' or 'auto'.
        delivery_mode: Display style — 'silent' or 'notify'.

    Returns:
        Confirmation message, or error if target session not found.
    """
    target = get_session(conn, target_session)
    if target is None:
        return f"*Error: session {target_session[:8]}... not found.*"

    insert_connection(conn, watcher_session, target_session, topic,
                      check_mode=check_mode, delivery_mode=delivery_mode)
    return (f'*Connected to session {target_session[:8]}... '
            f'— watching for highlights about "{topic}"*')


def connect_latest(conn, watcher_session: str, project_hash: str, topic: str,
                   check_mode: str = 'explicit', delivery_mode: str = 'silent') -> str:
    """Connect to the most recent active session in the same project.

    Finds the most recently started active session (ended_at IS NULL) that
    shares the given project_hash and is not the watcher itself, then
    delegates to connect().

    Args:
        conn: SQLite connection.
        watcher_session: The requesting session (excluded from search).
        project_hash: The project hash to search within.
        topic: Description of what to watch for.
        check_mode: How highlights are delivered.
        delivery_mode: Display style.

    Returns:
        Confirmation from connect(), or error if no eligible session found.
    """
    cur = conn.execute(
        "SELECT * FROM sessions "
        "WHERE project_hash = ? AND session_id != ? AND ended_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (project_hash, watcher_session),
    )
    row = cur.fetchone()
    if row is None:
        return "*Error: no other active sessions found in this project.*"

    target_session = row['session_id']
    return connect(conn, watcher_session, target_session, topic,
                   check_mode=check_mode, delivery_mode=delivery_mode)


def disconnect(conn, watcher_session: str, target_session: str) -> str:
    """Remove the connection between watcher and target.

    Args:
        conn: SQLite connection.
        watcher_session: The watching session.
        target_session: The session being watched.

    Returns:
        Confirmation string.
    """
    delete_connection(conn, watcher_session, target_session)
    return (f"*Disconnected from session {target_session[:8]}...*")


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

def format_inbox(highlights: List[Dict]) -> str:
    """Format a list of highlights into a readable inbox summary.

    Groups highlights by target session (using connection_topic), then formats
    each group with session ID, topic, and individual highlight entries.

    Args:
        highlights: List of highlight dicts enriched with 'connection_topic'.

    Returns:
        Formatted inbox string, or '*No new highlights.*' if empty.
    """
    if not highlights:
        return "*No new highlights.*"

    # Group by session_id, preserving insertion order
    groups: Dict[str, List[Dict]] = {}
    topics: Dict[str, str] = {}
    for h in highlights:
        sid = h['session_id']
        if sid not in groups:
            groups[sid] = []
            topics[sid] = h.get('connection_topic', '')
        groups[sid].append(h)

    total = len(highlights)
    lines = [f"**Inbox** ({total} new highlight{'s' if total != 1 else ''})"]

    for sid, items in groups.items():
        topic = topics[sid]
        lines.append("")
        lines.append(f"From session {sid[:8]}... ({topic}):")
        for h in items:
            summary = h.get('summary', '')
            tags_raw = h.get('tags', '')
            tags_str = f"[{tags_raw}]" if tags_raw else "[]"
            ts = format_timestamp(h.get('created_at', ''))
            ts_part = f" — {ts}" if ts else ""
            lines.append(f'  - "{summary}" {tags_str}{ts_part}')

    lines.append("")
    lines.append(
        "Use /recall search <keyword> --session <id> to pull full context."
    )

    return "\n".join(lines)


def inbox(conn, watcher_session: str) -> str:
    """Retrieve and display unchecked highlights for a watcher session.

    Fetches all unchecked highlights across the watcher's connections,
    formats them, then marks every connection as checked (updates
    last_checked_at to now).

    Args:
        conn: SQLite connection.
        watcher_session: The session whose inbox to display.

    Returns:
        Formatted inbox string.
    """
    highlights = get_highlights_for_connections(conn, watcher_session)
    result = format_inbox(highlights)

    # Update last_checked_at for all connections
    now = datetime.now(timezone.utc).isoformat()
    connections = get_connections(conn, watcher_session)
    for c in connections:
        update_connection_check(
            conn,
            connection_id=c['id'],
            check_counter=c.get('check_counter', 0) + 1,
            check_interval=c.get('check_interval', 7),
            last_checked_at=now,
        )

    return result


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

VALID_CONFIG_KEYS = {
    'check_mode', 'delivery_mode', 'auto_highlight',
    'skill_enabled', 'detection_signals', 'auto_run_highlight',
}


def config(conn, session_id: str, key: str, value: str) -> str:
    """Set a per-session configuration value.

    For 'check_mode' and 'delivery_mode', updates ALL connections where
    this session is the watcher.  For 'auto_highlight', writes to the
    session's metadata blob via set_session_config().

    Args:
        conn: SQLite connection.
        session_id: The session to configure.
        key: Config key — one of 'check_mode', 'delivery_mode', 'auto_highlight'.
        value: The new value (string; 'true'/'false' parsed as bool for auto_highlight).

    Returns:
        Confirmation string, or error for invalid key.
    """
    if key not in VALID_CONFIG_KEYS:
        valid = ', '.join(sorted(VALID_CONFIG_KEYS))
        return f"*Error: invalid config key '{key}'. Valid keys: {valid}.*"

    # Connection-level keys — update all connections for this watcher
    _COLUMN_SAFE = {'check_mode': 'check_mode', 'delivery_mode': 'delivery_mode'}
    if key in _COLUMN_SAFE:
        col = _COLUMN_SAFE[key]
        connections = get_connections(conn, session_id)
        for c in connections:
            conn.execute(
                f"UPDATE connections SET {col} = ? WHERE id = ?",
                (value, c['id']),
            )
        conn.commit()
        count = len(connections)
        return (f"*Config updated: {key} = '{value}' "
                f"on {count} connection{'s' if count != 1 else ''}.*")

    # Session metadata keys
    bool_keys = {'auto_highlight', 'skill_enabled', 'auto_run_highlight'}
    if key in bool_keys:
        if value.lower() in ('true', '1', 'yes'):
            parsed_value = True
        elif value.lower() in ('false', '0', 'no'):
            parsed_value = False
        else:
            parsed_value = value
        set_session_config(conn, session_id, key, parsed_value)
        return f"*Config updated: {key} = {parsed_value}.*"

    # String metadata keys (e.g., detection_signals)
    set_session_config(conn, session_id, key, value)
    return f"*Config updated: {key} = '{value}'.*"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI dispatcher for connection management commands."""
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  manage_connections.py connect <watcher> <target> \"topic\"\n"
            "  manage_connections.py connect-latest <watcher> <project_hash> \"topic\"\n"
            "  manage_connections.py disconnect <watcher> <target>\n"
            "  manage_connections.py inbox <watcher>\n"
            "  manage_connections.py config <session> <key> <value>",
            file=sys.stderr,
        )
        sys.exit(1)

    command = sys.argv[1]
    conn = get_connection()

    try:
        if command == 'connect':
            if len(sys.argv) < 5:
                print("Usage: manage_connections.py connect <watcher> <target> \"topic\"",
                      file=sys.stderr)
                sys.exit(1)
            watcher, target, topic = sys.argv[2], sys.argv[3], sys.argv[4]
            print(connect(conn, watcher, target, topic))

        elif command == 'connect-latest':
            if len(sys.argv) < 5:
                print("Usage: manage_connections.py connect-latest <watcher> <project_hash> \"topic\"",
                      file=sys.stderr)
                sys.exit(1)
            watcher, project_hash, topic = sys.argv[2], sys.argv[3], sys.argv[4]
            print(connect_latest(conn, watcher, project_hash, topic))

        elif command == 'disconnect':
            if len(sys.argv) < 4:
                print("Usage: manage_connections.py disconnect <watcher> <target>",
                      file=sys.stderr)
                sys.exit(1)
            watcher, target = sys.argv[2], sys.argv[3]
            print(disconnect(conn, watcher, target))

        elif command == 'inbox':
            if len(sys.argv) < 3:
                print("Usage: manage_connections.py inbox <watcher>", file=sys.stderr)
                sys.exit(1)
            watcher = sys.argv[2]
            print(inbox(conn, watcher))

        elif command == 'config':
            if len(sys.argv) < 5:
                print("Usage: manage_connections.py config <session> <key> <value>",
                      file=sys.stderr)
                sys.exit(1)
            session_id, key, value = sys.argv[2], sys.argv[3], sys.argv[4]
            print(config(conn, session_id, key, value))

        else:
            print(f"Unknown command: {command}", file=sys.stderr)
            sys.exit(1)

    finally:
        conn.close()


if __name__ == '__main__':
    main()
