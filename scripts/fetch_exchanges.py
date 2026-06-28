#!/usr/bin/env python3
"""Fetch full exchange content using intuitive arguments.

Reads from the SQLite database (db.py) rather than the legacy JSON index.

Usage:
    python3 fetch_exchanges.py --session <id> last5
    python3 fetch_exchanges.py --session <id> last10
    python3 fetch_exchanges.py --session <id> around 2pm
    python3 fetch_exchanges.py --session <id> search auth
    python3 fetch_exchanges.py --session <id> search auth --all
    python3 fetch_exchanges.py --session <id> search auth --global
    python3 fetch_exchanges.py --session <id> search auth --project triton-metal
    python3 fetch_exchanges.py --session <id> search auth --tag mytag
"""

import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import List, Dict, Optional, Set

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from db import (
    get_connection,
    get_session,
    get_exchanges,
    search_exchanges_fts,
    search_exchanges_global,
    list_sessions,
)
from manage_tags import search_by_tag
from utils import (
    resolve_session_id,
    resolve_project_hash,
    truncate_text,
    format_timestamp,
    format_short_date,
    parse_time_query,
    parse_date_time_query,
    find_exchanges_by_time,
    get_date_from_timestamp,
    search_in_text,
    MAX_CHARS_PER_MESSAGE,
    MAX_TOTAL_CHARS,
    AROUND_TIME_WINDOW,
)


# ---------------------------------------------------------------------------
# Pure helper functions (operate on plain dicts — no DB dependency)
# ---------------------------------------------------------------------------

def parse_last_n(arg: str, total_exchanges: int) -> Set[int]:
    """Parse lastN argument into set of exchange indices.

    Args:
        arg: String like 'last5' or 'last10'.
        total_exchanges: Total number of exchanges in the session.

    Returns:
        Set of 1-based exchange indices.
    """
    if arg.lower().startswith('last'):
        try:
            n = int(arg[4:])
            start = max(1, total_exchanges - n + 1)
            return set(range(start, total_exchanges + 1))
        except ValueError:
            pass
    return set()


def get_session_dates(exchanges: List[Dict]) -> List[str]:
    """Get sorted list of unique ISO date strings present in the exchanges."""
    dates = set()
    for ex in exchanges:
        date = get_date_from_timestamp(ex.get('timestamp', ''))
        if date:
            dates.add(date)
    return sorted(dates)


def format_exchanges(exchanges: List[Dict], query_type: str = "") -> str:
    """Format exchanges as markdown for recall.

    Works on plain exchange dicts (same shape whether sourced from JSON or DB).
    Cross-project results should be pre-enriched with 'project_path' and
    'session_started' fields; when present the header groups them.

    Args:
        exchanges: List of exchange dicts.
        query_type: Short description of the query, used in the header.

    Returns:
        Formatted markdown string.
    """
    if not exchanges:
        return "*No exchanges found.*"

    output = []
    total_chars = 0

    for ex in exchanges:
        idx = ex.get('idx', '?')
        time = format_timestamp(ex.get('timestamp', ''))
        date = format_short_date(ex.get('timestamp', ''))
        time_str = f" [{date} {time}]" if date and time else (f" [{time}]" if time else "")

        user_text = ex.get('user_text', ex.get('preview', ''))
        assistant_text = ex.get('assistant_text', '')

        # Truncate each message for display
        user_text = truncate_text(user_text, MAX_CHARS_PER_MESSAGE)
        assistant_text = truncate_text(assistant_text, MAX_CHARS_PER_MESSAGE)

        exchange_chars = len(user_text) + len(assistant_text)
        if total_chars + exchange_chars > MAX_TOTAL_CHARS:
            already_shown = sum(1 for l in output if l.startswith('### Exchange'))
            remaining = len(exchanges) - already_shown
            output.append(f"\n*[Reached size limit — {remaining} more exchange(s) not shown]*")
            break

        output.append(f"### Exchange #{idx}{time_str}")
        output.append("")
        output.append(f"**User:**\n{user_text}")
        output.append("")
        if assistant_text:
            output.append(f"**Assistant:**\n{assistant_text}")
            output.append("")
        output.append("---")
        output.append("")

        total_chars += exchange_chars

    return "\n".join(output)


def format_cross_project_results(exchanges: List[Dict], keyword: str) -> str:
    """Format search results that span multiple sessions/projects.

    Expects each exchange dict to have 'project_path' and 'session_id' fields
    (as returned by search_exchanges_global or search_by_tag).

    Args:
        exchanges: Enriched exchange dicts.
        keyword: The search keyword (for header).

    Returns:
        Formatted markdown string.
    """
    if not exchanges:
        return f"*No results found for \"{keyword}\".*"

    # Group by project_path then session_id
    by_project: Dict[str, Dict[str, List[Dict]]] = defaultdict(lambda: defaultdict(list))
    for ex in exchanges:
        project = ex.get('project_path', 'unknown')
        session = ex.get('session_id', 'unknown')
        by_project[project][session].append(ex)

    n_projects = len(by_project)
    n_matches = len(exchanges)
    lines = [f"### Results for \"{keyword}\" ({n_matches} match(es) across {n_projects} project(s))", ""]

    for project_path, sessions in sorted(by_project.items()):
        project_name = Path(project_path).name if project_path else project_path
        for session_id, exs in sorted(sessions.items()):
            # Use first exchange timestamp for session date label
            first_ts = exs[0].get('timestamp', '') if exs else ''
            session_date = format_short_date(first_ts) if first_ts else session_id[:8]
            lines.append(f"**{project_name}** — Session {session_date}")
            for ex in sorted(exs, key=lambda e: e.get('idx', 0)):
                idx = ex.get('idx', '?')
                time = format_timestamp(ex.get('timestamp', ''))
                user_text = truncate_text(ex.get('user_text', ex.get('preview', '')), 200)
                assistant_text = truncate_text(ex.get('assistant_text', ''), 200)
                lines.append(f"  (exchange #{idx}) [{time}]")
                lines.append(f"  User: \"{user_text}\"")
                if assistant_text:
                    lines.append(f"  Assistant: \"{assistant_text}\"")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Argument parsing helpers
# ---------------------------------------------------------------------------

def _build_arg_parser():
    """Build the argument parser for fetch_exchanges CLI."""
    import argparse
    parser = argparse.ArgumentParser(
        description='Fetch exchanges from SQLite recall database.',
        add_help=True,
    )
    parser.add_argument('--session', metavar='SESSION_ID',
                        help='Session ID to query (overrides RECALL_SESSION_ID env var).')
    parser.add_argument('--project-hash', metavar='HASH',
                        help='Project hash for --all scope searches. '
                             'Only affects --all; ignored by --global and --project.')
    # Search scope flags are mutually exclusive: only one of --all / --global /
    # --project may be supplied. argparse errors on conflict rather than
    # silently resolving by precedence.
    scope_group = parser.add_mutually_exclusive_group()
    scope_group.add_argument('--all', dest='scope_all', action='store_true',
                             help='Search across all sessions in the current project.')
    scope_group.add_argument('--global', dest='scope_global', action='store_true',
                             help='Search across all projects.')
    scope_group.add_argument('--project', metavar='NAME',
                             help='Search sessions whose project path contains NAME.')
    parser.add_argument('--tag', metavar='TAG',
                        help='Filter/search by tag name (delegates to manage_tags).')
    parser.add_argument('command', nargs='?', default='last5',
                        help='Command: last5, last10, around, search')
    parser.add_argument('rest', nargs='*',
                        help='Additional arguments for the command.')
    return parser


# ---------------------------------------------------------------------------
# Usage / print helpers
# ---------------------------------------------------------------------------

def print_usage():
    print("**Usage:**")
    print("- `/recall last5` — Recall last 5 exchanges")
    print("- `/recall last10` — Recall last 10 exchanges")
    print("- `/recall around 2pm` — Recall exchanges around 2pm")
    print("- `/recall around \"jan 5 2pm\"` — Recall exchanges around 2pm on Jan 5")
    print("- `/recall search keyword` — Search for exchanges containing keyword")
    print("- `/recall search keyword --all` — Search all sessions in current project")
    print("- `/recall search keyword --global` — Search all projects")
    print("- `/recall search keyword --project name` — Search sessions by project name")
    print("- `/recall search --tag mytag` — Find sessions tagged 'mytag'")
    print("")
    print("Or just run `/recall` for the interactive menu.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Split sys.argv into known args so positional 'rest' accumulates correctly
    parser = _build_arg_parser()

    # When no positional command is given, argparse applies the 'command'
    # default ('last5'), so no separate raw_args default is needed.
    try:
        args = parser.parse_args(sys.argv[1:])
    except SystemExit:
        return

    # Resolve session ID: CLI arg beats env var
    session_id = resolve_session_id(args.session)

    # Open DB connection
    conn = get_connection()
    try:
        command = (args.command or 'last5').lower()
        rest = args.rest or []

        # ------------------------------------------------------------------
        # Handle tag search — independent of session scope
        # ------------------------------------------------------------------
        if args.tag:
            results = search_by_tag(conn, args.tag)
            if not results:
                print(f"*No sessions found with tag '{args.tag}'.*")
                return
            # Enrich with project context if search is via tag
            print(f"*Found {len(results)} result(s) for tag '{args.tag}':*\n")
            for r in results:
                sid = r.get('session_id', '')
                project = r.get('project_path', '')
                started = format_short_date(r.get('session_started', ''))
                exchange_idx = r.get('exchange_idx')
                scope = f" (exchange #{exchange_idx})" if exchange_idx is not None else " (session)"
                print(f"- **{Path(project).name if project else sid}** — Session {started}{scope}")
            return

        # ------------------------------------------------------------------
        # Handle "search KEYWORD --global / --all / --project" modes
        # ------------------------------------------------------------------
        if command == 'search':
            # NB: args.tag is already handled (with a return) in the tag branch
            # above, so it is always falsy here — no need to re-check it.
            if not rest:
                print("*Please specify a search term, e.g., 'search authentication'*")
                return

            keyword = ' '.join(rest)

            # --global: search across all projects
            if args.scope_global:
                results = search_exchanges_global(conn, keyword, limit=20)
                if not results:
                    print(f"*No exchanges found matching '{keyword}' across all projects.*")
                    return
                print(f"*Global search for '{keyword}':*\n")
                print(format_cross_project_results(results, keyword))
                return

            # --project <name>: resolve sessions, then FTS within them
            if args.project:
                sessions = list_sessions(conn, project_path_contains=args.project)
                if not sessions:
                    print(f"*No sessions found for project matching '{args.project}'.*")
                    return
                all_results = []
                for s in sessions:
                    hits = search_exchanges_fts(conn, keyword, session_id=s['session_id'], limit=10)
                    for h in hits:
                        h['project_path'] = s['project_path']
                    all_results.extend(hits)
                if not all_results:
                    print(f"*No exchanges found matching '{keyword}' in project '{args.project}'.*")
                    return
                print(f"*Search for '{keyword}' in project '{args.project}': {len(all_results)} match(es)*\n")
                print(format_cross_project_results(all_results, keyword))
                return

            # --all: search all sessions in the current project
            if args.scope_all:
                project_hash = args.project_hash
                if not project_hash and session_id:
                    sess = get_session(conn, session_id)
                    if sess:
                        project_hash = sess.get('project_hash')
                # Fall back to the current project (derived from cwd) so --all
                # works without an explicit hash or a valid session.
                if not project_hash:
                    project_hash = resolve_project_hash()
                if not project_hash:
                    print("*--all: could not resolve a project hash.*")
                    return
                results = search_exchanges_fts(conn, keyword, project_hash=project_hash, limit=20)
                if not results:
                    print(f"*No exchanges found matching '{keyword}' in this project.*")
                    return
                print(f"*Search for '{keyword}' across project ({len(results)} match(es)):*\n")
                print(format_exchanges(results, f"search '{keyword}' --all"))
                return

            # Default: search current session
            if not session_id:
                print("*No session ID provided. Use --session <id> or set RECALL_SESSION_ID.*")
                return

            results = search_exchanges_fts(conn, keyword, session_id=session_id, limit=10)
            if not results:
                print(f"*No exchanges found matching '{keyword}'*")
                print("*Search looks in both user prompts AND assistant responses.*")
                return

            # NB: search_exchanges_fts already caps at limit=10, so no further
            # trimming is needed here.
            print(f"*Fetched {len(results)} exchange(s) (search '{keyword}'):*\n")
            print(format_exchanges(results, f"search '{keyword}'"))
            return

        # ------------------------------------------------------------------
        # Session-scoped commands: lastN, around
        # ------------------------------------------------------------------
        if not session_id:
            print("*No session ID provided. Use --session <id> or set RECALL_SESSION_ID.*")
            return

        # Verify session exists
        session = get_session(conn, session_id)
        if session is None:
            print(f"*Session '{session_id}' not found in database.*")
            return

        # Handle "lastN"
        if command.startswith('last'):
            # Reject non-numeric and non-positive N (e.g. 'last', 'last0',
            # 'last-3'). 'last0' passes isdigit() but n<=0 must NOT fall through
            # to get_exchanges(last_n=0), which would return ALL exchanges.
            if not command[4:].isdigit() or int(command[4:]) <= 0:
                print(f"*Invalid format: {command}. Try 'last5' or 'last10'.*")
                return
            n = int(command[4:])
            selected = get_exchanges(conn, session_id, last_n=n)
            if not selected:
                print("*No exchanges found in the current session.*")
                return
            print(f"*Fetched {len(selected)} exchange(s) ({command}):*\n")
            print(format_exchanges(selected, command))
            return

        # Handle "around TIME"
        if command == 'around':
            if not rest:
                print("*Please specify a time, e.g., 'around 2pm' or 'around \"jan 5 2pm\"'*")
                return

            time_str = ' '.join(rest)
            all_exchanges = get_exchanges(conn, session_id)

            if not all_exchanges:
                print("*No exchanges found in the current session.*")
                return

            result = parse_date_time_query(time_str, get_session_dates(all_exchanges))
            if not result:
                print(f"*Could not parse time: '{time_str}'. Try formats like '2pm', '2:30pm', 'jan 5 2pm'*")
                return

            target_time, target_date = result

            # Warn about multi-day sessions when no date specified
            session_dates = get_session_dates(all_exchanges)
            if len(session_dates) > 1 and not target_date:
                formatted = ', '.join(format_short_date(d + 'T00:00:00Z') for d in session_dates)
                print(f"*Note: Session spans {len(session_dates)} days: {formatted}*")
                print(f"*Showing closest match to {time_str}. Specify date for precision (e.g., 'jan 5 2pm')*\n")

            target_idx_list = find_exchanges_by_time(all_exchanges, target_time, target_date)
            if not target_idx_list:
                print(f"*No exchanges found around {time_str}*")
                return

            target_indices = set(target_idx_list)
            selected = [ex for ex in all_exchanges if ex['idx'] in target_indices]
            selected.sort(key=lambda x: x['idx'])
            print(f"*Fetched {len(selected)} exchange(s) (around {time_str}):*\n")
            print(format_exchanges(selected, f"around {time_str}"))
            return

        # Unknown command
        print(f"*Unknown command: '{command}'*\n")
        print_usage()
    finally:
        conn.close()


if __name__ == '__main__':
    main()
