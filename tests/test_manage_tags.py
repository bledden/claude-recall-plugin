#!/usr/bin/env python3
"""Unit tests for manage_tags.py — tagging management functions."""

import os
import shutil
import sys
import tempfile
import unittest

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from db import get_connection, insert_session, insert_exchanges, get_tags
from manage_tags import (
    add_tag,
    list_tags,
    search_by_tag,
    get_tags_by_session,
    format_tag_list,
    resolve_project_filter,
)


class TestAddTag(unittest.TestCase):
    """Tests for add_tag()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(
            self.conn, 'sess-001', '/proj/alpha', 'hash1',
            '2026-01-01T00:00:00Z',
        )
        insert_exchanges(self.conn, 'sess-001', [
            {
                'idx': 0, 'timestamp': '2026-01-01T00:01:00Z',
                'preview': 'hello', 'user_text': 'hello world',
                'assistant_text': 'hi there',
            },
        ])

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_add_session_tag(self):
        """add_tag with no exchange_idx attaches a session-level tag."""
        add_tag(self.conn, 'rust', 'sess-001')
        tags = get_tags(self.conn, session_id='sess-001')
        self.assertEqual(len(tags), 1)
        self.assertEqual(tags[0]['tag'], 'rust')
        self.assertEqual(tags[0]['source'], 'manual')
        self.assertIsNone(tags[0]['exchange_idx'])

    def test_add_exchange_tag(self):
        """add_tag with exchange_idx attaches a tag to a specific exchange."""
        add_tag(self.conn, 'cuda', 'sess-001', exchange_idx=0)
        tags = get_tags(self.conn, session_id='sess-001')
        self.assertEqual(len(tags), 1)
        self.assertEqual(tags[0]['tag'], 'cuda')
        self.assertEqual(tags[0]['exchange_idx'], 0)

    def test_duplicate_tag_ignored(self):
        """Adding the same tag twice does not create duplicates."""
        add_tag(self.conn, 'python', 'sess-001')
        add_tag(self.conn, 'python', 'sess-001')
        tags = get_tags(self.conn, session_id='sess-001')
        self.assertEqual(len(tags), 1)

    # WI-12: add_tag must report whether the tag was newly inserted.
    def test_add_session_tag_returns_true_when_inserted(self):
        """A first-time session-level add reports it was inserted."""
        inserted = add_tag(self.conn, 'rust', 'sess-001')
        self.assertTrue(inserted)

    def test_add_session_tag_returns_false_when_already_present(self):
        """A repeated session-level add reports the tag already existed."""
        add_tag(self.conn, 'rust', 'sess-001')
        inserted = add_tag(self.conn, 'rust', 'sess-001')
        self.assertFalse(inserted)

    def test_add_exchange_tag_returns_true_when_inserted(self):
        """A first-time exchange-level add reports it was inserted."""
        inserted = add_tag(self.conn, 'cuda', 'sess-001', exchange_idx=0)
        self.assertTrue(inserted)

    def test_add_exchange_tag_returns_false_when_already_present(self):
        """A repeated exchange-level add reports the tag already existed."""
        add_tag(self.conn, 'cuda', 'sess-001', exchange_idx=0)
        inserted = add_tag(self.conn, 'cuda', 'sess-001', exchange_idx=0)
        self.assertFalse(inserted)


class TestResolveProjectFilter(unittest.TestCase):
    """Tests for resolve_project_filter() — WI-13."""

    def test_none_passes_through(self):
        """A None filter resolves to None (no filtering)."""
        self.assertIsNone(resolve_project_filter(None))

    def test_hash_passed_through_unchanged(self):
        """A 16-char hex hash is treated as a hash and returned unchanged."""
        h = 'a1b2c3d4e5f60718'
        self.assertEqual(resolve_project_filter(h), h)

    def test_path_resolved_to_hash(self):
        """A filesystem path is resolved to its project hash."""
        from utils import compute_project_hash
        path = '/proj/alpha'
        self.assertEqual(
            resolve_project_filter(path),
            compute_project_hash(path),
        )

    def test_home_relative_path_resolved_to_hash(self):
        """A ~-prefixed path is recognized as a path and resolved."""
        from utils import compute_project_hash
        path = '~/code/myproj'
        self.assertEqual(
            resolve_project_filter(path),
            compute_project_hash(path),
        )


class TestSearchByTag(unittest.TestCase):
    """Tests for search_by_tag()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(
            self.conn, 'sess-a', '/proj/alpha', 'hash1',
            '2026-01-01T00:00:00Z',
        )
        insert_session(
            self.conn, 'sess-b', '/proj/beta', 'hash2',
            '2026-01-02T00:00:00Z',
        )
        add_tag(self.conn, 'triton', 'sess-a')
        add_tag(self.conn, 'triton', 'sess-b')
        add_tag(self.conn, 'cuda', 'sess-a')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_finds_tagged_sessions(self):
        """search_by_tag returns sessions that have the given tag."""
        results = search_by_tag(self.conn, 'triton')
        session_ids = [r['session_id'] for r in results]
        self.assertIn('sess-a', session_ids)
        self.assertIn('sess-b', session_ids)

    def test_enriched_results_have_project_path(self):
        """Results include project_path and session_started fields."""
        results = search_by_tag(self.conn, 'triton')
        for r in results:
            self.assertIn('project_path', r)
            self.assertIn('session_started', r)

    def test_no_results_for_nonexistent_tag(self):
        """Returns empty list when no session has the given tag."""
        results = search_by_tag(self.conn, 'nonexistent_tag_xyz')
        self.assertEqual(results, [])

    def test_scoped_to_matching_tag(self):
        """Only returns sessions with the specific tag requested."""
        results = search_by_tag(self.conn, 'cuda')
        session_ids = [r['session_id'] for r in results]
        self.assertIn('sess-a', session_ids)
        self.assertNotIn('sess-b', session_ids)


class TestGetTagsBySession(unittest.TestCase):
    """Tests for get_tags_by_session()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.conn = get_connection(self.db_path)
        insert_session(
            self.conn, 'sess-x', '/proj/x', 'hx',
            '2026-01-01T00:00:00Z',
        )
        for tag in ['rust', 'cuda', 'triton', 'metal', 'python', 'extra']:
            add_tag(self.conn, tag, 'sess-x')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_returns_top_5_tags(self):
        """Returns at most 5 distinct tags per session."""
        result = get_tags_by_session(self.conn, ['sess-x'])
        self.assertIn('sess-x', result)
        self.assertLessEqual(len(result['sess-x']), 5)

    def test_returns_dict_keyed_by_session_id(self):
        """Return type is a dict mapping session_id -> list of tag strings."""
        result = get_tags_by_session(self.conn, ['sess-x'])
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result['sess-x'], list)
        for tag in result['sess-x']:
            self.assertIsInstance(tag, str)

    def test_missing_session_id_absent_from_result(self):
        """Session IDs with no tags do not appear in the result."""
        result = get_tags_by_session(self.conn, ['no-such-session'])
        self.assertNotIn('no-such-session', result)

    def test_multiple_sessions(self):
        """Handles a list of multiple session IDs."""
        insert_session(self.conn, 'sess-y', '/proj/y', 'hy', '2026-01-02T00:00:00Z')
        add_tag(self.conn, 'gpu', 'sess-y')
        result = get_tags_by_session(self.conn, ['sess-x', 'sess-y'])
        self.assertIn('sess-x', result)
        self.assertIn('sess-y', result)
        self.assertIn('gpu', result['sess-y'])


class TestFormatTagList(unittest.TestCase):
    """Tests for format_tag_list()."""

    def test_groups_by_tag_name(self):
        """Output groups entries by tag name."""
        tags = [
            {'tag': 'rust', 'source': 'auto', 'session_id': 's1', 'exchange_idx': None},
            {'tag': 'rust', 'source': 'manual', 'session_id': 's2', 'exchange_idx': None},
            {'tag': 'cuda', 'source': 'auto', 'session_id': 's1', 'exchange_idx': 0},
        ]
        output = format_tag_list(tags)
        self.assertIn('rust', output)
        self.assertIn('cuda', output)

    def test_shows_count(self):
        """Output includes occurrence count for each tag."""
        tags = [
            {'tag': 'rust', 'source': 'auto', 'session_id': 's1', 'exchange_idx': None},
            {'tag': 'rust', 'source': 'auto', 'session_id': 's2', 'exchange_idx': None},
        ]
        output = format_tag_list(tags)
        self.assertIn('2', output)

    def test_empty_list(self):
        """Empty tag list returns a no-tags message."""
        output = format_tag_list([])
        self.assertIsInstance(output, str)
        self.assertGreater(len(output), 0)


if __name__ == '__main__':
    unittest.main()
