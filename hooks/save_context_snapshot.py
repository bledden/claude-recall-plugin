#!/usr/bin/env python3
"""Save context snapshots and build conversation index for the /recall command.

This hook runs on every UserPromptSubmit event. It:
1. Reads the transcript from transcript_path (available in hook input)
2. Builds a full timestamped index of all exchanges (for browsing)
3. Extracts the last N exchanges with full content (for quick recall)
4. Saves both to ~/.claude/context-recall/
5. Logs /recall events for observability

This allows the /recall command to show an index for selection
and fetch full content on demand.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple


# Configuration
PREVIEW_LENGTH = 80  # Characters for index preview
MAX_RECENT_EXCHANGES = 5  # Full content exchanges to keep
MAX_CHARS_PER_MESSAGE = 1000  # Truncation limit for full content
MAX_TOTAL_CHARS = 8000  # Total size limit for full content


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


def make_preview(text: str, max_length: int = PREVIEW_LENGTH) -> str:
    """Create a short preview of text for the index."""
    # Remove newlines and extra whitespace
    text = ' '.join(text.split())
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + '...'


def parse_transcript_with_timestamps(transcript_path: str) -> List[Dict[str, Any]]:
    """Parse transcript file and extract messages with timestamps.

    Returns list of dicts with keys: role, text, timestamp
    """
    messages = []

    if not transcript_path or not os.path.exists(transcript_path):
        return messages

    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    # Role can be at top level (type field) or in message
                    role = entry.get('type', '') or entry.get('role', '')
                    if role not in ('user', 'assistant'):
                        # Try getting from message object
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
        pass

    return messages


def truncate_text(text: str, max_chars: int = MAX_CHARS_PER_MESSAGE) -> str:
    """Truncate text to max_chars, adding indicator if truncated."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[...truncated...]"


def build_exchange_index(messages: List[Dict[str, Any]]) -> Tuple[List[Dict], List[Dict]]:
    """Build full index of exchanges and extract recent ones with full content.

    Returns:
        Tuple of (full_index, recent_exchanges)
        - full_index: List of {idx, preview, timestamp} for all exchanges
        - recent_exchanges: List of {user, assistant, timestamp} for last N exchanges
    """
    # First pass: identify all user/assistant pairs
    all_exchanges = []
    i = 0
    exchange_idx = 1

    while i < len(messages):
        if messages[i]['role'] == 'user':
            user_msg = messages[i]
            # Look for following assistant message
            if i + 1 < len(messages) and messages[i + 1]['role'] == 'assistant':
                assistant_msg = messages[i + 1]
                all_exchanges.append({
                    'idx': exchange_idx,
                    'user_text': user_msg['text'],
                    'assistant_text': assistant_msg['text'],
                    'timestamp': user_msg.get('timestamp', '')
                })
                exchange_idx += 1
                i += 2
            else:
                i += 1
        else:
            i += 1

    # Build index with previews (user message only - best memory trigger)
    full_index = []
    for ex in all_exchanges:
        full_index.append({
            'idx': ex['idx'],
            'preview': make_preview(ex['user_text']),
            'timestamp': ex['timestamp']
        })

    # Extract recent exchanges with full content (with truncation)
    recent_exchanges = []
    total_chars = 0

    for ex in reversed(all_exchanges[-MAX_RECENT_EXCHANGES:]):
        user_text = truncate_text(ex['user_text'], MAX_CHARS_PER_MESSAGE)
        assistant_text = truncate_text(ex['assistant_text'], MAX_CHARS_PER_MESSAGE)

        exchange_chars = len(user_text) + len(assistant_text)
        if total_chars + exchange_chars > MAX_TOTAL_CHARS:
            break

        recent_exchanges.insert(0, {
            'idx': ex['idx'],
            'user': user_text,
            'assistant': assistant_text,
            'timestamp': ex['timestamp']
        })
        total_chars += exchange_chars

    return full_index, recent_exchanges


def save_index_and_snapshot(
    session_id: str,
    transcript_path: str,
    full_index: List[Dict],
    recent_exchanges: List[Dict]
) -> None:
    """Save both the full index and recent exchanges snapshot."""
    snapshot_dir = Path.home() / '.claude' / 'context-recall'
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()

    # Determine session start from first exchange
    session_start = full_index[0]['timestamp'] if full_index else now

    # Full index for browsing
    index_data = {
        'session_id': session_id,
        'session_start': session_start,
        'updated_at': now,
        'total_exchanges': len(full_index),
        'transcript_path': transcript_path,
        'exchanges': full_index
    }

    # Recent exchanges for quick recall (backward compatible)
    snapshot_data = {
        'session_id': session_id,
        'timestamp': now,
        'message_count': len(full_index) * 2,  # Approximate message count
        'exchanges': [
            {'user': ex['user'], 'assistant': ex['assistant']}
            for ex in recent_exchanges
        ]
    }

    # Save index
    try:
        index_file = snapshot_dir / 'index.json'
        with open(index_file, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2)
    except Exception:
        pass

    # Save session-specific index
    try:
        session_index_file = snapshot_dir / f'{session_id}_index.json'
        with open(session_index_file, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, indent=2)
    except Exception:
        pass

    # Save current snapshot (backward compatible)
    try:
        current_file = snapshot_dir / 'current.json'
        with open(current_file, 'w', encoding='utf-8') as f:
            json.dump(snapshot_data, f, indent=2)
    except Exception:
        pass


def log_recall_event(session_id: str, exchange_count: int) -> None:
    """Log recall event for observability."""
    timestamp = datetime.now(timezone.utc).isoformat()
    log_entry = f"{timestamp} | session={session_id} | exchanges={exchange_count} | CONTEXT_RECALL_TRIGGERED\n"

    print(f"[context-recall] Context recall triggered at exchange #{exchange_count}", file=sys.stderr)

    log_dir = Path.home() / '.claude'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / 'recall-events.log'

    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    except Exception:
        pass


def main():
    """Main entry point for the hook."""
    try:
        input_data = json.load(sys.stdin)

        session_id = input_data.get('session_id', 'unknown')
        transcript_path = input_data.get('transcript_path', '')
        user_prompt = input_data.get('user_prompt', '')

        # Parse transcript with timestamps
        messages = parse_transcript_with_timestamps(transcript_path)

        # Build index and extract recent exchanges
        full_index, recent_exchanges = build_exchange_index(messages)

        # Save everything
        save_index_and_snapshot(session_id, transcript_path, full_index, recent_exchanges)

        # Check if this is a /recall command
        if user_prompt.strip().lower().startswith('/recall'):
            log_recall_event(session_id, len(full_index))
            result = {
                "systemMessage": f"[Observability] Context recall logged at exchange #{len(full_index)}"
            }
        else:
            result = {}

        print(json.dumps(result), file=sys.stdout)

    except Exception as e:
        error_output = {
            "systemMessage": f"[context-recall] Hook error (non-blocking): {str(e)}"
        }
        print(json.dumps(error_output), file=sys.stdout)

    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
