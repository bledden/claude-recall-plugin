#!/usr/bin/env python3
"""Unit tests for hooks/prompt_submit.py — SQLite-backed prompt submit hook."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add hooks and scripts directories to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from prompt_submit import build_new_exchanges, run_hook, parse_transcript_from_offset
from db import (get_connection, get_session, get_exchanges,
                insert_connection, insert_highlight, set_session_config)


def _write_transcript(path, entries):
    """Helper: write JSONL transcript entries to a file."""
    with open(path, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')


def _make_entry(role, text, timestamp='2025-01-05T09:00:00Z'):
    """Helper: build a single transcript JSONL entry."""
    return {
        'type': role,
        'message': {'content': [{'type': 'text', 'text': text}]},
        'timestamp': timestamp,
    }


# ---------------------------------------------------------------------------
# TestBuildNewExchanges
# ---------------------------------------------------------------------------

class TestBuildNewExchanges(unittest.TestCase):
    """Tests for build_new_exchanges."""

    def test_builds_exchanges_from_message_pairs(self):
        """Pairs user+assistant messages into exchanges with correct fields."""
        messages = [
            {'role': 'user', 'text': 'Question 1', 'timestamp': '2025-01-05T09:00:00Z'},
            {'role': 'assistant', 'text': 'Answer 1', 'timestamp': '2025-01-05T09:00:05Z'},
            {'role': 'user', 'text': 'Question 2', 'timestamp': '2025-01-05T09:01:00Z'},
            {'role': 'assistant', 'text': 'Answer 2', 'timestamp': '2025-01-05T09:01:05Z'},
        ]
        exchanges = build_new_exchanges(messages)

        self.assertEqual(len(exchanges), 2)
        self.assertEqual(exchanges[0]['idx'], 1)
        self.assertEqual(exchanges[1]['idx'], 2)
        self.assertIn('preview', exchanges[0])
        self.assertIn('user_text', exchanges[0])
        self.assertIn('assistant_text', exchanges[0])
        self.assertEqual(exchanges[0]['timestamp'], '2025-01-05T09:00:00Z')

    def test_empty_messages_returns_empty_list(self):
        """Empty input produces no exchanges."""
        exchanges = build_new_exchanges([])
        self.assertEqual(exchanges, [])

    def test_unpaired_messages_skipped(self):
        """Consecutive same-role messages skip the unpaired one."""
        messages = [
            {'role': 'user', 'text': 'Q1', 'timestamp': ''},
            {'role': 'user', 'text': 'Q2', 'timestamp': ''},
            {'role': 'assistant', 'text': 'A2', 'timestamp': ''},
        ]
        exchanges = build_new_exchanges(messages)
        # Only Q2+A2 pair succeeds
        self.assertEqual(len(exchanges), 1)
        self.assertIn('Q2', exchanges[0]['user_text'])

    def test_custom_start_idx_works(self):
        """start_idx offsets the exchange numbering."""
        messages = [
            {'role': 'user', 'text': 'Q', 'timestamp': ''},
            {'role': 'assistant', 'text': 'A', 'timestamp': ''},
        ]
        exchanges = build_new_exchanges(messages, start_idx=10)
        self.assertEqual(exchanges[0]['idx'], 10)


# ---------------------------------------------------------------------------
# TestRunHook
# ---------------------------------------------------------------------------

class TestRunHook(unittest.TestCase):
    """Tests for the run_hook function (core logic)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'test.db'
        self.transcript_path = Path(self.temp_dir) / 'transcript.jsonl'

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _base_input(self, **overrides):
        """Return a minimal valid input_data dict with optional overrides."""
        data = {
            'session_id': 'sess-001',
            'transcript_path': str(self.transcript_path),
            'user_prompt': 'hello',
            'project_path': '/tmp/project',
            'project_hash': 'abc123',
        }
        data.update(overrides)
        return data

    def test_creates_session_and_exchanges_from_transcript(self):
        """First call creates session + exchanges from the transcript."""
        entries = [
            _make_entry('user', 'What is Rust?', '2025-01-05T09:00:00Z'),
            _make_entry('assistant', 'Rust is a systems language.', '2025-01-05T09:00:05Z'),
            _make_entry('user', 'Tell me more', '2025-01-05T09:01:00Z'),
            _make_entry('assistant', 'It has ownership semantics.', '2025-01-05T09:01:05Z'),
        ]
        _write_transcript(self.transcript_path, entries)

        result = run_hook(self._base_input(), db_path=self.db_path)

        self.assertEqual(result, {})

        conn = get_connection(self.db_path)
        session = get_session(conn, 'sess-001')
        self.assertIsNotNone(session)
        self.assertEqual(session['exchange_count'], 2)
        self.assertGreater(session['byte_offset'], 0)

        exchanges = get_exchanges(conn, 'sess-001')
        self.assertEqual(len(exchanges), 2)
        self.assertEqual(exchanges[0]['idx'], 1)
        self.assertIn('Rust', exchanges[0]['user_text'])
        conn.close()

    def test_incremental_update_only_adds_new_exchanges(self):
        """Second call with a grown transcript only inserts new exchanges."""
        # First two exchanges
        entries = [
            _make_entry('user', 'Q1', '2025-01-05T09:00:00Z'),
            _make_entry('assistant', 'A1', '2025-01-05T09:00:05Z'),
        ]
        _write_transcript(self.transcript_path, entries)
        run_hook(self._base_input(), db_path=self.db_path)

        # Append more entries
        with open(self.transcript_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(_make_entry('user', 'Q2', '2025-01-05T09:02:00Z')) + '\n')
            f.write(json.dumps(_make_entry('assistant', 'A2', '2025-01-05T09:02:05Z')) + '\n')

        run_hook(self._base_input(), db_path=self.db_path)

        conn = get_connection(self.db_path)
        session = get_session(conn, 'sess-001')
        self.assertEqual(session['exchange_count'], 2)

        exchanges = get_exchanges(conn, 'sess-001')
        self.assertEqual(len(exchanges), 2)
        self.assertEqual(exchanges[0]['idx'], 1)
        self.assertEqual(exchanges[1]['idx'], 2)
        conn.close()

    def test_new_session_preserves_old(self):
        """A /clear that spawns a new session leaves the old one intact."""
        entries = [
            _make_entry('user', 'Q old', '2025-01-05T09:00:00Z'),
            _make_entry('assistant', 'A old', '2025-01-05T09:00:05Z'),
        ]
        _write_transcript(self.transcript_path, entries)
        run_hook(self._base_input(session_id='sess-old'), db_path=self.db_path)

        # New session with different transcript
        new_transcript = Path(self.temp_dir) / 'transcript2.jsonl'
        entries2 = [
            _make_entry('user', 'Q new', '2025-01-05T10:00:00Z'),
            _make_entry('assistant', 'A new', '2025-01-05T10:00:05Z'),
        ]
        _write_transcript(new_transcript, entries2)
        run_hook(self._base_input(session_id='sess-new', transcript_path=str(new_transcript)),
                 db_path=self.db_path)

        conn = get_connection(self.db_path)
        old_session = get_session(conn, 'sess-old')
        new_session = get_session(conn, 'sess-new')
        self.assertIsNotNone(old_session)
        self.assertIsNotNone(new_session)

        old_exchanges = get_exchanges(conn, 'sess-old')
        new_exchanges = get_exchanges(conn, 'sess-new')
        self.assertEqual(len(old_exchanges), 1)
        self.assertEqual(len(new_exchanges), 1)
        conn.close()

    def test_recall_command_returns_system_message_with_logging(self):
        """/recall triggers a systemMessage response and writes log file."""
        entries = [
            _make_entry('user', 'Q', '2025-01-05T09:00:00Z'),
            _make_entry('assistant', 'A', '2025-01-05T09:00:05Z'),
        ]
        _write_transcript(self.transcript_path, entries)

        # Use a custom log file in temp dir to avoid polluting real HOME
        log_file = Path(self.temp_dir) / 'recall-events.log'
        import prompt_submit
        original_log = prompt_submit.LOG_FILE
        prompt_submit.LOG_FILE = log_file

        try:
            result = run_hook(self._base_input(user_prompt='/recall'), db_path=self.db_path)

            self.assertIn('systemMessage', result)
            self.assertIn('recall', result['systemMessage'].lower())

            # Verify log file was written
            self.assertTrue(log_file.exists())
            content = log_file.read_text()
            self.assertIn('CONTEXT_RECALL_TRIGGERED', content)
            self.assertIn('sess-001', content)
        finally:
            prompt_submit.LOG_FILE = original_log


    def test_connection_check_injects_on_decay(self):
        """Test that connection check fires on decay schedule and injects highlights."""
        # Create watcher session and target session in the DB
        watcher_id = 'sess-watcher'
        target_id = 'sess-target'

        # Write transcripts for both sessions so they exist in the DB
        target_transcript = Path(self.temp_dir) / 'transcript_target.jsonl'
        target_entries = [
            _make_entry('user', 'Q target', '2025-01-05T09:00:00Z'),
            _make_entry('assistant', 'A target', '2025-01-05T09:00:05Z'),
        ]
        _write_transcript(target_transcript, target_entries)
        run_hook(self._base_input(session_id=target_id,
                                  transcript_path=str(target_transcript)),
                 db_path=self.db_path)

        _write_transcript(self.transcript_path, [
            _make_entry('user', 'Q watcher', '2025-01-05T09:01:00Z'),
            _make_entry('assistant', 'A watcher', '2025-01-05T09:01:05Z'),
        ])
        run_hook(self._base_input(session_id=watcher_id), db_path=self.db_path)

        # Set up connection with check_mode='decay' and delivery_mode='inject'
        # and check_interval=1 so the first run triggers
        conn = get_connection(self.db_path)
        insert_connection(conn, watcher_id, target_id, 'kernel work',
                          check_mode='decay', delivery_mode='inject')
        # Set interval to 1 so the very next hook call fires
        conn.execute(
            "UPDATE connections SET check_interval = 1 WHERE watcher_session = ?",
            (watcher_id,)
        )
        conn.commit()

        # Add a highlight to the target session
        insert_highlight(conn, target_id, 'Fixed coalescing bug', 'perf', 'explicit')
        conn.close()

        # Run hook for watcher — counter starts at 0, interval=1, so 0+1 >= 1 → fires
        result = run_hook(self._base_input(session_id=watcher_id), db_path=self.db_path)

        self.assertIn('systemMessage', result)
        self.assertIn('Cross-session', result['systemMessage'])
        self.assertIn('Fixed coalescing bug', result['systemMessage'])
        self.assertIn(target_id[:8], result['systemMessage'])

    def test_auto_detect_runs_when_enabled(self):
        """Test that auto_detect_highlights runs in the hook when config is set."""
        session_id = 'sess-autodetect'

        # Write transcript with a strong solution-signal response
        solution_text = (
            'The fix is to use __shared__ memory. The solution resolves the issue '
            'because shared memory has much lower latency than global. '
            'Resolved by moving the accumulator into shared memory. '
            'Fixed by adding the proper synchronization barriers. '
            'This works because thread blocks share L1 cache.'
        )
        entries = [
            _make_entry('user', 'Why is my kernel slow?', '2025-01-05T09:00:00Z'),
            _make_entry('assistant', solution_text, '2025-01-05T09:00:05Z'),
        ]
        _write_transcript(self.transcript_path, entries)

        # First run to register the session
        run_hook(self._base_input(session_id=session_id), db_path=self.db_path)

        # Enable auto_highlight on the session
        conn = get_connection(self.db_path)
        set_session_config(conn, session_id, 'auto_highlight', True)
        conn.close()

        # Append more content so the transcript grows (triggers incremental parse)
        solution_text2 = (
            'The answer is to align your memory accesses. Try using vectorized loads. '
            'The issue was misaligned 128-byte transactions causing cache thrashing. '
            'The problem was solved by padding the shared memory array. '
            'This works because aligned accesses coalesce into single transactions.'
        )
        with open(self.transcript_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(_make_entry('user', 'What about memory?',
                                           '2025-01-05T09:02:00Z')) + '\n')
            f.write(json.dumps(_make_entry('assistant', solution_text2,
                                           '2025-01-05T09:02:05Z')) + '\n')

        run_hook(self._base_input(session_id=session_id), db_path=self.db_path)

        # Verify a highlight was auto-created
        conn = get_connection(self.db_path)
        cur = conn.execute(
            "SELECT * FROM highlights WHERE session_id = ? AND source = 'auto'",
            (session_id,)
        )
        highlights = cur.fetchall()
        conn.close()

        self.assertGreater(len(highlights), 0,
                           "Expected at least one auto-detected highlight")


# ---------------------------------------------------------------------------
# TestParseTranscriptFromOffset
# ---------------------------------------------------------------------------

class TestParseTranscriptFromOffset(unittest.TestCase):
    """Tests for parse_transcript_from_offset — binary mode, UTF-8 safety."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _path(self, name='transcript.jsonl'):
        return str(Path(self.temp_dir) / name)

    def test_nonexistent_file_returns_empty(self):
        """Non-existent file returns empty messages and offset 0."""
        messages, offset = parse_transcript_from_offset('/no/such/file.jsonl', 0)
        self.assertEqual(messages, [])
        self.assertEqual(offset, 0)

    def test_empty_file_returns_empty(self):
        """Empty transcript file returns no messages and offset at EOF."""
        path = self._path()
        with open(path, 'wb') as f:
            pass  # empty file
        messages, offset = parse_transcript_from_offset(path, 0)
        self.assertEqual(messages, [])
        self.assertEqual(offset, 0)

    def test_malformed_json_lines_skipped(self):
        """Lines with bad JSON are skipped without crashing."""
        path = self._path()
        with open(path, 'wb') as f:
            f.write(b'not json at all\n')
            f.write(b'{broken json\n')
            f.write(json.dumps(_make_entry('user', 'Valid question')).encode('utf-8') + b'\n')
        messages, offset = parse_transcript_from_offset(path, 0)
        # Only the valid user entry extracted (no paired assistant so messages list has 1)
        self.assertEqual(len(messages), 1)
        self.assertGreater(offset, 0)

    def test_only_user_and_assistant_extracted(self):
        """Entries with other types (e.g. 'system') are ignored."""
        path = self._path()
        entries = [
            {'type': 'system', 'message': {'content': 'sys msg'}, 'timestamp': '2025-01-05T09:00:00Z'},
            _make_entry('user', 'Hello'),
            _make_entry('assistant', 'Hi there'),
        ]
        with open(path, 'wb') as f:
            for e in entries:
                f.write(json.dumps(e).encode('utf-8') + b'\n')
        messages, offset = parse_transcript_from_offset(path, 0)
        self.assertEqual(len(messages), 2)
        roles = [m['role'] for m in messages]
        self.assertIn('user', roles)
        self.assertIn('assistant', roles)
        self.assertNotIn('system', roles)

    def test_multibyte_utf8_content_no_offset_corruption(self):
        """Multi-byte UTF-8 characters parse correctly and offset stays valid."""
        path = self._path()
        # Japanese characters are 3 bytes each in UTF-8
        user_text = 'こんにちは'  # 5 x 3 = 15 bytes
        asst_text = '元気です'    # 4 x 3 = 12 bytes
        entries = [
            _make_entry('user', user_text),
            _make_entry('assistant', asst_text),
        ]
        with open(path, 'wb') as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False).encode('utf-8') + b'\n')

        messages, offset = parse_transcript_from_offset(path, 0)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['text'], user_text)
        self.assertEqual(messages[1]['text'], asst_text)

        # offset should equal file size
        file_size = Path(path).stat().st_size
        self.assertEqual(offset, file_size)

    def test_incremental_offset_parsing(self):
        """Parsing from a mid-file offset returns only later entries."""
        path = self._path()
        # Write first pair
        first_entries = [
            _make_entry('user', 'First question', '2025-01-05T09:00:00Z'),
            _make_entry('assistant', 'First answer', '2025-01-05T09:00:05Z'),
        ]
        with open(path, 'wb') as f:
            for e in first_entries:
                f.write(json.dumps(e).encode('utf-8') + b'\n')

        # Parse full file to get mid-file offset
        _, mid_offset = parse_transcript_from_offset(path, 0)
        self.assertGreater(mid_offset, 0)

        # Append second pair
        second_entries = [
            _make_entry('user', 'Second question', '2025-01-05T09:01:00Z'),
            _make_entry('assistant', 'Second answer', '2025-01-05T09:01:05Z'),
        ]
        with open(path, 'ab') as f:
            for e in second_entries:
                f.write(json.dumps(e).encode('utf-8') + b'\n')

        # Parse from mid_offset — should only get second pair
        messages, new_offset = parse_transcript_from_offset(path, mid_offset)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]['text'], 'Second question')
        self.assertEqual(messages[1]['text'], 'Second answer')
        self.assertGreater(new_offset, mid_offset)


if __name__ == '__main__':
    unittest.main()
