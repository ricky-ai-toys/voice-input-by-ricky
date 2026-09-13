"""Adapter so run_with_hooks.py can drive host_tip_harness.py (which wants a hold time)."""
from __future__ import annotations

import os
import runpy
import sys

here = os.path.dirname(os.path.abspath(__file__))
runtime = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "scratch")
hold = sys.argv[2] if len(sys.argv) > 2 else "5"
sys.argv = ["host_tip_harness.py", runtime, hold]
runpy.run_path(os.path.join(here, "host_tip_harness.py"), run_name="__main__")
