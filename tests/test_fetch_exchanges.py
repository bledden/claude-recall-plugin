#!/usr/bin/env python3
"""Unit tests for fetch_exchanges.py — DB-backed version."""

import io
import os
import sys
import tempfile
import shutil
import unittest
from contextlib import redirect_stdout
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from db import get_connection, insert_session, insert_exchanges
import fetch_exchanges
from fetch_exchanges import (
    parse_last_n,
    format_exchanges,
    get_session_dates,
    format_cross_project_results,
    _build_arg_parser,
)
from utils import MAX_CHARS_PER_MESSAGE


# ---------------------------------------------------------------------------
# Helper: run main() against a temp DB by patching get_connection + argv
# ---------------------------------------------------------------------------

def run_main(conn, argv):
    """Invoke fetch_exchanges.main() with a patched connection and argv.

    Returns captured stdout. SAFETY: get_connection is monkeypatched so main()
    NEVER touches the real database — it always uses the temp-DB `conn` passed in.
    The connection is wrapped so main()'s `conn.close()` does not close the
    test-owned connection.
    """
    class _NoCloseConn:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            pass  # keep the test-owned connection open

    orig_get_conn = fetch_exchanges.get_connection
    orig_argv = sys.argv
    buf = io.StringIO()
    try:
        fetch_exchanges.get_connection = lambda *a, **k: _NoCloseConn(conn)
        sys.argv = ['fetch_exchanges.py'] + list(argv)
        with redirect_stdout(buf):
            fetch_exchanges.main()
    finally:
        fetch_exchanges.get_connection = orig_get_conn
        sys.argv = orig_argv
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Shared DB fixture helpers
# ---------------------------------------------------------------------------

def make_db(tmp_dir: str):
    """Return an open connection to a fresh in-temp DB."""
    db_path = Path(tmp_dir) / 'recall.db'
    return get_connection(db_path=db_path)


def seed_session(conn, session_id: str, project_path: str = '/proj/foo',
                 project_hash: str = 'abc123',
                 exchanges=None):
    """Insert a session and optional exchanges."""
    insert_session(conn, session_id, project_path, project_hash,
                   started_at='2025-01-15T09:00:00Z')
    if exchanges:
        insert_exchanges(conn, session_id, exchanges)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_EXCHANGES = [
    {
        'idx': 1,
        'timestamp': '2025-01-15T09:00:00Z',
        'preview': 'Help with authentication',
        'user_text': 'Help me implement authentication flow',
        'assistant_text': 'Sure, here is how to set up auth...',
    },
    {
        'idx': 2,
        'timestamp': '2025-01-15T09:05:00Z',
        'preview': 'Fix the login bug',
        'user_text': 'There is a bug in the login page',
        'assistant_text': 'I can help debug that.',
    },
    {
        'idx': 3,
        'timestamp': '2025-01-15T09:10:00Z',
        'preview': 'Warp divergence performance',
        'user_text': 'The warp divergence is causing slowdowns',
        'assistant_text': 'Warp divergence happens when threads in a warp take different paths.',
    },
    {
        'idx': 4,
        'timestamp': '2025-01-15T10:00:00Z',
        'preview': 'Refactor the module',
        'user_text': 'How should I refactor this module?',
        'assistant_text': 'Consider splitting it by responsibility.',
    },
    {
        'idx': 5,
        'timestamp': '2025-01-15T11:00:00Z',
        'preview': 'Write unit tests',
        'user_text': 'Write unit tests for the parser',
        'assistant_text': 'Here are some test cases...',
    },
]


# ---------------------------------------------------------------------------
# parse_last_n
# ---------------------------------------------------------------------------

class TestParseLastN(unittest.TestCase):

    def test_last5_from_100(self):
        result = parse_last_n('last5', 100)
        self.assertEqual(result, {96, 97, 98, 99, 100})

    def test_last10_from_100(self):
        result = parse_last_n('last10', 100)
        self.assertEqual(result, set(range(91, 101)))

    def test_last_exceeds_total(self):
        result = parse_last_n('last10', 5)
        self.assertEqual(result, {1, 2, 3, 4, 5})

    def test_invalid_format(self):
        result = parse_last_n('invalid', 100)
        self.assertEqual(result, set())

    def test_last_without_number(self):
        result = parse_last_n('last', 100)
        self.assertEqual(result, set())

    def test_last1(self):
        result = parse_last_n('last1', 5)
        self.assertEqual(result, {5})


# ---------------------------------------------------------------------------
# test_last_n_fetches_correct_count (DB-backed)
# ---------------------------------------------------------------------------

class TestLastNFetchesCorrectCount(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = make_db(self.tmp)
        seed_session(self.conn, 'sess-1', exchanges=SAMPLE_EXCHANGES)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_last_n_returns_correct_count(self):
        from db import get_exchanges
        all_exs = get_exchanges(self.conn, 'sess-1')
        total = len(all_exs)
        self.assertEqual(total, 5)

        indices = parse_last_n('last3', total)
        self.assertEqual(len(indices), 3)

        selected = [ex for ex in all_exs if ex['idx'] in indices]
        self.assertEqual(len(selected), 3)
        # Should be the 3 most recent
        self.assertEqual(sorted(ex['idx'] for ex in selected), [3, 4, 5])


# ---------------------------------------------------------------------------
# test_search_finds_in_user_text (DB-backed)
# ---------------------------------------------------------------------------

class TestSearchFindsInUserText(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = make_db(self.tmp)
        seed_session(self.conn, 'sess-1', exchanges=SAMPLE_EXCHANGES)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fts_finds_in_user_text(self):
        from db import search_exchanges_fts
        results = search_exchanges_fts(self.conn, 'authentication', session_id='sess-1')
        self.assertGreater(len(results), 0)
        user_texts = [r.get('user_text', '') for r in results]
        self.assertTrue(any('authentication' in t.lower() for t in user_texts))

    def test_search_no_results(self):
        from db import search_exchanges_fts
        results = search_exchanges_fts(self.conn, 'completelymissingterm', session_id='sess-1')
        self.assertEqual(len(results), 0)


# ---------------------------------------------------------------------------
# test_search_finds_in_assistant_text (DB-backed)
# ---------------------------------------------------------------------------

class TestSearchFindsInAssistantText(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = make_db(self.tmp)
        seed_session(self.conn, 'sess-1', exchanges=SAMPLE_EXCHANGES)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fts_finds_in_assistant_text(self):
        from db import search_exchanges_fts
        # 'divergence' appears in exchange #3 assistant_text
        results = search_exchanges_fts(self.conn, 'divergence', session_id='sess-1')
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]['idx'], 3)


# ---------------------------------------------------------------------------
# test_search_no_results
# ---------------------------------------------------------------------------

class TestSearchNoResults(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = make_db(self.tmp)
        seed_session(self.conn, 'sess-1', exchanges=SAMPLE_EXCHANGES)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_results(self):
        from db import search_exchanges_fts
        results = search_exchanges_fts(self.conn, 'xyzzy_not_present_zzz', session_id='sess-1')
        self.assertEqual(results, [])


# ---------------------------------------------------------------------------
# test_around_time_finds_closest
# ---------------------------------------------------------------------------

class TestAroundTimeFindsClosest(unittest.TestCase):

    def test_finds_exchange_around_9am(self):
        from datetime import datetime
        from utils import find_exchanges_by_time

        target = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
        indices = find_exchanges_by_time(SAMPLE_EXCHANGES, target)
        # Exchange #1 is at 9:00 — should be in result
        self.assertIn(1, indices)

    def test_finds_exchange_around_10am(self):
        from datetime import datetime
        from utils import find_exchanges_by_time

        target = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)
        indices = find_exchanges_by_time(SAMPLE_EXCHANGES, target)
        # Exchange #4 is at 10:00
        self.assertIn(4, indices)


# ---------------------------------------------------------------------------
# test_format_exchanges_output
# ---------------------------------------------------------------------------

class TestFormatExchangesOutput(unittest.TestCase):

    def test_format_single_exchange(self):
        exchanges = [{
            'idx': 1,
            'user_text': 'Hello there',
            'assistant_text': 'Hi! How can I help?',
            'timestamp': '2025-01-15T09:00:00Z',
            'preview': 'Hello there',
        }]
        result = format_exchanges(exchanges)
        self.assertIn('Exchange #1', result)
        self.assertIn('Hello there', result)
        self.assertIn('Hi! How can I help?', result)

    def test_format_empty_list(self):
        result = format_exchanges([])
        self.assertIn('No exchanges found', result)

    def test_format_multiple_exchanges(self):
        result = format_exchanges(SAMPLE_EXCHANGES)
        self.assertIn('Exchange #1', result)
        self.assertIn('Exchange #5', result)

    def test_respects_char_limit(self):
        big_text = 'x' * (MAX_CHARS_PER_MESSAGE + 100)
        exchanges = [{
            'idx': 1,
            'user_text': big_text,
            'assistant_text': 'short reply',
            'timestamp': '2025-01-15T09:00:00Z',
            'preview': 'big',
        }]
        result = format_exchanges(exchanges)
        # Should contain truncation marker
        self.assertIn('truncated', result)


# ---------------------------------------------------------------------------
# test_cross_session_search
# ---------------------------------------------------------------------------

class TestCrossSessionSearch(unittest.TestCase):
    """Search with --all scope across 2 sessions in the same project."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = make_db(self.tmp)

        # Session A — project hash 'proj-hash-1'
        seed_session(self.conn, 'sess-A', project_path='/proj/foo',
                     project_hash='proj-hash-1',
                     exchanges=[
                         {
                             'idx': 1,
                             'timestamp': '2025-01-15T09:00:00Z',
                             'preview': 'triton kernel',
                             'user_text': 'Write a triton kernel for matmul',
                             'assistant_text': 'Here is a triton kernel...',
                         }
                     ])

        # Session B — same project hash
        seed_session(self.conn, 'sess-B', project_path='/proj/foo',
                     project_hash='proj-hash-1',
                     exchanges=[
                         {
                             'idx': 1,
                             'timestamp': '2025-01-16T10:00:00Z',
                             'preview': 'optimize triton kernel',
                             'user_text': 'Optimize the triton kernel further',
                             'assistant_text': 'You can use shared memory...',
                         }
                     ])

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cross_session_finds_both(self):
        from db import search_exchanges_fts
        results = search_exchanges_fts(self.conn, 'triton', project_hash='proj-hash-1', limit=20)
        session_ids = {r['session_id'] for r in results}
        self.assertIn('sess-A', session_ids)
        self.assertIn('sess-B', session_ids)
        self.assertEqual(len(results), 2)

    def test_session_scoped_finds_only_own(self):
        from db import search_exchanges_fts
        results = search_exchanges_fts(self.conn, 'triton', session_id='sess-A')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['session_id'], 'sess-A')


# ---------------------------------------------------------------------------
# test_global_search
# ---------------------------------------------------------------------------

class TestGlobalSearch(unittest.TestCase):
    """Search across 2 projects."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = make_db(self.tmp)

        seed_session(self.conn, 'sess-X', project_path='/proj/alpha',
                     project_hash='hash-alpha',
                     exchanges=[
                         {
                             'idx': 1,
                             'timestamp': '2025-01-15T09:00:00Z',
                             'preview': 'metal backend',
                             'user_text': 'Implement the Metal backend for MLIR',
                             'assistant_text': 'Metal shaders use MSL...',
                         }
                     ])

        seed_session(self.conn, 'sess-Y', project_path='/proj/beta',
                     project_hash='hash-beta',
                     exchanges=[
                         {
                             'idx': 1,
                             'timestamp': '2025-01-16T11:00:00Z',
                             'preview': 'metal performance',
                             'user_text': 'How to profile Metal shaders?',
                             'assistant_text': 'Use Xcode GPU frame debugger...',
                         }
                     ])

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_global_search_finds_both_projects(self):
        from db import search_exchanges_global
        results = search_exchanges_global(self.conn, 'Metal', limit=20)
        project_paths = {r['project_path'] for r in results}
        self.assertIn('/proj/alpha', project_paths)
        self.assertIn('/proj/beta', project_paths)
        self.assertEqual(len(results), 2)

    def test_global_results_include_project_path(self):
        from db import search_exchanges_global
        results = search_exchanges_global(self.conn, 'Metal', limit=20)
        for r in results:
            self.assertIn('project_path', r)
            self.assertIsNotNone(r['project_path'])

    def test_format_cross_project_results_groups_correctly(self):
        from db import search_exchanges_global
        results = search_exchanges_global(self.conn, 'Metal', limit=20)
        formatted = format_cross_project_results(results, 'Metal')
        self.assertIn('alpha', formatted)
        self.assertIn('beta', formatted)
        self.assertIn('Metal', formatted)


# ---------------------------------------------------------------------------
# get_session_dates
# ---------------------------------------------------------------------------

class TestGetSessionDates(unittest.TestCase):

    def test_single_date(self):
        exchanges = [
            {'idx': 1, 'timestamp': '2025-01-05T09:00:00Z'},
            {'idx': 2, 'timestamp': '2025-01-05T10:00:00Z'},
        ]
        result = get_session_dates(exchanges)
        self.assertEqual(result, ['2025-01-05'])

    def test_multiple_dates(self):
        exchanges = [
            {'idx': 1, 'timestamp': '2025-01-05T09:00:00Z'},
            {'idx': 2, 'timestamp': '2025-01-06T10:00:00Z'},
            {'idx': 3, 'timestamp': '2025-01-07T11:00:00Z'},
        ]
        result = get_session_dates(exchanges)
        self.assertEqual(result, ['2025-01-05', '2025-01-06', '2025-01-07'])

    def test_empty_exchanges(self):
        result = get_session_dates([])
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# WI-7: reject last<=0 instead of returning ALL exchanges labeled 'last0'
# ---------------------------------------------------------------------------

class TestLastNonPositiveRejected(unittest.TestCase):
    """'last0' (and any last<=0) must be rejected, not return all exchanges."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = make_db(self.tmp)
        seed_session(self.conn, 'sess-1', exchanges=SAMPLE_EXCHANGES)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_last0_is_invalid_and_returns_nothing(self):
        out = run_main(self.conn, ['--session', 'sess-1', 'last0'])
        self.assertIn('Invalid format', out)
        # Must NOT fetch/label exchanges as 'last0'
        self.assertNotIn('Fetched', out)
        self.assertNotIn('Exchange #', out)

    def test_negative_last_is_invalid(self):
        # 'last-3' must also be rejected (not parsed as returning all)
        out = run_main(self.conn, ['--session', 'sess-1', 'last-3'])
        self.assertIn('Invalid format', out)
        self.assertNotIn('Fetched', out)

    def test_last3_still_works(self):
        # Regression guard: a valid positive N still fetches.
        out = run_main(self.conn, ['--session', 'sess-1', 'last3'])
        self.assertIn('Fetched 3 exchange(s)', out)


# ---------------------------------------------------------------------------
# WI-9: scope flags (--all/--global/--project) mutually exclusive in argparse
# ---------------------------------------------------------------------------

class TestScopeFlagsMutuallyExclusive(unittest.TestCase):
    """--all / --global / --project must conflict via an argparse group."""

    def test_global_and_all_conflict(self):
        parser = _build_arg_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(['search', 'auth', '--global', '--all'])

    def test_global_and_project_conflict(self):
        parser = _build_arg_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(['search', 'auth', '--global', '--project', 'foo'])

    def test_all_and_project_conflict(self):
        parser = _build_arg_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(['search', 'auth', '--all', '--project', 'foo'])

    def test_single_scope_flag_is_accepted(self):
        # Regression guard: any single scope flag still parses fine.
        parser = _build_arg_parser()
        args = parser.parse_args(['search', 'auth', '--global'])
        self.assertTrue(args.scope_global)
        args = parser.parse_args(['search', 'auth', '--all'])
        self.assertTrue(args.scope_all)
        args = parser.parse_args(['search', 'auth', '--project', 'foo'])
        self.assertEqual(args.project, 'foo')


# ---------------------------------------------------------------------------
# WI-28: dead-code removal must not change live-path behavior
# ---------------------------------------------------------------------------

class TestDeadCodeRemovalBehavior(unittest.TestCase):
    """Behavior of live paths is preserved after removing unreachable code."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = make_db(self.tmp)
        seed_session(self.conn, 'sess-1', exchanges=SAMPLE_EXCHANGES)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_search_term_still_prompts(self):
        # The 'and not args.tag' guard removal must not change this message.
        out = run_main(self.conn, ['--session', 'sess-1', 'search'])
        self.assertIn('specify a search term', out)

    def test_session_search_returns_results(self):
        out = run_main(self.conn, ['--session', 'sess-1', 'search', 'authentication'])
        self.assertIn('authentication', out.lower())
        self.assertIn('Fetched', out)

    def test_no_args_defaults_to_last5(self):
        # Removing the duplicate raw_args default must keep the last5 default.
        out = run_main(self.conn, ['--session', 'sess-1'])
        self.assertIn('Fetched', out)
        self.assertIn('last5', out)


if __name__ == '__main__':
    unittest.main()
