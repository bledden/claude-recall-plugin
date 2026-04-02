#!/usr/bin/env python3
"""Unit tests for manage_sessions.py — session management functions."""

import json
import os
import shutil
import sys
import tempfile
import unittest

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from db import get_connection, insert_session, insert_exchanges
from manage_sessions import (
    format_session_list,
    format_stats,
    format_export,
)


class TestFormatSessionList(unittest.TestCase):
    """Tests for format_session_list()."""

    def test_formats_sessions(self):
        """format_session_list returns a non-empty string for a non-empty session list."""
        sessions = [
            {
                'session_id': 'sess-001',
                'project_path': '/proj/alpha',
                'project_hash': 'abc',
                'started_at': '2026-01-01T10:00:00Z',
                'ended_at': '2026-01-01T11:00:00Z',
                'exchange_count': 5,
                'transcript_path': None,
                'byte_offset': 0,
                'metadata': None,
            },
        ]
        tags_by_session = {'sess-001': ['rust', 'cuda']}
        output = format_session_list(sessions, tags_by_session)
        self.assertIsInstance(output, str)
        self.assertGreater(len(output), 0)
        self.assertNotEqual(output, '*No sessions found.*')

    def test_includes_exchange_count(self):
        """Output includes the exchange count for a session."""
        sessions = [
            {
                'session_id': 'sess-002',
                'project_path': '/proj/beta',
                'project_hash': 'def',
                'started_at': '2026-02-01T09:00:00Z',
                'ended_at': None,
                'exchange_count': 12,
                'transcript_path': None,
                'byte_offset': 0,
                'metadata': None,
            },
        ]
        output = format_session_list(sessions, {})
        self.assertIn('12', output)

    def test_shows_active_status_when_no_ended_at(self):
        """Sessions without ended_at are displayed as active."""
        sessions = [
            {
                'session_id': 'sess-active',
                'project_path': '/proj/x',
                'project_hash': 'gh',
                'started_at': '2026-03-01T08:00:00Z',
                'ended_at': None,
                'exchange_count': 3,
                'transcript_path': None,
                'byte_offset': 0,
                'metadata': None,
            },
        ]
        output = format_session_list(sessions, {})
        self.assertIn('active', output.lower())

    def test_shows_ended_status_when_ended_at_set(self):
        """Sessions with ended_at are displayed as ended."""
        sessions = [
            {
                'session_id': 'sess-ended',
                'project_path': '/proj/y',
                'project_hash': 'ij',
                'started_at': '2026-03-01T08:00:00Z',
                'ended_at': '2026-03-01T09:00:00Z',
                'exchange_count': 7,
                'transcript_path': None,
                'byte_offset': 0,
                'metadata': None,
            },
        ]
        output = format_session_list(sessions, {})
        self.assertIn('ended', output.lower())

    def test_empty_sessions_returns_no_sessions_message(self):
        """Empty session list returns the sentinel string."""
        output = format_session_list([], {})
        self.assertEqual(output, '*No sessions found.*')

    def test_includes_tags_in_output(self):
        """Tags provided in tags_by_session appear in the formatted output."""
        sessions = [
            {
                'session_id': 'sess-tagged',
                'project_path': '/proj/tagged',
                'project_hash': 'kl',
                'started_at': '2026-01-15T12:00:00Z',
                'ended_at': None,
                'exchange_count': 2,
                'transcript_path': None,
                'byte_offset': 0,
                'metadata': None,
            },
        ]
        tags_by_session = {'sess-tagged': ['triton', 'metal']}
        output = format_session_list(sessions, tags_by_session)
        self.assertIn('triton', output)
        self.assertIn('metal', output)


class TestFormatStats(unittest.TestCase):
    """Tests for format_stats()."""

    def _make_stats(self, db_size_bytes=4096, projects=None):
        if projects is None:
            projects = [
                {'project_path': '/proj/alpha', 'session_count': 3, 'exchange_total': 45},
                {'project_path': '/proj/beta', 'session_count': 1, 'exchange_total': 8},
            ]
        return {
            'total_sessions': 4,
            'total_exchanges': 53,
            'total_tags': 10,
            'db_size_bytes': db_size_bytes,
            'projects': projects,
        }

    def test_includes_totals(self):
        """Output includes total session, exchange, and tag counts."""
        output = format_stats(self._make_stats())
        self.assertIn('4', output)   # total_sessions
        self.assertIn('53', output)  # total_exchanges
        self.assertIn('10', output)  # total_tags

    def test_includes_project_breakdown(self):
        """Output includes a per-project breakdown."""
        output = format_stats(self._make_stats())
        self.assertIn('/proj/alpha', output)
        self.assertIn('/proj/beta', output)

    def test_db_size_shown_in_kb(self):
        """DB size below 1 MB is shown in KB."""
        output = format_stats(self._make_stats(db_size_bytes=512 * 1024))  # 512 KB
        self.assertIn('KB', output)

    def test_db_size_shown_in_mb(self):
        """DB size at or above 1 MB is shown in MB."""
        output = format_stats(self._make_stats(db_size_bytes=15 * 1024 * 1024))  # 15 MB
        self.assertIn('MB', output)

    def test_prune_suggestion_above_10mb(self):
        """Output suggests pruning when DB exceeds 10 MB."""
        output = format_stats(self._make_stats(db_size_bytes=11 * 1024 * 1024))
        self.assertIn('prun', output.lower())

    def test_no_prune_suggestion_below_10mb(self):
        """No pruning suggestion when DB is below 10 MB."""
        output = format_stats(self._make_stats(db_size_bytes=5 * 1024 * 1024))
        self.assertNotIn('prun', output.lower())

    def test_empty_projects(self):
        """Stats with no projects still returns a valid string."""
        stats = self._make_stats(projects=[])
        stats['total_sessions'] = 0
        stats['total_exchanges'] = 0
        output = format_stats(stats)
        self.assertIsInstance(output, str)
        self.assertGreater(len(output), 0)


class TestFormatExport(unittest.TestCase):
    """Tests for format_export()."""

    def test_returns_valid_json(self):
        """format_export returns a valid JSON string."""
        data = {
            'session_id': 'sess-exp',
            'project_path': '/proj/export',
            'exchanges': [
                {'idx': 0, 'user_text': 'hello', 'assistant_text': 'hi'},
            ],
            'tags': [{'tag': 'python', 'source': 'manual'}],
        }
        output = format_export(data)
        parsed = json.loads(output)
        self.assertEqual(parsed['session_id'], 'sess-exp')
        self.assertEqual(len(parsed['exchanges']), 1)

    def test_indented_output(self):
        """format_export produces indented (pretty-printed) JSON."""
        data = {'key': 'value', 'nested': {'a': 1}}
        output = format_export(data)
        self.assertIn('\n', output)

    def test_round_trip(self):
        """Data survives a format_export → json.loads round trip."""
        data = {
            'session_id': 'x',
            'exchanges': [],
            'tags': [],
        }
        output = format_export(data)
        parsed = json.loads(output)
        self.assertEqual(parsed['session_id'], 'x')
        self.assertEqual(parsed['exchanges'], [])


if __name__ == '__main__':
    unittest.main()
