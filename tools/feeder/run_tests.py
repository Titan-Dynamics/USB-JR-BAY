#!/usr/bin/env python3
"""
Run all unit tests for the USB JR Bay feeder application.

This script automatically discovers and runs all tests in the test/ directory.
"""

import sys
import unittest


def run_all_tests():
    """Discover and run all tests in the test directory."""
    # Discover all tests in the 'test' directory
    loader = unittest.TestLoader()
    start_dir = 'test'
    suite = loader.discover(start_dir, pattern='test_*.py')
    
    # Run the tests with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Return exit code based on test results
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_all_tests())
