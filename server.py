#!/usr/bin/env python3
"""Keynote MCP server entry point.

    python3 server.py [--root DIR ...] [--path-map 'Z:\\=/Users/me/KeynoteMCP'] [--allow-script]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from keynote_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
