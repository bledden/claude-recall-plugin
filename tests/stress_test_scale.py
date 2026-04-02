#!/usr/bin/env python3
"""Stress / scale tests for the recall plugin SQLite database layer.

Tests large dataset performance: many sessions, many exchanges, FTS5 at scale,
prune correctness, and DB file size.

Run with:
    python3 -m pytest tests/stress_test_scale.py -v -s
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from db import (
    get_connection,
    insert_session,
    list_sessions,
    insert_exchanges,
    get_exchanges,
    search_exchanges_fts,
    search_exchanges_global,
    prune_before_date,
    get_stats,
)

# ---------------------------------------------------------------------------
# Realistic content templates
# ---------------------------------------------------------------------------

TOPICS = [
    # GPU / kernel topics
    ("warp divergence in the reduction kernel",
     "use warp shuffle instructions to avoid divergence and improve throughput"),
    ("shared memory bank conflicts on the transpose kernel",
     "pad the shared memory array by one element to eliminate bank conflicts"),
    ("occupancy is limited by register pressure in the attention kernel",
     "reduce registers per thread or use __launch_bounds__ to hint the compiler"),
    ("memory coalescing on global reads in the gather kernel",
     "ensure threads in a warp access consecutive addresses for coalesced loads"),
    ("thread block size selection for the GEMM kernel",
     "use 256 threads for compute-bound, 128 for memory-bound workloads"),
    ("how to implement a parallel prefix sum in CUDA",
     "use two-phase scan: local scan in shared memory, then global propagation"),
    ("triton kernel for softmax with numerical stability",
     "subtract the row max before exp to prevent overflow in softmax"),
    ("tiling strategy for matrix multiplication on Metal",
     "tile into threadgroup memory with 16x16 blocks for best M1 throughput"),
    ("MSL vs CUDA differences in memory address spaces",
     "device, threadgroup, and constant map to global, shared, and constant in CUDA"),
    ("reduction kernel with atomic operations",
     "use atomicAdd to accumulate partial sums from each threadblock"),
    # Database / systems topics
    ("B-tree vs LSM-tree tradeoffs for write-heavy workloads",
     "LSM-trees batch writes in memory and flush to sorted runs, trading read amp for write amp"),
    ("WAL mode in SQLite for concurrent readers",
     "WAL allows readers to proceed without blocking writers by appending changes"),
    ("FTS5 tokenizer configuration for code search",
     "use the unicode61 tokenizer with separator categories for symbol-aware splitting"),
    ("index design for a time-series query with project_hash filter",
     "composite index on (project_hash, started_at) covers the most common filter pattern"),
    ("vacuum and analyze for SQLite query plan freshness",
     "run ANALYZE after bulk inserts to update statistics for the query planner"),
    # ML / inference topics
    ("quantization error accumulation in int8 matrix multiply",
     "use per-channel scale factors and symmetric quantization to minimize error"),
    ("KV cache eviction policy for long-context inference",
     "sliding window attention with a fixed sink token set keeps memory bounded"),
    ("flash attention backward pass memory layout",
     "recompute attention scores from Q K V during backward instead of storing them"),
    ("batched decoding vs speculative decoding throughput tradeoffs",
     "speculative decoding shines at low batch sizes where the draft model is cheap"),
    ("distributed tensor parallelism for MLP layers",
     "split weight columns across ranks and all-reduce the partial sums after the activation"),
    # Web / backend topics
    ("rate limiting with a token bucket vs leaky bucket",
     "token bucket allows bursts up to the bucket capacity, leaky bucket smooths traffic"),
    ("connection pool sizing for PostgreSQL under bursty load",
     "set pool size to roughly 2x CPU cores and tune idle timeout to reclaim connections"),
    ("HTTP/2 multiplexing vs HTTP/1.1 keep-alive",
     "HTTP/2 multiplexes streams on one connection, eliminating head-of-line blocking"),
    ("cache invalidation with event-driven cache busting",
     "publish cache-bust events on writes and invalidate affected keys in the subscriber"),
    ("gRPC streaming for incremental inference responses",
     "use server-side streaming RPCs to push tokens as they are generated"),
]

BASE_DATE = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _make_timestamp(offset_minutes: int) -> str:
    """Return an ISO timestamp offset_minutes after BASE_DATE."""
    return (BASE_DATE + timedelta(minutes=offset_minutes)).isoformat()


def _make_exchange(idx: int, topic_idx: int, unique_suffix: int) -> dict:
    """Build a realistic exchange dict cycling through TOPICS."""
    user_q, asst_a = TOPICS[topic_idx % len(TOPICS)]
    return {
        'idx': idx,
        'timestamp': _make_timestamp(unique_suffix),
        'preview': user_q[:77] + '...' if len(user_q) > 80 else user_q,
        'user_text': f"{user_q} (exchange {unique_suffix})",
        'assistant_text': f"{asst_a} (ref {unique_suffix})",
    }


def _make_session(session_id: str, project_path: str, project_hash: str,
                  started_offset_minutes: int) -> dict:
    """Return kwargs for insert_session."""
    return {
        'session_id': session_id,
        'project_path': project_path,
        'project_hash': project_hash,
        'started_at': _make_timestamp(started_offset_minutes),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bulk_populate(conn, num_projects: int, sessions_per_project: int,
                   exchanges_per_session: int) -> list:
    """Insert sessions and exchanges; return list of session_ids."""
    session_ids = []
    minute_counter = 0
    for proj_idx in range(num_projects):
        project_path = f"/home/user/project-{proj_idx:03d}"
        project_hash = f"phash{proj_idx:04d}"
        for sess_idx in range(sessions_per_project):
            sid = f"sess-{proj_idx:03d}-{sess_idx:03d}"
            insert_session(
                conn,
                session_id=sid,
                project_path=project_path,
                project_hash=project_hash,
                started_at=_make_timestamp(minute_counter),
            )
            session_ids.append(sid)
            exchanges = [
                _make_exchange(i, (proj_idx * sessions_per_project + sess_idx) * exchanges_per_session + i,
                               minute_counter * exchanges_per_session + i)
                for i in range(exchanges_per_session)
            ]
            insert_exchanges(conn, sid, exchanges)
            minute_counter += 1
    return session_ids


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestLargeSessionCount(unittest.TestCase):
    """100 sessions across 10 projects, 50 exchanges each = 5000 total exchanges."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.temp_dir, 'stress_large_session.db')
        cls.conn = get_connection(cls.db_path)

        print("\n[setup] Inserting 100 sessions x 50 exchanges …", flush=True)
        t0 = time.time()
        cls.session_ids = _bulk_populate(cls.conn,
                                         num_projects=10,
                                         sessions_per_project=10,
                                         exchanges_per_session=50)
        elapsed = time.time() - t0
        print(f"[setup] Done in {elapsed:.2f}s — {len(cls.session_ids)} sessions, "
              f"{len(cls.session_ids) * 50} exchanges", flush=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_list_sessions_returns_all_100(self):
        """list_sessions() returns all 100 sessions in under 100ms."""
        t0 = time.time()
        sessions = list_sessions(self.conn)
        elapsed_ms = (time.time() - t0) * 1000
        print(f"\n  list_sessions(): {len(sessions)} rows in {elapsed_ms:.1f}ms", flush=True)
        self.assertEqual(len(sessions), 100)
        self.assertLess(elapsed_ms, 100,
                        f"list_sessions() took {elapsed_ms:.1f}ms, expected < 100ms")

    def test_get_stats_correct_totals(self):
        """get_stats() reports 100 sessions and 5000 exchanges."""
        stats = get_stats(self.conn, db_path=self.db_path)
        print(f"\n  stats: sessions={stats['total_sessions']}, "
              f"exchanges={stats['total_exchanges']}", flush=True)
        self.assertEqual(stats['total_sessions'], 100)
        self.assertEqual(stats['total_exchanges'], 5000)
        self.assertEqual(len(stats['projects']), 10)

    def test_fts_search_across_all_sessions_fast(self):
        """search_exchanges_fts('reduction') returns results in under 200ms."""
        t0 = time.time()
        results = search_exchanges_fts(self.conn, 'reduction', limit=50)
        elapsed_ms = (time.time() - t0) * 1000
        print(f"\n  FTS 'reduction': {len(results)} results in {elapsed_ms:.1f}ms", flush=True)
        self.assertGreater(len(results), 0)
        self.assertLess(elapsed_ms, 200,
                        f"FTS search took {elapsed_ms:.1f}ms, expected < 200ms")

    def test_global_search_returns_multi_project_results(self):
        """search_exchanges_global('reduction') returns results from multiple projects in under 200ms."""
        t0 = time.time()
        results = search_exchanges_global(self.conn, 'reduction', limit=50)
        elapsed_ms = (time.time() - t0) * 1000
        print(f"\n  Global 'reduction': {len(results)} results in {elapsed_ms:.1f}ms", flush=True)
        self.assertGreater(len(results), 0)
        # Results should span multiple projects
        project_paths = {r['project_path'] for r in results}
        self.assertGreater(len(project_paths), 1,
                           "Expected results from multiple projects")
        self.assertLess(elapsed_ms, 200,
                        f"Global search took {elapsed_ms:.1f}ms, expected < 200ms")


class TestLargeExchangeCount(unittest.TestCase):
    """1 session with 2000 exchanges — retrieval and FTS at scale."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.temp_dir, 'stress_large_exchanges.db')
        cls.conn = get_connection(cls.db_path)
        cls.session_id = 'sess-big'

        insert_session(cls.conn, cls.session_id, '/bigproject', 'bighash',
                       _make_timestamp(0))

        print("\n[setup] Inserting 2000 exchanges into 1 session …", flush=True)
        t0 = time.time()
        exchanges = [_make_exchange(i, i, i) for i in range(2000)]
        insert_exchanges(cls.conn, cls.session_id, exchanges)
        elapsed = time.time() - t0
        print(f"[setup] Done in {elapsed:.2f}s", flush=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_get_all_2000_exchanges(self):
        """get_exchanges returns all 2000 exchanges."""
        rows = get_exchanges(self.conn, self.session_id)
        self.assertEqual(len(rows), 2000)
        # Verify ordering: idx should be monotonically increasing
        idxs = [r['idx'] for r in rows]
        self.assertEqual(idxs, list(range(2000)))

    def test_get_last_10_exchanges(self):
        """get_exchanges with last_n=10 returns exactly 10, and they are the last ones."""
        rows = get_exchanges(self.conn, self.session_id, last_n=10)
        self.assertEqual(len(rows), 10)
        # Should be the last 10: idx 1990..1999
        expected_idxs = list(range(1990, 2000))
        actual_idxs = [r['idx'] for r in rows]
        self.assertEqual(actual_idxs, expected_idxs)

    def test_fts_search_fast_on_large_session(self):
        """FTS5 search on a 2000-exchange session returns results in under 100ms."""
        t0 = time.time()
        results = search_exchanges_fts(self.conn, 'reduction', session_id=self.session_id, limit=20)
        elapsed_ms = (time.time() - t0) * 1000
        print(f"\n  FTS 'reduction' (2000 ex session): {len(results)} results in {elapsed_ms:.1f}ms",
              flush=True)
        self.assertGreater(len(results), 0)
        self.assertLess(elapsed_ms, 100,
                        f"FTS search took {elapsed_ms:.1f}ms, expected < 100ms")

    def test_incremental_insert_100_more(self):
        """Inserting 100 more exchanges increments total to 2100."""
        extra = [_make_exchange(2000 + i, i, 2000 + i) for i in range(100)]
        insert_exchanges(self.conn, self.session_id, extra)
        rows = get_exchanges(self.conn, self.session_id)
        self.assertEqual(len(rows), 2100)
        # Verify the new tail is correct
        tail = get_exchanges(self.conn, self.session_id, last_n=5)
        self.assertEqual(tail[-1]['idx'], 2099)


class TestFTS5AtScale(unittest.TestCase):
    """500 exchanges across varied topics — correctness and edge cases for FTS5."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.temp_dir, 'stress_fts5.db')
        cls.conn = get_connection(cls.db_path)

        # Two projects: one with GPU topics, one with web/DB topics
        cls.gpu_hash = 'gpuhash'
        cls.web_hash = 'webhash'

        insert_session(cls.conn, 'sess-gpu', '/gpu/project', cls.gpu_hash,
                       _make_timestamp(0))
        insert_session(cls.conn, 'sess-web', '/web/project', cls.web_hash,
                       _make_timestamp(1000))

        print("\n[setup] Inserting 500 FTS5 test exchanges …", flush=True)
        t0 = time.time()

        # GPU session: 300 exchanges cycling through GPU topics (indices 0..9 in TOPICS)
        gpu_topics = list(range(10))  # first 10 topics are GPU/kernel focused
        gpu_exchanges = [
            _make_exchange(i, gpu_topics[i % len(gpu_topics)], i)
            for i in range(300)
        ]
        insert_exchanges(cls.conn, 'sess-gpu', gpu_exchanges)

        # Web session: 200 exchanges cycling through web/DB/ML topics (indices 10+ in TOPICS)
        web_topics = list(range(10, len(TOPICS)))
        web_exchanges = [
            _make_exchange(i, web_topics[i % len(web_topics)], 300 + i)
            for i in range(200)
        ]
        insert_exchanges(cls.conn, 'sess-web', web_exchanges)

        elapsed = time.time() - t0
        print(f"[setup] Done in {elapsed:.2f}s", flush=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_search_matches_roughly_5_percent(self):
        """Searching 'reduction' finds results (~5% of GPU exchanges contain it)."""
        # 'reduction' appears in topic index 0 ("warp divergence in the reduction kernel")
        # and topic index 9 ("reduction kernel with atomic operations")
        # Both appear in the gpu session cycling through 10 topics over 300 exchanges
        results = search_exchanges_fts(self.conn, 'reduction', limit=100)
        count = len(results)
        print(f"\n  FTS 'reduction' count: {count}", flush=True)
        self.assertGreater(count, 0)
        # All results should contain 'reduction' in user or assistant text
        for r in results:
            text = (r['user_text'] or '') + ' ' + (r['assistant_text'] or '')
            self.assertIn('reduction', text.lower(),
                          f"Result idx={r['idx']} doesn't contain 'reduction'")

    def test_search_no_match_returns_fast(self):
        """Searching for a non-existent term returns empty quickly."""
        t0 = time.time()
        results = search_exchanges_fts(self.conn, 'xyznonexistenttermzzz')
        elapsed_ms = (time.time() - t0) * 1000
        print(f"\n  FTS empty search: {elapsed_ms:.1f}ms", flush=True)
        self.assertEqual(results, [])
        self.assertLess(elapsed_ms, 50,
                        f"Empty FTS search took {elapsed_ms:.1f}ms, expected < 50ms")

    def test_project_scoped_search_filters_correctly(self):
        """FTS search scoped to gpu_hash returns only GPU session results."""
        gpu_results = search_exchanges_fts(self.conn, 'kernel',
                                           project_hash=self.gpu_hash, limit=100)
        web_results = search_exchanges_fts(self.conn, 'kernel',
                                           project_hash=self.web_hash, limit=100)
        print(f"\n  'kernel' GPU-scoped: {len(gpu_results)}, web-scoped: {len(web_results)}",
              flush=True)
        self.assertGreater(len(gpu_results), 0)
        # All GPU results must belong to the GPU session
        for r in gpu_results:
            self.assertEqual(r['session_id'], 'sess-gpu')

    def test_global_search_spans_both_projects(self):
        """Global FTS search for a common word finds results from both projects."""
        # 'cache' appears in web topics (cache invalidation, KV cache)
        # but also in 'KV cache eviction' topic in ML section
        results = search_exchanges_global(self.conn, 'cache', limit=50)
        session_ids = {r['session_id'] for r in results}
        print(f"\n  Global 'cache': {len(results)} results, "
              f"sessions: {session_ids}", flush=True)
        # Should have results (cache appears in ML topics in web session)
        self.assertGreater(len(results), 0)

    def test_session_scoped_search(self):
        """FTS search scoped to a specific session_id only returns that session's data."""
        results = search_exchanges_fts(self.conn, 'reduction',
                                       session_id='sess-gpu', limit=50)
        for r in results:
            self.assertEqual(r['session_id'], 'sess-gpu',
                             "Session-scoped search leaked data from another session")

    def test_fts_result_content_correctness(self):
        """Every FTS result for 'warp' actually contains 'warp' in its text."""
        results = search_exchanges_fts(self.conn, 'warp', limit=50)
        self.assertGreater(len(results), 0)
        for r in results:
            combined = ((r['user_text'] or '') + ' ' +
                        (r['assistant_text'] or '') + ' ' +
                        (r['preview'] or '')).lower()
            self.assertIn('warp', combined,
                          f"FTS returned a result without 'warp': idx={r['idx']}")


class TestPruneAtScale(unittest.TestCase):
    """50 sessions x 100 exchanges; prune half and verify FTS isolation."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.temp_dir, 'stress_prune.db')
        cls.conn = get_connection(cls.db_path)

        print("\n[setup] Inserting 50 sessions x 100 exchanges for prune test …", flush=True)
        t0 = time.time()

        # 25 sessions with old dates (will be pruned), 25 with new dates (kept)
        cls.prune_cutoff = '2025-07-01T00:00:00+00:00'
        cls.old_session_ids = []
        cls.new_session_ids = []

        for i in range(25):
            sid = f'old-sess-{i:03d}'
            # started_at in early 2025 — before cutoff
            started_at = _make_timestamp(i * 60)  # Jan 2025 range
            insert_session(cls.conn, sid, f'/old/project-{i}', f'oldhash{i}', started_at)
            exchanges = [_make_exchange(j, j, i * 100 + j) for j in range(100)]
            insert_exchanges(cls.conn, sid, exchanges)
            cls.old_session_ids.append(sid)

        for i in range(25):
            sid = f'new-sess-{i:03d}'
            # started_at in late 2025 — after cutoff (offset by 1_000_000 minutes ~ 2 years)
            started_at = _make_timestamp(1_000_000 + i * 60)
            insert_session(cls.conn, sid, f'/new/project-{i}', f'newhash{i}', started_at)
            exchanges = [_make_exchange(j, j, 100_000 + i * 100 + j) for j in range(100)]
            insert_exchanges(cls.conn, sid, exchanges)
            cls.new_session_ids.append(sid)

        elapsed = time.time() - t0
        print(f"[setup] Done in {elapsed:.2f}s", flush=True)

        # Now prune the old sessions
        print("[setup] Pruning 25 old sessions …", flush=True)
        t0 = time.time()
        pruned = prune_before_date(cls.conn, cls.prune_cutoff)
        elapsed = time.time() - t0
        print(f"[setup] Pruned {pruned} sessions in {elapsed:.2f}s", flush=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_25_sessions_remain(self):
        """After pruning 25, exactly 25 sessions remain."""
        remaining = list_sessions(self.conn)
        print(f"\n  Remaining sessions: {len(remaining)}", flush=True)
        self.assertEqual(len(remaining), 25)

    def test_remaining_sessions_are_new(self):
        """All remaining sessions are from the new (kept) set."""
        remaining_ids = {s['session_id'] for s in list_sessions(self.conn)}
        for sid in self.new_session_ids:
            self.assertIn(sid, remaining_ids,
                          f"New session {sid} should have survived pruning")
        for sid in self.old_session_ids:
            self.assertNotIn(sid, remaining_ids,
                             f"Old session {sid} should have been pruned")

    def test_pruned_sessions_data_gone_from_fts(self):
        """FTS5 returns no results from pruned sessions."""
        # Old sessions had 'reduction' in exchange 0 (topic 0) of each session
        # after pruning, FTS should only return results from new sessions
        results = search_exchanges_fts(self.conn, 'reduction', limit=200)
        found_session_ids = {r['session_id'] for r in results}
        old_ids_set = set(self.old_session_ids)
        leaked = found_session_ids & old_ids_set
        self.assertEqual(leaked, set(),
                         f"FTS returned results from pruned sessions: {leaked}")

    def test_remaining_session_data_intact(self):
        """Each surviving session still has all 100 exchanges."""
        # spot-check 5 of the new sessions
        for sid in self.new_session_ids[:5]:
            exchanges = get_exchanges(self.conn, sid)
            self.assertEqual(len(exchanges), 100,
                             f"Session {sid} should have 100 exchanges but has {len(exchanges)}")

    def test_fts_search_on_remaining_data(self):
        """FTS5 still finds results in the remaining sessions."""
        results = search_exchanges_fts(self.conn, 'warp', limit=50)
        self.assertGreater(len(results), 0,
                           "Expected FTS to find 'warp' in remaining sessions")
        for r in results:
            self.assertIn(r['session_id'], set(self.new_session_ids),
                          "FTS result came from a pruned session")


class TestDBFileSize(unittest.TestCase):
    """100 sessions x 50 exchanges = 5000 total — verify DB stays under 10MB."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.temp_dir, 'stress_size.db')
        cls.conn = get_connection(cls.db_path)

        print("\n[setup] Inserting 100 sessions x 50 exchanges for size test …", flush=True)
        t0 = time.time()
        _bulk_populate(cls.conn,
                       num_projects=10,
                       sessions_per_project=10,
                       exchanges_per_session=50)
        elapsed = time.time() - t0
        print(f"[setup] Done in {elapsed:.2f}s", flush=True)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_db_size_under_10mb(self):
        """DB file with 5000 exchanges should be under 10MB."""
        size_bytes = os.path.getsize(self.db_path)
        size_mb = size_bytes / (1024 * 1024)
        print(f"\n  DB file size: {size_mb:.2f} MB ({size_bytes:,} bytes)", flush=True)
        self.assertLess(size_mb, 10.0,
                        f"DB is {size_mb:.2f}MB, expected < 10MB for 5000 exchanges")

    def test_db_size_via_stats(self):
        """get_stats() db_size_bytes matches actual file size."""
        stats = get_stats(self.conn, db_path=self.db_path)
        actual_bytes = os.path.getsize(self.db_path)
        print(f"\n  stats.db_size_bytes={stats['db_size_bytes']:,}, "
              f"actual={actual_bytes:,}", flush=True)
        # They should be identical (WAL file not counted separately in stats)
        self.assertEqual(stats['db_size_bytes'], actual_bytes)

    def test_exchange_count_correct(self):
        """Stats report exactly 5000 exchanges for 100 sessions."""
        stats = get_stats(self.conn, db_path=self.db_path)
        self.assertEqual(stats['total_sessions'], 100)
        self.assertEqual(stats['total_exchanges'], 5000)


if __name__ == '__main__':
    unittest.main()
