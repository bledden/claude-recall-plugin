#!/usr/bin/env python3
"""Unit tests for hooks/post_compact.py — PostCompact nudge hook."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add hooks and scripts directories to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from post_compact import build_nudge_message, run_hook
from db import get_connection, insert_session, insert_exchanges, update_session_offset, insert_tag


def _seed_db(conn):
    """Seed DB with one session containing 10 exchanges and some tags."""
    insert_session(conn, 'sess-1', '/proj/test', 'hash-t', '2026-01-05T09:00:00Z')
    insert_exchanges(conn, 'sess-1', [
        {
            'idx': i,
            'timestamp': f'2026-01-05T09:{i:02d}:00Z',
            'preview': f'Exchange {i} preview',
            'user_text': f'Q{i}',
            'assistant_text': f'A{i}',
        }
        for i in range(1, 11)
    ])
    update_session_offset(conn, 'sess-1', 0, 10)


class TestBuildNudgeMessage(unittest.TestCase):
    """Tests for build_nudge_message formatting."""

    def test_formats_nudge_with_previews(self):
        """All sections present; total length is reasonable."""
        msg = build_nudge_message(
            session_exchange_count=10,
            project_exchange_count=42,
            recent_previews=['preview alpha', 'preview beta', 'preview gamma'],
            tags=['python', 'refactor', 'bug'],
        )
        self.assertIn('10 exchanges indexed', msg)
        self.assertIn('42 total exchanges', msg)
        self.assertIn('Recent topics:', msg)
        self.assertIn('python', msg)
        self.assertIn('preview alpha', msg)
        self.assertIn('/recall', msg)
        self.assertLessEqual(len(msg), 600)

    def test_handles_empty_tags(self):
        """No 'Recent topics' line when tags list is empty."""
        msg = build_nudge_message(
            session_exchange_count=5,
            project_exchange_count=20,
            recent_previews=['some preview'],
            tags=[],
        )
        self.assertNotIn('Recent topics', msg)
        self.assertIn('/recall', msg)

    def test_handles_empty_previews(self):
        """No 'Last exchanges' section when previews is empty; /recall still present."""
        msg = build_nudge_message(
            session_exchange_count=3,
            project_exchange_count=15,
            recent_previews=[],
            tags=['python'],
        )
        self.assertNotIn('Last exchanges', msg)
        self.assertIn('/recall', msg)

    def test_both_empty_tags_and_previews(self):
        """With both empty, only core stats and /recall remain."""
        msg = build_nudge_message(
            session_exchange_count=1,
            project_exchange_count=1,
            recent_previews=[],
            tags=[],
        )
        self.assertIn('1 exchanges indexed', msg)
        self.assertIn('/recall', msg)
        self.assertNotIn('Recent topics', msg)
        self.assertNotIn('Last exchanges', msg)


class TestRunHook(unittest.TestCase):
    """Tests for run_hook integration with a real SQLite DB."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'test.db'

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_system_message(self):
        """run_hook with a real session returns a systemMessage key."""
        conn = get_connection(self.db_path)
        _seed_db(conn)
        insert_tag(conn, 'python', 'sess-1', source='auto')
        insert_tag(conn, 'refactor', 'sess-1', source='auto')
        conn.close()

        result = run_hook({'session_id': 'sess-1'}, db_path=self.db_path)

        self.assertIn('systemMessage', result)
        msg = result['systemMessage']
        self.assertIn('10 exchanges indexed', msg)
        self.assertIn('/recall', msg)

    def test_unknown_session_returns_empty(self):
        """run_hook with a nonexistent session_id returns {}."""
        conn = get_connection(self.db_path)
        conn.close()

        result = run_hook({'session_id': 'nonexistent-session'}, db_path=self.db_path)

        self.assertEqual(result, {})

    def test_missing_session_id_returns_empty(self):
        """run_hook with no session_id in input returns {}."""
        conn = get_connection(self.db_path)
        conn.close()

        result = run_hook({}, db_path=self.db_path)

        self.assertEqual(result, {})


if __name__ == '__main__':
    unittest.main()
