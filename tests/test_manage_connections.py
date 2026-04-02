#!/usr/bin/env python3
"""Unit tests for manage_connections.py — connect, inbox, and config."""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from db import (
    get_connection,
    insert_session,
    insert_highlight,
    get_connections,
    get_session_config,
)
from manage_connections import (
    connect,
    connect_latest,
    disconnect,
    inbox,
    config,
    format_inbox,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(conn, session_id, project_hash='proj-abc', started_at=None,
                  ended_at=None):
    """Insert a session and return its ID."""
    if started_at is None:
        started_at = '2026-04-01T00:00:00Z'
    insert_session(conn, session_id, '/tmp/proj', project_hash, started_at)
    if ended_at is not None:
        conn.execute(
            "UPDATE sessions SET ended_at = ? WHERE session_id = ?",
            (ended_at, session_id),
        )
        conn.commit()
    return session_id


def _make_highlight(conn, session_id, summary='Fixed the dtype issue',
                    tags='triton', created_at=None):
    """Insert a highlight and return summary."""
    if created_at is not None:
        # Insert with explicit created_at by going direct to SQL
        conn.execute(
            "INSERT OR IGNORE INTO highlights "
            "(session_id, summary, exchange_idx, tags, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, summary, None, tags, 'explicit', created_at),
        )
        conn.commit()
    else:
        insert_highlight(conn, session_id, summary, tags, source='explicit')
    return summary


# ---------------------------------------------------------------------------
# TestConnect
# ---------------------------------------------------------------------------

class TestConnect(unittest.TestCase):
    """Tests for connect() and connect_latest()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_connect.db')
        self.conn = get_connection(self.db_path)
        self.watcher = _make_session(self.conn, 'watcher-session-001')
        self.target = _make_session(self.conn, 'target-session-001')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_connect_to_existing_session_returns_confirmation(self):
        """connect() to an existing session returns the confirmation message."""
        result = connect(self.conn, self.watcher, self.target, 'GPU memory bugs')
        self.assertIn('Connected to session', result)
        self.assertIn(self.target[:8], result)
        self.assertIn('GPU memory bugs', result)

    def test_connect_to_nonexistent_session_returns_error(self):
        """connect() to a session that does not exist returns an error string."""
        result = connect(self.conn, self.watcher, 'nonexistent-session-xyz', 'topic')
        self.assertIn('Error', result)
        self.assertIn('not found', result)

    def test_connect_latest_finds_most_recent_active_session(self):
        """connect_latest() connects to the most recently started active session."""
        # Use a dedicated project hash so only these two sessions compete
        proj = 'proj-latest-test'
        watcher2 = _make_session(self.conn, 'watcher-latest-001', project_hash=proj,
                                 started_at='2026-02-01T00:00:00Z')
        older = _make_session(self.conn, 'older-session-002', project_hash=proj,
                              started_at='2026-03-01T00:00:00Z')
        newer = _make_session(self.conn, 'newer-session-002', project_hash=proj,
                              started_at='2026-03-15T00:00:00Z')
        result = connect_latest(self.conn, watcher2, proj, 'kernel perf')
        # Should connect to the newer session (most recent, not self)
        self.assertIn('Connected to session', result)
        self.assertIn(newer[:8], result)

    def test_connect_latest_returns_error_when_no_other_sessions(self):
        """connect_latest() returns an error when no other active sessions exist."""
        # Use a project hash that only has the watcher session
        solo = _make_session(self.conn, 'solo-session-001', project_hash='solo-hash')
        result = connect_latest(self.conn, solo, 'solo-hash', 'topic')
        self.assertIn('Error', result)
        self.assertIn('no other active sessions', result)


# ---------------------------------------------------------------------------
# TestDisconnect
# ---------------------------------------------------------------------------

class TestDisconnect(unittest.TestCase):
    """Tests for disconnect()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_disconnect.db')
        self.conn = get_connection(self.db_path)
        self.watcher = _make_session(self.conn, 'watcher-dc-001')
        self.target = _make_session(self.conn, 'target-dc-001')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_disconnect_removes_connection_and_confirms(self):
        """disconnect() removes an existing connection and returns confirmation."""
        connect(self.conn, self.watcher, self.target, 'some topic')
        connections_before = get_connections(self.conn, self.watcher)
        self.assertEqual(len(connections_before), 1)

        result = disconnect(self.conn, self.watcher, self.target)
        self.assertIn('Disconnected', result)
        self.assertIn(self.target[:8], result)

        connections_after = get_connections(self.conn, self.watcher)
        self.assertEqual(len(connections_after), 0)

    def test_disconnect_nonexistent_connection_is_no_op(self):
        """disconnect() on a connection that does not exist does not raise."""
        # Should complete without error even when connection doesn't exist
        result = disconnect(self.conn, self.watcher, self.target)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)


# ---------------------------------------------------------------------------
# TestInbox
# ---------------------------------------------------------------------------

class TestInbox(unittest.TestCase):
    """Tests for inbox()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_inbox.db')
        self.conn = get_connection(self.db_path)
        self.watcher = _make_session(self.conn, 'watcher-inbox-001')
        self.target = _make_session(self.conn, 'target-inbox-001')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_inbox_shows_highlights_from_connected_sessions(self):
        """inbox() returns formatted highlights from connected sessions."""
        connect(self.conn, self.watcher, self.target, 'memory layout')
        _make_highlight(self.conn, self.target, 'Solved the cache miss issue')

        result = inbox(self.conn, self.watcher)
        self.assertIn('Inbox', result)
        self.assertIn('Solved the cache miss issue', result)

    def test_inbox_shows_no_highlights_message_when_empty(self):
        """inbox() returns the sentinel message when there are no new highlights."""
        connect(self.conn, self.watcher, self.target, 'topic')
        result = inbox(self.conn, self.watcher)
        self.assertIn('No new highlights', result)

    def test_inbox_updates_last_checked_at_after_viewing(self):
        """inbox() updates last_checked_at on all connections."""
        connect(self.conn, self.watcher, self.target, 'topic')
        connections_before = get_connections(self.conn, self.watcher)
        self.assertIsNone(connections_before[0]['last_checked_at'])

        inbox(self.conn, self.watcher)

        connections_after = get_connections(self.conn, self.watcher)
        self.assertIsNotNone(connections_after[0]['last_checked_at'])

    def test_subsequent_inbox_call_shows_no_highlights(self):
        """After checking inbox, a second call shows no new highlights."""
        connect(self.conn, self.watcher, self.target, 'topic')
        _make_highlight(self.conn, self.target, 'First insight')

        # First call sees the highlight
        first_result = inbox(self.conn, self.watcher)
        self.assertIn('First insight', first_result)

        # Second call — no new highlights since last check
        second_result = inbox(self.conn, self.watcher)
        self.assertIn('No new highlights', second_result)


# ---------------------------------------------------------------------------
# TestConfig
# ---------------------------------------------------------------------------

class TestConfig(unittest.TestCase):
    """Tests for config()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_config.db')
        self.conn = get_connection(self.db_path)
        self.session = _make_session(self.conn, 'cfg-session-001')
        self.target = _make_session(self.conn, 'cfg-target-001')
        connect(self.conn, self.session, self.target, 'perf work')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_config_sets_check_mode_on_all_connections(self):
        """config() with check_mode updates all connections for the session."""
        result = config(self.conn, self.session, 'check_mode', 'auto')
        self.assertIn('check_mode', result)
        self.assertIn('auto', result)

        connections = get_connections(self.conn, self.session)
        for c in connections:
            self.assertEqual(c['check_mode'], 'auto')

    def test_config_sets_auto_highlight_in_session_config(self):
        """config() with auto_highlight writes to session metadata."""
        result = config(self.conn, self.session, 'auto_highlight', 'true')
        self.assertIn('auto_highlight', result)

        value = get_session_config(self.conn, self.session, 'auto_highlight')
        self.assertTrue(value)

    def test_config_rejects_invalid_key(self):
        """config() with an unrecognized key returns an error message."""
        result = config(self.conn, self.session, 'nonexistent_key', 'value')
        self.assertIn('Error', result)
        self.assertIn('invalid config key', result)


# ---------------------------------------------------------------------------
# TestFormatInbox
# ---------------------------------------------------------------------------

class TestFormatInbox(unittest.TestCase):
    """Tests for format_inbox() formatting logic (no DB required)."""

    def test_formats_highlights_grouped_by_session(self):
        """format_inbox groups highlights by session and includes summary and topic."""
        highlights = [
            {
                'session_id': 'aaaa-bbbb-cccc-dddd',
                'summary': 'Fixed the OOM issue',
                'tags': 'cuda, triton',
                'created_at': '2026-04-01T10:00:00Z',
                'connection_topic': 'memory leaks',
            },
            {
                'session_id': 'aaaa-bbbb-cccc-dddd',
                'summary': 'Reduced kernel launch overhead',
                'tags': 'triton',
                'created_at': '2026-04-01T10:05:00Z',
                'connection_topic': 'memory leaks',
            },
            {
                'session_id': 'eeee-ffff-0000-1111',
                'summary': 'MSL backend segfault root cause',
                'tags': 'metal',
                'created_at': '2026-04-01T11:00:00Z',
                'connection_topic': 'Metal backend',
            },
        ]
        result = format_inbox(highlights)
        self.assertIn('Inbox', result)
        self.assertIn('3 new highlights', result)
        # Both sessions should appear
        self.assertIn('aaaa-bbb', result)
        self.assertIn('eeee-fff', result)
        # Topics
        self.assertIn('memory leaks', result)
        self.assertIn('Metal backend', result)
        # Summaries
        self.assertIn('Fixed the OOM issue', result)
        self.assertIn('MSL backend segfault root cause', result)
        # Footer hint
        self.assertIn('/recall search', result)

    def test_handles_empty_list(self):
        """format_inbox returns the no-highlights sentinel for an empty list."""
        result = format_inbox([])
        self.assertEqual(result, '*No new highlights.*')


if __name__ == '__main__':
    unittest.main()
