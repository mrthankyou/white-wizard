#!/usr/bin/env python3
"""Standalone shim — runs White Wizard from the package in the same directory.

For development use without pip install. To install properly:
    pip install -e .   # installs the `wizard` command
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from white_wizard.__main__ import main

if __name__ == "__main__":
    main()
