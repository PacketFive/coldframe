"""Entry point for ``python -m coldframe``.

Available alongside the console script so the tool works before an editable
install has put anything on PATH.
"""

from __future__ import annotations

import sys

from coldframe.cli import main

if __name__ == "__main__":
    sys.exit(main())
