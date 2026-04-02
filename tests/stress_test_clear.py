#!/usr/bin/env python3
"""Stress tests for /clear survival and session integrity.

Validates that rapid session creation/destruction doesn't corrupt data —
all sessions, exchanges, tags, highlights, and connections remain consistent
after many simulated /clear cycles.
"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

# Add hooks and scripts directories to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

import prompt_submit as _prompt_submit_mod
from prompt_submit import run_hook as prompt_run_hook
from session_end import run_hook as session_end_run_hook
from db import (
    get_connection,
    get_session,
    get_exchanges,
    list_sessions,
    search_exchanges_fts,
    insert_highlight,
    insert_connection,
    get_highlights,
    get_connections,
)
from manage_tags import add_tag, search_by_tag
from manage_connections import connect
from highlight import create_highlight


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_transcript(path, entries):
    """Write JSONL transcript entries to a file."""
    with open(path, 'w', encoding='utf-8') as f:
        for entry in entries:
            f.write(json.dumps(entry) + '\n')


def _make_entry(role, text, timestamp='2026-01-05T09:00:00Z'):
    """Build a single transcript JSONL entry."""
    return {
        'type': role,
        'message': {'content': [{'type': 'text', 'text': text}]},
        'timestamp': timestamp,
    }


def _make_transcript_entries(n_exchanges, topic_prefix, base_ts='2026-01-05T09:00:00Z'):
    """Build 2*n_exchanges JSONL entries (n_exchanges user+assistant pairs)."""
    entries = []
    for i in range(1, n_exchanges + 1):
        entries.append(_make_entry('user', f'{topic_prefix} question {i}', base_ts))
        entries.append(_make_entry('assistant', f'{topic_prefix} answer {i}', base_ts))
    return entries


def _run_session(db_path, session_id, transcript_path, project_hash='stress-proj-hash',
                 project_path='/stress/project'):
    """Run prompt_submit.run_hook for a session (simulates a prompt in that session)."""
    return prompt_run_hook(
        {
            'session_id': session_id,
            'transcript_path': str(transcript_path),
            'user_prompt': 'stress test prompt',
            'project_path': project_path,
            'project_hash': project_hash,
        },
        db_path=db_path,
    )


def _end_session(db_path, session_id):
    """Run session_end.run_hook to mark a session as ended."""
    return session_end_run_hook({'session_id': session_id}, db_path=db_path)


# ---------------------------------------------------------------------------
# TestRapidClearCycles
# ---------------------------------------------------------------------------

class TestRapidClearCycles(unittest.TestCase):
    """20 rapid /clear cycles: each creates a session, adds 5 exchanges, then 'clears'."""

    NUM_CYCLES = 20
    EXCHANGES_PER_SESSION = 5
    PROJECT_HASH = 'rapid-clear-proj'

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'stress.db'
        # Suppress LOG_FILE writes during tests
        self._orig_log = _prompt_submit_mod.LOG_FILE
        _prompt_submit_mod.LOG_FILE = Path(self.temp_dir) / 'recall-events.log'

    def tearDown(self):
        _prompt_submit_mod.LOG_FILE = self._orig_log
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rapid_clear_all_sessions_exist(self):
        """After 20 rapid /clear cycles all 20 sessions must exist."""
        for cycle in range(self.NUM_CYCLES):
            sess_id = f'rapid-sess-{cycle:03d}'
            transcript = Path(self.temp_dir) / f'transcript_{cycle:03d}.jsonl'
            entries = _make_transcript_entries(self.EXCHANGES_PER_SESSION,
                                               f'cycle{cycle}')
            _write_transcript(transcript, entries)
            _run_session(self.db_path, sess_id, transcript,
                         project_hash=self.PROJECT_HASH)

        conn = get_connection(self.db_path)
        sessions = list_sessions(conn, project_hash=self.PROJECT_HASH)
        conn.close()

        self.assertEqual(len(sessions), self.NUM_CYCLES,
                         f'Expected {self.NUM_CYCLES} sessions, got {len(sessions)}')

    def test_rapid_clear_each_session_has_correct_exchange_count(self):
        """Each of the 20 sessions must have exactly 5 exchanges."""
        for cycle in range(self.NUM_CYCLES):
            sess_id = f'rapid-excount-{cycle:03d}'
            transcript = Path(self.temp_dir) / f'transcript_ec_{cycle:03d}.jsonl'
            entries = _make_transcript_entries(self.EXCHANGES_PER_SESSION,
                                               f'excount{cycle}')
            _write_transcript(transcript, entries)
            _run_session(self.db_path, sess_id, transcript,
                         project_hash=self.PROJECT_HASH)

        conn = get_connection(self.db_path)
        mismatches = []
        for cycle in range(self.NUM_CYCLES):
            sess_id = f'rapid-excount-{cycle:03d}'
            exchanges = get_exchanges(conn, sess_id)
            if len(exchanges) != self.EXCHANGES_PER_SESSION:
                mismatches.append((sess_id, len(exchanges)))
        conn.close()

        self.assertEqual(mismatches, [],
                         f'Sessions with wrong exchange count: {mismatches}')

    def test_rapid_clear_total_exchange_count(self):
        """Total exchange count across all 20 sessions must equal 100."""
        for cycle in range(self.NUM_CYCLES):
            sess_id = f'rapid-total-{cycle:03d}'
            transcript = Path(self.temp_dir) / f'transcript_tot_{cycle:03d}.jsonl'
            entries = _make_transcript_entries(self.EXCHANGES_PER_SESSION,
                                               f'total{cycle}')
            _write_transcript(transcript, entries)
            _run_session(self.db_path, sess_id, transcript,
                         project_hash=self.PROJECT_HASH)

        conn = get_connection(self.db_path)
        total = 0
        for cycle in range(self.NUM_CYCLES):
            sess_id = f'rapid-total-{cycle:03d}'
            total += len(get_exchanges(conn, sess_id))
        conn.close()

        expected = self.NUM_CYCLES * self.EXCHANGES_PER_SESSION
        self.assertEqual(total, expected,
                         f'Expected {expected} total exchanges, got {total}')

    def test_rapid_clear_cross_session_search_finds_all(self):
        """Cross-session FTS search must find results across all 20 sessions."""
        # Use a shared distinctive token so we can find all exchanges
        shared_token = 'STRESSTOKEN'
        for cycle in range(self.NUM_CYCLES):
            sess_id = f'rapid-fts-{cycle:03d}'
            transcript = Path(self.temp_dir) / f'transcript_fts_{cycle:03d}.jsonl'
            entries = _make_transcript_entries(self.EXCHANGES_PER_SESSION,
                                               f'{shared_token} cycle{cycle}')
            _write_transcript(transcript, entries)
            _run_session(self.db_path, sess_id, transcript,
                         project_hash=self.PROJECT_HASH)

        conn = get_connection(self.db_path)
        results = search_exchanges_fts(conn, shared_token, limit=200)
        conn.close()

        session_ids_found = {r['session_id'] for r in results}
        expected_ids = {f'rapid-fts-{c:03d}' for c in range(self.NUM_CYCLES)}
        missing = expected_ids - session_ids_found
        self.assertEqual(missing, set(),
                         f'FTS search missed sessions: {missing}')


# ---------------------------------------------------------------------------
# TestClearPreservesSearchIndex
# ---------------------------------------------------------------------------

class TestClearPreservesSearchIndex(unittest.TestCase):
    """After /clear, the FTS index from prior sessions must remain intact and isolated."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'stress.db'
        self._orig_log = _prompt_submit_mod.LOG_FILE
        _prompt_submit_mod.LOG_FILE = Path(self.temp_dir) / 'recall-events.log'

        # Session A — 10 exchanges about "warp divergence"
        self.sess_a = 'fts-sess-a'
        transcript_a = Path(self.temp_dir) / 'transcript_a.jsonl'
        entries_a = []
        for i in range(1, 11):
            entries_a.append(_make_entry('user', f'Question about warp divergence {i}'))
            entries_a.append(_make_entry('assistant', f'Answer about warp divergence execution {i}'))
        _write_transcript(transcript_a, entries_a)
        _run_session(self.db_path, self.sess_a, transcript_a)

        # Simulate /clear — session A ends, session B starts
        _end_session(self.db_path, self.sess_a)

        # Session B — 10 exchanges about "memory coalescing"
        self.sess_b = 'fts-sess-b'
        transcript_b = Path(self.temp_dir) / 'transcript_b.jsonl'
        entries_b = []
        for i in range(1, 11):
            entries_b.append(_make_entry('user', f'Question about memory coalescing {i}'))
            entries_b.append(_make_entry('assistant', f'Answer about coalescing transactions {i}'))
        _write_transcript(transcript_b, entries_b)
        _run_session(self.db_path, self.sess_b, transcript_b)

        # Simulate /clear — session B ends, session C starts
        _end_session(self.db_path, self.sess_b)

        # Session C — 10 exchanges about "occupancy"
        self.sess_c = 'fts-sess-c'
        transcript_c = Path(self.temp_dir) / 'transcript_c.jsonl'
        entries_c = []
        for i in range(1, 11):
            entries_c.append(_make_entry('user', f'Question about occupancy tuning {i}'))
            entries_c.append(_make_entry('assistant', f'Answer about occupancy and register usage {i}'))
        _write_transcript(transcript_c, entries_c)
        _run_session(self.db_path, self.sess_c, transcript_c)

    def tearDown(self):
        _prompt_submit_mod.LOG_FILE = self._orig_log
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_warp_divergence_only_in_session_a(self):
        """Search for 'warp divergence' must return ONLY session A results."""
        conn = get_connection(self.db_path)
        results = search_exchanges_fts(conn, 'warp divergence', limit=50)
        conn.close()

        self.assertGreater(len(results), 0, 'Expected warp divergence results, found none')
        wrong = [r for r in results if r['session_id'] != self.sess_a]
        self.assertEqual(wrong, [],
                         f'warp divergence results leaked into other sessions: '
                         f'{[r["session_id"] for r in wrong]}')

    def test_coalescing_only_in_session_b(self):
        """Search for 'coalescing' must return ONLY session B results."""
        conn = get_connection(self.db_path)
        results = search_exchanges_fts(conn, 'coalescing', limit=50)
        conn.close()

        self.assertGreater(len(results), 0, 'Expected coalescing results, found none')
        wrong = [r for r in results if r['session_id'] != self.sess_b]
        self.assertEqual(wrong, [],
                         f'coalescing results leaked into other sessions: '
                         f'{[r["session_id"] for r in wrong]}')

    def test_occupancy_only_in_session_c(self):
        """Search for 'occupancy' must return ONLY session C results."""
        conn = get_connection(self.db_path)
        results = search_exchanges_fts(conn, 'occupancy', limit=50)
        conn.close()

        self.assertGreater(len(results), 0, 'Expected occupancy results, found none')
        wrong = [r for r in results if r['session_id'] != self.sess_c]
        self.assertEqual(wrong, [],
                         f'occupancy results leaked into other sessions: '
                         f'{[r["session_id"] for r in wrong]}')

    def test_global_search_finds_all_three_sessions(self):
        """Global search for each term must hit across sessions A, B, and C."""
        conn = get_connection(self.db_path)

        warp_results = search_exchanges_fts(conn, 'warp', limit=50)
        coal_results = search_exchanges_fts(conn, 'coalescing', limit=50)
        occ_results = search_exchanges_fts(conn, 'occupancy', limit=50)

        conn.close()

        warp_sessions = {r['session_id'] for r in warp_results}
        coal_sessions = {r['session_id'] for r in coal_results}
        occ_sessions = {r['session_id'] for r in occ_results}

        self.assertIn(self.sess_a, warp_sessions,
                      f'Session A missing from warp search. Found: {warp_sessions}')
        self.assertIn(self.sess_b, coal_sessions,
                      f'Session B missing from coalescing search. Found: {coal_sessions}')
        self.assertIn(self.sess_c, occ_sessions,
                      f'Session C missing from occupancy search. Found: {occ_sessions}')


# ---------------------------------------------------------------------------
# TestClearPreservesTags
# ---------------------------------------------------------------------------

class TestClearPreservesTags(unittest.TestCase):
    """Tags on session A must survive a /clear that creates session B."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'stress.db'
        self._orig_log = _prompt_submit_mod.LOG_FILE
        _prompt_submit_mod.LOG_FILE = Path(self.temp_dir) / 'recall-events.log'

        # Create session A with exchanges (auto-tagger may fire)
        self.sess_a = 'tags-sess-a'
        transcript_a = Path(self.temp_dir) / 'transcript_a.jsonl'
        entries_a = [
            _make_entry('user', 'How do I optimize CUDA kernel launch parameters?'),
            _make_entry('assistant', 'Use occupancy calculator to tune block size.'),
            _make_entry('user', 'What about shared memory configuration?'),
            _make_entry('assistant', 'Set shared memory bank size to match access pattern.'),
        ]
        _write_transcript(transcript_a, entries_a)
        _run_session(self.db_path, self.sess_a, transcript_a)

        # Attach manual and auto-style tags to session A
        conn = get_connection(self.db_path)
        add_tag(conn, 'cuda-optimization', self.sess_a)
        add_tag(conn, 'kernel-tuning', self.sess_a)
        conn.close()

        # Simulate /clear — session A ends, session B begins
        _end_session(self.db_path, self.sess_a)

        self.sess_b = 'tags-sess-b'
        transcript_b = Path(self.temp_dir) / 'transcript_b.jsonl'
        entries_b = [
            _make_entry('user', 'Unrelated question about Python asyncio'),
            _make_entry('assistant', 'Use asyncio.gather for concurrent coroutines.'),
        ]
        _write_transcript(transcript_b, entries_b)
        _run_session(self.db_path, self.sess_b, transcript_b)

    def tearDown(self):
        _prompt_submit_mod.LOG_FILE = self._orig_log
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_session_a_tags_still_exist_after_clear(self):
        """Session A's tags must still be in the DB after session B is created."""
        conn = get_connection(self.db_path)
        rows = conn.execute(
            "SELECT tag FROM tags WHERE session_id = ? ORDER BY tag",
            (self.sess_a,)
        ).fetchall()
        conn.close()

        tag_names = {r['tag'] for r in rows}
        self.assertIn('cuda-optimization', tag_names,
                      f'cuda-optimization tag missing. Found: {tag_names}')
        self.assertIn('kernel-tuning', tag_names,
                      f'kernel-tuning tag missing. Found: {tag_names}')

    def test_search_by_tag_still_finds_session_a(self):
        """search_by_tag('cuda-optimization') must return session A after /clear."""
        conn = get_connection(self.db_path)
        results = search_by_tag(conn, 'cuda-optimization')
        conn.close()

        session_ids = {r['session_id'] for r in results}
        self.assertIn(self.sess_a, session_ids,
                      f'Session A not found by tag search. Found: {session_ids}')

    def test_session_b_tags_are_independent(self):
        """Session B's tags must not include session A's manual tags."""
        conn = get_connection(self.db_path)
        rows = conn.execute(
            "SELECT tag FROM tags WHERE session_id = ? AND source = 'manual'",
            (self.sess_b,)
        ).fetchall()
        conn.close()

        tag_names = {r['tag'] for r in rows}
        self.assertNotIn('cuda-optimization', tag_names,
                         'Session A tag leaked into session B')
        self.assertNotIn('kernel-tuning', tag_names,
                         'Session A tag leaked into session B')


# ---------------------------------------------------------------------------
# TestClearPreservesHighlights
# ---------------------------------------------------------------------------

class TestClearPreservesHighlights(unittest.TestCase):
    """Highlights on session A must survive /clear and be visible from session B."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'stress.db'
        self._orig_log = _prompt_submit_mod.LOG_FILE
        _prompt_submit_mod.LOG_FILE = Path(self.temp_dir) / 'recall-events.log'

        # Create session A with exchanges and highlights
        self.sess_a = 'hl-sess-a'
        transcript_a = Path(self.temp_dir) / 'transcript_a.jsonl'
        entries_a = [
            _make_entry('user', 'How to fix warp divergence?'),
            _make_entry('assistant', 'Use __ballot_sync to coalesce divergent paths.'),
            _make_entry('user', 'Show me an example'),
            _make_entry('assistant', 'Here is the ballot sync pattern for warp reduction.'),
        ]
        _write_transcript(transcript_a, entries_a)
        _run_session(self.db_path, self.sess_a, transcript_a)

        conn = get_connection(self.db_path)
        self.highlight_summary = 'Fixed warp divergence with ballot_sync'
        create_highlight(conn, self.sess_a, self.highlight_summary)
        conn.close()

        # Simulate /clear — A ends, B begins
        _end_session(self.db_path, self.sess_a)

        self.sess_b = 'hl-sess-b'
        transcript_b = Path(self.temp_dir) / 'transcript_b.jsonl'
        entries_b = [
            _make_entry('user', 'New session after clear'),
            _make_entry('assistant', 'Starting fresh context.'),
        ]
        _write_transcript(transcript_b, entries_b)
        _run_session(self.db_path, self.sess_b, transcript_b)

    def tearDown(self):
        _prompt_submit_mod.LOG_FILE = self._orig_log
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_session_a_highlights_still_in_db(self):
        """Session A's highlights must remain in the DB after /clear."""
        conn = get_connection(self.db_path)
        highlights = get_highlights(conn, self.sess_a)
        conn.close()

        summaries = [h['summary'] for h in highlights]
        self.assertIn(self.highlight_summary, summaries,
                      f'Expected highlight not found. Found: {summaries}')

    def test_session_b_can_read_session_a_highlights(self):
        """Session B must be able to read session A's highlights via get_highlights."""
        conn = get_connection(self.db_path)
        highlights_a = get_highlights(conn, self.sess_a)
        highlights_b = get_highlights(conn, self.sess_b)
        conn.close()

        # Session A highlights visible
        self.assertGreater(len(highlights_a), 0,
                           'Session A has no highlights — expected at least 1')

        # Session B has no highlights of its own (it was just created)
        a_summaries = {h['summary'] for h in highlights_a}
        b_summaries = {h['summary'] for h in highlights_b}
        # The key data integrity check: A's highlights are accessible from the same DB
        self.assertIn(self.highlight_summary, a_summaries)
        # B's highlights should not include A's (no cross-contamination)
        self.assertNotIn(self.highlight_summary, b_summaries,
                         'Session A highlight incorrectly attributed to session B')

    def test_highlight_count_stable_across_clears(self):
        """Total highlight count must not change after /clear creates a new session."""
        conn = get_connection(self.db_path)
        count_before = conn.execute(
            "SELECT COUNT(*) FROM highlights WHERE session_id = ?", (self.sess_a,)
        ).fetchone()[0]

        # Additional /clear cycles — create sessions C, D
        for extra in ('c', 'd'):
            sess = f'hl-sess-{extra}'
            t = Path(self.temp_dir) / f'transcript_{extra}.jsonl'
            entries = [
                _make_entry('user', f'Prompt in session {extra}'),
                _make_entry('assistant', f'Response in session {extra}'),
            ]
            _write_transcript(t, entries)
            _run_session(self.db_path, sess, t)
            _end_session(self.db_path, sess)

        count_after = conn.execute(
            "SELECT COUNT(*) FROM highlights WHERE session_id = ?", (self.sess_a,)
        ).fetchone()[0]
        conn.close()

        self.assertEqual(count_before, count_after,
                         f'Highlight count changed: {count_before} → {count_after}')


# ---------------------------------------------------------------------------
# TestClearPreservesConnections
# ---------------------------------------------------------------------------

class TestClearPreservesConnections(unittest.TestCase):
    """Connections between sessions must survive /clear on the watcher."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'stress.db'
        self._orig_log = _prompt_submit_mod.LOG_FILE
        _prompt_submit_mod.LOG_FILE = Path(self.temp_dir) / 'recall-events.log'

        # Create sessions A and B
        for sess_id, topic_prefix in [('conn-sess-a', 'sessionA'),
                                       ('conn-sess-b', 'sessionB')]:
            t = Path(self.temp_dir) / f'transcript_{sess_id}.jsonl'
            entries = _make_transcript_entries(3, topic_prefix)
            _write_transcript(t, entries)
            _run_session(self.db_path, sess_id, t)

        # Connect A → B
        conn = get_connection(self.db_path)
        connect(conn, 'conn-sess-a', 'conn-sess-b', 'shared kernel work')
        conn.close()

        # Simulate /clear on A — A ends, C begins
        _end_session(self.db_path, 'conn-sess-a')

        sess_c_transcript = Path(self.temp_dir) / 'transcript_conn-sess-c.jsonl'
        entries_c = _make_transcript_entries(3, 'sessionC')
        _write_transcript(sess_c_transcript, entries_c)
        _run_session(self.db_path, 'conn-sess-c', sess_c_transcript)

    def tearDown(self):
        _prompt_submit_mod.LOG_FILE = self._orig_log
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_connection_a_to_b_still_exists_after_clear(self):
        """Connection A → B must persist even though A is now ended."""
        conn = get_connection(self.db_path)
        connections = get_connections(conn, 'conn-sess-a')
        conn.close()

        targets = [c['target_session'] for c in connections]
        self.assertIn('conn-sess-b', targets,
                      f'Expected connection A→B not found. Connections: {connections}')

    def test_ended_session_a_connection_intact(self):
        """Session A's ended_at is set AND the connection to B is still present."""
        conn = get_connection(self.db_path)
        sess_a = get_session(conn, 'conn-sess-a')
        connections = get_connections(conn, 'conn-sess-a')
        conn.close()

        self.assertIsNotNone(sess_a['ended_at'],
                             'Session A ended_at should be set after /clear')
        targets = [c['target_session'] for c in connections]
        self.assertIn('conn-sess-b', targets,
                      'Connection A→B should survive session A ending')

    def test_session_c_can_create_new_connection_to_b(self):
        """After /clear, the new session C must be able to create a connection to B."""
        conn = get_connection(self.db_path)
        result = connect(conn, 'conn-sess-c', 'conn-sess-b', 'continued kernel work')
        connections = get_connections(conn, 'conn-sess-c')
        conn.close()

        self.assertNotIn('Error', result,
                         f'connect() returned error: {result}')
        targets = [c['target_session'] for c in connections]
        self.assertIn('conn-sess-b', targets,
                      f'Session C→B connection not created. Connections: {connections}')

    def test_original_connection_not_overwritten_by_new_session(self):
        """Creating C → B must not delete or overwrite the A → B connection."""
        conn = get_connection(self.db_path)
        connect(conn, 'conn-sess-c', 'conn-sess-b', 'continued kernel work')

        connections_a = get_connections(conn, 'conn-sess-a')
        connections_c = get_connections(conn, 'conn-sess-c')
        conn.close()

        a_targets = [c['target_session'] for c in connections_a]
        c_targets = [c['target_session'] for c in connections_c]

        self.assertIn('conn-sess-b', a_targets,
                      'A→B connection lost after creating C→B')
        self.assertIn('conn-sess-b', c_targets,
                      'C→B connection was not created')


# ---------------------------------------------------------------------------
# TestSessionEndOnClear
# ---------------------------------------------------------------------------

class TestSessionEndOnClear(unittest.TestCase):
    """Simulating /clear via session_end hook must mark the old session ended
    and leave the new session active."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'stress.db'
        self._orig_log = _prompt_submit_mod.LOG_FILE
        _prompt_submit_mod.LOG_FILE = Path(self.temp_dir) / 'recall-events.log'

    def tearDown(self):
        _prompt_submit_mod.LOG_FILE = self._orig_log
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_session_a_has_ended_at_after_clear(self):
        """After calling session_end on A, session A must have ended_at set."""
        sess_a = 'end-sess-a'
        transcript_a = Path(self.temp_dir) / 'transcript_a.jsonl'
        entries_a = [
            _make_entry('user', 'First question'),
            _make_entry('assistant', 'First answer'),
            _make_entry('user', 'Second question'),
            _make_entry('assistant', 'Second answer'),
        ]
        _write_transcript(transcript_a, entries_a)
        _run_session(self.db_path, sess_a, transcript_a)

        # Simulate /clear: trigger session_end for A
        _end_session(self.db_path, sess_a)

        conn = get_connection(self.db_path)
        session_a = get_session(conn, sess_a)
        conn.close()

        self.assertIsNotNone(session_a, 'Session A should still exist after end')
        self.assertIsNotNone(session_a['ended_at'],
                             'Session A ended_at must be set after session_end hook')
        self.assertIn('T', session_a['ended_at'],
                      'ended_at should be a valid ISO timestamp')

    def test_session_b_is_active_after_clear(self):
        """After /clear, the new session B must have ended_at=NULL (active)."""
        sess_a = 'end-active-a'
        transcript_a = Path(self.temp_dir) / 'transcript_active_a.jsonl'
        entries_a = [
            _make_entry('user', 'Session A question'),
            _make_entry('assistant', 'Session A answer'),
        ]
        _write_transcript(transcript_a, entries_a)
        _run_session(self.db_path, sess_a, transcript_a)

        # /clear: end A, start B
        _end_session(self.db_path, sess_a)

        sess_b = 'end-active-b'
        transcript_b = Path(self.temp_dir) / 'transcript_active_b.jsonl'
        entries_b = [
            _make_entry('user', 'Session B question'),
            _make_entry('assistant', 'Session B answer'),
        ]
        _write_transcript(transcript_b, entries_b)
        _run_session(self.db_path, sess_b, transcript_b)

        conn = get_connection(self.db_path)
        session_a = get_session(conn, sess_a)
        session_b = get_session(conn, sess_b)
        conn.close()

        self.assertIsNotNone(session_a['ended_at'],
                             'Session A must be marked ended')
        self.assertIsNone(session_b['ended_at'],
                          'Session B must be active (ended_at = NULL)')

    def test_multiple_clears_each_session_correctly_ended(self):
        """A sequence of 5 clears: sessions 0-3 ended, session 4 still active."""
        NUM_SESSIONS = 5
        sessions = [f'end-multi-{i}' for i in range(NUM_SESSIONS)]

        for i, sess_id in enumerate(sessions):
            t = Path(self.temp_dir) / f'transcript_multi_{i}.jsonl'
            entries = _make_transcript_entries(2, f'multi{i}')
            _write_transcript(t, entries)
            _run_session(self.db_path, sess_id, t)

            # /clear: end current before starting next (except the last)
            if i < NUM_SESSIONS - 1:
                _end_session(self.db_path, sess_id)

        conn = get_connection(self.db_path)
        ended_wrong = []
        for i, sess_id in enumerate(sessions):
            s = get_session(conn, sess_id)
            if i < NUM_SESSIONS - 1:
                if s['ended_at'] is None:
                    ended_wrong.append(f'{sess_id} should be ended but ended_at=NULL')
            else:
                if s['ended_at'] is not None:
                    ended_wrong.append(f'{sess_id} should be active but ended_at={s["ended_at"]}')
        conn.close()

        self.assertEqual(ended_wrong, [],
                         f'Session end state errors: {ended_wrong}')

    def test_exchanges_preserved_after_session_end(self):
        """Calling session_end must not delete exchanges from session A."""
        sess_a = 'end-exchanges-a'
        transcript_a = Path(self.temp_dir) / 'transcript_end_ex.jsonl'
        entries_a = _make_transcript_entries(4, 'preserve')
        _write_transcript(transcript_a, entries_a)
        _run_session(self.db_path, sess_a, transcript_a)

        # Count exchanges before ending
        conn = get_connection(self.db_path)
        before = len(get_exchanges(conn, sess_a))
        conn.close()

        _end_session(self.db_path, sess_a)

        conn = get_connection(self.db_path)
        after = len(get_exchanges(conn, sess_a))
        conn.close()

        self.assertEqual(before, after,
                         f'Exchange count changed on session_end: {before} → {after}')
        self.assertEqual(after, 4,
                         f'Expected 4 exchanges after end, got {after}')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
