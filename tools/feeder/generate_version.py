#!/usr/bin/env python3
"""
Generate a static version_info.py file at build time.
This script is run during CI builds to bake version information into the executable.
"""

import subprocess
import os
import sys


def get_git_sha():
    """Get the short git SHA (7 characters) from the current commit."""
    try:
        result = subprocess.run(
            ['git', 'rev-parse', '--short=7', 'HEAD'],
            capture_output=True,
            text=True,
            check=True,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 'unknown'


def get_semantic_version():
    """Get the semantic version from the latest git tag."""
    try:
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
        return '0.0.0-dev'


def main():
    # Allow override from environment variables (set by CI)
    version = os.getenv('APP_VERSION', get_semantic_version())
    git_sha = os.getenv('GIT_SHA', get_git_sha())

    # Generate the version_info.py file
    version_file_content = f'''# Auto-generated at build time - DO NOT EDIT
# This file contains static version information baked into the build

VERSION = "{version}"
GIT_SHA = "{git_sha}"
'''

    output_path = os.path.join(os.path.dirname(__file__), 'version_info.py')

    with open(output_path, 'w') as f:
        f.write(version_file_content)

    print(f"Generated version_info.py: VERSION={version}, GIT_SHA={git_sha}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
