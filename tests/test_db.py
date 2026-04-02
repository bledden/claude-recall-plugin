#!/usr/bin/env python3
"""Unit tests for db.py — SQLite schema, CRUD, FTS5, and maintenance."""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from db import (
    get_connection,
    insert_session,
    get_session,
    list_sessions,
    end_session,
    update_session_offset,
    insert_exchanges,
    get_exchanges,
    search_exchanges_fts,
    search_exchanges_global,
    prune_session,
    prune_before_date,
    get_stats,
    export_session_json,
    insert_tag,
    get_tags,
    insert_highlight,
    get_highlights,
    get_highlights_for_connections,
    insert_connection,
    get_connections,
    update_connection_check,
    delete_connection,
    get_session_config,
    set_session_config,
    DB_DIR,
    DB_PATH,
    DB_BUSY_TIMEOUT_MS,
)


class TestSchemaAndConnection(unittest.TestCase):
    """Tests for schema creation and WAL mode."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_tables(self):
        """Schema creates sessions, exchanges, and tags tables."""
        conn = get_connection(self.db_path)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = sorted([row['name'] for row in cur.fetchall()])
        self.assertIn('sessions', tables)
        self.assertIn('exchanges', tables)
        self.assertIn('tags', tables)
        conn.close()

    def test_creates_fts5(self):
        """Schema creates the exchanges_fts FTS5 virtual table."""
        conn = get_connection(self.db_path)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='exchanges_fts'"
        )
        rows = cur.fetchall()
        self.assertEqual(len(rows), 1)
        conn.close()

    def test_wal_mode_enabled(self):
        """Connection enables WAL journal mode."""
        conn = get_connection(self.db_path)
        cur = conn.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
        self.assertEqual(mode, 'wal')
        conn.close()

    def test_busy_timeout_set(self):
        """Connection sets busy_timeout to DB_BUSY_TIMEOUT_MS."""
        conn = get_connection(self.db_path)
        cur = conn.execute("PRAGMA busy_timeout")
        timeout = cur.fetchone()[0]
        self.assertEqual(timeout, DB_BUSY_TIMEOUT_MS)
        conn.close()

    def test_idempotent_creation(self):
        """Calling get_connection twice on the same DB does not error."""
        conn1 = get_connection(self.db_path)
        conn1.close()
        conn2 = get_connection(self.db_path)
        cur = conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        )
        self.assertEqual(len(cur.fetchall()), 1)
        conn2.close()


class TestSessionCRUD(unittest.TestCase):
    """Tests for session insert, get, list, end, and offset update."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_insert_and_get_session(self):
        """Insert a session and retrieve it by ID."""
        insert_session(
            self.conn,
            session_id='sess-001',
            project_path='/home/user/proj',
            project_hash='abc123',
            started_at='2025-01-01T00:00:00Z',
            transcript_path='/tmp/transcript.jsonl',
        )
        s = get_session(self.conn, 'sess-001')
        self.assertIsNotNone(s)
        self.assertEqual(s['session_id'], 'sess-001')
        self.assertEqual(s['project_path'], '/home/user/proj')
        self.assertEqual(s['project_hash'], 'abc123')
        self.assertEqual(s['transcript_path'], '/tmp/transcript.jsonl')
        self.assertEqual(s['exchange_count'], 0)
        self.assertEqual(s['byte_offset'], 0)

    def test_get_nonexistent_session(self):
        """Getting a nonexistent session returns None."""
        s = get_session(self.conn, 'no-such-session')
        self.assertIsNone(s)

    def test_list_sessions_by_project(self):
        """List sessions filtered by project_hash."""
        insert_session(self.conn, 'sess-a', '/p1', 'hash1', '2025-01-01T00:00:00Z')
        insert_session(self.conn, 'sess-b', '/p2', 'hash2', '2025-01-02T00:00:00Z')
        insert_session(self.conn, 'sess-c', '/p1', 'hash1', '2025-01-03T00:00:00Z')
        results = list_sessions(self.conn, project_hash='hash1')
        self.assertEqual(len(results), 2)
        # Ordered DESC by started_at
        self.assertEqual(results[0]['session_id'], 'sess-c')
        self.assertEqual(results[1]['session_id'], 'sess-a')

    def test_list_all_sessions(self):
        """List all sessions with no filter."""
        insert_session(self.conn, 'sess-x', '/px', 'hx', '2025-06-01T00:00:00Z')
        insert_session(self.conn, 'sess-y', '/py', 'hy', '2025-06-02T00:00:00Z')
        results = list_sessions(self.conn)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['session_id'], 'sess-y')

    def test_end_session(self):
        """End a session by setting ended_at."""
        insert_session(self.conn, 'sess-end', '/p', 'h', '2025-01-01T00:00:00Z')
        end_session(self.conn, 'sess-end', '2025-01-01T01:00:00Z')
        s = get_session(self.conn, 'sess-end')
        self.assertEqual(s['ended_at'], '2025-01-01T01:00:00Z')

    def test_update_session_offset(self):
        """Update byte_offset and exchange_count on a session."""
        insert_session(self.conn, 'sess-off', '/p', 'h', '2025-01-01T00:00:00Z')
        update_session_offset(self.conn, 'sess-off', byte_offset=4096, exchange_count=12)
        s = get_session(self.conn, 'sess-off')
        self.assertEqual(s['byte_offset'], 4096)
        self.assertEqual(s['exchange_count'], 12)


class TestExchangeCRUD(unittest.TestCase):
    """Tests for exchange insert and get."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-ex', '/p', 'h', '2025-01-01T00:00:00Z')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_insert_and_get_exchanges(self):
        """Insert exchanges and retrieve them in order."""
        exchanges = [
            {'idx': 0, 'timestamp': '2025-01-01T00:01:00Z', 'preview': 'hello',
             'user_text': 'hello world', 'assistant_text': 'hi there'},
            {'idx': 1, 'timestamp': '2025-01-01T00:02:00Z', 'preview': 'follow-up',
             'user_text': 'how are you', 'assistant_text': 'doing well'},
        ]
        insert_exchanges(self.conn, 'sess-ex', exchanges)
        rows = get_exchanges(self.conn, 'sess-ex')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['idx'], 0)
        self.assertEqual(rows[1]['idx'], 1)
        self.assertEqual(rows[0]['user_text'], 'hello world')

    def test_get_exchanges_with_last_n(self):
        """Get only the last N exchanges."""
        exchanges = [
            {'idx': i, 'timestamp': f'2025-01-01T00:0{i}:00Z', 'preview': f'msg{i}',
             'user_text': f'user msg {i}', 'assistant_text': f'asst msg {i}'}
            for i in range(5)
        ]
        insert_exchanges(self.conn, 'sess-ex', exchanges)
        rows = get_exchanges(self.conn, 'sess-ex', last_n=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['idx'], 3)
        self.assertEqual(rows[1]['idx'], 4)

    def test_get_exchanges_empty_session(self):
        """Get exchanges for a session with none returns empty list."""
        rows = get_exchanges(self.conn, 'sess-ex')
        self.assertEqual(rows, [])


class TestFTS5Search(unittest.TestCase):
    """Tests for full-text search via FTS5."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-fts', '/p', 'hfts', '2025-01-01T00:00:00Z')
        exchanges = [
            {'idx': 0, 'timestamp': '2025-01-01T00:01:00Z', 'preview': 'alpha',
             'user_text': 'Tell me about quantum computing',
             'assistant_text': 'Quantum computing uses qubits'},
            {'idx': 1, 'timestamp': '2025-01-01T00:02:00Z', 'preview': 'beta',
             'user_text': 'What is machine learning',
             'assistant_text': 'Machine learning is a subset of AI'},
            {'idx': 2, 'timestamp': '2025-01-01T00:03:00Z', 'preview': 'gamma',
             'user_text': 'More about quantum entanglement',
             'assistant_text': 'Entanglement links particles together'},
        ]
        insert_exchanges(self.conn, 'sess-fts', exchanges)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_search_match_in_user_text(self):
        """FTS5 finds matches in user_text."""
        results = search_exchanges_fts(self.conn, 'quantum')
        self.assertGreaterEqual(len(results), 1)
        found_texts = [r['user_text'] for r in results]
        self.assertTrue(any('quantum' in (t or '').lower() for t in found_texts))

    def test_search_match_in_assistant_text(self):
        """FTS5 finds matches in assistant_text."""
        results = search_exchanges_fts(self.conn, 'machine learning')
        self.assertGreaterEqual(len(results), 1)
        found_texts = [r['assistant_text'] for r in results]
        self.assertTrue(any('machine learning' in (t or '').lower() for t in found_texts))

    def test_search_no_results(self):
        """FTS5 returns empty list for unmatched query."""
        results = search_exchanges_fts(self.conn, 'xyznonexistent')
        self.assertEqual(len(results), 0)

    def test_search_multiple_matches(self):
        """FTS5 returns multiple matches across exchanges."""
        results = search_exchanges_fts(self.conn, 'quantum')
        self.assertGreaterEqual(len(results), 2)

    def test_multi_word_search_matches_non_adjacent_terms(self):
        """Multi-word query matches when both terms appear anywhere, not just as adjacent phrase."""
        # "quantum" and "qubits" appear in the same exchange but not adjacent
        # ("quantum computing uses qubits")
        results = search_exchanges_fts(self.conn, 'quantum qubits')
        self.assertGreaterEqual(len(results), 1)

    def test_multi_word_search_no_match_when_one_term_missing(self):
        """Multi-word AND search requires ALL terms present."""
        # "quantum" exists but "blockchain" does not
        results = search_exchanges_fts(self.conn, 'quantum blockchain')
        self.assertEqual(len(results), 0)

    def test_quoted_phrase_search_requires_adjacency(self):
        """User-quoted phrase search requires exact adjacent match."""
        # "quantum computing" is an exact phrase in the text
        results = search_exchanges_fts(self.conn, '"quantum computing"')
        self.assertGreaterEqual(len(results), 1)
        # "computing quantum" is NOT an adjacent phrase
        results2 = search_exchanges_fts(self.conn, '"computing quantum"')
        self.assertEqual(len(results2), 0)


class TestCrossSessionSearch(unittest.TestCase):
    """Tests for search across sessions and projects."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)

        # Two sessions in the same project
        insert_session(self.conn, 'sess-p1a', '/proj/alpha', 'phash1', '2025-01-01T00:00:00Z')
        insert_session(self.conn, 'sess-p1b', '/proj/alpha', 'phash1', '2025-01-02T00:00:00Z')
        # One session in a different project
        insert_session(self.conn, 'sess-p2', '/proj/beta', 'phash2', '2025-01-03T00:00:00Z')

        insert_exchanges(self.conn, 'sess-p1a', [
            {'idx': 0, 'timestamp': '2025-01-01T00:01:00Z', 'preview': 'ex1',
             'user_text': 'deploy the Rust service', 'assistant_text': 'deploying now'},
        ])
        insert_exchanges(self.conn, 'sess-p1b', [
            {'idx': 0, 'timestamp': '2025-01-02T00:01:00Z', 'preview': 'ex2',
             'user_text': 'check Rust build logs', 'assistant_text': 'build passed'},
        ])
        insert_exchanges(self.conn, 'sess-p2', [
            {'idx': 0, 'timestamp': '2025-01-03T00:01:00Z', 'preview': 'ex3',
             'user_text': 'run Python tests', 'assistant_text': 'all tests pass'},
        ])

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_search_across_project_sessions(self):
        """FTS5 search scoped to a project_hash finds matches in multiple sessions."""
        results = search_exchanges_fts(self.conn, 'Rust', project_hash='phash1')
        self.assertEqual(len(results), 2)

    def test_global_search(self):
        """Global search finds results across all projects."""
        results = search_exchanges_global(self.conn, 'Rust')
        self.assertGreaterEqual(len(results), 2)
        # Should include enriched fields
        self.assertIn('project_path', dict(results[0]))

    def test_search_by_project_path_substring(self):
        """list_sessions with project_path_contains filters by substring."""
        results = list_sessions(self.conn, project_path_contains='alpha')
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIn('alpha', r['project_path'])

    def test_clear_survival(self):
        """After pruning one session, search still finds data from remaining sessions."""
        prune_session(self.conn, 'sess-p1a')
        results = search_exchanges_fts(self.conn, 'Rust', project_hash='phash1')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['session_id'], 'sess-p1b')


class TestMaintenance(unittest.TestCase):
    """Tests for prune, stats, and export."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-m1', '/p', 'h', '2025-01-01T00:00:00Z')
        insert_session(self.conn, 'sess-m2', '/p', 'h', '2025-06-01T00:00:00Z')
        insert_exchanges(self.conn, 'sess-m1', [
            {'idx': 0, 'timestamp': '2025-01-01T00:01:00Z', 'preview': 'hi',
             'user_text': 'hello', 'assistant_text': 'world'},
        ])
        insert_exchanges(self.conn, 'sess-m2', [
            {'idx': 0, 'timestamp': '2025-06-01T00:01:00Z', 'preview': 'yo',
             'user_text': 'good', 'assistant_text': 'morning'},
        ])
        # Insert a tag for sess-m1
        self.conn.execute(
            "INSERT INTO tags (tag, session_id, exchange_idx, source, created_at) VALUES (?, ?, ?, ?, ?)",
            ('rust', 'sess-m1', 0, 'auto', '2025-01-01T00:01:00Z'),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_prune_session(self):
        """Prune a session removes session, exchanges, FTS entries, and tags."""
        prune_session(self.conn, 'sess-m1')
        self.assertIsNone(get_session(self.conn, 'sess-m1'))
        self.assertEqual(get_exchanges(self.conn, 'sess-m1'), [])
        # Tags should also be gone
        cur = self.conn.execute("SELECT * FROM tags WHERE session_id='sess-m1'")
        self.assertEqual(len(cur.fetchall()), 0)

    def test_prune_before_date(self):
        """Prune sessions started before a given date."""
        count = prune_before_date(self.conn, '2025-03-01T00:00:00Z')
        self.assertEqual(count, 1)
        self.assertIsNone(get_session(self.conn, 'sess-m1'))
        self.assertIsNotNone(get_session(self.conn, 'sess-m2'))

    def test_get_stats(self):
        """get_stats returns summary statistics."""
        stats = get_stats(self.conn, db_path=self.db_path)
        self.assertEqual(stats['total_sessions'], 2)
        self.assertEqual(stats['total_exchanges'], 2)
        self.assertEqual(stats['total_tags'], 1)
        self.assertIn('db_size_bytes', stats)
        self.assertIsInstance(stats['projects'], list)

    def test_export_session_json(self):
        """Export a session as a complete JSON-serializable dict."""
        data = export_session_json(self.conn, 'sess-m1')
        self.assertEqual(data['session_id'], 'sess-m1')
        self.assertEqual(len(data['exchanges']), 1)
        self.assertEqual(len(data['tags']), 1)
        self.assertEqual(data['tags'][0]['tag'], 'rust')


class TestHighlightCRUD(unittest.TestCase):
    """Tests for highlight insert, get, and dedup."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-h', '/p', 'h', '2025-01-01T00:00:00Z')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_insert_and_get_highlight(self):
        """Insert a highlight and retrieve it for the session."""
        insert_highlight(self.conn, 'sess-h', 'Fixed the memory leak', 'bug,memory', 'manual', exchange_idx=3)
        rows = get_highlights(self.conn, 'sess-h')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['summary'], 'Fixed the memory leak')
        self.assertEqual(rows[0]['tags'], 'bug,memory')
        self.assertEqual(rows[0]['source'], 'manual')
        self.assertEqual(rows[0]['exchange_idx'], 3)

    def test_get_highlights_since_timestamp(self):
        """get_highlights with since= returns only newer highlights."""
        # Manually insert two highlights with known timestamps
        self.conn.execute(
            "INSERT INTO highlights (session_id, summary, tags, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ('sess-h', 'Old highlight', '', 'auto', '2025-01-01T00:00:00Z'),
        )
        self.conn.execute(
            "INSERT INTO highlights (session_id, summary, tags, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ('sess-h', 'New highlight', '', 'auto', '2025-06-01T00:00:00Z'),
        )
        self.conn.commit()
        rows = get_highlights(self.conn, 'sess-h', since='2025-03-01T00:00:00Z')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['summary'], 'New highlight')

    def test_duplicate_summary_ignored(self):
        """Inserting a duplicate (session_id, summary) pair is silently ignored."""
        insert_highlight(self.conn, 'sess-h', 'Same summary', 'tag', 'manual')
        insert_highlight(self.conn, 'sess-h', 'Same summary', 'other', 'auto')
        rows = get_highlights(self.conn, 'sess-h')
        self.assertEqual(len(rows), 1)

    def test_get_highlights_for_connections_enriched(self):
        """get_highlights_for_connections returns unchecked highlights across connections."""
        insert_session(self.conn, 'sess-w', '/w', 'hw', '2025-01-01T00:00:00Z')
        insert_session(self.conn, 'sess-t', '/t', 'ht', '2025-01-01T00:00:00Z')
        insert_connection(self.conn, 'sess-w', 'sess-t', 'performance work')
        insert_highlight(self.conn, 'sess-t', 'Optimised hot path', 'perf', 'manual')
        results = get_highlights_for_connections(self.conn, 'sess-w')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['summary'], 'Optimised hot path')
        self.assertEqual(results[0]['connection_topic'], 'performance work')


class TestConnectionCRUD(unittest.TestCase):
    """Tests for connection insert, get, update, and delete."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-w', '/w', 'hw', '2025-01-01T00:00:00Z')
        insert_session(self.conn, 'sess-t1', '/t1', 'ht1', '2025-01-01T00:00:00Z')
        insert_session(self.conn, 'sess-t2', '/t2', 'ht2', '2025-01-01T00:00:00Z')
        insert_session(self.conn, 'sess-other', '/o', 'ho', '2025-01-01T00:00:00Z')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_insert_and_get_connection(self):
        """Insert a connection and retrieve it."""
        insert_connection(self.conn, 'sess-w', 'sess-t1', 'ML experiments',
                          check_mode='explicit', delivery_mode='silent')
        conns = get_connections(self.conn, 'sess-w')
        self.assertEqual(len(conns), 1)
        c = conns[0]
        self.assertEqual(c['watcher_session'], 'sess-w')
        self.assertEqual(c['target_session'], 'sess-t1')
        self.assertEqual(c['topic'], 'ML experiments')
        self.assertEqual(c['check_mode'], 'explicit')
        self.assertEqual(c['delivery_mode'], 'silent')
        self.assertIsNone(c['last_checked_at'])

    def test_get_connections_returns_only_for_watcher(self):
        """get_connections does not return connections belonging to other watchers."""
        insert_connection(self.conn, 'sess-w', 'sess-t1', 'topic A')
        insert_connection(self.conn, 'sess-other', 'sess-t2', 'topic B')
        conns = get_connections(self.conn, 'sess-w')
        self.assertEqual(len(conns), 1)
        self.assertEqual(conns[0]['target_session'], 'sess-t1')

    def test_update_connection_check_state(self):
        """update_connection_check persists counter, interval, and last_checked_at."""
        insert_connection(self.conn, 'sess-w', 'sess-t1', 'topic')
        conn_id = get_connections(self.conn, 'sess-w')[0]['id']
        update_connection_check(self.conn, conn_id,
                                check_counter=3, check_interval=14,
                                last_checked_at='2025-06-01T12:00:00Z')
        c = get_connections(self.conn, 'sess-w')[0]
        self.assertEqual(c['check_counter'], 3)
        self.assertEqual(c['check_interval'], 14)
        self.assertEqual(c['last_checked_at'], '2025-06-01T12:00:00Z')

    def test_delete_connection(self):
        """delete_connection removes the row."""
        insert_connection(self.conn, 'sess-w', 'sess-t1', 'topic')
        self.assertEqual(len(get_connections(self.conn, 'sess-w')), 1)
        delete_connection(self.conn, 'sess-w', 'sess-t1')
        self.assertEqual(len(get_connections(self.conn, 'sess-w')), 0)

    def test_duplicate_connection_ignored(self):
        """Inserting a duplicate (watcher, target) pair is silently ignored."""
        insert_connection(self.conn, 'sess-w', 'sess-t1', 'topic A')
        insert_connection(self.conn, 'sess-w', 'sess-t1', 'topic B')
        conns = get_connections(self.conn, 'sess-w')
        self.assertEqual(len(conns), 1)
        self.assertEqual(conns[0]['topic'], 'topic A')


class TestSessionConfig(unittest.TestCase):
    """Tests for get_session_config / set_session_config."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'sess-cfg', '/p', 'h', '2025-01-01T00:00:00Z')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_set_and_get_config_value(self):
        """set_session_config writes a value; get_session_config reads it back."""
        set_session_config(self.conn, 'sess-cfg', 'auto_highlight', True)
        val = get_session_config(self.conn, 'sess-cfg', 'auto_highlight')
        self.assertIs(val, True)

    def test_get_nonexistent_key_returns_none(self):
        """get_session_config returns None for a key that was never set."""
        val = get_session_config(self.conn, 'sess-cfg', 'missing_key')
        self.assertIsNone(val)

    def test_set_overwrites_existing_value(self):
        """set_session_config merges into existing JSON, overwriting the same key."""
        set_session_config(self.conn, 'sess-cfg', 'threshold', 5)
        set_session_config(self.conn, 'sess-cfg', 'mode', 'auto')
        set_session_config(self.conn, 'sess-cfg', 'threshold', 10)
        self.assertEqual(get_session_config(self.conn, 'sess-cfg', 'threshold'), 10)
        # Other keys must survive the overwrite
        self.assertEqual(get_session_config(self.conn, 'sess-cfg', 'mode'), 'auto')


class TestHighlightsForConnections(unittest.TestCase):
    """Tests for get_highlights_for_connections filtering logic."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(self.conn, 'watcher', '/w', 'hw', '2025-01-01T00:00:00Z')
        insert_session(self.conn, 'target', '/t', 'ht', '2025-01-01T00:00:00Z')
        # Pre-insert highlights with fixed timestamps
        self.conn.execute(
            "INSERT INTO highlights (session_id, summary, tags, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ('target', 'Early highlight', '', 'auto', '2025-01-15T00:00:00Z'),
        )
        self.conn.execute(
            "INSERT INTO highlights (session_id, summary, tags, source, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ('target', 'Late highlight', '', 'auto', '2025-06-15T00:00:00Z'),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_highlights_after_last_checked_at(self):
        """Only highlights created after last_checked_at are returned."""
        insert_connection(self.conn, 'watcher', 'target', 'topic')
        conn_id = get_connections(self.conn, 'watcher')[0]['id']
        update_connection_check(self.conn, conn_id,
                                check_counter=1, check_interval=7,
                                last_checked_at='2025-03-01T00:00:00Z')
        results = get_highlights_for_connections(self.conn, 'watcher')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['summary'], 'Late highlight')

    def test_returns_all_highlights_when_never_checked(self):
        """When last_checked_at is NULL (first check), all highlights are returned."""
        insert_connection(self.conn, 'watcher', 'target', 'topic')
        results = get_highlights_for_connections(self.conn, 'watcher')
        self.assertEqual(len(results), 2)


if __name__ == '__main__':
    unittest.main()
