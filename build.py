#!/usr/bin/env python3
"""Generate white_wizard.py — a standalone shim that runs the package without pip.

The source of truth is now white_wizard/app.py. Edit that, then run this script
to regenerate the convenience shim for users who prefer `python3 white_wizard.py`.
"""

SHIM = '''\
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
'''

with open("white_wizard.py", "w") as fh:
    fh.write(SHIM)

print("Wrote white_wizard.py (shim)")
