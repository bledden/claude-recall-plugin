#!/usr/bin/env python3
"""Stress tests for the cross-session sharing system.

Validates highlights, connections, decay scheduling, and inbox under load.
Each test uses an isolated tempfile.mkdtemp() DB to prevent cross-test
contamination.
"""

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
    get_session,
    insert_session,
    insert_highlight,
    insert_connection,
    get_connections,
    get_highlights,
    update_connection_check,
    set_session_config,
    insert_exchanges,
    prune_session,
)
from manage_connections import connect, inbox
from highlight import auto_detect_highlights
from auto_tagger import compute_auto_tags


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_session(conn, session_id: str, project_path: str = '/tmp/proj',
                  project_hash: str = 'hash-default',
                  started_at: str = '2026-04-01T00:00:00Z') -> str:
    insert_session(conn, session_id, project_path, project_hash, started_at)
    return session_id


def _make_highlight(conn, session_id: str, summary: str,
                    tags: str = 'stress', created_at: str = None) -> None:
    if created_at is not None:
        conn.execute(
            "INSERT OR IGNORE INTO highlights "
            "(session_id, summary, exchange_idx, tags, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, summary, None, tags, 'explicit', created_at),
        )
        conn.commit()
    else:
        insert_highlight(conn, session_id, summary, tags, source='explicit')


def _make_exchange(idx: int, assistant_text: str,
                   user_text: str = 'What is the fix?') -> dict:
    return {
        'idx': idx,
        'timestamp': f'2026-04-01T00:{idx:02d}:00Z',
        'preview': user_text[:60],
        'user_text': user_text,
        'assistant_text': assistant_text,
    }


# ---------------------------------------------------------------------------
# TestManyConnections
# ---------------------------------------------------------------------------

class TestManyConnections(unittest.TestCase):
    """Session 1 watches 9 sessions; each has 5 highlights — 45 total."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'many_connections.db')
        self.conn = get_connection(self.db_path)

        # Create 10 sessions: session 0 is the watcher, 1-9 are targets
        self.watcher = _make_session(self.conn, 'watcher-mc-000')
        self.targets = []
        for i in range(1, 10):
            sid = f'target-mc-{i:03d}'
            _make_session(self.conn, sid)
            self.targets.append(sid)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_inbox_returns_all_45_highlights_grouped_by_session(self):
        """Inbox aggregates highlights from all 9 connected sessions (45 total)."""
        # Connect watcher to all 9 targets
        for i, target in enumerate(self.targets):
            connect(self.conn, self.watcher, target, f'topic-{i}')

        # Add 5 highlights to each target
        for i, target in enumerate(self.targets):
            for j in range(5):
                _make_highlight(self.conn, target, f'Insight {i}-{j} about session {i}')

        # Run inbox
        result = inbox(self.conn, self.watcher)

        self.assertIn('Inbox', result)
        self.assertIn('45 new highlights', result)

        # All 9 target session prefixes should appear in the output
        for target in self.targets:
            self.assertIn(target[:8], result,
                          f"Session {target[:8]} not in inbox output")

    def test_last_checked_at_updated_on_all_connections_after_inbox(self):
        """inbox() sets last_checked_at on every one of the 9 connections."""
        for target in self.targets:
            connect(self.conn, self.watcher, target, 'topic', check_mode='decay')
        for target in self.targets:
            _make_highlight(self.conn, target, f'Something from {target[:8]}')

        # Before inbox: all last_checked_at should be None
        conns_before = get_connections(self.conn, self.watcher)
        self.assertEqual(len(conns_before), 9)
        for c in conns_before:
            self.assertIsNone(c['last_checked_at'],
                              f"Connection {c['id']} already has last_checked_at set")

        inbox(self.conn, self.watcher, mark_read=True)

        conns_after = get_connections(self.conn, self.watcher)
        for c in conns_after:
            self.assertIsNotNone(c['last_checked_at'],
                                 f"Connection {c['id']} still has None last_checked_at after inbox")

    def test_second_inbox_returns_empty(self):
        """After the first inbox check, a second call returns no highlights."""
        for target in self.targets:
            connect(self.conn, self.watcher, target, 'topic', check_mode='decay')
        for target in self.targets:
            for j in range(5):
                _make_highlight(self.conn, target, f'Insight {target[:6]}-{j}')

        # First call with mark_read — returns all 45 and marks them seen
        first = inbox(self.conn, self.watcher, mark_read=True)
        self.assertIn('45 new highlights', first)

        # Second call — nothing new
        second = inbox(self.conn, self.watcher)
        self.assertIn('No new highlights', second)


# ---------------------------------------------------------------------------
# TestHighlightVolume
# ---------------------------------------------------------------------------

class TestHighlightVolume(unittest.TestCase):
    """Session A watches session B; B gets 100 highlights."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'highlight_volume.db')
        self.conn = get_connection(self.db_path)
        self.session_a = _make_session(self.conn, 'vol-session-A')
        self.session_b = _make_session(self.conn, 'vol-session-B')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_all_100_highlights_returned(self):
        """inbox returns all 100 highlights with no duplicates."""
        connect(self.conn, self.session_a, self.session_b, 'volume test')

        # Insert 100 highlights to session B
        for i in range(100):
            _make_highlight(self.conn, self.session_b,
                            f'Volume highlight number {i:03d} — unique content here')

        # Verify the DB has exactly 100
        db_highlights = get_highlights(self.conn, self.session_b, limit=200)
        self.assertEqual(len(db_highlights), 100,
                         "Expected exactly 100 highlights in DB")

        # Fetch highlights for connections directly (inbox default limit is 20,
        # but get_highlights_for_connections has no artificial limit)
        from db import get_highlights_for_connections
        raw = get_highlights_for_connections(self.conn, self.session_a)
        self.assertEqual(len(raw), 100,
                         f"Expected 100 from get_highlights_for_connections, got {len(raw)}")

        # Verify no duplicates by summary
        summaries = [h['summary'] for h in raw]
        self.assertEqual(len(summaries), len(set(summaries)),
                         "Duplicate highlights detected in results")

    def test_no_duplicates_in_inbox_output(self):
        """Running inbox twice does not double-count highlights."""
        connect(self.conn, self.session_a, self.session_b, 'dedup test', check_mode='decay')

        for i in range(10):
            _make_highlight(self.conn, self.session_b, f'Dedup highlight {i}')

        result1 = inbox(self.conn, self.session_a, mark_read=True)
        result2 = inbox(self.conn, self.session_a)

        # Count how many times a unique summary appears in combined results
        # If dedup is working, a highlight should only appear once total
        count_in_first = result1.count('Dedup highlight 0')
        count_in_second = result2.count('Dedup highlight 0')
        self.assertEqual(count_in_first, 1, "Highlight appeared multiple times in first inbox")
        self.assertEqual(count_in_second, 0, "Highlight appeared in second inbox (should be empty)")


# ---------------------------------------------------------------------------
# TestDecayProgression
# ---------------------------------------------------------------------------

class TestDecayProgression(unittest.TestCase):
    """Validate the decay/back-off schedule for check_mode='decay' connections.

    The logic (from prompt_submit._check_connections):
      - counter increments each call
      - when counter >= interval: fire check, reset counter to 0, interval += 3 (cap 30)
      - so with initial interval=7: first fire at prompt 7, then interval becomes 10
      - second fire at prompt 7 + 10 = 17, then interval becomes 13
      - pattern continues until interval caps at 30

    We manipulate connection state directly via update_connection_check to
    avoid needing real transcript files.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'decay.db')
        self.conn = get_connection(self.db_path)
        self.watcher = _make_session(self.conn, 'decay-watcher')
        self.target = _make_session(self.conn, 'decay-target')
        insert_connection(self.conn, self.watcher, self.target, 'decay topic',
                          check_mode='decay', delivery_mode='inject')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _simulate_prompt(self) -> bool:
        """Simulate one prompt tick using the decay logic from _check_connections.

        Directly mirrors the counter/interval logic from prompt_submit.py
        without going through the full hook (no transcript files required).

        Returns True if a check fired on this tick.
        """
        connections = get_connections(self.conn, self.watcher)
        fired = False
        for connection in connections:
            if connection['check_mode'] != 'decay':
                continue

            counter = (connection['check_counter'] or 0) + 1
            interval = connection['check_interval'] or 7

            if counter >= interval:
                # Check fired
                now = _now()
                new_interval = min(30, interval + 3)
                update_connection_check(self.conn, connection['id'], 0, new_interval, now)
                fired = True
            else:
                update_connection_check(
                    self.conn,
                    connection['id'],
                    counter,
                    interval,
                    connection['last_checked_at'],
                )
        return fired

    def test_first_check_fires_at_prompt_7(self):
        """With default interval=7, the first decay check fires at exactly prompt 7."""
        fire_prompts = []
        for prompt_num in range(1, 51):
            if self._simulate_prompt():
                fire_prompts.append(prompt_num)

        self.assertGreater(len(fire_prompts), 0, "No decay check ever fired in 50 prompts")
        self.assertEqual(fire_prompts[0], 7,
                         f"First check should fire at prompt 7, fired at {fire_prompts[0]}")

    def test_second_check_fires_at_prompt_17(self):
        """Second decay check fires at prompt 17 (7 + 10, since interval grew to 10)."""
        fire_prompts = []
        for prompt_num in range(1, 51):
            if self._simulate_prompt():
                fire_prompts.append(prompt_num)

        self.assertGreaterEqual(len(fire_prompts), 2,
                                f"Expected at least 2 fires in 50 prompts, got {len(fire_prompts)}")
        self.assertEqual(fire_prompts[1], 17,
                         f"Second check should fire at prompt 17, fired at {fire_prompts[1]}")

    def test_intervals_grow_by_3_each_time(self):
        """Check intervals grow by exactly 3 on each fire: 7, 10, 13, 16, ..."""
        expected_intervals = [7, 10, 13, 16, 19, 22, 25, 28, 30, 30]
        observed_intervals = []

        for _ in range(300):
            connections = get_connections(self.conn, self.watcher)
            c = connections[0]
            counter = (c['check_counter'] or 0) + 1
            interval = c['check_interval'] or 7

            if counter >= interval:
                observed_intervals.append(interval)
                now = _now()
                new_interval = min(30, interval + 3)
                update_connection_check(self.conn, c['id'], 0, new_interval, now)
            else:
                update_connection_check(
                    self.conn, c['id'], counter, interval, c['last_checked_at']
                )

            if len(observed_intervals) >= len(expected_intervals):
                break

        self.assertEqual(len(observed_intervals), len(expected_intervals),
                         f"Expected {len(expected_intervals)} fires, got {len(observed_intervals)}")
        for i, (observed, expected) in enumerate(zip(observed_intervals, expected_intervals)):
            self.assertEqual(observed, expected,
                             f"Fire #{i+1}: expected interval={expected}, got {observed}")

    def test_interval_stabilizes_at_30(self):
        """After enough prompts, check_interval stays at 30 and never exceeds it."""
        # Simulate enough prompts to push interval past the cap
        for _ in range(500):
            connections = get_connections(self.conn, self.watcher)
            c = connections[0]
            counter = (c['check_counter'] or 0) + 1
            interval = c['check_interval'] or 7
            if counter >= interval:
                now = _now()
                new_interval = min(30, interval + 3)
                update_connection_check(self.conn, c['id'], 0, new_interval, now)
            else:
                update_connection_check(
                    self.conn, c['id'], counter, interval, c['last_checked_at']
                )

        # After 500 prompts the interval must be capped at 30
        final = get_connections(self.conn, self.watcher)[0]
        self.assertEqual(final['check_interval'], 30,
                         f"Interval should be capped at 30, got {final['check_interval']}")
        self.assertLessEqual(final['check_counter'], 30,
                             "Counter should never exceed max interval of 30")


# ---------------------------------------------------------------------------
# TestHighlightDedup
# ---------------------------------------------------------------------------

class TestHighlightDedup(unittest.TestCase):
    """Duplicate highlight summaries within a session are rejected by UNIQUE constraint."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'dedup.db')
        self.conn = get_connection(self.db_path)
        self.session = _make_session(self.conn, 'dedup-session-001')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_identical_summary_inserts_only_once(self):
        """Inserting the same summary twice results in exactly 1 row."""
        summary = 'Fixed the coalescing bug in the CUDA kernel'
        insert_highlight(self.conn, self.session, summary, 'cuda', 'explicit')
        insert_highlight(self.conn, self.session, summary, 'cuda', 'explicit')

        highlights = get_highlights(self.conn, self.session, limit=10)
        self.assertEqual(len(highlights), 1,
                         "UNIQUE constraint should prevent duplicate (session_id, summary)")

    def test_different_summary_inserts_separately(self):
        """Two distinct summaries both insert correctly — total is 2."""
        insert_highlight(self.conn, self.session, 'First unique insight', 'cuda', 'explicit')
        insert_highlight(self.conn, self.session, 'Second unique insight', 'metal', 'explicit')

        highlights = get_highlights(self.conn, self.session, limit=10)
        self.assertEqual(len(highlights), 2,
                         "Two distinct summaries should both be stored")

    def test_same_summary_different_sessions_both_stored(self):
        """Same summary in two different sessions creates 2 rows (constraint is per-session)."""
        other = _make_session(self.conn, 'dedup-session-002')
        summary = 'Shared insight text across sessions'

        insert_highlight(self.conn, self.session, summary, 'tag1', 'explicit')
        insert_highlight(self.conn, other, summary, 'tag2', 'explicit')

        hl1 = get_highlights(self.conn, self.session, limit=10)
        hl2 = get_highlights(self.conn, other, limit=10)
        self.assertEqual(len(hl1), 1)
        self.assertEqual(len(hl2), 1)


# ---------------------------------------------------------------------------
# TestAutoDetectVolume
# ---------------------------------------------------------------------------

class TestAutoDetectVolume(unittest.TestCase):
    """auto_detect_highlights on 50 exchanges, ~10 of which have 2+ solution signals."""

    # Template for a strong solution response (2+ signals, 25+ words).
    # The exchange number appears early so summaries remain unique after the
    # 100-char truncation applied by auto_detect_highlights.
    _SOLUTION_TEMPLATE = (
        "Exchange {n:03d}: the issue was a misaligned memory access. "
        "The fix is to pad the shared memory array by one element per warp. "
        "This resolves the bank-conflict bottleneck observed in the profiler output."
    )

    # Template for a generic response (no signals or only 1, short)
    _GENERIC_TEMPLATE = (
        "Sure, let me look at that for you. "
        "Here is exchange {n}."
    )

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'auto_detect_vol.db')
        self.conn = get_connection(self.db_path)
        self.session = _make_session(self.conn, 'autovol-session-001')
        set_session_config(self.conn, self.session, 'auto_highlight', True)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build_exchanges(self):
        """Build 50 exchanges where indices 0, 5, 10, 15, 20, 25, 30, 35, 40, 45
        (every 5th starting at 0) are solution responses — exactly 10 signal exchanges.
        """
        exchanges = []
        solution_indices = set(range(0, 50, 5))  # 0, 5, 10, ..., 45 — 10 total
        for i in range(50):
            if i in solution_indices:
                text = self._SOLUTION_TEMPLATE.format(n=i)
            else:
                text = self._GENERIC_TEMPLATE.format(n=i)
            exchanges.append(_make_exchange(i, text))
        return exchanges, solution_indices

    def test_exactly_10_highlights_created_not_50(self):
        """auto_detect_highlights fires for exactly the 10 signal exchanges."""
        exchanges, _ = self._build_exchanges()
        summaries = auto_detect_highlights(self.conn, self.session, exchanges)

        self.assertEqual(len(summaries), 10,
                         f"Expected 10 auto highlights, got {len(summaries)}")

        db_highlights = get_highlights(self.conn, self.session, limit=100)
        self.assertEqual(len(db_highlights), 10,
                         f"Expected 10 highlights in DB, got {len(db_highlights)}")

    def test_all_auto_highlights_have_source_auto(self):
        """Every auto-detected highlight has source='auto'."""
        exchanges, _ = self._build_exchanges()
        auto_detect_highlights(self.conn, self.session, exchanges)

        db_highlights = get_highlights(self.conn, self.session, limit=100)
        for h in db_highlights:
            self.assertEqual(h['source'], 'auto',
                             f"Highlight '{h['summary'][:40]}' has source='{h['source']}'")

    def test_generic_exchanges_do_not_create_highlights(self):
        """None of the 40 generic exchanges produce highlights."""
        exchanges, solution_indices = self._build_exchanges()
        auto_detect_highlights(self.conn, self.session, exchanges)

        db_highlights = get_highlights(self.conn, self.session, limit=100)
        # All highlights must come from solution exchanges
        # Check that every highlight's exchange_idx corresponds to a solution index
        for h in db_highlights:
            self.assertIn(h['exchange_idx'], solution_indices,
                          f"Highlight at exchange_idx={h['exchange_idx']} is not a solution exchange")


# ---------------------------------------------------------------------------
# TestCrossProjectHighlightIsolation
# ---------------------------------------------------------------------------

class TestCrossProjectHighlightIsolation(unittest.TestCase):
    """Sessions in project A and B can share highlights without contamination."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'isolation.db')
        self.conn = get_connection(self.db_path)

        self.sess_a = _make_session(self.conn, 'proj-a-session-001',
                                    project_path='/work/projectA',
                                    project_hash='hash-project-A')
        self.sess_b = _make_session(self.conn, 'proj-b-session-001',
                                    project_path='/work/projectB',
                                    project_hash='hash-project-B')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cross_project_inbox_sees_target_highlights(self):
        """Session A (project A) can read highlights from session B (project B)."""
        connect(self.conn, self.sess_a, self.sess_b, 'cross-project work')
        _make_highlight(self.conn, self.sess_b, 'Insight from project B session')

        result = inbox(self.conn, self.sess_a)

        self.assertIn('Inbox', result)
        self.assertIn('Insight from project B session', result)
        self.assertIn(self.sess_b[:8], result)

    def test_project_a_exchanges_unaffected_by_connection(self):
        """Adding highlights to project B does not create highlights in project A."""
        connect(self.conn, self.sess_a, self.sess_b, 'cross-project work')

        # Add several highlights to B
        for i in range(5):
            _make_highlight(self.conn, self.sess_b, f'B insight {i}')

        # Project A should have zero highlights of its own
        a_highlights = get_highlights(self.conn, self.sess_a, limit=50)
        self.assertEqual(len(a_highlights), 0,
                         "Project A should have no highlights after B receives them")

    def test_project_b_highlights_not_visible_from_unconnected_a_session(self):
        """A second project A session (not connected) sees nothing from B."""
        # Don't connect sess_a2 to sess_b
        sess_a2 = _make_session(self.conn, 'proj-a-session-002',
                                project_path='/work/projectA',
                                project_hash='hash-project-A')

        _make_highlight(self.conn, self.sess_b, 'B only insight')

        result = inbox(self.conn, sess_a2)
        self.assertIn('No new highlights', result)


# ---------------------------------------------------------------------------
# TestInboxAfterPrune
# ---------------------------------------------------------------------------

class TestInboxAfterPrune(unittest.TestCase):
    """Pruning the target session and its effect on A's inbox.

    BUG DOCUMENTED HERE: prune_session() does not delete highlights or
    connections before removing the sessions row.  With PRAGMA foreign_keys=ON
    (which db.get_connection() sets), this causes a sqlite3.IntegrityError
    when a session has dependent highlights or connections.

    The tests below both document the FK crash as a confirmed bug AND verify
    the correct post-prune inbox behavior by manually cleaning FK dependents
    first (simulating what a fixed prune_session() should do).
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'prune.db')
        self.conn = get_connection(self.db_path)
        self.sess_a = _make_session(self.conn, 'prune-session-A')
        self.sess_b = _make_session(self.conn, 'prune-session-B')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _full_prune(self, session_id: str) -> None:
        """prune_session now handles highlights and connections natively."""
        prune_session(self.conn, session_id)

    # ------------------------------------------------------------------
    # BUG EXPOSURE TESTS
    # ------------------------------------------------------------------

    def test_prune_session_cleans_highlights(self):
        """prune_session() should delete highlights before removing the session."""
        _make_highlight(self.conn, self.sess_b, 'Highlight to be pruned')
        prune_session(self.conn, self.sess_b)  # Should NOT raise
        self.assertIsNone(get_session(self.conn, self.sess_b))
        highlights = get_highlights(self.conn, self.sess_b)
        self.assertEqual(len(highlights), 0)

    def test_prune_session_cleans_connections(self):
        """prune_session() should delete connections before removing the session."""
        connect(self.conn, self.sess_a, self.sess_b, 'will be pruned')
        prune_session(self.conn, self.sess_b)  # Should NOT raise
        self.assertIsNone(get_session(self.conn, self.sess_b))
        conns = get_connections(self.conn, self.sess_a)
        self.assertEqual(len(conns), 0)

    # ------------------------------------------------------------------
    # PRUNE + INBOX BEHAVIOR TESTS
    # ------------------------------------------------------------------

    def test_inbox_empty_after_target_fully_pruned(self):
        """After session B is fully pruned (highlights+connections cleaned first),
        A's inbox returns no highlights.

        This tests the intended post-prune inbox behavior independent of the
        FK bug in prune_session().
        """
        connect(self.conn, self.sess_a, self.sess_b, 'pruned session work')
        for i in range(5):
            _make_highlight(self.conn, self.sess_b, f'B highlight {i} before prune')

        pre_prune = get_highlights(self.conn, self.sess_b, limit=10)
        self.assertEqual(len(pre_prune), 5, "Expected 5 highlights before prune")

        # Full prune: clear highlights + connections first, then prune session
        self._full_prune(self.sess_b)

        result = inbox(self.conn, self.sess_a)
        self.assertIn('No new highlights', result,
                      "Inbox should be empty after target session is fully pruned")

    def test_highlights_gone_after_explicit_delete_then_prune(self):
        """After manually deleting highlights and then pruning, no highlight rows remain."""
        _make_highlight(self.conn, self.sess_b, 'Will be fully pruned')

        pre = get_highlights(self.conn, self.sess_b, limit=10)
        self.assertEqual(len(pre), 1)

        self._full_prune(self.sess_b)

        cur = self.conn.execute(
            "SELECT COUNT(*) FROM highlights WHERE session_id = ?", (self.sess_b,)
        )
        count = cur.fetchone()[0]
        self.assertEqual(count, 0, "All highlights should be gone after full prune")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
