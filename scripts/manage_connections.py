#!/usr/bin/env python3
"""Connection management for Claude Context Recall plugin.

Provides functions to connect sessions, display inbox highlights, and
configure per-session behavior. Wraps connection CRUD from db.py.

Canonical connection vocabulary (matches db.py, config() validators, and
SKILL.md) — there is exactly ONE set of accepted values:
    check_mode    in {'explicit', 'decay'}   (default: 'explicit')
    delivery_mode in {'silent', 'inject'}    (default: 'silent')

CLI usage:
    manage_connections.py connect <watcher> <target> "topic"
        [--check-mode {explicit,decay}] [--delivery-mode {silent,inject}]
    manage_connections.py connect-latest <watcher> "topic"   (project self-resolves from cwd)
        [--check-mode {explicit,decay}] [--delivery-mode {silent,inject}]
    manage_connections.py disconnect <watcher> <target>
    manage_connections.py inbox <watcher> [--mark-read]
    manage_connections.py config <session> <key> <value>
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from db import (get_connection, get_session, list_sessions, insert_connection,
                get_connections, delete_connection, update_connection_check,
                get_highlights_for_connections, get_session_config, set_session_config)
from utils import format_timestamp, resolve_project_hash

# ---------------------------------------------------------------------------
# Connect / Disconnect
# ---------------------------------------------------------------------------

def connect(conn: sqlite3.Connection, watcher_session: str, target_session: str, topic: str,
            check_mode: str = 'explicit', delivery_mode: str = 'silent') -> str:
    """Connect watcher_session to target_session, watching for highlights about topic.

    Args:
        conn: SQLite connection.
        watcher_session: The session that will receive updates.
        target_session: The session being watched.
        topic: Description of what to watch for.
        check_mode: Polling behavior — 'explicit' (highlights only surface via
            /recall inbox) or 'decay' (the prompt hook polls on a decaying
            cadence). Defaults to 'explicit'.
        delivery_mode: Display style — 'silent' (queue for inbox) or 'inject'
            (auto-inject highlights as system messages). Defaults to 'silent'.

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


def connect_latest(conn: sqlite3.Connection, watcher_session: str, project_hash: str, topic: str,
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
        check_mode: Polling behavior — 'explicit' or 'decay' (default 'explicit').
        delivery_mode: Display style — 'silent' or 'inject' (default 'silent').

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


def disconnect(conn: sqlite3.Connection, watcher_session: str, target_session: str) -> str:
    """Remove the connection between watcher and target.

    Args:
        conn: SQLite connection.
        watcher_session: The watching session.
        target_session: The session being watched.

    Returns:
        Confirmation string, or a 'no such connection' notice when nothing
        was removed (db.delete_connection silently no-ops on a missing row,
        so existence is verified here before reporting success).
    """
    existing = any(
        c['target_session'] == target_session
        for c in get_connections(conn, watcher_session)
    )
    delete_connection(conn, watcher_session, target_session)
    if not existing:
        return (f"*No such connection to session {target_session[:8]}...*")
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


def inbox(conn: sqlite3.Connection, watcher_session: str,
          mark_read: bool = False) -> str:
    """Retrieve and display unchecked highlights for a watcher session.

    Fetches all unchecked highlights across the watcher's connections and
    formats them. This is a read-only VIEW by default: a plain inbox does
    NOT advance any counters or last_checked_at, so it is idempotent and can
    be called repeatedly without consuming highlights.

    Only when ``mark_read`` is True does the inbox advance state, and even
    then ONLY for connections whose check_mode is 'decay' — 'explicit'
    connections are never advanced (their highlights persist until the user
    explicitly checks them).

    Args:
        conn: SQLite connection.
        watcher_session: The session whose inbox to display.
        mark_read: When True, advance check_counter and last_checked_at for
            decay-mode connections. Defaults to False (pure read).

    Returns:
        Formatted inbox string.
    """
    highlights = get_highlights_for_connections(conn, watcher_session)
    result = format_inbox(highlights)

    if mark_read:
        # Advance check state only for decay-mode connections.
        now = datetime.now(timezone.utc).isoformat()
        connections = get_connections(conn, watcher_session)
        for c in connections:
            if c.get('check_mode') == 'explicit':
                continue
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


_TRUE_TOKENS = ('true', '1', 'yes', 'on')
_FALSE_TOKENS = ('false', '0', 'no', 'off')
_BOOL_KEYS = {'auto_highlight', 'skill_enabled', 'auto_run_highlight'}


def config(conn: sqlite3.Connection, session_id: str, key: str,
           value: str) -> Tuple[str, int]:
    """Set a per-session configuration value.

    There are six valid config keys, split across two persistence layers:

    Connection-row keys (written to the ``connections`` table for every
    connection where this session is the watcher):
        check_mode    — polling behavior; one of {'explicit', 'decay'}.
        delivery_mode — display style; one of {'silent', 'inject'}.

    Session-metadata keys (written to ``sessions.metadata`` JSON blob via
    set_session_config()):
        auto_highlight     — bool; auto-detect highlights from responses.
        skill_enabled      — bool; whether the recall skill is active.
        auto_run_highlight — bool; auto-run the highlight command.
        detection_signals  — string; comma-separated detection signal list.

    Boolean keys accept {true,1,yes,on}/{false,0,no,off} (case-insensitive)
    and reject any other token.

    Persistence is verified before reporting success: connection-row keys
    warn (non-zero exit) when 0 connections are affected, and metadata keys
    first confirm the session exists via a read-only get_session().

    Args:
        conn: SQLite connection.
        session_id: The session to configure.
        key: One of the six valid config keys (see above).
        value: The new value as a string.

    Returns:
        A (message, exit_code) tuple. exit_code is 0 on success and non-zero
        when validation fails or nothing was persisted.
    """
    if key not in VALID_CONFIG_KEYS:
        valid = ', '.join(sorted(VALID_CONFIG_KEYS))
        return (f"*Error: invalid config key '{key}'. Valid keys: {valid}.*", 1)

    if key == 'check_mode' and value not in ('explicit', 'decay'):
        return (f"*Error: check_mode must be 'explicit' or 'decay', "
                f"got '{value}'.*", 1)

    if key == 'delivery_mode' and value not in ('silent', 'inject'):
        return (f"*Error: delivery_mode must be 'silent' or 'inject', "
                f"got '{value}'.*", 1)

    # Connection-level keys — update all connections for this watcher.
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
        if count == 0:
            return (f"*Warning: {key} = '{value}' persisted to 0 connections "
                    f"(session {session_id[:8]}... has no connections).*", 1)
        return (f"*Config updated: {key} = '{value}' "
                f"on {count} connection{'s' if count != 1 else ''}.*", 0)

    # Session metadata keys — verify the session exists (read-only) first.
    if get_session(conn, session_id) is None:
        return (f"*Error: session {session_id[:8]}... not found; "
                f"cannot persist {key}.*", 1)

    if key in _BOOL_KEYS:
        low = value.lower()
        if low in _TRUE_TOKENS:
            parsed_value: object = True
        elif low in _FALSE_TOKENS:
            parsed_value = False
        else:
            return (f"*Error: {key} expects a boolean "
                    f"({'/'.join(_TRUE_TOKENS)} or {'/'.join(_FALSE_TOKENS)}), "
                    f"got '{value}'.*", 1)
        set_session_config(conn, session_id, key, parsed_value)
        return (f"*Config updated: {key} = {parsed_value}.*", 0)

    # String metadata keys (e.g., detection_signals)
    set_session_config(conn, session_id, key, value)
    return (f"*Config updated: {key} = '{value}'.*", 0)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

CHECK_MODES = ('explicit', 'decay')
DELIVERY_MODES = ('silent', 'inject')


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser for the connection-management CLI.

    Subcommands map 1:1 to the public functions. Using argparse gives us
    ``--help`` for free and turns unknown subcommands into a usage error
    (non-zero exit) rather than a silent fall-through.
    """
    parser = argparse.ArgumentParser(
        prog='manage_connections.py',
        description='Connection management for Claude Context Recall.',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p_connect = sub.add_parser(
        'connect', help='Connect a watcher to a specific target session.')
    p_connect.add_argument('watcher')
    p_connect.add_argument('target')
    p_connect.add_argument('topic')
    p_connect.add_argument('--check-mode', dest='check_mode',
                           choices=CHECK_MODES, default='explicit')
    p_connect.add_argument('--delivery-mode', dest='delivery_mode',
                           choices=DELIVERY_MODES, default='silent')

    p_latest = sub.add_parser(
        'connect-latest',
        help='Connect to the most recent active session in a project.')
    p_latest.add_argument('watcher')
    p_latest.add_argument('topic')
    p_latest.add_argument('--check-mode', dest='check_mode',
                          choices=CHECK_MODES, default='explicit')
    p_latest.add_argument('--delivery-mode', dest='delivery_mode',
                          choices=DELIVERY_MODES, default='silent')

    p_disc = sub.add_parser('disconnect', help='Remove a connection.')
    p_disc.add_argument('watcher')
    p_disc.add_argument('target')

    p_inbox = sub.add_parser('inbox', help='Show unchecked highlights (read-only).')
    p_inbox.add_argument('watcher')
    p_inbox.add_argument('--mark-read', dest='mark_read', action='store_true',
                         help='Advance check state for decay-mode connections.')

    p_config = sub.add_parser('config', help='Set a per-session config value.')
    p_config.add_argument('session')
    p_config.add_argument('key')
    p_config.add_argument('value')

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    """CLI dispatcher for connection management commands."""
    parser = build_parser()
    args = parser.parse_args(argv)

    conn = get_connection()
    try:
        if args.command == 'connect':
            print(connect(conn, args.watcher, args.target, args.topic,
                          check_mode=args.check_mode,
                          delivery_mode=args.delivery_mode))

        elif args.command == 'connect-latest':
            # Project hash self-resolves from cwd (no longer a CLI positional).
            print(connect_latest(conn, args.watcher, resolve_project_hash(), args.topic,
                                 check_mode=args.check_mode,
                                 delivery_mode=args.delivery_mode))

        elif args.command == 'disconnect':
            print(disconnect(conn, args.watcher, args.target))

        elif args.command == 'inbox':
            print(inbox(conn, args.watcher, mark_read=args.mark_read))

        elif args.command == 'config':
            message, code = config(conn, args.session, args.key, args.value)
            print(message)
            if code != 0:
                sys.exit(code)

    finally:
        conn.close()


if __name__ == '__main__':
    main()
