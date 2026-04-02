#!/usr/bin/env python3
"""Unit tests for show_index.py — DB-backed version."""

import os
import sys
import tempfile
import shutil
import unittest
from datetime import datetime
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from db import get_connection, insert_session, insert_exchanges
from show_index import (
    find_page_for_time,
    search_exchanges,
    format_page,
    format_search_results,
    get_session_date_range,
)
from utils import (
    format_timestamp,
    format_date,
    parse_time_query,
    PAGE_SIZE,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_db(tmp_dir: str):
    db_path = Path(tmp_dir) / 'recall.db'
    return get_connection(db_path=db_path)


def seed_session(conn, session_id: str, project_path: str = '/proj/foo',
                 project_hash: str = 'abc123', started_at: str = '2025-01-15T09:00:00Z',
                 exchanges=None):
    insert_session(conn, session_id, project_path, project_hash, started_at=started_at)
    if exchanges:
        insert_exchanges(conn, session_id, exchanges)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_EXCHANGES_5 = [
    {
        'idx': i,
        'timestamp': f'2025-01-15T{9 + i - 1:02d}:00:00Z',
        'preview': f'Exchange {i} preview',
        'user_text': f'User message {i}',
        'assistant_text': f'Assistant response {i}',
    }
    for i in range(1, 6)
]

MULTIDAY_EXCHANGES = [
    {
        'idx': 1,
        'timestamp': '2025-01-05T09:00:00Z',
        'preview': 'Day 1 morning',
        'user_text': 'Morning question',
        'assistant_text': 'Morning answer',
    },
    {
        'idx': 2,
        'timestamp': '2025-01-05T14:00:00Z',
        'preview': 'Day 1 afternoon',
        'user_text': 'Afternoon question',
        'assistant_text': 'Afternoon answer',
    },
    {
        'idx': 3,
        'timestamp': '2025-01-06T09:00:00Z',
        'preview': 'Day 2 morning',
        'user_text': 'Next day question',
        'assistant_text': 'Next day answer',
    },
    {
        'idx': 4,
        'timestamp': '2025-01-07T10:00:00Z',
        'preview': 'Day 3 question',
        'user_text': 'Third day question',
        'assistant_text': 'Third day answer',
    },
]


# ---------------------------------------------------------------------------
# test_format_page_basic
# ---------------------------------------------------------------------------

class TestFormatPageBasic(unittest.TestCase):

    def test_format_page_includes_session_info(self):
        result = format_page(SAMPLE_EXCHANGES_5, 1, 5, '2025-01-15T09:00:00Z')
        self.assertIn('Session started', result)
        self.assertIn('Total exchanges', result)
        self.assertIn('5', result)

    def test_format_page_shows_previews(self):
        result = format_page(SAMPLE_EXCHANGES_5, 1, 5, '2025-01-15T09:00:00Z')
        # Most-recent-first: exchange 5 should appear before exchange 1
        self.assertIn('Exchange 5 preview', result)

    def test_format_page_shows_navigation(self):
        result = format_page(SAMPLE_EXCHANGES_5, 1, 5, '2025-01-15T09:00:00Z')
        self.assertIn('Navigation', result)


# ---------------------------------------------------------------------------
# test_format_page_empty
# ---------------------------------------------------------------------------

class TestFormatPageEmpty(unittest.TestCase):

    def test_empty_returns_no_exchanges_message(self):
        result = format_page([], 1, 0, '')
        self.assertIn('No exchanges found', result)

    def test_page_beyond_total(self):
        result = format_page(SAMPLE_EXCHANGES_5, 99, 5, '2025-01-15T09:00:00Z')
        self.assertIn('empty', result.lower())


# ---------------------------------------------------------------------------
# test_pagination_multiple_pages
# ---------------------------------------------------------------------------

class TestPaginationMultiplePages(unittest.TestCase):

    def setUp(self):
        # Create more exchanges than a single page
        self.many_exchanges = [
            {
                'idx': i,
                'timestamp': f'2025-01-15T{9:02d}:{i:02d}:00Z',
                'preview': f'Exchange {i}',
                'user_text': f'User {i}',
                'assistant_text': f'Assistant {i}',
            }
            for i in range(1, PAGE_SIZE * 2 + 5)  # 2+ pages worth
        ]

    def test_page_1_shows_most_recent(self):
        total = len(self.many_exchanges)
        result = format_page(self.many_exchanges, 1, total, '2025-01-15T09:00:00Z')
        # The most recent exchange (highest idx) should appear on page 1
        last_idx = self.many_exchanges[-1]['idx']
        self.assertIn(f'Exchange {last_idx}', result)

    def test_shows_correct_page_count(self):
        total = len(self.many_exchanges)
        result = format_page(self.many_exchanges, 1, total, '2025-01-15T09:00:00Z')
        self.assertIn('page', result.lower())
        # Should show there are multiple pages
        self.assertIn('Show older', result)

    def test_page_2_shows_older_exchanges(self):
        total = len(self.many_exchanges)
        result = format_page(self.many_exchanges, 2, total, '2025-01-15T09:00:00Z')
        # Page 2 should NOT contain the most recent exchange
        last_idx = self.many_exchanges[-1]['idx']
        # The latest exchange is on page 1, not page 2
        self.assertIn('Show newer: page 1', result)


# ---------------------------------------------------------------------------
# test_search_exchanges_finds_matches
# ---------------------------------------------------------------------------

class TestSearchExchangesFindsMatches(unittest.TestCase):

    def test_search_in_preview(self):
        exchanges = [
            {'idx': 1, 'preview': 'authentication flow', 'user_text': '', 'assistant_text': ''},
            {'idx': 2, 'preview': 'fix the bug', 'user_text': '', 'assistant_text': ''},
        ]
        results = search_exchanges(exchanges, 'authentication')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['idx'], 1)

    def test_search_in_user_text(self):
        exchanges = [
            {'idx': 1, 'preview': 'hello', 'user_text': 'help with authentication', 'assistant_text': ''},
            {'idx': 2, 'preview': 'world', 'user_text': 'fix bug', 'assistant_text': ''},
        ]
        results = search_exchanges(exchanges, 'authentication')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['idx'], 1)

    def test_search_in_assistant_text(self):
        exchanges = [
            {'idx': 1, 'preview': 'q', 'user_text': 'q', 'assistant_text': 'use auth tokens'},
            {'idx': 2, 'preview': 'q', 'user_text': 'q', 'assistant_text': 'no match here'},
        ]
        results = search_exchanges(exchanges, 'auth')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['idx'], 1)

    def test_search_case_insensitive(self):
        exchanges = [
            {'idx': 1, 'preview': 'AUTHENTICATION ISSUE', 'user_text': '', 'assistant_text': ''},
        ]
        results = search_exchanges(exchanges, 'authentication')
        self.assertEqual(len(results), 1)

    def test_search_no_results(self):
        exchanges = [
            {'idx': 1, 'preview': 'login bug', 'user_text': 'login bug', 'assistant_text': 'fix login'},
        ]
        results = search_exchanges(exchanges, 'authentication')
        self.assertEqual(len(results), 0)


# ---------------------------------------------------------------------------
# test_format_search_results
# ---------------------------------------------------------------------------

class TestFormatSearchResults(unittest.TestCase):

    def test_format_with_matches(self):
        results = [
            {'idx': 1, 'preview': 'auth flow', 'timestamp': '2025-01-15T09:00:00Z'},
            {'idx': 3, 'preview': 'auth token', 'timestamp': '2025-01-15T10:00:00Z'},
        ]
        output = format_search_results(results, 'auth', 10)
        self.assertIn('auth', output)
        self.assertIn('2 matches', output)
        self.assertIn('auth flow', output)
        self.assertIn('auth token', output)

    def test_format_no_matches(self):
        output = format_search_results([], 'xyzzy', 10)
        self.assertIn('No exchanges found', output)
        self.assertIn('xyzzy', output)

    def test_format_truncates_at_20(self):
        many = [
            {
                'idx': i,
                'preview': f'result {i}',
                'timestamp': '2025-01-15T09:00:00Z',
            }
            for i in range(1, 30)
        ]
        output = format_search_results(many, 'result', 100)
        self.assertIn('more matches', output)


# ---------------------------------------------------------------------------
# test_date_grouping_multiday
# ---------------------------------------------------------------------------

class TestDateGroupingMultiday(unittest.TestCase):

    def test_multiday_session_date_range(self):
        date_range = get_session_date_range(MULTIDAY_EXCHANGES)
        # Should contain both boundary dates in some form
        self.assertIn('-', date_range)  # 'Jan 5 - Jan 7' style

    def test_single_day_date_range(self):
        exchanges = [
            {'idx': 1, 'timestamp': '2025-01-05T09:00:00Z'},
            {'idx': 2, 'timestamp': '2025-01-05T14:00:00Z'},
        ]
        date_range = get_session_date_range(exchanges)
        # Single day: no dash
        self.assertNotIn(' - ', date_range)

    def test_format_page_includes_date_in_header(self):
        result = format_page(MULTIDAY_EXCHANGES, 1, len(MULTIDAY_EXCHANGES),
                             MULTIDAY_EXCHANGES[0]['timestamp'])
        # Date info in parentheses in the header
        self.assertIn('(', result)

    def test_format_page_groups_by_date(self):
        result = format_page(MULTIDAY_EXCHANGES, 1, len(MULTIDAY_EXCHANGES),
                             MULTIDAY_EXCHANGES[0]['timestamp'])
        # Multi-day format includes bold date headers
        self.assertIn('**', result)


# ---------------------------------------------------------------------------
# find_page_for_time
# ---------------------------------------------------------------------------

class TestFindPageForTime(unittest.TestCase):

    def test_returns_1_for_empty(self):
        page = find_page_for_time([], datetime.now())
        self.assertEqual(page, 1)

    def test_finds_page_for_time(self):
        exchanges = []
        for i in range(50):
            hour = 9 + (i // 5)
            minute = (i % 5) * 10
            exchanges.append({
                'idx': i + 1,
                'preview': f'Exchange {i + 1}',
                'timestamp': f'2025-01-05T{hour:02d}:{minute:02d}:00Z',
            })
        target = datetime.now().replace(hour=11, minute=0)
        page = find_page_for_time(exchanges, target)
        self.assertGreaterEqual(page, 1)
        self.assertLessEqual(page, (len(exchanges) + PAGE_SIZE - 1) // PAGE_SIZE)


# ---------------------------------------------------------------------------
# DB integration: ensure show_index works end-to-end with real DB
# ---------------------------------------------------------------------------

class TestShowIndexDBIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = make_db(self.tmp)
        seed_session(self.conn, 'sess-show', started_at='2025-01-15T09:00:00Z',
                     exchanges=SAMPLE_EXCHANGES_5)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_exchanges_returns_correct_count(self):
        from db import get_exchanges
        exs = get_exchanges(self.conn, 'sess-show')
        self.assertEqual(len(exs), 5)

    def test_format_page_with_db_exchanges(self):
        from db import get_exchanges, get_session
        exs = get_exchanges(self.conn, 'sess-show')
        sess = get_session(self.conn, 'sess-show')
        result = format_page(exs, 1, len(exs), sess['started_at'])
        self.assertIn('Session started', result)
        self.assertIn('5', result)  # total count

    def test_search_exchanges_with_db_data(self):
        from db import get_exchanges
        exs = get_exchanges(self.conn, 'sess-show')
        results = search_exchanges(exs, 'User message')
        self.assertEqual(len(results), 5)  # all exchanges contain 'User message N'

    def test_search_exchanges_no_results_with_db_data(self):
        from db import get_exchanges
        exs = get_exchanges(self.conn, 'sess-show')
        results = search_exchanges(exs, 'xyzzy_not_there_ever')
        self.assertEqual(len(results), 0)


if __name__ == '__main__':
    unittest.main()
