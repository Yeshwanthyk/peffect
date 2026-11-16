"""Drive the executable Queue example through the same CLI its reader uses."""

import subprocess
import sys
from pathlib import Path


def test_composed_worker_example_completes_all_its_controlled_scenarios():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "examples/queue_worker.py"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
