#!/usr/bin/env python3
"""SQLite-backed prompt submit hook for Claude Context Recall plugin v2.

Runs on every UserPromptSubmit event.  Reads JSON from stdin, incrementally
indexes new exchanges into SQLite, runs auto-tagging, and handles v1->v2
migration from the legacy JSON index.

The incremental indexer (``index_transcript``) lives here and is shared with
the Stop and SessionEnd hooks, so a turn is captured as soon as it completes
and the final turn of a session is never lost.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from utils import (extract_text_content, make_preview, truncate_text,
                   compute_project_hash, MAX_CHARS_PER_MESSAGE, MAX_ASSISTANT_CHARS)
from db import (get_connection, insert_session, get_session, insert_exchanges,
                update_session_offset, get_exchanges, insert_tag, DB_PATH,
                get_connections, get_highlights, update_connection_check,
                get_exchange_count, get_session_config)
from auto_tagger import compute_auto_tags
from highlight import auto_detect_highlights


# ---------------------------------------------------------------------------
# Proactive recall suggestion — the deterministic counterpart to the skill's
# "explicit context-loss" detection. Running it from the hook means it fires
# every time the pattern appears, instead of depending on the model noticing.
# Gated on the per-session skill_enabled config (opt-in, default off).
# ---------------------------------------------------------------------------

_CONTEXT_LOSS_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"did\s*n['’]?t we (?:already )?(?:discuss|talk about|cover|go over)",
    r"what was that (?:thing )?(?:about|called)",
    r"earlier you (?:said|mentioned)",
    r"we (?:discussed|talked about|covered) (?:this|that|it)(?: before| earlier| already)?",
    r"remind me (?:what|how|about|again)",
    r"you mentioned (?:something )?(?:about )?",
    r"as we (?:discussed|talked about)",
    r"weren['’]?t we (?:working on|talking about)",
)]


def _maybe_suggest_recall(conn, session_id, user_prompt):
    """Return a recall suggestion when the prompt shows an explicit context-loss
    signal AND the recall-assistant skill is enabled for this session, else None.
    """
    if not user_prompt:
        return None
    if not get_session_config(conn, session_id, 'skill_enabled'):
        return None
    if any(p.search(user_prompt) for p in _CONTEXT_LOSS_PATTERNS):
        return ("[Recall] That sounds like earlier context — you can run "
                "`/recall search <topic>` or `/recall last10` to recover it.")
    return None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOG_FILE = Path.home() / '.claude' / 'recall-events.log'

# Per-invocation catch-up caps. Kept small so one hook run fits well within the
# 10s timeout: a killed hook commits nothing, so an oversized read on a huge,
# actively-growing transcript would wedge the byte offset forever (it re-reads
# the same chunk and dies every prompt). Small reads bank progress each prompt
# and converge.
MAX_BYTES_PER_READ = 2 * 1024 * 1024      # 2 MB
MAX_MESSAGES_PER_READ = 1000
LEGACY_INDEX_FILE = Path.home() / '.claude' / 'context-recall' / 'index.json'

# Module-level flag: skip the filesystem stat on every prompt after first check
_migration_checked = False


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

def parse_transcript_from_offset(
    transcript_path: str,
    byte_offset: int = 0,
) -> Tuple[List[Dict], int]:
    """Parse transcript JSONL starting from a byte offset.

    Opens the file, seeks to *byte_offset*, and reads every subsequent
    JSONL line that contains a user or assistant message.

    Args:
        transcript_path: Path to the JSONL transcript file.
        byte_offset: Position to seek to before reading.

    Returns:
        Tuple of (messages, new_byte_offset) where each message is a dict
        with keys: role, text, timestamp.
    """
    messages: List[Dict] = []
    new_offset = byte_offset

    if not transcript_path or not os.path.exists(transcript_path):
        return messages, new_offset

    try:
        with open(transcript_path, 'rb') as f:
            if byte_offset > 0:
                f.seek(byte_offset)

            consumed = 0
            for line_bytes in f:
                # Stop BEFORE consuming a line that would exceed the caps, so it
                # is re-read next time rather than skipped (the old code broke
                # AFTER consuming, then used a buffered f.tell() that lost the
                # unread tail). Advance the durable offset only past lines we
                # actually consumed, so a timeout still banks progress.
                if (consumed + len(line_bytes) > MAX_BYTES_PER_READ
                        or len(messages) >= MAX_MESSAGES_PER_READ):
                    break
                line_start = byte_offset + consumed
                consumed += len(line_bytes)
                new_offset = byte_offset + consumed
                try:
                    line = line_bytes.decode('utf-8').strip()
                except UnicodeDecodeError:
                    continue

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
                                'timestamp': timestamp,
                                # byte span of this line: lets the indexer resume
                                # exactly at a held-back (incomplete) turn
                                '_start': line_start,
                                '_end': new_offset,
                            })
                except json.JSONDecodeError:
                    continue

    except Exception as e:
        print(f"[context-recall] Transcript parse error: {e}", file=sys.stderr)

    return messages, new_offset


# ---------------------------------------------------------------------------
# Exchange building
# ---------------------------------------------------------------------------

def build_new_exchanges(
    messages: List[Dict],
    start_idx: int = 1,
) -> List[Dict]:
    """Pair each user message with ALL the assistant text that follows it.

    In an agentic turn Claude's reply arrives as several assistant text blocks
    separated by tool calls ("Let me look…", <tool>, "Found it…", <tool>, the
    actual answer). Pairing a user message with only the *next* assistant
    message kept the preamble and dropped the answer — 71% of assistant text on
    a real transcript. Every assistant message up to the next user message is
    now merged (blank-line separated) into one ``assistant_text``.

    Each exchange dict has: idx, preview, timestamp, user_text, assistant_text.
    A user message with no assistant reply is skipped; assistant messages that
    precede any user message are skipped.

    Args:
        messages: List of message dicts with role, text, timestamp.
        start_idx: Starting exchange index number.

    Returns:
        List of exchange dicts.
    """
    exchanges: List[Dict] = []
    exchange_idx = start_idx
    i = 0
    n = len(messages)

    while i < n:
        if messages[i]['role'] != 'user':
            i += 1
            continue
        user_msg = messages[i]
        j = i + 1
        parts: List[str] = []
        while j < n and messages[j]['role'] == 'assistant':
            if messages[j]['text']:
                parts.append(messages[j]['text'])
            j += 1
        if parts:
            exchanges.append({
                'idx': exchange_idx,
                'preview': make_preview(user_msg['text']),
                'timestamp': user_msg.get('timestamp', ''),
                'user_text': truncate_text(user_msg['text'], MAX_CHARS_PER_MESSAGE),
                'assistant_text': truncate_text('\n\n'.join(parts), MAX_ASSISTANT_CHARS),
            })
            exchange_idx += 1
            i = j
        else:
            i += 1

    return exchanges


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

def migrate_from_json(conn, legacy_path: Path = None) -> None:
    """Migrate a v1 JSON index into the SQLite database.

    Reads the legacy ``index.json``, inserts its session and exchanges
    into the DB, then renames the file to ``index.json.migrated``.

    No-op if the legacy file does not exist.  After the first check the
    module-level ``_migration_checked`` flag is set so subsequent calls
    skip the Path.exists() stat entirely.

    Args:
        conn: SQLite connection.
        legacy_path: Override path for the legacy file.
    """
    global _migration_checked
    if _migration_checked:
        return

    if legacy_path is None:
        legacy_path = LEGACY_INDEX_FILE

    _migration_checked = True

    if not legacy_path.exists():
        return

    try:
        with open(legacy_path, 'r', encoding='utf-8') as f:
            legacy_data = json.load(f)

        session_id = legacy_data.get('session_id', 'migrated-unknown')
        started_at = legacy_data.get('session_start', datetime.now(timezone.utc).isoformat())
        transcript_path = legacy_data.get('transcript_path', '')

        # Insert session (IGNORE if already migrated in a previous run)
        insert_session(
            conn,
            session_id=session_id,
            project_path='',
            project_hash='',
            started_at=started_at,
            transcript_path=transcript_path,
        )

        # Insert exchanges
        exchanges = legacy_data.get('exchanges', [])
        if exchanges:
            insert_exchanges(conn, session_id, exchanges)
            update_session_offset(
                conn,
                session_id,
                byte_offset=legacy_data.get('_byte_offset', 0),
                exchange_count=len(exchanges),
            )

        # Rename legacy file so we never migrate twice
        migrated_path = legacy_path.with_suffix('.json.migrated')
        legacy_path.rename(migrated_path)

        print(f"[context-recall] Migrated {len(exchanges)} exchanges from v1 index", file=sys.stderr)

    except Exception as exc:
        print(f"[context-recall] Migration error (non-blocking): {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def log_recall_event(session_id: str, exchange_count: int) -> None:
    """Append a recall-triggered log line to LOG_FILE.

    Format:
        {timestamp} | session={id} | exchanges={count} | CONTEXT_RECALL_TRIGGERED
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    log_entry = f"{timestamp} | session={session_id} | exchanges={exchange_count} | CONTEXT_RECALL_TRIGGERED\n"

    print(f"[context-recall] Context recall triggered at exchange #{exchange_count}", file=sys.stderr)

    # RECALL_LOG_FILE override keeps tests/tooling out of the real event log.
    log_file = Path(os.environ.get('RECALL_LOG_FILE') or LOG_FILE)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    except Exception as e:
        print(f"[context-recall] Failed to write log: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Auto-tagging
# ---------------------------------------------------------------------------

def _store_auto_tags(conn, session_id: str, exchanges: List[Dict],
                     commit: bool = True) -> None:
    """Compute and persist auto-tags for a session's exchanges.

    Args:
        conn: SQLite connection.
        session_id: Session to tag.
        exchanges: Exchange dicts to derive tags from.
        commit: If True (default), commit after each tag insertion.
    """
    tags = compute_auto_tags(exchanges)
    for tag in tags:
        insert_tag(conn, tag, session_id, exchange_idx=None, source='auto',
                   commit=commit)


# ---------------------------------------------------------------------------
# Connection checks
# ---------------------------------------------------------------------------

def _check_connections(conn, session_id: str) -> Optional[str]:
    """Check connections for new highlights. Returns system message or None.

    Iterates all connections for the session.  Connections in 'explicit' mode
    are skipped (the user checks manually via /recall inbox).  For 'decay'
    mode connections the counter is incremented each call; when it reaches the
    interval, highlights are fetched from the target session and a formatted
    message is assembled.  The counter then resets and the interval grows by 3
    (capped at 30) to create exponential back-off.

    Only connections with delivery_mode == 'inject' produce a returned message;
    'silent' connections still update their counter but return nothing.

    Args:
        conn: SQLite connection.
        session_id: The watcher session ID.

    Returns:
        A formatted multi-line string if there are new highlights to surface,
        or None if nothing should be injected.
    """
    connections = get_connections(conn, session_id)
    if not connections:
        return None

    messages = []
    for connection in connections:
        if connection['check_mode'] != 'decay':
            continue  # User checks manually via /recall inbox

        # Decay mode: increment counter
        counter = (connection['check_counter'] or 0) + 1
        interval = connection['check_interval'] or 7

        if counter >= interval:
            # Time to check
            last_checked = connection['last_checked_at']
            target_highlights = get_highlights(conn, connection['target_session'], since=last_checked)

            connection_messages = []
            if target_highlights:
                for h in target_highlights:
                    connection_messages.append(f'  - "{h["summary"]}" [{h["tags"]}]')

                if connection_messages and connection['delivery_mode'] == 'inject':
                    topic = connection['topic']
                    target_id = connection['target_session'][:8]
                    connection_messages.insert(
                        0,
                        f'[Cross-session] New from session {target_id}... ({topic}):',
                    )
                    connection_messages.append(
                        f'Use /recall search <keyword> --session {connection["target_session"]}'
                        ' for full context.'
                    )
                    messages.extend(connection_messages)

            # Reset counter, grow interval (decay back-off)
            now = datetime.now(timezone.utc).isoformat()
            new_interval = min(30, interval + 3)
            update_connection_check(conn, connection['id'], 0, new_interval, now,
                                    commit=False)
        else:
            # Just increment counter, preserve last_checked_at
            update_connection_check(
                conn,
                connection['id'],
                counter,
                interval,
                connection['last_checked_at'],
                commit=False,
            )

    if not messages:
        return None

    return '\n'.join(messages)


# ---------------------------------------------------------------------------
# Core hook logic
# ---------------------------------------------------------------------------

# Auto-tags are computed over a rolling window of the session's most recent
# exchanges (not just the exchanges added by this hook run), so a term that
# recurs across several prompts still reaches AUTO_TAG_MIN_FREQUENCY.
AUTO_TAG_WINDOW = 50


def _hook_context(message: str, event: str = 'UserPromptSubmit') -> Dict:
    """Wrap *message* as context Claude will actually read.

    ``systemMessage`` is a warning shown to the USER only. Anything the model
    must act on has to travel via ``hookSpecificOutput.additionalContext``.
    """
    return {"hookSpecificOutput": {"hookEventName": event,
                                   "additionalContext": message}}


def _trim_incomplete_tail(messages: List[Dict], final: bool):
    """Hold back the last turn unless it is provably complete.

    Returns ``(messages_to_index, resume_offset)``. ``resume_offset`` is the
    byte position of the held-back user message (so the next run re-reads it),
    or None when nothing is held back.

    Rules:
      * a trailing user message with no assistant reply is never consumed
        (it pairs up on a later run; consuming it would orphan the reply);
      * a trailing user+assistant group is consumed only when ``final`` is
        True — i.e. the caller knows the turn is over and the transcript is
        fully flushed. Otherwise a partial reply would be stored and the rest
        of the turn's blocks orphaned on the next read.
    """
    if not messages:
        return messages, None
    last_user = -1
    for i, m in enumerate(messages):
        if m['role'] == 'user':
            last_user = i
    if last_user == -1:
        return messages, None
    has_reply = any(m['role'] == 'assistant' for m in messages[last_user + 1:])
    if has_reply and final:
        return messages, None
    return messages[:last_user], messages[last_user].get('_start')


def _turn_is_flushed(messages: List[Dict], last_assistant_message: str) -> bool:
    """At Stop time the transcript file may lag the in-memory turn. Treat the
    trailing turn as complete only if the payload's ``last_assistant_message``
    (the final response text) is present in the assistant text read from disk.
    """
    if not last_assistant_message:
        return False
    tail = ' '.join(last_assistant_message.split())[-200:]
    if not tail:
        return False
    last_user = -1
    for i, m in enumerate(messages):
        if m['role'] == 'user':
            last_user = i
    on_disk = ' '.join(' '.join(m['text'] for m in messages[last_user + 1:]
                                if m['role'] == 'assistant').split())
    return tail in on_disk


def index_transcript(conn, session_id: str, transcript_path: str,
                     project_path: str = '', project_hash: str = '',
                     now: Optional[str] = None, final: bool = True,
                     last_assistant_message: Optional[str] = None) -> List[Dict]:
    """Incrementally index a session's transcript into the DB (no commit).

    Shared by the UserPromptSubmit, Stop and SessionEnd hooks. Ensures the
    session row exists, reads the transcript from the saved byte offset, pairs
    new messages into exchanges, inserts them (+ FTS), advances the offset,
    refreshes auto-tags over a rolling window, and runs auto-highlight
    detection on the new exchanges. The caller commits.

    ``final`` says whether the trailing turn may be consumed (see
    ``_trim_incomplete_tail``). When ``last_assistant_message`` is given (the
    Stop hook), ``final`` is instead derived by checking that text reached the
    transcript file. A read that stopped at the size cap is never final.

    Returns the list of newly inserted exchange dicts (may be empty).
    """
    now = now or datetime.now(timezone.utc).isoformat()
    insert_session(conn, session_id=session_id, project_path=project_path,
                   project_hash=project_hash, started_at=now,
                   transcript_path=transcript_path, commit=False)

    session = get_session(conn, session_id)
    byte_offset = session['byte_offset'] if session else 0
    existing_count = (session['exchange_count'] or 0) if session else 0

    current_size = 0
    if transcript_path and os.path.exists(transcript_path):
        current_size = os.path.getsize(transcript_path)

    new_exchanges_list: List[Dict] = []
    if current_size > byte_offset:
        new_messages, new_offset = parse_transcript_from_offset(transcript_path, byte_offset)
        reached_eof = new_offset >= current_size
        if last_assistant_message is not None:
            final = _turn_is_flushed(new_messages, last_assistant_message)
        usable, resume = _trim_incomplete_tail(new_messages, final and reached_eof)
        if resume is not None:
            new_offset = resume
        if usable:
            new_exchanges_list = build_new_exchanges(usable, existing_count + 1)
            if new_exchanges_list:
                insert_exchanges(conn, session_id, new_exchanges_list, commit=False)
        update_session_offset(conn, session_id, new_offset,
                              existing_count + len(new_exchanges_list), commit=False)

    if new_exchanges_list:
        window = get_exchanges(conn, session_id, last_n=AUTO_TAG_WINDOW)
        _store_auto_tags(conn, session_id, window, commit=False)
        auto_detect_highlights(conn, session_id, new_exchanges_list, commit=False)

    return new_exchanges_list


def run_hook(input_data: Dict, db_path: Path = None) -> Dict:
    """Core hook logic, separated from stdin/stdout for testability.

    1. Extract session metadata from *input_data*.
    2. Open (or create) the SQLite DB.
    3. Run one-time v1 migration if a legacy index.json exists.
    4. Index any new transcript data (``index_transcript``).
    5. Poll cross-session connections.
    6. Commit once.
    7. Return user-facing ``systemMessage`` for ``/recall`` observability, or
       ``additionalContext`` for anything Claude must act on.

    Args:
        input_data: Dict parsed from the hook's stdin JSON.
        db_path: Override path for the database (used in tests).

    Returns:
        Dict to be printed as JSON to stdout.  ``{}`` for normal prompts.
    """
    session_id = input_data.get('session_id', 'unknown')
    transcript_path = input_data.get('transcript_path', '')
    # Current Claude Code sends 'prompt' and 'cwd'; older payloads used
    # 'user_prompt'/'project_path'. Accept the new fields, fall back to legacy.
    user_prompt = input_data.get('prompt') or input_data.get('user_prompt', '')
    project_path = input_data.get('cwd') or input_data.get('project_path', '')
    # project_hash is no longer supplied by the runtime — derive it from cwd
    # (fall back to a payload-provided hash if one is ever present).
    project_hash = input_data.get('project_hash') or compute_project_hash(project_path)

    conn = get_connection(db_path)

    try:
        # One-time v1 migration (no-op if legacy file absent or already checked)
        migrate_from_json(conn)

        # At prompt-submit time no turn is in flight, so every turn on disk is
        # complete: the trailing group may be consumed (final=True).
        index_transcript(conn, session_id, transcript_path,
                         project_path=project_path, project_hash=project_hash,
                         final=True)

        # Check connections for incoming highlights (updates written with commit=False)
        connection_msg = _check_connections(conn, session_id)

        # Single commit covering all writes above
        conn.commit()

        # Handle /recall (user-facing observability notice — systemMessage is right here)
        if user_prompt.strip().lower().startswith('/recall'):
            updated = get_session(conn, session_id)
            exchange_count = updated['exchange_count'] or 0 if updated else 0
            log_recall_event(session_id, exchange_count)
            return {
                "systemMessage": f"[Observability] Context recall logged at exchange #{exchange_count}"
            }

        # Proactive recall suggestion (deterministic; gated on skill_enabled).
        # Claude must ACT on this, so it goes into its context, not to the user.
        suggestion = _maybe_suggest_recall(conn, session_id, user_prompt)
        if suggestion:
            return _hook_context(suggestion)

        if connection_msg:
            return _hook_context(connection_msg)

        return {}

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    """Read stdin JSON, run the hook, print result to stdout."""
    try:
        raw = sys.stdin.read(1_000_000)  # 1 MB max
        input_data = json.loads(raw)
        result = run_hook(input_data)
        print(json.dumps(result), file=sys.stdout)
    except Exception as e:
        print(f"[context-recall] Hook error: {e}", file=sys.stderr)
        error_output = {
            "systemMessage": "[context-recall] Hook encountered an error. Check logs for details."
        }
        print(json.dumps(error_output), file=sys.stdout)
    finally:
        sys.exit(0)


if __name__ == '__main__':
    main()
