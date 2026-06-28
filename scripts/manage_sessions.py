#!/usr/bin/env python3
"""Session management for Claude Context Recall plugin.

Provides functions to list, prune, export, and report stats on sessions.
Wraps maintenance functions from db.py and adds display formatting.

CLI usage:
    manage_sessions.py list [--all] [--project <name>] [project_hash]
    manage_sessions.py prune (--session <id> | --before <date>)
    manage_sessions.py export --session <id>
    manage_sessions.py stats
"""

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Dict, List

# Allow running from any working directory
sys.path.insert(0, os.path.dirname(__file__))

from db import (
    get_connection,
    list_sessions,
    prune_session,
    prune_before_date,
    get_stats,
    export_session_json,
    get_session,
)
from manage_tags import get_tags_by_session
from utils import format_date, format_short_date, resolve_project_hash

_10_MB = 10 * 1024 * 1024


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_session_list(
    sessions: List[Dict],
    tags_by_session: Dict[str, List[str]],
) -> str:
    """Format a list of sessions for human-readable display.

    Args:
        sessions: List of session dicts (from list_sessions).
        tags_by_session: Mapping of session_id -> list of tag strings.

    Returns:
        Formatted multi-line string, or '*No sessions found.*' if empty.
    """
    if not sessions:
        return '*No sessions found.*'

    lines = []
    for s in sessions:
        sid = s['session_id']
        project = s.get('project_path', '')
        started = format_date(s.get('started_at', ''))
        exchange_count = s.get('exchange_count', 0)
        status = 'ended' if s.get('ended_at') else 'active'
        tags = tags_by_session.get(sid, [])
        tag_str = '  tags: ' + ', '.join(tags) if tags else ''

        lines.append(
            f'  {sid[:12]}  {started}  {exchange_count} exchange(s)  '
            f'[{status}]  {project}{tag_str}'
        )

    return '\n'.join(lines)


def format_stats(stats: Dict) -> str:
    """Format database statistics for human-readable display.

    Args:
        stats: Dict from get_stats() with total_sessions, total_exchanges,
               total_tags, db_size_bytes, and projects list.

    Returns:
        Formatted multi-line string.
    """
    db_bytes = stats.get('db_size_bytes', 0)
    if db_bytes >= 1024 * 1024:
        size_str = f'{db_bytes / (1024 * 1024):.1f} MB'
    else:
        size_str = f'{db_bytes / 1024:.1f} KB'

    lines = [
        'Database statistics:',
        f'  Total sessions:  {stats.get("total_sessions", 0)}',
        f'  Total exchanges: {stats.get("total_exchanges", 0)}',
        f'  Total tags:      {stats.get("total_tags", 0)}',
        f'  Database size:   {size_str}',
    ]

    projects = stats.get('projects', [])
    if projects:
        lines.append('')
        lines.append('Per-project breakdown:')
        for p in projects:
            lines.append(
                f'  {p["project_path"]}  '
                f'sessions={p["session_count"]}  '
                f'exchanges={p["exchange_total"]}'
            )

    if db_bytes > _10_MB:
        lines.append('')
        lines.append(
            'Tip: database exceeds 10 MB — consider pruning old sessions with '
            "'manage_sessions.py prune --before <date>'."
        )

    return '\n'.join(lines)


def format_export(data: Dict) -> str:
    """Serialize export data as pretty-printed JSON.

    Args:
        data: Dict from export_session_json().

    Returns:
        JSON string with 2-space indentation.
    """
    return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# Validated operations
# ---------------------------------------------------------------------------

def normalize_before_date(value: str) -> str:
    """Validate and normalize a --before date for pruning (WI-14).

    Accepts a bare ISO date (YYYY-MM-DD) or a full ISO datetime and returns a
    normalized ISO string suitable for comparison against stored ``started_at``
    values.  Rejects anything ``datetime.fromisoformat`` cannot parse, so a
    destructive ``DELETE ... WHERE started_at < ?`` is never run with an
    unvalidated raw string.

    Args:
        value: The raw --before argument.

    Returns:
        Normalized ISO-8601 string.

    Raises:
        ValueError: If ``value`` is empty or not a parseable ISO date/datetime.
    """
    if value is None or not str(value).strip():
        raise ValueError('date is empty')
    # Tolerate a trailing 'Z' (UTC) which older Python fromisoformat rejects.
    candidate = str(value).strip()
    normalized = candidate[:-1] + '+00:00' if candidate.endswith('Z') else candidate
    # Raises ValueError on anything unparseable.
    datetime.fromisoformat(normalized)
    return candidate


def export_session(conn, session_id: str) -> Dict:
    """Export a session, raising if it does not exist (WI-15).

    Unlike ``export_session_json``, which returns a misleading empty document
    (``{"exchanges": [], "tags": []}``) for a nonexistent session, this raises
    so callers can report an error instead of emitting an empty doc.

    Args:
        conn: SQLite connection.
        session_id: Session ID to export.

    Returns:
        The export dict from ``export_session_json``.

    Raises:
        LookupError: If no session with ``session_id`` exists.
    """
    if get_session(conn, session_id) is None:
        raise LookupError(f"session '{session_id}' not found")
    return export_session_json(conn, session_id)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='manage_sessions.py',
        description='Session management for Claude Context Recall.',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # list
    list_p = sub.add_parser('list', help='List sessions.')
    list_p.add_argument('--all', action='store_true', dest='list_all',
                        help='Show all sessions across all projects.')
    list_p.add_argument('--project', metavar='NAME',
                        help='Filter by project path substring.')
    list_p.add_argument('project_hash', nargs='?',
                        help='Filter by exact project hash.')

    # prune
    prune_p = sub.add_parser('prune', help='Delete sessions.')
    prune_group = prune_p.add_mutually_exclusive_group(required=True)
    prune_group.add_argument('--session', metavar='SESSION_ID',
                              help='Delete a specific session by ID.')
    prune_group.add_argument('--before', metavar='DATE',
                              help='Delete sessions started before this ISO date.')

    # export
    export_p = sub.add_parser('export', help='Export a session as JSON.')
    export_p.add_argument('--session', metavar='SESSION_ID', required=True,
                           help='Session ID to export.')

    # stats
    sub.add_parser('stats', help='Show database statistics.')

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    conn = get_connection()
    try:
        if args.command == 'list':
            if args.list_all:
                project_hash = None
                project_path_contains = None
            else:
                project_hash = getattr(args, 'project_hash', None)
                project_path_contains = getattr(args, 'project', None)
                # Bare `sessions` (no --all, no explicit scope) means the CURRENT
                # project — resolve it from cwd instead of listing everything.
                if not project_hash and not project_path_contains:
                    project_hash = resolve_project_hash()
            sessions = list_sessions(
                conn,
                project_hash=project_hash,
                project_path_contains=project_path_contains,
            )
            session_ids = [s['session_id'] for s in sessions]
            tags_by_session = get_tags_by_session(conn, session_ids)
            print(format_session_list(sessions, tags_by_session))

        elif args.command == 'prune':
            if args.session:
                prune_session(conn, args.session)
                print(f"Session '{args.session}' pruned.")
            elif args.before:
                try:
                    before = normalize_before_date(args.before)
                except ValueError:
                    print(
                        f"Error: invalid --before date '{args.before}'. "
                        "Expected an ISO date (YYYY-MM-DD) or datetime "
                        "(YYYY-MM-DDTHH:MM:SS).",
                        file=sys.stderr,
                    )
                    sys.exit(2)
                count = prune_before_date(conn, before)
                print(f'{count} session(s) pruned before {before}.')

        elif args.command == 'export':
            try:
                data = export_session(conn, args.session)
            except LookupError:
                print(
                    f"Error: session '{args.session}' not found.",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(format_export(data))

        elif args.command == 'stats':
            stats = get_stats(conn)
            print(format_stats(stats))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
