#!/usr/bin/env python3
"""Unit tests for save_context_snapshot.py (the hook)"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add hooks directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'hooks'))

from save_context_snapshot import (
    extract_text_content,
    make_preview,
    parse_transcript_with_timestamps,
    truncate_text,
    build_exchange_index,
    PREVIEW_LENGTH,
    MAX_CHARS_PER_MESSAGE
)


class TestExtractTextContent(unittest.TestCase):
    """Tests for extract_text_content function."""

    def test_simple_text_content(self):
        """Test extraction from standard message format."""
        message = {
            'content': [
                {'type': 'text', 'text': 'Hello world'}
            ]
        }
        result = extract_text_content(message)
        self.assertEqual(result, 'Hello world')

    def test_string_content(self):
        """Test extraction when content is a simple string."""
        message = {'content': 'Direct string content'}
        result = extract_text_content(message)
        self.assertEqual(result, 'Direct string content')

    def test_empty_content(self):
        """Test extraction with empty content."""
        message = {'content': []}
        result = extract_text_content(message)
        self.assertEqual(result, '')

    def test_multiple_text_parts(self):
        """Test extraction with multiple text parts."""
        message = {
            'content': [
                {'type': 'text', 'text': 'Part 1'},
                {'type': 'text', 'text': 'Part 2'}
            ]
        }
        result = extract_text_content(message)
        self.assertEqual(result, 'Part 1\nPart 2')


class TestMakePreview(unittest.TestCase):
    """Tests for make_preview function."""

    def test_short_text(self):
        """Test preview of short text."""
        result = make_preview('Hello world')
        self.assertEqual(result, 'Hello world')

    def test_long_text_truncated(self):
        """Test preview truncates long text."""
        long_text = 'x' * 200
        result = make_preview(long_text, max_length=80)
        self.assertEqual(len(result), 80)
        self.assertTrue(result.endswith('...'))

    def test_removes_newlines(self):
        """Test that newlines are removed."""
        text = 'Line 1\nLine 2\nLine 3'
        result = make_preview(text)
        self.assertNotIn('\n', result)
        self.assertIn('Line 1', result)


class TestParseTranscriptWithTimestamps(unittest.TestCase):
    """Tests for parse_transcript_with_timestamps function."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_file = Path(self.temp_dir) / 'transcript.jsonl'

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_valid_transcript(self):
        """Test parsing a valid transcript file."""
        lines = [
            json.dumps({
                'type': 'user',
                'message': {'content': [{'type': 'text', 'text': 'Hello'}]},
                'timestamp': '2025-01-05T09:00:00Z'
            }),
            json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': 'Hi there!'}]},
                'timestamp': '2025-01-05T09:00:05Z'
            }),
        ]
        with open(self.transcript_file, 'w') as f:
            f.write('\n'.join(lines))

        result = parse_transcript_with_timestamps(str(self.transcript_file))

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['role'], 'user')
        self.assertEqual(result[0]['text'], 'Hello')
        self.assertEqual(result[0]['timestamp'], '2025-01-05T09:00:00Z')

    def test_empty_path(self):
        """Test with empty path."""
        result = parse_transcript_with_timestamps('')
        self.assertEqual(result, [])

    def test_nonexistent_file(self):
        """Test with non-existent file."""
        result = parse_transcript_with_timestamps('/nonexistent/file.jsonl')
        self.assertEqual(result, [])


class TestTruncateText(unittest.TestCase):
    """Tests for truncate_text function."""

    def test_short_text_unchanged(self):
        """Test short text is not modified."""
        text = 'Short text'
        result = truncate_text(text, MAX_CHARS_PER_MESSAGE)
        self.assertEqual(result, text)

    def test_long_text_truncated(self):
        """Test long text gets truncated."""
        text = 'x' * 2000
        result = truncate_text(text, MAX_CHARS_PER_MESSAGE)
        self.assertLess(len(result), 2000)
        self.assertIn('[...truncated...]', result)


class TestBuildExchangeIndex(unittest.TestCase):
    """Tests for build_exchange_index function."""

    def test_builds_index_and_recent(self):
        """Test building index and recent exchanges."""
        messages = [
            {'role': 'user', 'text': 'Question 1', 'timestamp': '2025-01-05T09:00:00Z'},
            {'role': 'assistant', 'text': 'Answer 1', 'timestamp': '2025-01-05T09:00:05Z'},
            {'role': 'user', 'text': 'Question 2', 'timestamp': '2025-01-05T09:01:00Z'},
            {'role': 'assistant', 'text': 'Answer 2', 'timestamp': '2025-01-05T09:01:05Z'},
        ]

        full_index, recent = build_exchange_index(messages)

        self.assertEqual(len(full_index), 2)
        self.assertEqual(full_index[0]['idx'], 1)
        self.assertEqual(full_index[0]['preview'], 'Question 1')

        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]['user'], 'Question 1')

    def test_empty_messages(self):
        """Test with empty message list."""
        full_index, recent = build_exchange_index([])
        self.assertEqual(len(full_index), 0)
        self.assertEqual(len(recent), 0)

    def test_unpaired_messages(self):
        """Test with unpaired messages."""
        messages = [
            {'role': 'user', 'text': 'Question 1', 'timestamp': ''},
            {'role': 'user', 'text': 'Question 2', 'timestamp': ''},  # No assistant response
            {'role': 'assistant', 'text': 'Answer', 'timestamp': ''},
        ]

        full_index, recent = build_exchange_index(messages)

        # Only one complete pair: Question 2 -> Answer
        self.assertEqual(len(full_index), 1)

    def test_index_has_timestamps(self):
        """Test that index entries include timestamps."""
        messages = [
            {'role': 'user', 'text': 'Q', 'timestamp': '2025-01-05T14:30:00Z'},
            {'role': 'assistant', 'text': 'A', 'timestamp': '2025-01-05T14:30:05Z'},
        ]

        full_index, _ = build_exchange_index(messages)

        self.assertEqual(full_index[0]['timestamp'], '2025-01-05T14:30:00Z')


class TestMainHookBehavior(unittest.TestCase):
    """Integration tests for main hook behavior using subprocess."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.transcript_file = Path(self.temp_dir) / 'transcript.jsonl'
        self.context_dir = Path(self.temp_dir) / '.claude' / 'context-recall'

    def tearDown(self):
        """Clean up test fixtures."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_recall_command_logs_event(self):
        """Test that /recall command triggers logging."""
        import subprocess

        # Create transcript
        lines = [
            json.dumps({
                'type': 'user',
                'message': {'content': [{'type': 'text', 'text': 'Hello'}]},
                'timestamp': '2025-01-05T09:00:00Z'
            }),
            json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': 'Hi!'}]},
                'timestamp': '2025-01-05T09:00:05Z'
            }),
        ]
        with open(self.transcript_file, 'w') as f:
            f.write('\n'.join(lines))

        # Run hook
        hook_script = Path(__file__).parent.parent / 'hooks' / 'save_context_snapshot.py'
        input_data = json.dumps({
            'session_id': 'test-session',
            'transcript_path': str(self.transcript_file),
            'user_prompt': '/recall'
        })

        env = os.environ.copy()
        env['HOME'] = self.temp_dir

        result = subprocess.run(
            ['python3', str(hook_script)],
            input=input_data,
            capture_output=True,
            text=True,
            env=env
        )

        # Check output
        output = json.loads(result.stdout)
        self.assertIn('systemMessage', output)
        self.assertIn('Context recall logged', output['systemMessage'])

    def test_non_recall_command(self):
        """Test that non-recall commands save index without logging."""
        import subprocess

        # Create transcript
        lines = [
            json.dumps({
                'type': 'user',
                'message': {'content': [{'type': 'text', 'text': 'Hello'}]},
                'timestamp': '2025-01-05T09:00:00Z'
            }),
            json.dumps({
                'type': 'assistant',
                'message': {'content': [{'type': 'text', 'text': 'Hi!'}]},
                'timestamp': '2025-01-05T09:00:05Z'
            }),
        ]
        with open(self.transcript_file, 'w') as f:
            f.write('\n'.join(lines))

        hook_script = Path(__file__).parent.parent / 'hooks' / 'save_context_snapshot.py'
        input_data = json.dumps({
            'session_id': 'test-session',
            'transcript_path': str(self.transcript_file),
            'user_prompt': 'Just a regular message'
        })

        env = os.environ.copy()
        env['HOME'] = self.temp_dir

        result = subprocess.run(
            ['python3', str(hook_script)],
            input=input_data,
            capture_output=True,
            text=True,
            env=env
        )

        output = json.loads(result.stdout)
        self.assertEqual(output, {})

        # Check that index was created
        index_file = self.context_dir / 'index.json'
        self.assertTrue(index_file.exists())


if __name__ == '__main__':
    unittest.main()
