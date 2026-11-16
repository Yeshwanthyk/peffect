"""Prove the child-wait regression test rejects a real kernel break in a scratch copy."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = "tests/test_kernel.py::test_interrupt_while_waiting_for_children_lands"
OLD = "        if not live:\n            self._publish(exit_)"
NEW = "        if True:\n            self._publish(exit_)"


def run_case(work: Path, evidence: Path, name: str) -> tuple[int, int, int]:
    """Return process status, assertion failures and infrastructure/test errors."""
    env = {**os.environ, "PYTHONPATH": str(work / "src"), "PYTEST_ADDOPTS": ""}
    report = evidence / f"{name}.xml"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        TARGET,
        "-o",
        "asyncio_mode=auto",
        "--junitxml",
        str(report),
    ]
    result = subprocess.run(
        command,
        cwd=work,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    (evidence / f"{name}.log").write_text(result.stdout + result.stderr)
    suites = ET.parse(report).getroot().iter("testsuite")
    counts = [(int(s.get("failures", "0")), int(s.get("errors", "0"))) for s in suites]
    return result.returncode, sum(f for f, _ in counts), sum(e for _, e in counts)


def main() -> int:
    evidence = Path(tempfile.mkdtemp(prefix="peffect-mutations-"))
    print(f"evidence: {evidence}", flush=True)
    with tempfile.TemporaryDirectory(prefix="peffect-mutation-source-") as directory:
        work = Path(directory)
        shutil.copytree(ROOT / "src", work / "src", ignore=shutil.ignore_patterns("__pycache__"))
        (work / "tests").mkdir()
        shutil.copy2(ROOT / "tests/test_kernel.py", work / "tests/test_kernel.py")
        # A collected assertion pins the import to the isolated source, even with editable installs.
        (work / "tests/conftest.py").write_text(
            "from pathlib import Path\nimport peffect\n"
            "assert Path(peffect.__file__).resolve() == "
            "Path(__file__).resolve().parents[1] / 'src/peffect/__init__.py'\n"
        )
        if run_case(work, evidence, "baseline") != (0, 0, 0):
            raise RuntimeError("Baseline failed; mutation evidence is invalid")
        fiber = work / "src/peffect/fiber.py"
        source = fiber.read_text()
        if source.count(OLD) != 1:
            raise RuntimeError("Mutation anchor changed; review _done before updating the break")
        fiber.write_text(source.replace(OLD, NEW))
        status, failures, errors = run_case(work, evidence, "skip_child_wait")
        if status != 1 or failures != 1 or errors:
            raise RuntimeError("Expected one assertion failure; survivor or invalid mutation run")
    (evidence / "summary.txt").write_text(
        "PASS: clean baseline; skip_child_wait killed by its linked assertion; scratch removed\n"
    )
    print((evidence / "summary.txt").read_text(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
