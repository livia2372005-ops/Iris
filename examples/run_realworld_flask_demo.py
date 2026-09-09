"""CLI runner for the real-world Flask flight recorder demonstration."""

import sys
from pathlib import Path

# Ensure Iris is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tests.test_realworld_flask import test_trace_realworld_flask

if __name__ == "__main__":
    test_trace_realworld_flask()
