#!/usr/bin/env python3
"""Display paginated conversation index for the /recall command.

Reads from the SQLite database (db.py) rather than the legacy JSON index.

Usage:
    python3 show_index.py --session <id>
    python3 show_index.py --session <id> --page 2
    python3 show_index.py --session <id> --around "2:30pm"
    python3 show_index.py --session <id> --search "keyword"
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from db import get_connection, get_session, get_exchanges, search_exchanges_fts, get_exchange_count
from utils import (
    resolve_session_id,
    format_timestamp,
    format_date,
    format_short_date,
    parse_time_query,
    get_date_from_timestamp,
    PAGE_SIZE,
)


# ---------------------------------------------------------------------------
# Pure formatting helpers (operate on plain dicts)
# ---------------------------------------------------------------------------

def find_page_for_time(exchanges: List[Dict], target_time: datetime) -> int:
    """Find the page number whose exchanges are closest to target_time.

    Pages are 1-indexed and ordered most-recent-first (same convention as
    format_page).

    Args:
        exchanges: All exchanges for the session, ordered by idx ascending.
        target_time: The target datetime (only hour/minute are used).

    Returns:
        1-based page number.
    """
    if not exchanges:
        return 1

    best_idx = 0
    best_diff = float('inf')

    for i, ex in enumerate(exchanges):
        try:
            # Stored timestamps are UTC; the target (from parse_time_query) is a
            # LOCAL-clock time and display is local. Convert to local before
            # comparing hour/minute so --around matches what the user sees.
            ex_time = datetime.fromisoformat(
                ex['timestamp'].replace('Z', '+00:00')
            ).astimezone()
            ex_minutes = ex_time.hour * 60 + ex_time.minute
            target_minutes = target_time.hour * 60 + target_time.minute
            diff = abs(ex_minutes - target_minutes)
            if diff < best_diff:
                best_diff = diff
                best_idx = i
        except Exception:
            continue

    total = len(exchanges)
    pos_from_end = total - 1 - best_idx
    page = (pos_from_end // PAGE_SIZE) + 1
    return page


def search_session(conn, session_id: str, keyword: str,
                   limit: int = 100) -> List[Dict]:
    """Search a session's exchanges via the FTS5 index.

    Routes through db.search_exchanges_fts (consistent with how the rest of the
    DB layer queries exchanges) rather than scanning rows in Python. The FTS
    index covers preview, user_text and assistant_text.

    Args:
        conn: SQLite connection.
        session_id: Session to restrict the search to.
        keyword: Search query string.
        limit: Max results to return.

    Returns:
        List of matching exchange dicts (ordered by idx via the FTS query).
    """
    return search_exchanges_fts(conn, keyword, session_id=session_id, limit=limit)


def get_session_date_range(exchanges: List[Dict]) -> str:
    """Return a human-readable date range string for the given exchanges."""
    if not exchanges:
        return ""

    dates = set()
    for ex in exchanges:
        date = get_date_from_timestamp(ex.get('timestamp', ''))
        if date:
            dates.add(date)

    if not dates:
        return ""
    if len(dates) == 1:
        return format_short_date(list(dates)[0] + 'T00:00:00Z')

    sorted_dates = sorted(dates)
    start = format_short_date(sorted_dates[0] + 'T00:00:00Z')
    end = format_short_date(sorted_dates[-1] + 'T00:00:00Z')
    return f"{start} - {end}"


def format_page(
    exchanges: List[Dict],
    page: int,
    total_exchanges: int,
    session_start: str,
) -> str:
    """Format a page of exchanges as markdown.

    Args:
        exchanges: All session exchanges (used for date-range and reversed pagination).
        page: 1-based page number (page 1 = most recent).
        total_exchanges: Total exchange count for the session.
        session_start: ISO timestamp of session start (for header).

    Returns:
        Formatted markdown string.
    """
    total_pages = max(1, (total_exchanges + PAGE_SIZE - 1) // PAGE_SIZE)

    if not exchanges:
        return "*No exchanges found in this session.*"

    # Guard out-of-range page numbers. A page < 1 would otherwise produce a
    # negative-index slice (garbage) labeled e.g. "page -1".
    if page < 1:
        return f"*Invalid page number. Total pages: {total_pages}*"

    start_from_end = (page - 1) * PAGE_SIZE
    end_from_end = start_from_end + PAGE_SIZE

    page_exchanges = list(reversed(exchanges))
    page_slice = page_exchanges[start_from_end:end_from_end]

    if not page_slice:
        return f"*Page {page} is empty. Total pages: {total_pages}*"

    date_range = get_session_date_range(exchanges)
    date_info = f" ({date_range})" if date_range else ""

    lines = []
    lines.append(f"**Session started:** {format_date(session_start)}{date_info}")
    lines.append(f"**Total exchanges:** {total_exchanges}")
    lines.append("")
    lines.append(f"**Showing page {page} of {total_pages}** (most recent first):")
    lines.append("")

    current_date = None
    for ex in page_slice:
        ex_date = get_date_from_timestamp(ex.get('timestamp', ''))

        if ex_date != current_date:
            current_date = ex_date
            if ex_date:
                lines.append(f"\n**{format_short_date(ex_date + 'T00:00:00Z')}:**")

        idx = ex.get('idx', '?')
        time = format_timestamp(ex.get('timestamp', ''))
        preview = ex.get('preview', '(no preview)')
        lines.append(f"**#{idx}** [{time}] \"{preview}\"")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**Navigation:**")
    if page > 1:
        lines.append(f"- Show newer: page {page - 1}")
    if page < total_pages:
        lines.append(f"- Show older: page {page + 1}")
    lines.append("- Jump to time: e.g., \"around 2pm\" or \"around jan 5 2pm\"")
    lines.append("- Search: e.g., \"search authentication\"")

    return "\n".join(lines)


def format_search_results(results: List[Dict], keyword: str) -> str:
    """Format search results as markdown.

    Args:
        results: Matching exchange dicts.
        keyword: The search term (for header).

    Returns:
        Formatted markdown string.
    """
    if not results:
        return (
            f"*No exchanges found matching \"{keyword}\"*\n"
            "*Search looks in both user prompts AND assistant responses.*"
        )

    lines = []
    lines.append(f"**Search results for \"{keyword}\":** ({len(results)} matches)")
    lines.append("")

    current_date = None
    for ex in results[:20]:
        ex_date = get_date_from_timestamp(ex.get('timestamp', ''))
        if ex_date != current_date:
            current_date = ex_date
            if ex_date:
                lines.append(f"\n**{format_short_date(ex_date + 'T00:00:00Z')}:**")

        idx = ex.get('idx', '?')
        time = format_timestamp(ex.get('timestamp', ''))
        preview = ex.get('preview', '(no preview)')
        lines.append(f"**#{idx}** [{time}] \"{preview}\"")

    if len(results) > 20:
        lines.append(f"*... and {len(results) - 20} more matches*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Display conversation index from SQLite DB.')
    parser.add_argument('--session', metavar='SESSION_ID',
                        help='Session ID to browse (overrides RECALL_SESSION_ID env var).')
    parser.add_argument('--page', type=int, default=1,
                        help='Page number to display (1-indexed, most recent first).')
    parser.add_argument('--around', type=str,
                        help='Show the page containing exchanges closest to this time.')
    parser.add_argument('--search', type=str,
                        help='Search for exchanges containing this keyword.')

    args = parser.parse_args()

    # Resolve session ID
    session_id = resolve_session_id(args.session)

    if not session_id:
        print("*No session ID provided. Use --session <id> or set RECALL_SESSION_ID.*")
        return

    conn = get_connection()
    try:
        session = get_session(conn, session_id)
        if session is None:
            print(f"*Session '{session_id}' not found in database.*")
            return

        total_exchanges = get_exchange_count(conn, session_id)
        session_start = session.get('started_at', '')

        if total_exchanges == 0:
            print("*No exchanges found in the current session.*")
            return

        # Handle search via the FTS index (no full Python scan needed).
        if args.search:
            results = search_session(conn, session_id, args.search)
            print(format_search_results(results, args.search))
            return

        # Handle time-based navigation — needs timestamps, load all exchanges
        page = args.page
        if args.around:
            target_time = parse_time_query(args.around)
            if target_time:
                exchanges = get_exchanges(conn, session_id)
                page = find_page_for_time(exchanges, target_time)
            else:
                print(f"*Could not parse time: {args.around}. Try formats like '2:30pm' or '14:30'*")
                return

        # For normal pagination, load all exchanges so format_page can compute
        # date ranges and do reversed-index slicing. The COUNT(*) above already
        # saved us a full data load on the early-exit (empty) path.
        exchanges = get_exchanges(conn, session_id)
        print(format_page(exchanges, page, total_exchanges, session_start))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
