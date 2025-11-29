#!/usr/bin/env python3
"""
Version information utility for USB JR Bay application.
Retrieves git SHA and semantic version information.

Priority order:
1. If version_info.py exists (generated at build time), use those static values
2. Otherwise, query git dynamically (for local development)
"""

import subprocess
import os
import sys


def get_git_sha():
    """
    Get the short git SHA (7 characters) from the current commit.
    Returns 'unknown' if git is not available or not in a git repository.
    """
    try:
        # Try to get the short SHA from git
        result = subprocess.run(
            ['git', 'rev-parse', '--short=7', 'HEAD'],
            capture_output=True,
            text=True,
            check=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        # If git command fails or git is not installed
        return 'unknown'


def get_semantic_version():
    """
    Get the semantic version from the latest git tag.
    Returns '0.0.0-dev' if no tag exists or not in a git repository.
    Expected tag format: vX.Y.Z or X.Y.Z
    """
    try:
        # Try to get the most recent tag
        result = subprocess.run(
            ['git', 'describe', '--tags', '--exact-match', 'HEAD'],
            capture_output=True,
            text=True,
            check=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        tag = result.stdout.strip()
        # Remove 'v' prefix if present
        if tag.startswith('v'):
            tag = tag[1:]
        return tag
    except (subprocess.CalledProcessError, FileNotFoundError):
        # If no exact tag match or git is not available, return dev version
        return '0.0.0-dev'


# Try to import from generated version_info.py (created at build time)
# If it exists, use those static values; otherwise fall back to dynamic git queries
try:
    from version_info import VERSION as _STATIC_VERSION, GIT_SHA as _STATIC_SHA
    VERSION = _STATIC_VERSION
    GIT_SHA = _STATIC_SHA
except ImportError:
    # No static version file - use dynamic git queries (development mode)
    VERSION = get_semantic_version()
    GIT_SHA = get_git_sha()


def get_version_info():
    """
    Get both version and SHA as a dictionary.
    Returns:
        dict: {'version': str, 'sha': str}
    """
    return {
        'version': VERSION,
        'sha': GIT_SHA
    }


if __name__ == '__main__':
    # Simple test when run directly
    info = get_version_info()
    print(f"Version: {info['version']}")
    print(f"Git SHA: {info['sha']}")
