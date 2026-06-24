#!/usr/bin/env python3
"""Highlight creation system for Claude Context Recall plugin.

Two paths:
  - Explicit: Claude runs `highlight.py <session_id> "summary"` via slash-command.
  - Auto-detection: opt-in heuristic that scans assistant text for solution signals.

Pure stdlib — no external dependencies.
"""

import argparse
import re
import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent))
from db import (get_connection, insert_highlight, get_tags, get_exchanges,
                get_session_config)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOLUTION_SIGNALS = [
    "the fix is", "this works because", "the solution",
    "try using", "the issue was", "the problem was",
    "resolved by", "fixed by", "the answer is",
]
SOLUTION_SIGNAL_THRESHOLD = 2
HIGHLIGHT_SUMMARY_MAX_CHARS = 100
MIN_WORD_COUNT_FOR_AUTO = 25  # Reject short/generic responses to avoid false positives

# ---------------------------------------------------------------------------
# Explicit highlight creation
# ---------------------------------------------------------------------------


def create_highlight(conn: sqlite3.Connection, session_id: str, summary: str,
                     exchange_idx: Optional[int] = None) -> str:
    """Create a highlight explicitly (Claude-triggered).

    Fetches the session's auto-tags, builds a comma-joined tags string, then
    inserts the highlight with source='explicit'.  If no exchange_idx is given
    the latest exchange index for the session is used.

    Args:
        conn: SQLite connection.
        session_id: The active session ID.
        summary: Human-readable summary of the insight to capture.
        exchange_idx: Optional exchange index to anchor the highlight.

    Returns:
        Confirmation message: *Highlighted: "<summary>" [<tags>]*
    """
    # Resolve exchange_idx if not provided
    if exchange_idx is None:
        exchanges = get_exchanges(conn, session_id)
        if exchanges:
            exchange_idx = exchanges[-1]['idx']

    # Build tags string from session tags
    tag_rows = get_tags(conn, session_id)
    tags = ', '.join(row['tag'] for row in tag_rows)

    insert_highlight(conn, session_id, summary, tags,
                     source='explicit', exchange_idx=exchange_idx)

    return f'*Highlighted: "{summary}" [{tags}]*'


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------


def detect_solution_signals(text: str) -> int:
    """Count how many distinct SOLUTION_SIGNALS appear in text (case-insensitive).

    Each signal is counted at most once regardless of how many times it appears.
    Returns a count of distinct matching signals, not total occurrences.

    Args:
        text: Assistant response text to scan.

    Returns:
        Number of distinct signal phrases found.
    """
    lowered = text.lower()
    count = 0
    for signal in SOLUTION_SIGNALS:
        if signal in lowered:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------


def auto_detect_highlights(conn: sqlite3.Connection, session_id: str,
                            new_exchanges: List[Dict],
                            commit: bool = True) -> List[str]:
    """Scan new exchanges for solution signals and auto-create highlights.

    Only runs when the session's 'auto_highlight' config key is truthy.
    For each exchange, if the assistant text contains >= SOLUTION_SIGNAL_THRESHOLD
    distinct signals a highlight is created with source='auto'.

    The summary is the first HIGHLIGHT_SUMMARY_MAX_CHARS characters of the
    assistant text (whitespace collapsed), with '...' appended if truncated.

    Args:
        conn: SQLite connection.
        session_id: The active session ID.
        new_exchanges: List of exchange dicts (same structure as db.get_exchanges).

    Returns:
        List of summary strings for highlights that were created (for logging).
        Returns an empty list when auto_highlight is off or no signals fire.
    """
    if not get_session_config(conn, session_id, 'auto_highlight'):
        return []

    tag_rows = get_tags(conn, session_id)
    tags = ', '.join(row['tag'] for row in tag_rows)

    created_summaries: List[str] = []

    for exchange in new_exchanges:
        assistant_text = exchange.get('assistant_text') or ''
        if not assistant_text:
            continue

        # Skip short responses — they're unlikely to contain real findings
        word_count = len(assistant_text.split())
        if word_count < MIN_WORD_COUNT_FOR_AUTO:
            continue

        signal_count = detect_solution_signals(assistant_text)
        if signal_count < SOLUTION_SIGNAL_THRESHOLD:
            continue

        # Build summary: collapse whitespace, then truncate
        # Include exchange idx in summary to avoid collisions when multiple
        # exchanges produce similar assistant text
        cleaned = re.sub(r'\s+', ' ', assistant_text).strip()
        exchange_idx = exchange.get('idx')
        prefix = f"[#{exchange_idx}] " if exchange_idx is not None else ""
        max_text = HIGHLIGHT_SUMMARY_MAX_CHARS - len(prefix)
        if len(cleaned) > max_text:
            summary = prefix + cleaned[:max_text] + '...'
        else:
            summary = prefix + cleaned

        insert_highlight(conn, session_id, summary, tags,
                         source='auto', exchange_idx=exchange_idx,
                         commit=commit)
        created_summaries.append(summary)

    return created_summaries


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _non_negative_int(raw: str) -> int:
    """argparse type for --exchange: a non-negative integer.

    Raises argparse.ArgumentTypeError for non-int or negative values so the
    parser emits a clean error (exit code 2) instead of an uncaught ValueError.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"invalid int value: {raw!r}")
    if value < 0:
        raise argparse.ArgumentTypeError(
            f"exchange index must be >= 0, got {value}")
    return value


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the highlight CLI.

    Positional: session_id, summary.
    Optional:   --exchange (non-negative int).

    Using argparse makes -h/--help safe (it exits before any DB access) and
    turns a bad --exchange value into a clean error instead of a crash.
    """
    parser = argparse.ArgumentParser(
        prog="highlight.py",
        description="Create an explicit highlight for a Claude Context Recall session.",
    )
    parser.add_argument("session_id", help="The active session ID.")
    parser.add_argument("summary", help="Human-readable summary of the insight.")
    parser.add_argument(
        "--exchange",
        type=_non_negative_int,
        default=None,
        metavar="IDX",
        help="Exchange index to anchor the highlight (must be >= 0).",
    )
    return parser


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments without touching the database.

    Args:
        argv: Argument list (excluding the program name). Defaults to sys.argv[1:].

    Returns:
        Parsed namespace with session_id, summary, and exchange attributes.

    Raises:
        SystemExit: on -h/--help (code 0) or invalid arguments (code 2).
    """
    return build_arg_parser().parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    """CLI: highlight.py <session_id> <summary> [--exchange IDX]"""
    args = parse_args(argv)

    conn = get_connection()
    try:
        result = create_highlight(conn, args.session_id, args.summary,
                                  args.exchange)
        print(result)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
