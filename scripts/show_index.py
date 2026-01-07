#!/usr/bin/env python3
"""Display paginated conversation index for the /recall command.

This script reads the index built by the hook and displays it
in a paginated format for user browsing.

Usage:
    python3 show_index.py                    # Show most recent page (default)
    python3 show_index.py --page 1           # Show specific page (1-indexed)
    python3 show_index.py --around "2:30pm"  # Show exchanges around a time
    python3 show_index.py --search "keyword" # Search for exchanges containing keyword

Output is formatted markdown for display in the /recall command.
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional


# Configuration
PAGE_SIZE = 20  # Exchanges per page


def load_index() -> Optional[Dict]:
    """Load the conversation index."""
    index_file = Path.home() / '.claude' / 'context-recall' / 'index.json'

    if not index_file.exists():
        return None

    try:
        with open(index_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def format_timestamp(iso_timestamp: str) -> str:
    """Format ISO timestamp as human-readable time."""
    if not iso_timestamp:
        return "??:??"

    try:
        dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        # Convert to local time
        local_dt = dt.astimezone()
        return local_dt.strftime("%-I:%M %p").lower()
    except Exception:
        return "??:??"


def format_date(iso_timestamp: str) -> str:
    """Format ISO timestamp as human-readable date."""
    if not iso_timestamp:
        return "Unknown date"

    try:
        dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        local_dt = dt.astimezone()
        return local_dt.strftime("%b %d, %Y at %-I:%M %p")
    except Exception:
        return "Unknown date"


def parse_time_query(time_str: str) -> Optional[datetime]:
    """Parse a time query like '2:30pm', '14:30', 'around 3pm'."""
    time_str = time_str.lower().strip()
    time_str = re.sub(r'^around\s+', '', time_str)

    # Try various formats
    formats = [
        "%I:%M%p",   # 2:30pm
        "%I:%M %p", # 2:30 pm
        "%I%p",      # 2pm
        "%I %p",     # 2 pm
        "%H:%M",     # 14:30
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(time_str, fmt)
            # Use today's date
            today = datetime.now()
            return parsed.replace(year=today.year, month=today.month, day=today.day)
        except ValueError:
            continue

    return None


def find_page_for_time(exchanges: List[Dict], target_time: datetime) -> int:
    """Find the page number containing exchanges closest to target_time."""
    if not exchanges:
        return 1

    # Find the exchange closest to target_time
    best_idx = 0
    best_diff = float('inf')

    for i, ex in enumerate(exchanges):
        try:
            ex_time = datetime.fromisoformat(ex['timestamp'].replace('Z', '+00:00'))
            # Compare only time portion
            ex_minutes = ex_time.hour * 60 + ex_time.minute
            target_minutes = target_time.hour * 60 + target_time.minute
            diff = abs(ex_minutes - target_minutes)
            if diff < best_diff:
                best_diff = diff
                best_idx = i
        except Exception:
            continue

    # Calculate page (1-indexed, showing most recent first)
    total = len(exchanges)
    # Position from end (most recent = 0)
    pos_from_end = total - 1 - best_idx
    page = (pos_from_end // PAGE_SIZE) + 1
    return page


def search_exchanges(exchanges: List[Dict], keyword: str) -> List[Dict]:
    """Search exchanges for keyword in preview."""
    keyword_lower = keyword.lower()
    return [ex for ex in exchanges if keyword_lower in ex.get('preview', '').lower()]


def format_page(
    exchanges: List[Dict],
    page: int,
    total_exchanges: int,
    session_start: str
) -> str:
    """Format a page of exchanges as markdown."""
    total_pages = (total_exchanges + PAGE_SIZE - 1) // PAGE_SIZE

    if not exchanges:
        return "*No exchanges found in this session.*"

    # Calculate slice for this page (most recent first)
    # Page 1 = most recent PAGE_SIZE exchanges
    start_from_end = (page - 1) * PAGE_SIZE
    end_from_end = start_from_end + PAGE_SIZE

    # Get slice in reverse order (most recent first)
    page_exchanges = list(reversed(exchanges))
    page_slice = page_exchanges[start_from_end:end_from_end]

    if not page_slice:
        return f"*Page {page} is empty. Total pages: {total_pages}*"

    # Build output
    lines = []
    lines.append(f"**Session started:** {format_date(session_start)} ({total_exchanges} exchanges)")
    lines.append("")
    lines.append(f"**Showing page {page} of {total_pages}** (most recent first):")
    lines.append("")

    for ex in page_slice:
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
    lines.append("- Jump to time: e.g., \"around 2pm\"")
    lines.append("- Search: e.g., \"search authentication\"")

    return "\n".join(lines)


def format_search_results(results: List[Dict], keyword: str, total_exchanges: int) -> str:
    """Format search results as markdown."""
    if not results:
        return f"*No exchanges found matching \"{keyword}\"*"

    lines = []
    lines.append(f"**Search results for \"{keyword}\":** ({len(results)} matches)")
    lines.append("")

    # Show up to 20 results
    for ex in results[:20]:
        idx = ex.get('idx', '?')
        time = format_timestamp(ex.get('timestamp', ''))
        preview = ex.get('preview', '(no preview)')
        lines.append(f"**#{idx}** [{time}] \"{preview}\"")

    if len(results) > 20:
        lines.append(f"*... and {len(results) - 20} more matches*")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description='Display conversation index')
    parser.add_argument('--page', type=int, default=1, help='Page number (1-indexed)')
    parser.add_argument('--around', type=str, help='Show exchanges around a time')
    parser.add_argument('--search', type=str, help='Search for keyword')

    args = parser.parse_args()

    index = load_index()

    if not index:
        print("*No conversation index found. The recall hook may not be active yet.*")
        print("*Try sending another message first, then run /recall again.*")
        return

    exchanges = index.get('exchanges', [])
    total_exchanges = index.get('total_exchanges', len(exchanges))
    session_start = index.get('session_start', '')

    if not exchanges:
        print("*No exchanges found in the current session.*")
        return

    # Handle search
    if args.search:
        results = search_exchanges(exchanges, args.search)
        print(format_search_results(results, args.search, total_exchanges))
        return

    # Handle time-based navigation
    page = args.page
    if args.around:
        target_time = parse_time_query(args.around)
        if target_time:
            page = find_page_for_time(exchanges, target_time)
        else:
            print(f"*Could not parse time: {args.around}. Try formats like '2:30pm' or '14:30'*")
            return

    print(format_page(exchanges, page, total_exchanges, session_start))


if __name__ == '__main__':
    main()
