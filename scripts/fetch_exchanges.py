#!/usr/bin/env python3
"""Fetch full exchange content using intuitive arguments.

This script reads the transcript to get full content for exchanges
based on user-friendly queries.

Usage:
    python3 fetch_exchanges.py last5              # Fetch last 5 exchanges
    python3 fetch_exchanges.py last10             # Fetch last 10 exchanges
    python3 fetch_exchanges.py around 2pm         # Fetch exchanges around 2pm
    python3 fetch_exchanges.py around "2:30 pm"   # Fetch exchanges around 2:30pm
    python3 fetch_exchanges.py search auth        # Search for "auth" in exchanges
    python3 fetch_exchanges.py search "PAI dim"   # Search for phrase

Output is formatted markdown with full content (with truncation limits).
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Set, Optional


# Configuration
MAX_CHARS_PER_MESSAGE = 1000
MAX_TOTAL_CHARS = 8000
AROUND_TIME_WINDOW = 5  # Number of exchanges to show around a time match


def load_index() -> Dict:
    """Load the conversation index."""
    index_file = Path.home() / '.claude' / 'context-recall' / 'index.json'

    if not index_file.exists():
        return {}

    try:
        with open(index_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def extract_text_content(message: Dict[str, Any]) -> str:
    """Extract text content from a message object."""
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


def truncate_text(text: str, max_chars: int = MAX_CHARS_PER_MESSAGE) -> str:
    """Truncate text to max_chars, adding indicator if truncated."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[...truncated...]"


def parse_time_query(time_str: str) -> Optional[datetime]:
    """Parse a time query like '2:30pm', '14:30', '3pm'."""
    time_str = time_str.lower().strip()

    # Try various formats
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
            # Use today's date
            today = datetime.now()
            return parsed.replace(year=today.year, month=today.month, day=today.day)
        except ValueError:
            continue

    return None


def find_exchanges_around_time(exchanges: List[Dict], target_time: datetime) -> Set[int]:
    """Find exchange indices around a target time."""
    if not exchanges:
        return set()

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

    # Get window of exchanges around the best match
    start = max(0, best_idx - AROUND_TIME_WINDOW // 2)
    end = min(len(exchanges), best_idx + AROUND_TIME_WINDOW // 2 + 1)

    return {exchanges[i]['idx'] for i in range(start, end)}


def search_exchanges(exchanges: List[Dict], keyword: str) -> Set[int]:
    """Search exchanges for keyword in preview, return matching indices."""
    keyword_lower = keyword.lower()
    matching = set()

    for ex in exchanges:
        if keyword_lower in ex.get('preview', '').lower():
            matching.add(ex['idx'])

    return matching


def parse_last_n(arg: str, total_exchanges: int) -> Set[int]:
    """Parse lastN argument into set of exchange indices."""
    if arg.lower().startswith('last'):
        try:
            n = int(arg[4:])
            start = max(1, total_exchanges - n + 1)
            return set(range(start, total_exchanges + 1))
        except ValueError:
            pass
    return set()


def fetch_exchanges_from_transcript(
    transcript_path: str,
    target_indices: Set[int]
) -> List[Dict]:
    """Read transcript and extract specified exchanges."""
    if not transcript_path or not os.path.exists(transcript_path):
        return []

    # Parse all messages
    messages = []
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    role = entry.get('type', '') or entry.get('role', '')
                    if role not in ('user', 'assistant'):
                        message_obj = entry.get('message', {})
                        role = message_obj.get('role', '')

                    if role in ('user', 'assistant'):
                        message_obj = entry.get('message', {})
                        text = extract_text_content(message_obj)
                        timestamp = entry.get('timestamp', '')
                        if text:
                            messages.append({
                                'role': role,
                                'text': text,
                                'timestamp': timestamp
                            })
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []

    # Build exchanges and filter to target indices
    exchanges = []
    i = 0
    exchange_idx = 1

    while i < len(messages):
        if messages[i]['role'] == 'user':
            user_msg = messages[i]
            if i + 1 < len(messages) and messages[i + 1]['role'] == 'assistant':
                assistant_msg = messages[i + 1]

                if exchange_idx in target_indices:
                    exchanges.append({
                        'idx': exchange_idx,
                        'user': user_msg['text'],
                        'assistant': assistant_msg['text'],
                        'timestamp': user_msg.get('timestamp', '')
                    })

                exchange_idx += 1
                i += 2
            else:
                i += 1
        else:
            i += 1

    return sorted(exchanges, key=lambda x: x['idx'])


def format_timestamp(iso_timestamp: str) -> str:
    """Format ISO timestamp as human-readable."""
    if not iso_timestamp:
        return ""

    try:
        dt = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        local_dt = dt.astimezone()
        return local_dt.strftime("%-I:%M %p").lower()
    except Exception:
        return ""


def format_exchanges(exchanges: List[Dict], query_type: str = "") -> str:
    """Format exchanges as markdown for recall."""
    if not exchanges:
        return "*No exchanges found.*"

    output = []
    total_chars = 0

    for ex in exchanges:
        idx = ex.get('idx', '?')
        time = format_timestamp(ex.get('timestamp', ''))
        time_str = f" [{time}]" if time else ""

        user_text = truncate_text(ex['user'], MAX_CHARS_PER_MESSAGE)
        assistant_text = truncate_text(ex['assistant'], MAX_CHARS_PER_MESSAGE)

        exchange_chars = len(user_text) + len(assistant_text)
        if total_chars + exchange_chars > MAX_TOTAL_CHARS:
            output.append(f"\n*[Reached size limit - {len(exchanges) - len(output)} more exchanges not shown]*")
            break

        output.append(f"### Exchange #{idx}{time_str}")
        output.append("")
        output.append(f"**User:**\n{user_text}")
        output.append("")
        output.append(f"**Assistant:**\n{assistant_text}")
        output.append("")
        output.append("---")
        output.append("")

        total_chars += exchange_chars

    return "\n".join(output)


def print_usage():
    """Print usage information."""
    print("**Usage:**")
    print("- `/recall last5` - Recall last 5 exchanges")
    print("- `/recall last10` - Recall last 10 exchanges")
    print("- `/recall around 2pm` - Recall exchanges around 2pm")
    print("- `/recall search keyword` - Search for exchanges containing keyword")
    print("")
    print("Or just run `/recall` for the interactive menu.")


def main():
    args = sys.argv[1:]

    if not args:
        # Default to last5
        args = ['last5']

    index = load_index()

    if not index:
        print("*No conversation index found. The recall hook may not be active yet.*")
        print("*Try sending another message first, then run /recall again.*")
        return

    total_exchanges = index.get('total_exchanges', 0)
    transcript_path = index.get('transcript_path', '')
    exchanges_index = index.get('exchanges', [])

    if total_exchanges == 0:
        print("*No exchanges found in the current session.*")
        return

    target_indices = set()
    query_type = ""

    # Parse arguments
    first_arg = args[0].lower()

    # Handle "lastN" format
    if first_arg.startswith('last'):
        target_indices = parse_last_n(first_arg, total_exchanges)
        query_type = first_arg
        if not target_indices:
            print(f"*Invalid format: {first_arg}. Try 'last5' or 'last10'.*")
            return

    # Handle "around TIME" format
    elif first_arg == 'around':
        if len(args) < 2:
            print("*Please specify a time, e.g., 'around 2pm' or 'around 14:30'*")
            return
        time_str = ' '.join(args[1:])
        target_time = parse_time_query(time_str)
        if not target_time:
            print(f"*Could not parse time: '{time_str}'. Try formats like '2pm', '2:30pm', or '14:30'*")
            return
        target_indices = find_exchanges_around_time(exchanges_index, target_time)
        query_type = f"around {time_str}"
        if not target_indices:
            print(f"*No exchanges found around {time_str}*")
            return

    # Handle "search KEYWORD" format
    elif first_arg == 'search':
        if len(args) < 2:
            print("*Please specify a search term, e.g., 'search authentication'*")
            return
        keyword = ' '.join(args[1:])
        target_indices = search_exchanges(exchanges_index, keyword)
        query_type = f"search '{keyword}'"
        if not target_indices:
            print(f"*No exchanges found matching '{keyword}'*")
            return
        # Limit search results to prevent overwhelming output
        if len(target_indices) > 10:
            # Take only the 10 most recent matches
            sorted_indices = sorted(target_indices, reverse=True)[:10]
            target_indices = set(sorted_indices)
            print(f"*Found many matches for '{keyword}', showing 10 most recent:*\n")

    else:
        # Unknown format
        print(f"*Unknown command: '{first_arg}'*\n")
        print_usage()
        return

    # Fetch the exchanges
    exchanges = fetch_exchanges_from_transcript(transcript_path, target_indices)

    if not exchanges:
        print(f"*Could not fetch exchanges from transcript.*")
        return

    print(f"*Fetched {len(exchanges)} exchange(s) ({query_type}):*\n")
    print(format_exchanges(exchanges, query_type))


if __name__ == '__main__':
    main()
