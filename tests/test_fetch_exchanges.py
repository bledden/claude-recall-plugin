#!/usr/bin/env python3
"""Unit tests for fetch_exchanges.py"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add scripts directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from fetch_exchanges import (
    parse_range_arg,
    fetch_exchanges_from_transcript,
    format_exchanges,
    truncate_text,
    MAX_CHARS_PER_MESSAGE
)


class TestParseRangeArg(unittest.TestCase):
    """Tests for parse_range_arg function."""

    def test_single_number(self):
        """Test parsing a single exchange number."""
        result = parse_range_arg('5', 100)
        self.assertEqual(result, {5})

    def test_range(self):
        """Test parsing a range like '10-15'."""
        result = parse_range_arg('10-15', 100)
        self.assertEqual(result, {10, 11, 12, 13, 14, 15})

    def test_comma_separated(self):
        """Test parsing comma-separated numbers."""
        result = parse_range_arg('1,5,10', 100)
        self.assertEqual(result, {1, 5, 10})

    def test_last_n(self):
        """Test parsing 'lastN' format."""
        result = parse_range_arg('last5', 100)
        self.assertEqual(result, {96, 97, 98, 99, 100})

    def test_last_n_exceeds_total(self):
        """Test 'lastN' when N > total exchanges."""
        result = parse_range_arg('last10', 5)
        self.assertEqual(result, {1, 2, 3, 4, 5})

    def test_out_of_range_filtered(self):
        """Test that out-of-range indices are filtered."""
        result = parse_range_arg('95-105', 100)
        self.assertEqual(result, {95, 96, 97, 98, 99, 100})

    def test_invalid_string(self):
        """Test with invalid string."""
        result = parse_range_arg('invalid', 100)
        self.assertEqual(result, set())

    def test_mixed_comma_and_range(self):
        """Test comma-separated with ranges."""
        result = parse_range_arg('1,5-7,10', 100)
        self.assertEqual(result, {1, 5, 6, 7, 10})


class TestFetchExchangesFromTranscript(unittest.TestCase):
    """Tests for fetch_exchanges_from_transcript function."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_file = Path(self.temp_dir) / 'transcript.jsonl'

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fetch_single_exchange(self):
        """Test fetching a single exchange."""
        # Create transcript with exchanges
        lines = [
            json.dumps({'type': 'user', 'message': {'content': [{'type': 'text', 'text': 'Hello'}]}, 'timestamp': '2025-01-05T09:00:00Z'}),
            json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Hi there!'}]}, 'timestamp': '2025-01-05T09:00:05Z'}),
            json.dumps({'type': 'user', 'message': {'content': [{'type': 'text', 'text': 'Question 2'}]}, 'timestamp': '2025-01-05T09:01:00Z'}),
            json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Answer 2'}]}, 'timestamp': '2025-01-05T09:01:05Z'}),
        ]
        with open(self.transcript_file, 'w') as f:
            f.write('\n'.join(lines))

        result = fetch_exchanges_from_transcript(str(self.transcript_file), {1})

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['idx'], 1)
        self.assertEqual(result[0]['user'], 'Hello')
        self.assertEqual(result[0]['assistant'], 'Hi there!')

    def test_fetch_range(self):
        """Test fetching a range of exchanges."""
        # Create 5 exchanges
        lines = []
        for i in range(5):
            lines.append(json.dumps({
                'type': 'user',
                'message': {'content': [{'type': 'text', 'text': f'User {i+1}'}]},
                'timestamp': f'2025-01-05T09:{i:02d}:00Z'
            }))
            lines.append(json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': f'Assistant {i+1}'}]},
                'timestamp': f'2025-01-05T09:{i:02d}:05Z'
            }))

        with open(self.transcript_file, 'w') as f:
            f.write('\n'.join(lines))

        result = fetch_exchanges_from_transcript(str(self.transcript_file), {2, 3, 4})

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]['idx'], 2)
        self.assertEqual(result[1]['idx'], 3)
        self.assertEqual(result[2]['idx'], 4)

    def test_nonexistent_file(self):
        """Test with non-existent file."""
        result = fetch_exchanges_from_transcript('/nonexistent/file.jsonl', {1})
        self.assertEqual(result, [])


class TestFormatExchanges(unittest.TestCase):
    """Tests for format_exchanges function."""

    def test_format_single_exchange(self):
        """Test formatting a single exchange."""
        exchanges = [{
            'idx': 1,
            'user': 'Hello',
            'assistant': 'Hi there!',
            'timestamp': '2025-01-05T09:00:00Z'
        }]
        result = format_exchanges(exchanges)

        self.assertIn('Exchange #1', result)
        self.assertIn('Hello', result)
        self.assertIn('Hi there!', result)

    def test_format_empty_list(self):
        """Test formatting empty exchange list."""
        result = format_exchanges([])
        self.assertIn('No exchanges found', result)

    def test_truncation_applied(self):
        """Test that long messages are truncated."""
        long_text = 'x' * 2000
        exchanges = [{
            'idx': 1,
            'user': long_text,
            'assistant': 'Short',
            'timestamp': ''
        }]
        result = format_exchanges(exchanges)

        self.assertIn('[...truncated...]', result)


class TestTruncateText(unittest.TestCase):
    """Tests for truncate_text function."""

    def test_short_text_unchanged(self):
        """Test that short text is not truncated."""
        text = 'Short text'
        result = truncate_text(text, MAX_CHARS_PER_MESSAGE)
        self.assertEqual(result, text)

    def test_long_text_truncated(self):
        """Test that long text is truncated."""
        text = 'x' * 2000
        result = truncate_text(text, MAX_CHARS_PER_MESSAGE)

        self.assertLess(len(result), 2000)
        self.assertIn('[...truncated...]', result)


if __name__ == '__main__':
    unittest.main()
