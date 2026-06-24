#!/usr/bin/env python3
"""Tests for utils.compute_project_hash (WI-1/WI-2: stable project identity from cwd)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from utils import compute_project_hash


class TestComputeProjectHash(unittest.TestCase):
    def test_stable_for_same_path(self):
        """Same path always yields the same hash (so a project groups consistently)."""
        self.assertEqual(
            compute_project_hash('/Users/x/proj'),
            compute_project_hash('/Users/x/proj'),
        )

    def test_nonempty_for_real_path(self):
        self.assertTrue(compute_project_hash('/Users/x/proj'))

    def test_empty_path_returns_empty(self):
        """No cwd -> empty hash (never a hash of '')."""
        self.assertEqual(compute_project_hash(''), '')

    def test_different_paths_differ(self):
        self.assertNotEqual(
            compute_project_hash('/a/one'),
            compute_project_hash('/a/two'),
        )


if __name__ == '__main__':
    unittest.main()
