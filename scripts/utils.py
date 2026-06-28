#!/usr/bin/env python3
"""Shared utilities for the recall plugin.

This module contains common functions used across multiple scripts
to avoid code duplication.
"""

import hashlib
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple


# Configuration constants
PREVIEW_LENGTH = 80
MAX_CHARS_PER_MESSAGE = 1000
MAX_TOTAL_CHARS = 8000
PAGE_SIZE = 20
AROUND_TIME_WINDOW = 5

# File paths
LOG_FILE = Path.home() / '.claude' / 'recall-events.log'


def compute_project_hash(project_path: str) -> str:
    """Stable 16-char hash identifying a project by its filesystem path.

    Used to group sessions by project for cross-project recall. Both the
    SessionStart hook and the UserPromptSubmit hook derive this from the same
    ``cwd`` value, so a given project always maps to the same hash. An empty
    path yields ``''`` (never a hash of the empty string).
    """
    if not project_path:
        return ''
    normalized = os.path.normpath(os.path.expanduser(project_path))
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:16]


def resolve_session_id(explicit: str = '') -> str:
    """Resolve the current session id — concurrency-safe.

    Precedence:
      1. an explicit value (e.g. a ``--session`` CLI arg),
      2. ``CLAUDE_CODE_SESSION_ID`` — injected by Claude Code into every command
         subprocess, so it is correct PER session even when several sessions run
         concurrently (the fix for "recall returns another session's id"),
      3. ``RECALL_SESSION_ID`` — legacy fallback written by the SessionStart hook,
         for Claude Code versions that don't provide the native variable.

    Returns ``''`` if none is available (callers should fail safe — never guess
    another session).
    """
    return (explicit
            or os.environ.get('CLAUDE_CODE_SESSION_ID', '')
            or os.environ.get('RECALL_SESSION_ID', ''))


def resolve_project_hash(explicit: str = '') -> str:
    """Resolve the current project hash.

    Precedence: explicit value > ``RECALL_PROJECT_HASH`` env > derived from the
    current working directory (commands run with the project root as cwd).
    """
    return (explicit
            or os.environ.get('RECALL_PROJECT_HASH', '')
            or compute_project_hash(os.getcwd()))


def extract_text_content(message: Dict[str, Any]) -> str:
    """Extract text content from a message object.

    Handles both string content and array content formats.
    """
    content = message.get('content', [])
    if isinstance(content, str):
        return content

    text_parts = []
    for item in content:
        if isinstance(item, dict) and item.get('type') == 'text':
            text_parts.append(item.get('text', ''))
        elif isinstance(item, str):
            text_parts.append(item)

    return '\n'.join(text_parts)


def make_preview(text: str, max_length: int = PREVIEW_LENGTH) -> str:
    """Create a short preview of text for the index."""
    text = ' '.join(text.split())
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + '...'


def truncate_text(text: str, max_chars: int = MAX_CHARS_PER_MESSAGE) -> str:
    """Truncate text to max_chars, adding indicator if truncated."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[...truncated...]"


def parse_time_query(time_str: str) -> Optional[datetime]:
    """Parse a time query like '2:30pm', '14:30', 'around 3pm'.

    Returns a datetime with today's date and the parsed time.
    """
    time_str = time_str.lower().strip()
    time_str = re.sub(r'^around\s+', '', time_str)

    formats = [
        "%I:%M%p",   # 2:30pm
        "%I:%M %p",  # 2:30 pm
        "%I%p",      # 2pm
        "%I %p",     # 2 pm
        "%H:%M",     # 14:30
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(time_str, fmt)
            today = datetime.now()
            return parsed.replace(year=today.year, month=today.month, day=today.day)
        except ValueError:
            continue

    return None


def parse_date_time_query(time_str: str, reference_dates: List[str] = None) -> Optional[Tuple[datetime, Optional[str]]]:
    """Parse a time query with optional date awareness.

    Args:
        time_str: Time string like '2pm', '2pm yesterday', 'jan 5 2pm'
        reference_dates: List of ISO date strings from the session to help resolve ambiguity

    Returns:
        Tuple of (datetime, matched_date_str) or None if parsing fails
    """
    time_str = time_str.lower().strip()
    time_str = re.sub(r'^around\s+', '', time_str)

    # Check for relative date keywords
    target_date = None
    if 'yesterday' in time_str:
        target_date = datetime.now().date() - timedelta(days=1)
        time_str = time_str.replace('yesterday', '').strip()
    elif 'today' in time_str:
        target_date = datetime.now().date()
        time_str = time_str.replace('today', '').strip()

    # Check for date patterns like "jan 5" or "1/5"
    date_patterns = [
        (r'(\w{3})\s+(\d{1,2})', '%b %d'),  # jan 5
        (r'(\d{1,2})/(\d{1,2})', '%m/%d'),   # 1/5
    ]

    current_year = datetime.now().year
    for pattern, date_fmt in date_patterns:
        match = re.search(pattern, time_str)
        if match:
            try:
                date_str = match.group(0)
                # Add year to avoid Python 3.15 deprecation warning
                parsed_date = datetime.strptime(f"{date_str} {current_year}", f"{date_fmt} %Y")
                target_date = parsed_date.date()
                time_str = time_str.replace(date_str, '').strip()
                break
            except ValueError:
                continue

    # Parse the time portion
    parsed_time = parse_time_query(time_str)
    if not parsed_time:
        return None

    # Combine date and time
    if target_date:
        result = parsed_time.replace(year=target_date.year, month=target_date.month, day=target_date.day)
        return (result, target_date.isoformat())

    return (parsed_time, None)


def format_timestamp(iso_timestamp: str) -> str:
    """Format ISO timestamp as human-readable time (e.g., '2:30 pm')."""
    if not iso_timestamp:
        return ""

    try:
        dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        local_dt = dt.astimezone()
        return local_dt.strftime("%-I:%M %p").lower()
    except (ValueError, TypeError):
        return ""


def format_date(iso_timestamp: str) -> str:
    """Format ISO timestamp as human-readable date (e.g., 'Jan 5, 2026 at 2:30 PM')."""
    if not iso_timestamp:
        return "Unknown date"

    try:
        dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        local_dt = dt.astimezone()
        return local_dt.strftime("%b %d, %Y at %-I:%M %p")
    except (ValueError, TypeError):
        return "Unknown date"


def format_short_date(iso_timestamp: str) -> str:
    """Format ISO timestamp as short date (e.g., 'Jan 5')."""
    if not iso_timestamp:
        return ""

    try:
        dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        local_dt = dt.astimezone()
        return local_dt.strftime("%b %-d")
    except (ValueError, TypeError):
        return ""


def get_date_from_timestamp(iso_timestamp: str) -> Optional[str]:
    """Extract just the date portion from an ISO timestamp."""
    if not iso_timestamp:
        return None
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        return dt.date().isoformat()
    except (ValueError, TypeError):
        return None


def find_exchanges_by_time(
    exchanges: List[Dict],
    target_time: datetime,
    target_date: Optional[str] = None,
    window: int = AROUND_TIME_WINDOW
) -> List[int]:
    """Find exchange indices around a target time.

    Args:
        exchanges: List of exchange dicts with 'timestamp' and 'idx' keys
        target_time: Target datetime to search around
        target_date: Optional specific date (ISO format) to match
        window: Number of exchanges to return around the match

    Returns:
        List of exchange indices
    """
    if not exchanges:
        return []

    # If target_date specified, filter to that date first
    if target_date:
        date_matches = [
            (i, ex) for i, ex in enumerate(exchanges)
            if get_date_from_timestamp(ex.get('timestamp', '')) == target_date
        ]
        if not date_matches:
            # No exact date match, fall back to time-only matching
            pass
        else:
            # Find closest time within that date
            best_idx = 0
            best_diff = float('inf')

            for list_idx, (orig_idx, ex) in enumerate(date_matches):
                try:
                    ex_time = datetime.fromisoformat(ex['timestamp'].replace('Z', '+00:00'))
                    ex_minutes = ex_time.hour * 60 + ex_time.minute
                    target_minutes = target_time.hour * 60 + target_time.minute
                    diff = abs(ex_minutes - target_minutes)
                    if diff < best_diff:
                        best_diff = diff
                        best_idx = list_idx
                except Exception:
                    continue

            # Return window around the match within date_matches
            start = max(0, best_idx - window // 2)
            end = min(len(date_matches), best_idx + window // 2 + 1)
            return [date_matches[i][1]['idx'] for i in range(start, end)]

    # Time-only matching (original behavior)
    best_idx = 0
    best_diff = float('inf')

    for i, ex in enumerate(exchanges):
        try:
            ex_time = datetime.fromisoformat(ex['timestamp'].replace('Z', '+00:00'))
            ex_minutes = ex_time.hour * 60 + ex_time.minute
            target_minutes = target_time.hour * 60 + target_time.minute
            diff = abs(ex_minutes - target_minutes)
            if diff < best_diff:
                best_diff = diff
                best_idx = i
        except Exception:
            continue

    start = max(0, best_idx - window // 2)
    end = min(len(exchanges), best_idx + window // 2 + 1)
    return [exchanges[i]['idx'] for i in range(start, end)]


def search_in_text(text: str, keyword: str) -> bool:
    """Check if keyword exists in text (case-insensitive).

    Returns False if either text or keyword is empty.
    """
    if not text or not keyword:
        return False
    return keyword.lower() in text.lower()
