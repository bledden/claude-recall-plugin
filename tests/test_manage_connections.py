#!/usr/bin/env python3
"""Unit tests for manage_connections.py — connect, inbox, and config."""

import inspect
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import manage_connections
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

    def test_inbox_mark_read_updates_last_checked_at_after_viewing(self):
        """inbox(mark_read=True) updates last_checked_at on decay connections."""
        connect(self.conn, self.watcher, self.target, 'topic', check_mode='decay')
        connections_before = get_connections(self.conn, self.watcher)
        self.assertIsNone(connections_before[0]['last_checked_at'])

        inbox(self.conn, self.watcher, mark_read=True)

        connections_after = get_connections(self.conn, self.watcher)
        self.assertIsNotNone(connections_after[0]['last_checked_at'])

    def test_subsequent_mark_read_inbox_call_shows_no_highlights(self):
        """After mark_read, a second call shows no new highlights for decay conns."""
        connect(self.conn, self.watcher, self.target, 'topic', check_mode='decay')
        _make_highlight(self.conn, self.target, 'First insight')

        # First call (mark_read) sees the highlight and advances last_checked_at
        first_result = inbox(self.conn, self.watcher, mark_read=True)
        self.assertIn('First insight', first_result)

        # Second call — no new highlights since last check
        second_result = inbox(self.conn, self.watcher, mark_read=True)
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
        message, code = config(self.conn, self.session, 'check_mode', 'decay')
        self.assertEqual(code, 0)
        self.assertIn('check_mode', message)
        self.assertIn('decay', message)

        connections = get_connections(self.conn, self.session)
        for c in connections:
            self.assertEqual(c['check_mode'], 'decay')

    def test_config_sets_auto_highlight_in_session_config(self):
        """config() with auto_highlight writes to session metadata."""
        message, code = config(self.conn, self.session, 'auto_highlight', 'true')
        self.assertEqual(code, 0)
        self.assertIn('auto_highlight', message)

        value = get_session_config(self.conn, self.session, 'auto_highlight')
        self.assertTrue(value)

    def test_config_rejects_invalid_key(self):
        """config() with an unrecognized key returns an error message."""
        message, code = config(self.conn, self.session, 'nonexistent_key', 'value')
        self.assertNotEqual(code, 0)
        self.assertIn('Error', message)
        self.assertIn('invalid config key', message)


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


# ---------------------------------------------------------------------------
# WI-3: Unified vocabulary in docstrings
# ---------------------------------------------------------------------------

class TestVocabularyDocstrings(unittest.TestCase):
    """WI-3: module + connect docstrings must use canonical vocabulary."""

    def test_module_docstring_has_no_legacy_terms(self):
        """Module docstring must not reference legacy 'auto'/'notify' modes."""
        doc = manage_connections.__doc__ or ''
        self.assertNotIn("'auto'", doc)
        self.assertNotIn("'notify'", doc)

    def test_connect_docstring_uses_canonical_terms(self):
        """connect() docstring documents explicit/decay and silent/inject."""
        doc = connect.__doc__ or ''
        self.assertNotIn("'auto'", doc)
        self.assertNotIn("'notify'", doc)
        self.assertIn('explicit', doc)
        self.assertIn('decay', doc)
        self.assertIn('silent', doc)
        self.assertIn('inject', doc)

    def test_connect_latest_docstring_uses_canonical_terms(self):
        """connect_latest() docstring must not reference legacy terms."""
        doc = connect_latest.__doc__ or ''
        self.assertNotIn("'auto'", doc)
        self.assertNotIn("'notify'", doc)


# ---------------------------------------------------------------------------
# WI-17: optional check_mode/delivery_mode flags on connect / connect-latest
# ---------------------------------------------------------------------------

class TestConnectModeFlags(unittest.TestCase):
    """WI-17: connect()/connect_latest() accept mode overrides at creation."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_modeflags.db')
        self.conn = get_connection(self.db_path)
        self.watcher = _make_session(self.conn, 'watcher-mf-001')
        self.target = _make_session(self.conn, 'target-mf-001')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_connect_persists_custom_modes(self):
        """connect() with check_mode/delivery_mode persists them on the row."""
        connect(self.conn, self.watcher, self.target, 'topic',
                check_mode='decay', delivery_mode='inject')
        c = get_connections(self.conn, self.watcher)[0]
        self.assertEqual(c['check_mode'], 'decay')
        self.assertEqual(c['delivery_mode'], 'inject')

    def test_connect_defaults_are_explicit_silent(self):
        """connect() defaults remain explicit/silent."""
        connect(self.conn, self.watcher, self.target, 'topic')
        c = get_connections(self.conn, self.watcher)[0]
        self.assertEqual(c['check_mode'], 'explicit')
        self.assertEqual(c['delivery_mode'], 'silent')


class TestArgparseDispatch(unittest.TestCase):
    """WI-17: main() uses argparse — --help and bad subcommands behave."""

    def test_build_parser_exists(self):
        """A build_parser() helper must exist for argparse dispatch."""
        self.assertTrue(hasattr(manage_connections, 'build_parser'))

    def test_help_flag_exits_zero(self):
        """--help triggers argparse SystemExit(0)."""
        parser = manage_connections.build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(['--help'])
        self.assertEqual(ctx.exception.code, 0)

    def test_unknown_subcommand_exits_nonzero(self):
        """An unknown subcommand exits with a non-zero (usage) error."""
        parser = manage_connections.build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(['bogus-command'])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_connect_parses_mode_flags(self):
        """connect subcommand accepts --check-mode / --delivery-mode."""
        parser = manage_connections.build_parser()
        ns = parser.parse_args(
            ['connect', 'w', 't', 'topic',
             '--check-mode', 'decay', '--delivery-mode', 'inject'])
        self.assertEqual(ns.check_mode, 'decay')
        self.assertEqual(ns.delivery_mode, 'inject')

    def test_connect_latest_parses_mode_flags(self):
        """connect-latest takes (watcher, topic) — project hash self-resolves
        from cwd, so it is no longer a positional — plus --check/--delivery-mode."""
        parser = manage_connections.build_parser()
        ns = parser.parse_args(
            ['connect-latest', 'w', 'topic', '--check-mode', 'decay'])
        self.assertEqual(ns.watcher, 'w')
        self.assertEqual(ns.topic, 'topic')
        self.assertEqual(ns.check_mode, 'decay')


# ---------------------------------------------------------------------------
# WI-18: disconnect reports 'no such connection' when nothing removed
# ---------------------------------------------------------------------------

class TestDisconnectRowcount(unittest.TestCase):
    """WI-18: disconnect must not claim success when nothing was removed."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_dc_rowcount.db')
        self.conn = get_connection(self.db_path)
        self.watcher = _make_session(self.conn, 'watcher-dcr-001')
        self.target = _make_session(self.conn, 'target-dcr-001')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_disconnect_nonexistent_reports_no_such_connection(self):
        """Disconnecting a connection that does not exist reports no such connection."""
        result = disconnect(self.conn, self.watcher, self.target)
        self.assertIn('no such connection', result.lower())
        self.assertNotIn('Disconnected', result)

    def test_disconnect_existing_still_confirms(self):
        """Disconnecting a real connection still confirms success."""
        connect(self.conn, self.watcher, self.target, 'topic')
        result = disconnect(self.conn, self.watcher, self.target)
        self.assertIn('Disconnected', result)


# ---------------------------------------------------------------------------
# WI-19: inbox idempotency — plain inbox does NOT advance counters
# ---------------------------------------------------------------------------

class TestInboxIdempotency(unittest.TestCase):
    """WI-19: plain inbox is a read-only VIEW; --mark-read advances state."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_inbox_idem.db')
        self.conn = get_connection(self.db_path)
        self.watcher = _make_session(self.conn, 'watcher-idem-001')
        self.target = _make_session(self.conn, 'target-idem-001')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_plain_inbox_does_not_advance_counters(self):
        """Plain inbox() leaves check_counter and last_checked_at untouched."""
        connect(self.conn, self.watcher, self.target, 'topic',
                check_mode='decay')
        _make_highlight(self.conn, self.target, 'Insight A')

        inbox(self.conn, self.watcher)

        c = get_connections(self.conn, self.watcher)[0]
        self.assertIsNone(c['last_checked_at'])
        self.assertEqual(c.get('check_counter', 0), 0)

    def test_plain_inbox_is_idempotent_shows_highlights_twice(self):
        """Without mark_read, highlights remain visible on a second call."""
        connect(self.conn, self.watcher, self.target, 'topic', check_mode='decay')
        _make_highlight(self.conn, self.target, 'Repeatable insight')

        first = inbox(self.conn, self.watcher)
        second = inbox(self.conn, self.watcher)
        self.assertIn('Repeatable insight', first)
        self.assertIn('Repeatable insight', second)

    def test_mark_read_advances_decay_connection(self):
        """inbox(mark_read=True) advances counter/last_checked_at for decay conns."""
        connect(self.conn, self.watcher, self.target, 'topic', check_mode='decay')
        _make_highlight(self.conn, self.target, 'Insight B')

        inbox(self.conn, self.watcher, mark_read=True)

        c = get_connections(self.conn, self.watcher)[0]
        self.assertIsNotNone(c['last_checked_at'])
        self.assertEqual(c.get('check_counter', 0), 1)

    def test_mark_read_does_not_advance_explicit_connection(self):
        """Even with mark_read, explicit-mode connections are never advanced."""
        connect(self.conn, self.watcher, self.target, 'topic',
                check_mode='explicit')
        _make_highlight(self.conn, self.target, 'Insight C')

        inbox(self.conn, self.watcher, mark_read=True)

        c = get_connections(self.conn, self.watcher)[0]
        self.assertIsNone(c['last_checked_at'])
        self.assertEqual(c.get('check_counter', 0), 0)


# ---------------------------------------------------------------------------
# WI-20: config docstring documents all 6 keys
# ---------------------------------------------------------------------------

class TestConfigDocstring(unittest.TestCase):
    """WI-20: config() docstring must document all six valid config keys."""

    def test_docstring_lists_all_keys(self):
        """All six VALID_CONFIG_KEYS appear in the config() docstring."""
        doc = config.__doc__ or ''
        for key in ('check_mode', 'delivery_mode', 'auto_highlight',
                    'skill_enabled', 'detection_signals', 'auto_run_highlight'):
            self.assertIn(key, doc, f"{key} missing from config docstring")

    def test_docstring_mentions_persistence_locations(self):
        """Docstring explains connection-row vs sessions.metadata persistence."""
        doc = (config.__doc__ or '').lower()
        self.assertIn('connection', doc)
        self.assertIn('metadata', doc)


# ---------------------------------------------------------------------------
# WI-21: config persistence reporting and validation
# ---------------------------------------------------------------------------

class TestConfigPersistenceReporting(unittest.TestCase):
    """WI-21: config must warn (non-zero exit) when nothing was persisted."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_cfg_persist.db')
        self.conn = get_connection(self.db_path)
        self.session = _make_session(self.conn, 'cfg-persist-001')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_check_mode_with_no_connections_warns(self):
        """check_mode on a session with no connections reports a warning."""
        result, code = config(self.conn, self.session, 'check_mode', 'decay')
        self.assertNotEqual(code, 0)
        self.assertIn('0 connection', result)

    def test_check_mode_with_connections_succeeds(self):
        """check_mode succeeds (exit 0) when connections exist."""
        target = _make_session(self.conn, 'cfg-persist-target')
        connect(self.conn, self.session, target, 'topic')
        result, code = config(self.conn, self.session, 'check_mode', 'decay')
        self.assertEqual(code, 0)
        for c in get_connections(self.conn, self.session):
            self.assertEqual(c['check_mode'], 'decay')

    def test_metadata_key_missing_session_warns(self):
        """auto_highlight on a missing session warns and exits non-zero."""
        result, code = config(self.conn, 'no-such-session', 'auto_highlight', 'true')
        self.assertNotEqual(code, 0)
        self.assertIn('not found', result.lower())

    def test_metadata_key_existing_session_succeeds(self):
        """auto_highlight on an existing session persists and exits 0."""
        result, code = config(self.conn, self.session, 'auto_highlight', 'true')
        self.assertEqual(code, 0)
        self.assertTrue(get_session_config(self.conn, self.session, 'auto_highlight'))

    def test_bool_key_rejects_unrecognized_token(self):
        """A non true/false token for a bool key is rejected with non-zero exit."""
        result, code = config(self.conn, self.session, 'auto_highlight', 'maybe')
        self.assertNotEqual(code, 0)
        self.assertIn('Error', result)

    def test_invalid_key_returns_nonzero(self):
        """An invalid config key returns a non-zero exit code."""
        result, code = config(self.conn, self.session, 'bogus_key', 'x')
        self.assertNotEqual(code, 0)
        self.assertIn('Error', result)

    def test_string_metadata_key_succeeds(self):
        """detection_signals (string metadata) persists on existing session."""
        result, code = config(self.conn, self.session, 'detection_signals', 'foo,bar')
        self.assertEqual(code, 0)
        self.assertEqual(
            get_session_config(self.conn, self.session, 'detection_signals'),
            'foo,bar')


if __name__ == '__main__':
    unittest.main()
