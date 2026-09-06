#!/usr/bin/env python3
"""v2.4: search is ranked by BM25 relevance re-weighted by recency, with snippets."""
import os, shutil, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from db import get_connection, insert_session, insert_exchanges, search_exchanges_fts, search_exchanges_global


def _ts(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')


class TestRanking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn = get_connection(os.path.join(self.tmp, 't.db'))
        insert_session(self.conn, 's', '/p', 'h', _ts(400))
        insert_exchanges(self.conn, 's', [
            # idx 0: OLD but dense match; idx 1: NEW single mention; idx 2: new, no match
            {'idx': 0, 'timestamp': _ts(200), 'preview': 'old', 'user_text': 'warp shuffle reduction kernel',
             'assistant_text': 'the warp shuffle removes divergence in the reduction kernel; warp shuffle again'},
            {'idx': 1, 'timestamp': _ts(1), 'preview': 'new', 'user_text': 'unrelated question about warp scheduling',
             'assistant_text': 'answer'},
            {'idx': 2, 'timestamp': _ts(0), 'preview': 'none', 'user_text': 'lunch', 'assistant_text': 'ok'},
        ])

    def tearDown(self):
        self.conn.close(); shutil.rmtree(self.tmp, ignore_errors=True)

    def test_relevance_beats_recency_when_match_is_much_stronger(self):
        hits = search_exchanges_fts(self.conn, 'warp shuffle', session_id='s')
        self.assertEqual([h['idx'] for h in hits], [0])            # AND: idx 1 lacks 'shuffle'
        # Single term, near-equal relevance (BM25 saturates on repeats): recency tips it.
        hits = search_exchanges_fts(self.conn, 'warp', session_id='s')
        self.assertEqual(hits[0]['idx'], 1)
        # Pure relevance (half_life 0): the denser old match wins.
        hits = search_exchanges_fts(self.conn, 'warp', session_id='s', half_life_days=0)
        self.assertEqual(hits[0]['idx'], 0)

    def test_recency_breaks_ties(self):
        insert_exchanges(self.conn, 's', [
            {'idx': 3, 'timestamp': _ts(30), 'preview': 'a', 'user_text': 'tile size tuning', 'assistant_text': 'x'},
            {'idx': 4, 'timestamp': _ts(2), 'preview': 'b', 'user_text': 'tile size tuning', 'assistant_text': 'x'},
        ])
        hits = search_exchanges_fts(self.conn, 'tile', session_id='s')
        self.assertEqual([h['idx'] for h in hits], [4, 3])

    def test_half_life_zero_is_pure_relevance(self):
        insert_exchanges(self.conn, 's', [
            {'idx': 5, 'timestamp': _ts(300), 'preview': 'a', 'user_text': 'cutlass cutlass cutlass', 'assistant_text': 'x'},
            {'idx': 6, 'timestamp': _ts(0), 'preview': 'b', 'user_text': 'cutlass', 'assistant_text': 'x'},
        ])
        self.assertEqual(search_exchanges_fts(self.conn, 'cutlass', session_id='s', half_life_days=0)[0]['idx'], 5)

    def test_snippet_marks_the_match(self):
        hit = search_exchanges_fts(self.conn, 'divergence', session_id='s')[0]
        self.assertIn('«divergence»', hit['snippet'])
        self.assertNotIn('_bm25', hit)

    def test_global_search_has_snippet_and_project(self):
        hit = search_exchanges_global(self.conn, 'reduction')[0]
        self.assertIn('snippet', hit); self.assertEqual(hit['project_path'], '/p')


if __name__ == '__main__':
    unittest.main()
