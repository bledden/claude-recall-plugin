#!/usr/bin/env python3
"""Tag management for Claude Context Recall plugin.

Provides functions to add, list, search, and format tags. Wraps the
insert_tag/get_tags primitives from db.py and adds a JOIN-based
search_by_tag query plus display formatting.

CLI usage:
    manage_tags.py add <tag> <session_id> [exchange_idx]
    manage_tags.py list [--project <project_hash>]
    manage_tags.py search <tag>
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from typing import Dict, List, Optional

# Allow running from any working directory
sys.path.insert(0, os.path.dirname(__file__))

from db import get_connection, insert_tag, get_tags


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def add_tag(conn: sqlite3.Connection, tag: str, session_id: str, exchange_idx: int = None) -> None:
    """Add a manual tag to a session or a specific exchange.

    Delegates to insert_tag() with source='manual'.  For session-level tags
    (exchange_idx=None) an explicit duplicate check is performed first because
    SQLite's UNIQUE constraint treats NULL as distinct from NULL, so
    INSERT OR IGNORE alone cannot prevent duplicates when exchange_idx is NULL.

    Args:
        conn: SQLite connection.
        tag: Tag string to attach.
        session_id: Target session ID.
        exchange_idx: If provided, scopes the tag to that exchange index.
    """
    if exchange_idx is None:
        # Guard against NULL-uniqueness gap in SQLite
        existing = conn.execute(
            "SELECT 1 FROM tags WHERE tag = ? AND session_id = ? AND exchange_idx IS NULL",
            (tag, session_id),
        ).fetchone()
        if existing:
            return
    insert_tag(conn, tag, session_id, exchange_idx=exchange_idx, source='manual')


def list_tags(conn: sqlite3.Connection, project_hash: str = None) -> List[Dict]:
    """Return all tags, optionally filtered to a project.

    Args:
        conn: SQLite connection.
        project_hash: If provided, restrict to sessions in this project.

    Returns:
        List of tag dicts.
    """
    return get_tags(conn, project_hash=project_hash)


def search_by_tag(conn: sqlite3.Connection, tag: str) -> List[Dict]:
    """Find all sessions that carry a given tag, enriched with project info.

    Performs a JOIN that db.py's get_tags does not expose.

    Args:
        conn: SQLite connection.
        tag: Exact tag string to look for.

    Returns:
        List of dicts with tag row fields plus project_path and session_started.
    """
    sql = (
        "SELECT t.*, s.project_path, s.started_at AS session_started "
        "FROM tags t "
        "JOIN sessions s ON t.session_id = s.session_id "
        "WHERE t.tag = ? "
        "ORDER BY s.started_at DESC"
    )
    cur = conn.execute(sql, (tag,))
    return [dict(r) for r in cur.fetchall()]


def get_tags_by_session(conn: sqlite3.Connection, session_ids: List[str]) -> Dict[str, List[str]]:
    """Return up to 5 distinct tag strings for each session ID.

    Designed for use by manage_sessions to display session summaries.

    Args:
        conn: SQLite connection.
        session_ids: List of session IDs to query.

    Returns:
        Dict mapping session_id -> list of up to 5 tag strings.
        Session IDs with no tags are omitted from the result.
    """
    result: Dict[str, List[str]] = {}
    for sid in session_ids:
        rows = conn.execute(
            "SELECT DISTINCT tag FROM tags WHERE session_id = ? "
            "ORDER BY tag LIMIT 5",
            (sid,),
        ).fetchall()
        if rows:
            result[sid] = [row['tag'] for row in rows]
    return result


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_tag_list(tags: List[Dict]) -> str:
    """Format a list of tag dicts for human-readable display.

    Groups entries by tag name, showing occurrence count and sources.

    Args:
        tags: List of tag dicts (from get_tags or similar).

    Returns:
        Formatted string.
    """
    if not tags:
        return 'No tags found.'

    # Group by tag name
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for t in tags:
        groups[t['tag']].append(t)

    lines = []
    for tag_name in sorted(groups):
        entries = groups[tag_name]
        count = len(entries)
        sources = sorted({e['source'] for e in entries})
        source_str = '/'.join(sources)
        lines.append(f'  {tag_name:30s}  count={count}  source={source_str}')

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='manage_tags.py',
        description='Tag management for Claude Context Recall.',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # add
    add_p = sub.add_parser('add', help='Attach a manual tag to a session or exchange.')
    add_p.add_argument('tag', help='Tag string to add.')
    add_p.add_argument('session_id', help='Target session ID.')
    add_p.add_argument('exchange_idx', nargs='?', type=int, default=None,
                       help='Exchange index to scope the tag to (optional).')

    # list
    list_p = sub.add_parser('list', help='List all tags.')
    list_p.add_argument('--project', metavar='PROJECT_HASH',
                        help='Filter to a specific project hash.')

    # search
    search_p = sub.add_parser('search', help='Find sessions by tag.')
    search_p.add_argument('tag', help='Tag string to search for.')

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    conn = get_connection()
    try:
        if args.command == 'add':
            add_tag(conn, args.tag, args.session_id, exchange_idx=args.exchange_idx)
            scope = f' (exchange {args.exchange_idx})' if args.exchange_idx is not None else ''
            print(f"Tag '{args.tag}' added to session {args.session_id}{scope}.")

        elif args.command == 'list':
            tags = list_tags(conn, project_hash=args.project)
            print(format_tag_list(tags))

        elif args.command == 'search':
            results = search_by_tag(conn, args.tag)
            if not results:
                print(f"No sessions found with tag '{args.tag}'.")
            else:
                for r in results:
                    print(
                        f"  {r['session_id']}  project={r['project_path']}  "
                        f"started={r['session_started']}"
                    )
    finally:
        conn.close()


if __name__ == '__main__':
    main()
