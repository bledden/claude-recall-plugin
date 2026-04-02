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
        self.assertEqual(data['session']['session_id'], 'sess-m1')
        self.assertEqual(len(data['exchanges']), 1)
        self.assertEqual(len(data['tags']), 1)
        self.assertEqual(data['tags'][0]['tag'], 'rust')


if __name__ == '__main__':
    unittest.main()
