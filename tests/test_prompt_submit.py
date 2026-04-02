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

from prompt_submit import build_new_exchanges, run_hook
from db import get_connection, get_session, get_exchanges


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


if __name__ == '__main__':
    unittest.main()
