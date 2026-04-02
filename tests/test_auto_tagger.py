#!/usr/bin/env python3
"""Unit tests for auto_tagger.py — hybrid tagging system."""

import os
import shutil
import sys
import tempfile
import unittest

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from db import get_connection, insert_session, insert_exchanges, get_exchanges
from auto_tagger import (
    tokenize,
    is_technical_term,
    extract_terms,
    select_tags,
    get_session_texts,
    compute_auto_tags,
    MAX_AUTO_TAGS_PER_SESSION,
    AUTO_TAG_MIN_FREQUENCY,
)


# ---------------------------------------------------------------------------
# TestTokenize
# ---------------------------------------------------------------------------

class TestTokenize(unittest.TestCase):
    """Tests for tokenize()."""

    def test_basic_tokenization(self):
        """Splits on whitespace and punctuation, lowercases, removes punctuation."""
        tokens = tokenize("Hello, World! This is a TEST.")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)
        self.assertIn("this", tokens)
        self.assertIn("test", tokens)
        # commas, exclamation marks, periods should not appear as tokens
        for tok in tokens:
            self.assertNotIn(",", tok)
            self.assertNotIn("!", tok)
            self.assertNotIn(".", tok)

    def test_filters_short_tokens(self):
        """Tokens shorter than 3 characters are removed."""
        tokens = tokenize("an it to do of is fp32 the GPU")
        for tok in tokens:
            self.assertGreaterEqual(len(tok), 3)
        # fp32 has 4 chars — should survive
        self.assertIn("fp32", tokens)
        # "gpu" has 3 chars — should survive
        self.assertIn("gpu", tokens)

    def test_preserves_hyphens_and_underscores(self):
        """Compound terms with hyphens and underscores are kept as single tokens."""
        tokens = tokenize("warp-divergence shared_memory bank_conflict fp16 sm_90")
        self.assertIn("warp-divergence", tokens)
        self.assertIn("shared_memory", tokens)
        self.assertIn("bank_conflict", tokens)
        self.assertIn("fp16", tokens)
        self.assertIn("sm_90", tokens)


# ---------------------------------------------------------------------------
# TestIsTechnicalTerm
# ---------------------------------------------------------------------------

class TestIsTechnicalTerm(unittest.TestCase):
    """Tests for is_technical_term()."""

    def test_special_chars_always_technical(self):
        """Terms containing -, _, or digits are always technical."""
        self.assertTrue(is_technical_term("fp16"))
        self.assertTrue(is_technical_term("sm_90"))
        self.assertTrue(is_technical_term("warp-divergence"))
        self.assertTrue(is_technical_term("shared_memory"))
        self.assertTrue(is_technical_term("llm_inference"))
        self.assertTrue(is_technical_term("cuda12"))

    def test_generic_programming_terms_return_false(self):
        """Generic programming terms are not considered technical."""
        self.assertFalse(is_technical_term("function"))
        self.assertFalse(is_technical_term("method"))
        self.assertFalse(is_technical_term("variable"))
        self.assertFalse(is_technical_term("error"))
        self.assertFalse(is_technical_term("string"))
        self.assertFalse(is_technical_term("class"))

    def test_stopwords_return_false(self):
        """Common stopwords are not considered technical."""
        self.assertFalse(is_technical_term("the"))
        self.assertFalse(is_technical_term("and"))
        self.assertFalse(is_technical_term("with"))
        self.assertFalse(is_technical_term("actually"))
        self.assertFalse(is_technical_term("probably"))
        self.assertFalse(is_technical_term("yeah"))


# ---------------------------------------------------------------------------
# TestExtractTerms
# ---------------------------------------------------------------------------

class TestExtractTerms(unittest.TestCase):
    """Tests for extract_terms()."""

    def test_counts_term_frequency(self):
        """Counts term frequency across multiple texts."""
        texts = [
            "the triton kernel uses shared memory for tiling",
            "shared memory access in the triton kernel must be coalesced",
            "the triton kernel tile size affects shared memory usage",
        ]
        counts = extract_terms(texts)
        # "triton", "kernel", "shared", "memory" appear across all texts
        self.assertIn("triton", counts)
        self.assertIn("kernel", counts)
        self.assertGreaterEqual(counts["triton"], 3)
        self.assertGreaterEqual(counts["kernel"], 3)
        # stopwords should NOT appear
        self.assertNotIn("the", counts)
        self.assertNotIn("for", counts)

    def test_empty_texts_returns_empty_dict(self):
        """Empty list and empty strings both return empty dict."""
        self.assertEqual(extract_terms([]), {})
        self.assertEqual(extract_terms([""]), {})
        self.assertEqual(extract_terms(["", ""]), {})


# ---------------------------------------------------------------------------
# TestSelectTags
# ---------------------------------------------------------------------------

class TestSelectTags(unittest.TestCase):
    """Tests for select_tags()."""

    def test_selects_frequent_technical_terms(self):
        """Selects terms that are frequent and technical, filters stopwords."""
        counts = {
            "triton": 8,
            "kernel": 7,
            "warp-divergence": 5,
            "shared_memory": 4,
            "the": 20,        # stopword — should be excluded
            "function": 15,   # generic — should be excluded
            "and": 10,        # stopword — should be excluded
        }
        tags = select_tags(counts, max_tags=10, min_frequency=3)
        self.assertIn("triton", tags)
        self.assertIn("kernel", tags)
        self.assertIn("warp-divergence", tags)
        self.assertIn("shared_memory", tags)
        self.assertNotIn("the", tags)
        self.assertNotIn("function", tags)
        self.assertNotIn("and", tags)

    def test_respects_max_tags_limit(self):
        """Never returns more tags than max_tags."""
        counts = {f"term{i}gpu": i + 5 for i in range(20)}
        tags = select_tags(counts, max_tags=5, min_frequency=1)
        self.assertLessEqual(len(tags), 5)

    def test_respects_min_frequency_threshold(self):
        """Terms below min_frequency are excluded."""
        counts = {
            "cuda": 5,
            "metal": 2,   # below threshold
            "triton": 4,
        }
        tags = select_tags(counts, max_tags=10, min_frequency=3)
        self.assertIn("cuda", tags)
        self.assertIn("triton", tags)
        self.assertNotIn("metal", tags)

    def test_empty_counts_returns_empty_list(self):
        """Empty input returns empty list."""
        self.assertEqual(select_tags({}, max_tags=10, min_frequency=1), [])


# ---------------------------------------------------------------------------
# TestComputeAutoTags (integration)
# ---------------------------------------------------------------------------

class TestComputeAutoTags(unittest.TestCase):
    """Integration test for compute_auto_tags() using a live DB."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_tags.db")
        self.conn = get_connection(self.db_path)
        self.session_id = "test-session-autotag-001"
        insert_session(
            self.conn,
            self.session_id,
            "/tmp/test-project",
            "abc123",
            "2026-04-01T00:00:00Z",
        )

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_pipeline_kernel_optimization(self):
        """Full pipeline on kernel optimization exchanges surfaces relevant terms."""
        exchanges_data = [
            {
                "idx": 0,
                "timestamp": "2026-04-01T00:00:00Z",
                "preview": "warp-divergence in CUDA kernel",
                "user_text": (
                    "I'm seeing warp-divergence in my CUDA kernel. "
                    "The warp-divergence happens inside the inner loop "
                    "when threads take different branches."
                ),
                "assistant_text": (
                    "Warp-divergence occurs when threads in a warp follow "
                    "different execution paths. To reduce warp-divergence, "
                    "restructure the kernel to minimize branching."
                ),
            },
            {
                "idx": 1,
                "timestamp": "2026-04-01T00:01:00Z",
                "preview": "shared_memory optimization",
                "user_text": (
                    "How should I use shared_memory to improve the kernel throughput? "
                    "I have a tiling strategy but shared_memory bank conflicts "
                    "are hurting throughput."
                ),
                "assistant_text": (
                    "For shared_memory optimization, pad your shared_memory arrays "
                    "by one element to avoid bank conflicts. "
                    "This helps when accessing shared_memory in a strided pattern."
                ),
            },
            {
                "idx": 2,
                "timestamp": "2026-04-01T00:02:00Z",
                "preview": "fp16 tensor cores",
                "user_text": (
                    "Can I use fp16 with tensor cores? "
                    "The fp16 accumulation might lose precision but "
                    "fp16 throughput is much higher with tensor cores."
                ),
                "assistant_text": (
                    "Yes, fp16 is natively supported by tensor cores. "
                    "Use fp16 for the matrix operands and fp32 for accumulation "
                    "to balance fp16 throughput with numerical stability."
                ),
            },
        ]

        insert_exchanges(self.conn, self.session_id, exchanges_data)
        exchanges = get_exchanges(self.conn, self.session_id)

        tags = compute_auto_tags(exchanges, max_tags=MAX_AUTO_TAGS_PER_SESSION,
                                 min_frequency=AUTO_TAG_MIN_FREQUENCY)

        # These domain-specific terms appear frequently enough to be tagged
        self.assertIn("warp-divergence", tags)
        self.assertIn("shared_memory", tags)
        self.assertIn("fp16", tags)
        # Result must be capped
        self.assertLessEqual(len(tags), MAX_AUTO_TAGS_PER_SESSION)


if __name__ == "__main__":
    unittest.main()
