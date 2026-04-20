#!/usr/bin/env python3
"""Back-compat shim — the real code moved to src/kayak/tracing/trace.py
and the CLI entry point is now `levels trace`.

This file preserves the old invocation path for anyone who still runs
`python3 scripts/trace_reach.py --putin ... --takeout ...`.
"""

import sys

from kayak.tracing.trace import main

if __name__ == "__main__":
    sys.exit(main())
