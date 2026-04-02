#!/usr/bin/env python3
"""Concurrent access stress tests for the Claude Context Recall plugin.

Validates that WAL mode handles multiple writers correctly by spawning threads
that simulate different Claude sessions writing to the same database file
simultaneously.
"""

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

# Add hooks and scripts directories to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'hooks'))
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from db import (
    get_connection,
    insert_session,
    get_session,
    get_exchanges,
    insert_exchanges,
    search_exchanges_fts,
    get_tags,
)
from auto_tagger import compute_auto_tags
from prompt_submit import run_hook


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exchange(idx, user_text, assistant_text, session_tag=""):
    """Build a minimal exchange dict."""
    return {
        'idx': idx,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'preview': user_text[:80],
        'user_text': user_text,
        'assistant_text': assistant_text,
    }


def _make_session_id(thread_num):
    return f"stress-session-{thread_num:02d}"


# ---------------------------------------------------------------------------
# TestConcurrentSessionWrites
# ---------------------------------------------------------------------------

class TestConcurrentSessionWrites(unittest.TestCase):
    """5 threads each simulate a Claude session inserting 50 exchanges one-by-one."""

    NUM_THREADS = 5
    EXCHANGES_PER_SESSION = 50

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'stress.db'
        # Pre-create the schema once so threads don't race on first-init
        conn = get_connection(self.db_path)
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _worker(self, thread_num, barrier, errors):
        """Thread body: create a session and insert EXCHANGES_PER_SESSION exchanges."""
        session_id = _make_session_id(thread_num)
        # Synchronise all threads so they actually write concurrently
        barrier.wait()
        try:
            conn = get_connection(self.db_path)
            try:
                insert_session(
                    conn,
                    session_id=session_id,
                    project_path=f'/proj/thread-{thread_num}',
                    project_hash=f'hash-{thread_num:02d}',
                    started_at=datetime.now(timezone.utc).isoformat(),
                )
                for i in range(self.EXCHANGES_PER_SESSION):
                    ex = _make_exchange(
                        idx=i,
                        user_text=f"Thread {thread_num} user message number {i} about threading and concurrency",
                        assistant_text=f"Thread {thread_num} assistant reply number {i} about WAL mode and sqlite",
                    )
                    insert_exchanges(conn, session_id, [ex])
            finally:
                conn.close()
        except Exception as exc:
            errors.append((thread_num, exc))

    def test_all_sessions_and_exchanges_present(self):
        """All 5 sessions with exactly 50 exchanges each survive concurrent writes."""
        errors = []
        barrier = threading.Barrier(self.NUM_THREADS)
        threads = [
            threading.Thread(target=self._worker, args=(n, barrier, errors))
            for n in range(self.NUM_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        # Report thread errors first
        self.assertEqual(errors, [], f"Thread errors: {errors}")

        # Verify all sessions exist and have the right exchange count
        conn = get_connection(self.db_path)
        try:
            for n in range(self.NUM_THREADS):
                session_id = _make_session_id(n)
                session = get_session(conn, session_id)
                self.assertIsNotNone(session, f"Session {session_id} not found in DB")

                exchanges = get_exchanges(conn, session_id)
                self.assertEqual(
                    len(exchanges), self.EXCHANGES_PER_SESSION,
                    f"Session {session_id}: expected {self.EXCHANGES_PER_SESSION} exchanges, got {len(exchanges)}",
                )

                # Verify content integrity — each exchange idx matches its position
                for ex in exchanges:
                    self.assertEqual(
                        ex['idx'], exchanges.index(ex),
                        f"Session {session_id}: exchange idx mismatch at position {exchanges.index(ex)}",
                    )
                    # Verify the user_text contains this thread's number
                    self.assertIn(
                        f"Thread {n} user message",
                        ex['user_text'],
                        f"Session {session_id}: unexpected content in exchange idx={ex['idx']}",
                    )
        finally:
            conn.close()

    def test_fts5_index_consistent_after_concurrent_writes(self):
        """FTS5 search returns correct results for each session after concurrent writes."""
        errors = []
        barrier = threading.Barrier(self.NUM_THREADS)
        threads = [
            threading.Thread(target=self._worker, args=(n, barrier, errors))
            for n in range(self.NUM_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        conn = get_connection(self.db_path)
        try:
            for n in range(self.NUM_THREADS):
                session_id = _make_session_id(n)
                # Use a single distinctive word present in every exchange's user_text.
                # search_exchanges_fts wraps the query in double quotes (phrase match),
                # so multi-word phrases that aren't adjacent will fail to match.
                results = search_exchanges_fts(conn, "concurrency", session_id=session_id, limit=100)
                self.assertGreater(
                    len(results), 0,
                    f"FTS5 search returned no results for session {session_id}",
                )
                # All results must belong to this session
                for r in results:
                    self.assertEqual(
                        r['session_id'], session_id,
                        f"FTS5 result belongs to wrong session: {r['session_id']}",
                    )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# TestConcurrentReadWrite
# ---------------------------------------------------------------------------

class TestConcurrentReadWrite(unittest.TestCase):
    """Thread A writes 100 exchanges while Thread B continuously reads/searches."""

    NUM_WRITES = 100

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'readwrite.db'
        conn = get_connection(self.db_path)
        insert_session(
            conn,
            session_id='writer-session',
            project_path='/proj/writer',
            project_hash='writerhash',
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _writer(self, start_event, done_event, errors):
        """Insert NUM_WRITES exchanges, one at a time with small delays."""
        start_event.wait()
        try:
            conn = get_connection(self.db_path)
            try:
                for i in range(self.NUM_WRITES):
                    ex = _make_exchange(
                        idx=i,
                        user_text=f"Write operation {i}: discussing kernel optimisation techniques",
                        assistant_text=f"Reply {i}: WAL mode allows concurrent reads during writes",
                    )
                    insert_exchanges(conn, 'writer-session', [ex])
                    time.sleep(0.001)  # ~1ms delay to increase interleaving
            finally:
                conn.close()
        except Exception as exc:
            errors.append(('writer', exc))
        finally:
            done_event.set()

    def _reader(self, start_event, done_event, errors, read_results):
        """Continuously read and search while the writer is active."""
        start_event.wait()
        iteration = 0
        while not done_event.is_set() or iteration < 10:
            try:
                conn = get_connection(self.db_path)
                try:
                    # Read all exchanges — should never partial-read a write
                    exchanges = get_exchanges(conn, 'writer-session')
                    # Search — should never raise "database locked"
                    results = search_exchanges_fts(conn, 'kernel', limit=50)
                    read_results.append((len(exchanges), len(results)))
                finally:
                    conn.close()
            except Exception as exc:
                errors.append(('reader', exc))
                break
            iteration += 1
            time.sleep(0.002)

    def test_reader_never_locked_and_sees_consistent_state(self):
        """Reader B never gets database locked; after both finish all 100 writes are present."""
        errors = []
        read_results = []
        start_event = threading.Event()
        done_event = threading.Event()

        writer = threading.Thread(target=self._writer, args=(start_event, done_event, errors))
        reader = threading.Thread(target=self._reader, args=(start_event, done_event, errors, read_results))

        reader.start()
        writer.start()
        start_event.set()

        writer.join(timeout=30)
        reader.join(timeout=15)

        # No errors from either thread (no "database locked" or other exceptions)
        self.assertEqual(errors, [], f"Thread errors: {errors}")

        # Reader must have performed at least some reads
        self.assertGreater(len(read_results), 0, "Reader never performed any reads")

        # After all writes complete, the DB must have all 100 exchanges
        conn = get_connection(self.db_path)
        try:
            final_exchanges = get_exchanges(conn, 'writer-session')
            self.assertEqual(
                len(final_exchanges), self.NUM_WRITES,
                f"Expected {self.NUM_WRITES} exchanges, got {len(final_exchanges)}",
            )
        finally:
            conn.close()

        # Verify monotonically non-decreasing reads (no partial state regression)
        exchange_counts = [r[0] for r in read_results]
        for i in range(1, len(exchange_counts)):
            self.assertGreaterEqual(
                exchange_counts[i], exchange_counts[i - 1],
                f"Read regression at index {i}: {exchange_counts[i]} < {exchange_counts[i-1]}",
            )


# ---------------------------------------------------------------------------
# TestConcurrentFTS5
# ---------------------------------------------------------------------------

class TestConcurrentFTS5(unittest.TestCase):
    """Thread A writes 'kernel' exchanges, B writes 'database' exchanges,
    C searches for 'kernel' while both are writing."""

    EXCHANGES_PER_THREAD = 30

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'fts5.db'
        conn = get_connection(self.db_path)
        for name in ('kernel-session', 'database-session'):
            insert_session(
                conn,
                session_id=name,
                project_path=f'/proj/{name}',
                project_hash=f'hash-{name}',
                started_at=datetime.now(timezone.utc).isoformat(),
            )
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _kernel_writer(self, barrier, errors):
        barrier.wait()
        try:
            conn = get_connection(self.db_path)
            try:
                for i in range(self.EXCHANGES_PER_THREAD):
                    ex = _make_exchange(
                        idx=i,
                        user_text=f"How does the Linux kernel scheduler {i} handle preemption?",
                        assistant_text=f"The kernel uses CFS {i} with red-black tree scheduling queues.",
                    )
                    insert_exchanges(conn, 'kernel-session', [ex])
                    time.sleep(0.002)
            finally:
                conn.close()
        except Exception as exc:
            errors.append(('kernel-writer', exc))

    def _database_writer(self, barrier, errors):
        barrier.wait()
        try:
            conn = get_connection(self.db_path)
            try:
                for i in range(self.EXCHANGES_PER_THREAD):
                    ex = _make_exchange(
                        idx=i,
                        user_text=f"Explain database indexing strategy {i} for columnar storage.",
                        assistant_text=f"Database B-tree indexes {i} provide O(log n) lookup complexity.",
                    )
                    insert_exchanges(conn, 'database-session', [ex])
                    time.sleep(0.002)
            finally:
                conn.close()
        except Exception as exc:
            errors.append(('database-writer', exc))

    def _searcher(self, barrier, stop_event, errors, search_log):
        barrier.wait()
        while not stop_event.is_set():
            try:
                conn = get_connection(self.db_path)
                try:
                    results = search_exchanges_fts(conn, 'kernel', limit=200)
                    search_log.append([r['session_id'] for r in results])
                finally:
                    conn.close()
            except Exception as exc:
                errors.append(('searcher', exc))
                break
            time.sleep(0.005)

    def test_fts5_no_cross_contamination(self):
        """'kernel' search returns only kernel-session exchanges;
        'database' returns only database-session exchanges; no corruption."""
        errors = []
        search_log = []
        barrier = threading.Barrier(3)
        stop_event = threading.Event()

        t_kernel = threading.Thread(target=self._kernel_writer, args=(barrier, errors))
        t_database = threading.Thread(target=self._database_writer, args=(barrier, errors))
        t_searcher = threading.Thread(target=self._searcher, args=(barrier, stop_event, errors, search_log))

        for t in (t_kernel, t_database, t_searcher):
            t.start()

        t_kernel.join(timeout=30)
        t_database.join(timeout=30)
        stop_event.set()
        t_searcher.join(timeout=10)

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        # Final state assertions
        conn = get_connection(self.db_path)
        try:
            # 'kernel' search must only return kernel-session rows
            kernel_results = search_exchanges_fts(conn, 'kernel', limit=200)
            self.assertGreater(len(kernel_results), 0, "No kernel results found")
            for r in kernel_results:
                self.assertEqual(
                    r['session_id'], 'kernel-session',
                    f"'kernel' search returned row from wrong session: {r['session_id']}",
                )

            # 'database' search must only return database-session rows
            db_results = search_exchanges_fts(conn, 'database', limit=200)
            self.assertGreater(len(db_results), 0, "No database results found")
            for r in db_results:
                self.assertEqual(
                    r['session_id'], 'database-session',
                    f"'database' search returned row from wrong session: {r['session_id']}",
                )

            # FTS5 integrity check: every search result must match the content table
            for r in kernel_results + db_results:
                content_row = conn.execute(
                    "SELECT user_text, assistant_text FROM exchanges WHERE id = ?",
                    (r['id'],),
                ).fetchone()
                self.assertIsNotNone(
                    content_row,
                    f"FTS5 result id={r['id']} has no matching row in exchanges (index corruption)",
                )
                self.assertEqual(
                    r['user_text'], content_row['user_text'],
                    f"FTS5 user_text mismatch for id={r['id']}",
                )
        finally:
            conn.close()

        # During-write search results must only have had kernel-session entries
        for snapshot in search_log:
            for session_id in snapshot:
                self.assertEqual(
                    session_id, 'kernel-session',
                    f"Mid-write 'kernel' search returned row from session: {session_id}",
                )


# ---------------------------------------------------------------------------
# TestConcurrentAutoTag
# ---------------------------------------------------------------------------

class TestConcurrentAutoTag(unittest.TestCase):
    """3 threads each run a simulated prompt_submit hook with distinct content."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'autotag.db'
        # Create schema
        conn = get_connection(self.db_path)
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run_session_hooks(self, session_num, topic_word, barrier, errors):
        """Simulate multiple prompt_submit calls for one session using run_hook."""
        session_id = f"autotag-session-{session_num:02d}"
        # Build a transcript file with topic-specific content
        transcript_path = Path(self.temp_dir) / f'transcript-{session_num}.jsonl'
        import json

        entries = []
        for i in range(10):
            entries.append(json.dumps({
                'type': 'user',
                'message': {'content': [{'type': 'text', 'text': f'{topic_word} {topic_word} {topic_word} discussion point {i}'}]},
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }))
            entries.append(json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': f'Reply about {topic_word} {topic_word} topic {i}'}]},
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }))

        with open(transcript_path, 'w') as f:
            f.write('\n'.join(entries) + '\n')

        input_data = {
            'session_id': session_id,
            'transcript_path': str(transcript_path),
            'user_prompt': f'Tell me more about {topic_word}',
            'project_path': f'/proj/autotag-{session_num}',
            'project_hash': f'autotag-hash-{session_num:02d}',
        }

        barrier.wait()
        try:
            run_hook(input_data, db_path=self.db_path)
        except Exception as exc:
            errors.append((session_num, exc))

    def test_auto_tags_per_session_no_cross_contamination(self):
        """Each session gets its own auto-tags with no cross-contamination."""
        # Use clearly distinct technical-sounding words that the tagger won't filter
        topics = [
            (0, 'triton'),    # technical, no digits/stopwords
            (1, 'pytorch'),   # technical
            (2, 'jax'),       # technical (short — may or may not tag)
        ]
        errors = []
        barrier = threading.Barrier(len(topics))
        threads = [
            threading.Thread(target=self._run_session_hooks, args=(num, word, barrier, errors))
            for num, word in topics
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        conn = get_connection(self.db_path)
        try:
            for num, topic_word in topics:
                session_id = f"autotag-session-{num:02d}"
                tags = get_tags(conn, session_id=session_id)
                tag_names = [t['tag'] for t in tags]

                # Session must own some tags
                self.assertGreater(
                    len(tag_names), 0,
                    f"Session {session_id} has no auto-tags (topic: {topic_word})",
                )

                # No tag from another session's topic should appear
                other_topics = [w for n, w in topics if n != num]
                for other_word in other_topics:
                    self.assertNotIn(
                        other_word, tag_names,
                        f"Session {session_id} incorrectly has tag '{other_word}' (cross-contamination)",
                    )

                # Verify all tags in this session belong to this session_id in DB
                for tag_row in tags:
                    self.assertEqual(
                        tag_row['session_id'], session_id,
                        f"Tag '{tag_row['tag']}' has wrong session_id: {tag_row['session_id']}",
                    )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# TestBusyTimeout
# ---------------------------------------------------------------------------

class TestBusyTimeout(unittest.TestCase):
    """A long write holds the DB lock; a second writer must wait and succeed."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / 'busy.db'
        conn = get_connection(self.db_path)
        for sid in ('sess-holder', 'sess-waiter'):
            insert_session(
                conn,
                session_id=sid,
                project_path='/proj/busy',
                project_hash='busyhash',
                started_at=datetime.now(timezone.utc).isoformat(),
            )
        conn.close()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _long_writer(self, hold_seconds, release_event, started_event, errors):
        """Open a connection and hold an exclusive write transaction for hold_seconds."""
        try:
            conn = get_connection(self.db_path)
            conn.execute("BEGIN EXCLUSIVE")
            # Insert something within the exclusive transaction
            conn.execute(
                "INSERT OR IGNORE INTO exchanges "
                "(session_id, idx, timestamp, preview, user_text, assistant_text) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ('sess-holder', 0, datetime.now(timezone.utc).isoformat(),
                 'lock holder preview', 'lock holder user', 'lock holder assistant'),
            )
            started_event.set()
            # Hold the lock for hold_seconds
            time.sleep(hold_seconds)
            conn.commit()
            conn.close()
        except Exception as exc:
            errors.append(('long-writer', exc))
            started_event.set()  # unblock waiter even on error

    def _waiting_writer(self, start_after_event, timing, errors):
        """Wait for the long writer to start, then attempt a write (should wait, not fail)."""
        start_after_event.wait()
        t0 = time.monotonic()
        try:
            conn = get_connection(self.db_path)
            try:
                ex = _make_exchange(
                    idx=0,
                    user_text="Waiter thread user text about database busy timeout handling",
                    assistant_text="Waiter thread assistant text about WAL mode retries",
                )
                insert_exchanges(conn, 'sess-waiter', [ex])
            finally:
                conn.close()
            timing['elapsed'] = time.monotonic() - t0
            timing['success'] = True
        except Exception as exc:
            timing['elapsed'] = time.monotonic() - t0
            timing['success'] = False
            errors.append(('waiting-writer', exc))

    def test_second_writer_succeeds_after_waiting(self):
        """Second writer waits up to 5s busy timeout and succeeds, not instant failure."""
        hold_seconds = 0.5  # Hold lock for 500ms — safely within 5s busy timeout
        errors = []
        timing = {}
        started_event = threading.Event()

        holder = threading.Thread(
            target=self._long_writer,
            args=(hold_seconds, None, started_event, errors),
        )
        waiter = threading.Thread(
            target=self._waiting_writer,
            args=(started_event, timing, errors),
        )

        holder.start()
        waiter.start()
        holder.join(timeout=15)
        waiter.join(timeout=15)

        self.assertEqual(errors, [], f"Thread errors: {errors}")

        # Waiter must have succeeded
        self.assertTrue(
            timing.get('success', False),
            "Waiting writer did not succeed — likely got 'database locked' immediately",
        )

        # Waiter must have waited at least some time (not instant failure)
        elapsed = timing.get('elapsed', 0)
        self.assertGreater(
            elapsed, 0.05,
            f"Waiting writer returned too quickly ({elapsed:.3f}s), may not have actually waited",
        )

        # Both writes must be present in the DB
        conn = get_connection(self.db_path)
        try:
            holder_exchanges = get_exchanges(conn, 'sess-holder')
            waiter_exchanges = get_exchanges(conn, 'sess-waiter')
            self.assertEqual(len(holder_exchanges), 1, "Holder's exchange not persisted")
            self.assertEqual(len(waiter_exchanges), 1, "Waiter's exchange not persisted")
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
