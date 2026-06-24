#!/usr/bin/env python3
"""
Integration tests for Claude Context Recall plugin v2.

Covers full lifecycle, v1-to-v2 migration, and cross-project search
using the real SQLite backend — no mocks.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from prompt_submit import run_hook as prompt_hook
from post_compact import run_hook as compact_hook
from session_end import run_hook as end_hook
from db import (get_connection, get_session, get_exchanges, search_exchanges_fts,
                search_exchanges_global, get_stats, insert_connection, insert_highlight,
                get_highlights_for_connections)
from manage_tags import add_tag, search_by_tag
from manage_connections import inbox


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _write_transcript(path, pairs):
    """Write test transcript.

    Args:
        path: Path (str or Path) to write.
        pairs: List of (user_text, assistant_text, timestamp) tuples.
    """
    with open(path, 'w') as f:
        for user, asst, ts in pairs:
            f.write(json.dumps({
                'type': 'user',
                'message': {'content': [{'type': 'text', 'text': user}]},
                'timestamp': ts,
            }) + '\n')
            f.write(json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': asst}]},
                'timestamp': ts,
            }) + '\n')


# ---------------------------------------------------------------------------
# TestFullLifecycle
# ---------------------------------------------------------------------------

class TestFullLifecycle(unittest.TestCase):
    """End-to-end: create session, add exchanges, /clear, cross-session search,
    tagging, PostCompact nudge, SessionEnd."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.transcript = str(Path(self.temp_dir) / 'transcript.jsonl')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _hook_input(self, session_id, transcript, project_path='triton-metal',
                    project_hash='hash-001', user_prompt='hello'):
        return {
            'session_id': session_id,
            'transcript_path': transcript,
            'user_prompt': user_prompt,
            'project_path': project_path,
            'project_hash': project_hash,
        }

    def test_full_lifecycle(self):
        import prompt_submit

        # Prevent migration from picking up any real legacy index.json
        original_legacy_file = prompt_submit.LEGACY_INDEX_FILE
        prompt_submit.LEGACY_INDEX_FILE = Path(self.temp_dir) / 'no-such-index.json'

        try:
            self._run_lifecycle(prompt_submit)
        finally:
            prompt_submit.LEGACY_INDEX_FILE = original_legacy_file

    def _run_lifecycle(self, prompt_submit):
        # ------------------------------------------------------------------ #
        # Step 1 — Write transcript with 2 kernel exchanges                   #
        # ------------------------------------------------------------------ #
        _write_transcript(self.transcript, [
            ('How does triton reduction work?', 'It uses a tl.sum reduction kernel.', '2025-01-05T09:00:00Z'),
            ('What is the tile size for reduction?', 'Typically BLOCK_SIZE=256 for Metal backends.', '2025-01-05T09:01:00Z'),
        ])

        # ------------------------------------------------------------------ #
        # Step 2 — prompt_hook() → session created, exchange_count=2          #
        # ------------------------------------------------------------------ #
        result = prompt_hook(self._hook_input('sess-lifecycle-a', self.transcript),
                             db_path=self.db_path)
        self.assertEqual(result, {})

        conn = get_connection(self.db_path)
        session_a = get_session(conn, 'sess-lifecycle-a')
        self.assertIsNotNone(session_a)
        self.assertEqual(session_a['exchange_count'], 2)
        exchanges_a = get_exchanges(conn, 'sess-lifecycle-a')
        self.assertEqual(len(exchanges_a), 2)
        conn.close()

        # ------------------------------------------------------------------ #
        # Step 3 — New transcript (simulates /clear) with new session_id      #
        # ------------------------------------------------------------------ #
        transcript_b = str(Path(self.temp_dir) / 'transcript_b.jsonl')
        _write_transcript(transcript_b, [
            ('Now optimize the backward pass.', 'Use autograd with Metal dispatch.', '2025-01-05T10:00:00Z'),
        ])

        result = prompt_hook(self._hook_input('sess-lifecycle-b', transcript_b),
                             db_path=self.db_path)
        self.assertEqual(result, {})

        conn = get_connection(self.db_path)
        session_a_check = get_session(conn, 'sess-lifecycle-a')
        session_b = get_session(conn, 'sess-lifecycle-b')
        self.assertIsNotNone(session_a_check, 'Session A must still exist after /clear')
        self.assertIsNotNone(session_b, 'Session B must be created')
        conn.close()

        # ------------------------------------------------------------------ #
        # Step 4 — Cross-session FTS search for "reduction" within project    #
        # ------------------------------------------------------------------ #
        conn = get_connection(self.db_path)
        fts_results = search_exchanges_fts(conn, 'reduction',
                                           project_hash='hash-001')
        self.assertGreater(len(fts_results), 0,
                           'FTS search for "reduction" should match session A')
        conn.close()

        # ------------------------------------------------------------------ #
        # Step 5 — Manual tags on both sessions, search_by_tag finds both     #
        # ------------------------------------------------------------------ #
        conn = get_connection(self.db_path)
        add_tag(conn, 'triton', 'sess-lifecycle-a')
        add_tag(conn, 'triton', 'sess-lifecycle-b')
        conn.close()

        conn = get_connection(self.db_path)
        tag_results = search_by_tag(conn, 'triton')
        tagged_sessions = {r['session_id'] for r in tag_results}
        self.assertIn('sess-lifecycle-a', tagged_sessions)
        self.assertIn('sess-lifecycle-b', tagged_sessions)
        conn.close()

        # ------------------------------------------------------------------ #
        # Step 6 — compact_hook() → systemMessage with exchange count         #
        # ------------------------------------------------------------------ #
        compact_result = compact_hook(
            {'session_id': 'sess-lifecycle-a'},
            db_path=self.db_path,
        )
        self.assertIn('systemMessage', compact_result)
        msg = compact_result['systemMessage']
        # Should mention the exchange count for this session
        self.assertIn('2', msg)

        # ------------------------------------------------------------------ #
        # Step 7 — end_hook() → ended_at set                                 #
        # ------------------------------------------------------------------ #
        end_result = end_hook(
            {'session_id': 'sess-lifecycle-b'},
            db_path=self.db_path,
        )
        self.assertEqual(end_result, {})

        conn = get_connection(self.db_path)
        session_b_ended = get_session(conn, 'sess-lifecycle-b')
        self.assertIsNotNone(session_b_ended['ended_at'],
                             'ended_at must be set after end_hook')
        conn.close()

        # ------------------------------------------------------------------ #
        # Step 8 — get_stats() → total_sessions=2, total_exchanges=3         #
        # ------------------------------------------------------------------ #
        conn = get_connection(self.db_path)
        stats = get_stats(conn, db_path=self.db_path)
        conn.close()

        self.assertEqual(stats['total_sessions'], 2)
        self.assertEqual(stats['total_exchanges'], 3)


# ---------------------------------------------------------------------------
# TestMigrationFromV1
# ---------------------------------------------------------------------------

class TestMigrationFromV1(unittest.TestCase):
    """Migration from a v1-format index.json."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'
        self.transcript = str(Path(self.temp_dir) / 'transcript.jsonl')

        # Create the legacy recall dir within the temp dir
        self.legacy_dir = Path(self.temp_dir) / 'context-recall'
        self.legacy_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_index = self.legacy_dir / 'index.json'

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_migration_from_v1(self):
        import prompt_submit

        # ------------------------------------------------------------------ #
        # Step 1 — Create a v1-format index.json with 2 exchanges             #
        # ------------------------------------------------------------------ #
        v1_data = {
            'session_id': 'sess-legacy-v1',
            'session_start': '2025-01-01T08:00:00Z',
            'transcript_path': '/old/transcript.jsonl',
            '_byte_offset': 0,
            'exchanges': [
                {
                    'idx': 1,
                    'timestamp': '2025-01-01T08:01:00Z',
                    'preview': 'How does cuda reduction work?',
                    'user_text': 'How does cuda reduction work?',
                    'assistant_text': 'CUDA uses warp shuffle for reduction.',
                },
                {
                    'idx': 2,
                    'timestamp': '2025-01-01T08:02:00Z',
                    'preview': 'What about shared memory?',
                    'user_text': 'What about shared memory?',
                    'assistant_text': 'Shared memory tiles dramatically speed up reductions.',
                },
            ],
        }
        with open(self.legacy_index, 'w') as f:
            json.dump(v1_data, f)

        # ------------------------------------------------------------------ #
        # Step 2 — Write a minimal transcript for the new session             #
        # ------------------------------------------------------------------ #
        _write_transcript(self.transcript, [
            ('New session question.', 'New session answer.', '2025-01-05T10:00:00Z'),
        ])

        # ------------------------------------------------------------------ #
        # Step 3 — prompt_hook() with new session_id → triggers migration     #
        # ------------------------------------------------------------------ #
        original_legacy_file = prompt_submit.LEGACY_INDEX_FILE
        original_migration_checked = prompt_submit._migration_checked
        prompt_submit.LEGACY_INDEX_FILE = self.legacy_index
        prompt_submit._migration_checked = False  # reset so migration runs
        try:
            result = prompt_hook(
                {
                    'session_id': 'sess-new-after-migration',
                    'transcript_path': self.transcript,
                    'user_prompt': 'hello',
                    'project_path': '/tmp/project',
                    'project_hash': 'hash-mig',
                },
                db_path=self.db_path,
            )
        finally:
            prompt_submit.LEGACY_INDEX_FILE = original_legacy_file
            prompt_submit._migration_checked = original_migration_checked

        self.assertEqual(result, {})

        # ------------------------------------------------------------------ #
        # Step 4 — Legacy session in DB with its exchanges; new session exists #
        # ------------------------------------------------------------------ #
        conn = get_connection(self.db_path)

        legacy_session = get_session(conn, 'sess-legacy-v1')
        self.assertIsNotNone(legacy_session, 'Legacy session should be migrated into DB')
        legacy_exchanges = get_exchanges(conn, 'sess-legacy-v1')
        self.assertEqual(len(legacy_exchanges), 2, 'Both legacy exchanges should be in DB')

        new_session = get_session(conn, 'sess-new-after-migration')
        self.assertIsNotNone(new_session, 'New session should also exist')
        conn.close()

        # ------------------------------------------------------------------ #
        # Step 5 — index.json renamed to index.json.migrated                 #
        # ------------------------------------------------------------------ #
        migrated_path = self.legacy_index.with_suffix('.json.migrated')
        self.assertFalse(self.legacy_index.exists(),
                         'Original index.json should be gone after migration')
        self.assertTrue(migrated_path.exists(),
                        'index.json.migrated should exist after migration')

        # ------------------------------------------------------------------ #
        # Step 6 — Global search finds legacy content                         #
        # ------------------------------------------------------------------ #
        conn = get_connection(self.db_path)
        global_results = search_exchanges_global(conn, 'cuda reduction')
        conn.close()
        self.assertGreater(len(global_results), 0,
                           'Global FTS search should find legacy exchange content')
        texts = [r['user_text'] for r in global_results]
        self.assertTrue(any('cuda' in (t or '').lower() for t in texts))


# ---------------------------------------------------------------------------
# TestCrossProjectSearch
# ---------------------------------------------------------------------------

class TestCrossProjectSearch(unittest.TestCase):
    """Search across different projects."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_transcript(self, name, pairs):
        path = str(Path(self.temp_dir) / name)
        _write_transcript(path, pairs)
        return path

    def test_cross_project_search(self):
        import prompt_submit

        # Prevent migration from picking up any real legacy index.json
        original_legacy_file = prompt_submit.LEGACY_INDEX_FILE
        prompt_submit.LEGACY_INDEX_FILE = Path(self.temp_dir) / 'no-such-index.json'

        try:
            self._run_cross_project(prompt_submit)
        finally:
            prompt_submit.LEGACY_INDEX_FILE = original_legacy_file

    def _run_cross_project(self, prompt_submit):
        # ------------------------------------------------------------------ #
        # Step 1 — prompt_hook() for project A (triton-metal, hash-a)         #
        # ------------------------------------------------------------------ #
        transcript_a = self._make_transcript('transcript_a.jsonl', [
            ('Explain triton reduction kernel.', 'The triton reduction uses tl.sum with a tile.', '2025-01-05T09:00:00Z'),
        ])
        prompt_hook(
            {
                'session_id': 'sess-proj-a',
                'transcript_path': transcript_a,
                'user_prompt': 'hello',
                'project_path': 'triton-metal',
                'project_hash': 'hash-a',
            },
            db_path=self.db_path,
        )

        # ------------------------------------------------------------------ #
        # Step 2 — prompt_hook() for project B (cuda-kernels, hash-b)         #
        # ------------------------------------------------------------------ #
        transcript_b = self._make_transcript('transcript_b.jsonl', [
            ('Show me CUDA reduction code.', 'In CUDA, reduction uses warp shuffle for speed.', '2025-01-05T10:00:00Z'),
        ])
        prompt_hook(
            {
                'session_id': 'sess-proj-b',
                'transcript_path': transcript_b,
                'user_prompt': 'hello',
                'project_path': 'cuda-kernels',
                'project_hash': 'hash-b',
            },
            db_path=self.db_path,
        )

        # ------------------------------------------------------------------ #
        # Step 3 — search_exchanges_global() for "reduction" → both projects  #
        # ------------------------------------------------------------------ #
        conn = get_connection(self.db_path)
        global_results = search_exchanges_global(conn, 'reduction')
        conn.close()

        self.assertGreaterEqual(len(global_results), 2,
                                'Global search should find results from both projects')
        session_ids = {r['session_id'] for r in global_results}
        self.assertIn('sess-proj-a', session_ids,
                      'Project A session should appear in global results')
        self.assertIn('sess-proj-b', session_ids,
                      'Project B session should appear in global results')

        # ------------------------------------------------------------------ #
        # Step 4 — project_path is included in results                        #
        # ------------------------------------------------------------------ #
        for result in global_results:
            self.assertIn('project_path', result,
                          'Each global search result should include project_path')
        project_paths = {r['project_path'] for r in global_results}
        self.assertIn('triton-metal', project_paths)
        self.assertIn('cuda-kernels', project_paths)


# ---------------------------------------------------------------------------
# TestHighlightConnectionInbox
# ---------------------------------------------------------------------------

class TestHighlightConnectionInbox(unittest.TestCase):
    """Integration: highlight created on session B surfaces in session A's inbox."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'recall.db'

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_transcript(self, name, pairs):
        path = str(Path(self.temp_dir) / name)
        _write_transcript(path, pairs)
        return path

    def _hook_input(self, session_id, transcript, project_hash='hash-inbox'):
        return {
            'session_id': session_id,
            'transcript_path': transcript,
            'user_prompt': 'hello',
            'project_path': 'inbox-project',
            'project_hash': project_hash,
        }

    def test_highlight_appears_in_inbox_then_clears(self):
        import prompt_submit

        # Prevent migration from picking up any real legacy index.json
        original_legacy_file = prompt_submit.LEGACY_INDEX_FILE
        prompt_submit.LEGACY_INDEX_FILE = Path(self.temp_dir) / 'no-such-index.json'

        try:
            # ------------------------------------------------------------------ #
            # Step 1 — Create session A and session B via prompt_hook             #
            # ------------------------------------------------------------------ #
            transcript_a = self._make_transcript('transcript_a.jsonl', [
                ('What is session A doing?', 'Session A is doing X.', '2025-01-05T09:00:00Z'),
            ])
            prompt_hook(self._hook_input('sess-inbox-a', transcript_a),
                        db_path=self.db_path)

            transcript_b = self._make_transcript('transcript_b.jsonl', [
                ('What is session B doing?', 'Session B is doing Y.', '2025-01-05T09:01:00Z'),
            ])
            prompt_hook(self._hook_input('sess-inbox-b', transcript_b),
                        db_path=self.db_path)

            # ------------------------------------------------------------------ #
            # Step 2 — Connect A to watch B                                       #
            # ------------------------------------------------------------------ #
            conn = get_connection(self.db_path)
            insert_connection(conn, 'sess-inbox-a', 'sess-inbox-b', 'kernel work',
                               check_mode='decay', delivery_mode='silent')
            conn.close()

            # ------------------------------------------------------------------ #
            # Step 3 — Create a highlight on B                                    #
            # ------------------------------------------------------------------ #
            conn = get_connection(self.db_path)
            insert_highlight(conn, 'sess-inbox-b', 'Found a warp divergence fix', 'perf', 'explicit')
            conn.close()

            # ------------------------------------------------------------------ #
            # Step 4 — Run inbox for A — highlight should appear                  #
            # ------------------------------------------------------------------ #
            conn = get_connection(self.db_path)
            result = inbox(conn, 'sess-inbox-a')
            conn.close()

            self.assertIn('warp divergence fix', result,
                          'Highlight from session B should appear in session A inbox')
            self.assertIn('sess-inbox-b'[:8], result,
                          'Session B short ID should appear in inbox')

            # ------------------------------------------------------------------ #
            # Step 5 — Mark read (WI-19: a plain view never clears; an explicit  #
            #          mark_read on a decay connection does), then re-view empty #
            # ------------------------------------------------------------------ #
            conn = get_connection(self.db_path)
            inbox(conn, 'sess-inbox-a', mark_read=True)   # marks highlights as seen
            result2 = inbox(conn, 'sess-inbox-a')         # plain view now empty
            conn.close()

            self.assertIn('No new highlights', result2,
                          'After mark_read, a subsequent inbox view should be empty')

        finally:
            prompt_submit.LEGACY_INDEX_FILE = original_legacy_file


if __name__ == '__main__':
    unittest.main()
