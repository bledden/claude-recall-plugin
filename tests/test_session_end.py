#!/usr/bin/env python3
"""Unit tests for hooks/session_end.py — SessionEnd hook."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add hooks and scripts directories to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from session_end import run_hook
from db import get_connection, insert_session, get_session


class TestSessionEndHook(unittest.TestCase):
    """Tests for run_hook in session_end.py."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'test.db'
        conn = get_connection(self.db_path)
        insert_session(conn, 'sess-end-1', '/proj/x', 'hash-x', '2026-01-05T09:00:00Z')
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_marks_session_ended(self):
        """run_hook sets ended_at on the session row."""
        result = run_hook({'session_id': 'sess-end-1'}, db_path=self.db_path)

        self.assertEqual(result, {})

        conn = get_connection(self.db_path)
        session = get_session(conn, 'sess-end-1')
        conn.close()

        self.assertIsNotNone(session['ended_at'])
        # ended_at should be a valid UTC ISO timestamp
        self.assertIn('T', session['ended_at'])

    def test_unknown_session_noop(self):
        """run_hook with a nonexistent session_id returns {} without error."""
        result = run_hook({'session_id': 'does-not-exist'}, db_path=self.db_path)
        self.assertEqual(result, {})

    def test_missing_session_id_returns_empty(self):
        """run_hook with no session_id in input returns {}."""
        result = run_hook({}, db_path=self.db_path)
        self.assertEqual(result, {})


if __name__ == '__main__':
    unittest.main()
