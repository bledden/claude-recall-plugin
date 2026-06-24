#!/usr/bin/env python3
"""Tests for highlight.py — explicit + auto-detection highlight creation.

The auto-detection heuristic must be conservative. The SOLUTION_SIGNAL_THRESHOLD
of 2 means a single signal phrase (however clear) does NOT create a highlight.
Two or more distinct signals in the same response are required.
"""

import os
import shutil
import sys
import tempfile
import unittest

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from db import (
    get_connection,
    insert_session,
    insert_exchanges,
    get_exchanges,
    get_highlights,
    get_tags,
    insert_tag,
    set_session_config,
)
from highlight import (
    create_highlight,
    detect_solution_signals,
    auto_detect_highlights,
    build_arg_parser,
    parse_args,
    SOLUTION_SIGNALS,
    SOLUTION_SIGNAL_THRESHOLD,
    HIGHLIGHT_SUMMARY_MAX_CHARS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(conn, session_id='test-session-hl-001', project_path='/tmp/proj',
                  project_hash='abc123', started_at='2026-04-01T00:00:00Z'):
    insert_session(conn, session_id, project_path, project_hash, started_at)
    return session_id


def _make_exchange(idx, assistant_text, user_text='what is the solution?',
                   preview=None):
    if preview is None:
        preview = user_text[:60]
    return {
        'idx': idx,
        'timestamp': f'2026-04-01T00:0{idx}:00Z',
        'preview': preview,
        'user_text': user_text,
        'assistant_text': assistant_text,
    }


# ---------------------------------------------------------------------------
# TestDetectSolutionSignals
# ---------------------------------------------------------------------------

class TestDetectSolutionSignals(unittest.TestCase):
    """Tests for detect_solution_signals()."""

    def test_returns_zero_for_no_signals(self):
        """Generic conversation text with no signal phrases returns 0."""
        text = "Sure, I can help with that. Let me look at the code for you."
        self.assertEqual(detect_solution_signals(text), 0)

    def test_returns_one_for_single_signal(self):
        """Text containing exactly one signal phrase returns 1."""
        text = "Looking at the traceback, the issue was that you forgot to initialize the variable."
        self.assertEqual(detect_solution_signals(text), 1)

    def test_returns_correct_count_for_multiple_signals(self):
        """Text containing two or more distinct signals returns the correct count."""
        text = (
            "The issue was that the dtype was wrong. "
            "The fix is to cast to float32 before the matmul. "
            "The solution also requires updating the initializer."
        )
        count = detect_solution_signals(text)
        self.assertGreaterEqual(count, 2)

    def test_case_insensitive(self):
        """Signal detection is case-insensitive."""
        text = "The Fix Is to use a smaller batch size. The Solution is clear."
        count = detect_solution_signals(text)
        self.assertGreaterEqual(count, 2)

    def test_does_not_double_count_overlapping_signals(self):
        """Each distinct signal phrase is counted at most once, not per occurrence."""
        # Repeat the same signal many times — should still count as 1 distinct signal
        text = "the fix is simple. the fix is easy. the fix is done."
        count = detect_solution_signals(text)
        self.assertEqual(count, 1)


# ---------------------------------------------------------------------------
# TestCreateHighlight
# ---------------------------------------------------------------------------

class TestCreateHighlight(unittest.TestCase):
    """Tests for create_highlight()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_hl.db')
        self.conn = get_connection(self.db_path)
        self.session_id = _make_session(self.conn)
        insert_exchanges(self.conn, self.session_id, [
            _make_exchange(0, 'The fix is to add padding.'),
            _make_exchange(1, 'The solution is to use fp16.'),
        ])
        insert_tag(self.conn, 'triton', self.session_id, source='auto')
        insert_tag(self.conn, 'cuda', self.session_id, source='auto')

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_creates_highlight_with_explicit_source_and_session_tags(self):
        """create_highlight inserts a highlight row with source='explicit' and session tags."""
        create_highlight(self.conn, self.session_id, 'Padding trick for shared memory')
        highlights = get_highlights(self.conn, self.session_id)
        self.assertEqual(len(highlights), 1)
        hl = highlights[0]
        self.assertEqual(hl['summary'], 'Padding trick for shared memory')
        self.assertEqual(hl['source'], 'explicit')
        # Tags should include the session tags we inserted
        self.assertIn('cuda', hl['tags'])
        self.assertIn('triton', hl['tags'])

    def test_uses_latest_exchange_idx_when_not_provided(self):
        """When exchange_idx is omitted, the highlight uses the session's latest idx."""
        create_highlight(self.conn, self.session_id, 'Auto idx test')
        highlights = get_highlights(self.conn, self.session_id)
        self.assertEqual(len(highlights), 1)
        # Latest exchange idx is 1
        self.assertEqual(highlights[0]['exchange_idx'], 1)

    def test_returns_confirmation_message(self):
        """create_highlight returns a formatted confirmation string."""
        result = create_highlight(self.conn, self.session_id, 'Fixed the dtype bug',
                                  exchange_idx=0)
        self.assertIn('Fixed the dtype bug', result)
        # Message should be enclosed in some kind of quoting
        self.assertIn('"Fixed the dtype bug"', result)
        # Tags should appear in the message
        self.assertIn('cuda', result)
        self.assertIn('triton', result)


# ---------------------------------------------------------------------------
# TestAutoDetectHighlights
# ---------------------------------------------------------------------------

class TestAutoDetectHighlights(unittest.TestCase):
    """Tests for auto_detect_highlights()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_auto_hl.db')
        self.conn = get_connection(self.db_path)
        self.session_id = _make_session(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_skips_when_auto_highlight_not_set(self):
        """Returns empty list when auto_highlight config key is not present."""
        exchanges = [_make_exchange(0, 'The fix is X. The solution is Y. The answer is Z.')]
        result = auto_detect_highlights(self.conn, self.session_id, exchanges)
        self.assertEqual(result, [])
        highlights = get_highlights(self.conn, self.session_id)
        self.assertEqual(len(highlights), 0)

    def test_skips_when_auto_highlight_is_false(self):
        """Returns empty list when auto_highlight config is explicitly set to False."""
        set_session_config(self.conn, self.session_id, 'auto_highlight', False)
        exchanges = [_make_exchange(0, 'The fix is X. The solution is Y. The answer is Z.')]
        result = auto_detect_highlights(self.conn, self.session_id, exchanges)
        self.assertEqual(result, [])
        highlights = get_highlights(self.conn, self.session_id)
        self.assertEqual(len(highlights), 0)

    def test_detects_exchange_with_two_or_more_signals(self):
        """Creates a highlight when an exchange has >= 2 signal phrases."""
        set_session_config(self.conn, self.session_id, 'auto_highlight', True)
        solution_text = (
            "After reviewing the stack trace, the issue was that the tensor shape "
            "was mismatched. The fix is to transpose the weight matrix before the "
            "matmul so the dimensions align correctly."
        )
        exchanges = [_make_exchange(0, solution_text)]
        result = auto_detect_highlights(self.conn, self.session_id, exchanges)
        self.assertEqual(len(result), 1)
        highlights = get_highlights(self.conn, self.session_id)
        self.assertEqual(len(highlights), 1)
        self.assertEqual(highlights[0]['source'], 'auto')

    def test_skips_exchange_with_only_one_signal(self):
        """Does NOT create a highlight when signal count is below threshold (< 2)."""
        set_session_config(self.conn, self.session_id, 'auto_highlight', True)
        single_signal_text = (
            "The issue was that you forgot to set requires_grad=True on the "
            "leaf tensor before computing gradients."
        )
        exchanges = [_make_exchange(0, single_signal_text)]
        result = auto_detect_highlights(self.conn, self.session_id, exchanges)
        self.assertEqual(result, [])
        highlights = get_highlights(self.conn, self.session_id)
        self.assertEqual(len(highlights), 0)

    def test_summary_truncated_to_100_chars_with_ellipsis(self):
        """Long assistant text is truncated to HIGHLIGHT_SUMMARY_MAX_CHARS and appended '...'."""
        set_session_config(self.conn, self.session_id, 'auto_highlight', True)
        # Craft a response long enough to trigger truncation, with 2+ signals
        long_text = (
            "The issue was that the kernel launch configuration was wrong. "
            "The fix is to compute the grid dimensions based on the total element count "
            "divided by the block size, rounding up. This ensures every element is "
            "processed even when the array length is not a multiple of the block size. "
            "You should also make sure to handle the boundary condition inside the kernel "
            "itself using an early-return guard so out-of-bounds threads do nothing."
        )
        self.assertGreater(len(long_text), HIGHLIGHT_SUMMARY_MAX_CHARS)
        exchanges = [_make_exchange(0, long_text)]
        result = auto_detect_highlights(self.conn, self.session_id, exchanges)
        self.assertEqual(len(result), 1)
        summary = result[0]
        self.assertLessEqual(len(summary), HIGHLIGHT_SUMMARY_MAX_CHARS + 3)  # +3 for "..."
        self.assertTrue(summary.endswith('...'))


# ---------------------------------------------------------------------------
# TestAutoDetectConservative
# ---------------------------------------------------------------------------

class TestAutoDetectConservative(unittest.TestCase):
    """Conservative edge-case tests for the auto-detection heuristic.

    The user is concerned about false positives. These tests verify that
    short/generic text that happens to contain signal words does NOT trigger
    highlight creation, while genuinely solution-dense text does.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test_conservative.db')
        self.conn = get_connection(self.db_path)
        self.session_id = _make_session(self.conn)
        set_session_config(self.conn, self.session_id, 'auto_highlight', True)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_short_generic_text_with_signals_does_not_trigger(self):
        """A short response with signal words should NOT trigger due to min word count guard.

        'try using this, the fix is simple' contains both 'try using' and 'the fix is'
        but is far too short to represent a real captured insight.
        """
        short_generic = "Sure, try using this approach. The fix is simple."
        exchanges = [_make_exchange(0, short_generic)]
        result = auto_detect_highlights(self.conn, self.session_id, exchanges)
        self.assertEqual(len(result), 0, "Short text should be rejected by min word count guard")

    def test_single_signal_normal_conversation_does_not_trigger(self):
        """Normal conversational response with one signal doesn't create a highlight."""
        normal_conv = (
            "The issue was that you had a typo in the variable name. "
            "I can see 'initalize' should be 'initialize'. "
            "Python is case-sensitive, so it raised a NameError at runtime. "
            "Double-check your spelling when defining or referencing variables."
        )
        exchanges = [_make_exchange(0, normal_conv)]
        result = auto_detect_highlights(self.conn, self.session_id, exchanges)
        # Only 1 signal ("the issue was") — must NOT trigger
        self.assertEqual(result, [],
                         "Single-signal normal conversation must not create a highlight.")

    def test_genuinely_solution_dense_text_triggers(self):
        """A response that explains a real technical fix with multiple signals triggers."""
        solution_text = (
            "After profiling the kernel, the problem was the uncoalesced global memory "
            "access pattern — each warp was striding across rows instead of columns. "
            "The solution is to transpose the data layout so that threads in a warp "
            "access consecutive memory addresses. The fix is to change the indexing from "
            "`A[row * N + col]` to `A[col * M + row]` after transposing the matrix offline. "
            "This resolved the bandwidth bottleneck and brought throughput from 120 GB/s "
            "to 890 GB/s on an A100."
        )
        exchanges = [_make_exchange(0, solution_text)]
        result = auto_detect_highlights(self.conn, self.session_id, exchanges)
        self.assertGreater(len(result), 0,
                           "Genuinely solution-dense text must create a highlight.")


# ---------------------------------------------------------------------------
# Constant sanity checks
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# TestArgParsing (WI-16): argparse migration
# ---------------------------------------------------------------------------

class TestArgParsing(unittest.TestCase):
    """Tests for the argparse-based CLI argument handling (WI-16).

    These tests exercise ONLY argument parsing — they never open the real
    database. A footgun in the old positional-only sys.argv parser was that
    `highlight.py --help "x"` performed a REAL insert with session_id='--help',
    and a non-int exchange_idx raised an uncaught ValueError.
    """

    def test_parses_positional_session_id_and_summary(self):
        """session_id and summary are parsed as positionals; exchange defaults to None."""
        args = parse_args(['sess-123', 'My summary'])
        self.assertEqual(args.session_id, 'sess-123')
        self.assertEqual(args.summary, 'My summary')
        self.assertIsNone(args.exchange)

    def test_parses_optional_exchange_as_int(self):
        """--exchange is parsed as an int."""
        args = parse_args(['sess-123', 'My summary', '--exchange', '4'])
        self.assertEqual(args.exchange, 4)
        self.assertIsInstance(args.exchange, int)

    def test_help_flag_exits_without_insert(self):
        """-h/--help must exit cleanly (SystemExit code 0), never treated as a session_id.

        The old parser would call create_highlight with session_id='--help'
        and perform a real insert. argparse must intercept the flag and exit
        with code 0 before any DB work.
        """
        for flag in ('-h', '--help'):
            with self.assertRaises(SystemExit) as ctx:
                parse_args([flag, 'x'])
            self.assertEqual(ctx.exception.code, 0)

    def test_non_int_exchange_gives_clean_argparse_error(self):
        """A non-int --exchange triggers an argparse error (SystemExit code 2), not ValueError."""
        with self.assertRaises(SystemExit) as ctx:
            parse_args(['sess-123', 'summary', '--exchange', 'notanint'])
        self.assertEqual(ctx.exception.code, 2)

    def test_negative_exchange_is_rejected(self):
        """--exchange must be >= 0; a negative value is an argparse error (code 2)."""
        with self.assertRaises(SystemExit) as ctx:
            parse_args(['sess-123', 'summary', '--exchange', '-1'])
        self.assertEqual(ctx.exception.code, 2)

    def test_zero_exchange_is_accepted(self):
        """--exchange of 0 is valid (>= 0 boundary)."""
        args = parse_args(['sess-123', 'summary', '--exchange', '0'])
        self.assertEqual(args.exchange, 0)

    def test_missing_required_args_errors(self):
        """Missing required positionals is an argparse error (code 2), not a real insert."""
        with self.assertRaises(SystemExit) as ctx:
            parse_args(['only-session-id'])
        self.assertEqual(ctx.exception.code, 2)

    def test_build_arg_parser_returns_argument_parser(self):
        """build_arg_parser returns an argparse.ArgumentParser instance."""
        import argparse
        self.assertIsInstance(build_arg_parser(), argparse.ArgumentParser)


class TestConstants(unittest.TestCase):
    """Verify the module constants have the expected values from the spec."""

    def test_solution_signal_threshold_is_2(self):
        self.assertEqual(SOLUTION_SIGNAL_THRESHOLD, 2)

    def test_highlight_summary_max_chars_is_100(self):
        self.assertEqual(HIGHLIGHT_SUMMARY_MAX_CHARS, 100)

    def test_solution_signals_contains_expected_phrases(self):
        expected = {"the fix is", "the solution", "try using", "the issue was"}
        for phrase in expected:
            self.assertIn(phrase, SOLUTION_SIGNALS)


if __name__ == '__main__':
    unittest.main()
