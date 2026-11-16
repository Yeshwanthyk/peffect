"""Gate sensitivity: malformed/incomplete evidence and extraction cannot silently pass."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_complexity.py"
spec = importlib.util.spec_from_file_location("complexity_gate", SCRIPT)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.fixture(scope="module")
def evidence():
    def report(args: list[str]):
        result = subprocess.run(
            ["harrow-code", *args], cwd=ROOT, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    metrics = report(["metrics", "src/peffect"])
    callbacks = report(
        ["check", ".", "--select", "callback-family", "--format", "json", "--max-findings", "10000"]
    )
    files = {str(p.relative_to(ROOT / "src/peffect")) for p in (ROOT / "src").rglob("*.py")}
    policy = json.loads(gate.POLICY.read_text())
    return metrics, callbacks, files, policy


def test_real_source_only_gate(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all 26 source files" in result.stdout


def test_missing_harrow_fails_closed(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path)],
        cwd=ROOT,
        env={**os.environ, "PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "complexity gate FAILED" in result.stderr


@pytest.mark.parametrize(
    "change",
    [
        "metric_schema",
        "metric_version",
        "check_schema",
        "metric_diagnostic",
        "check_diagnostic",
        "metric_file",
        "check_file",
        "omission",
        "suppression",
        "detector",
        "family_format",
    ],
)
def test_incomplete_reports_fail_closed(evidence: Any, change: str):
    metrics, callbacks, files, _ = copy.deepcopy(evidence)
    match change:
        case "metric_schema":
            metrics["schema_version"] = "future"
        case "metric_version":
            metrics["metric_version"] = "future"
        case "check_schema":
            callbacks["schema_version"] = "future"
        case "metric_diagnostic":
            metrics["diagnostics"] = ["partial"]
        case "check_diagnostic":
            callbacks["diagnostics"] = ["partial"]
        case "metric_file":
            metrics["files"].pop()
        case "check_file":
            callbacks["files"].pop()
        case "omission":
            callbacks["omitted_findings"] = 1
        case "suppression":
            callbacks["summary"]["suppressed"] = 1
        case "detector":
            for detector in callbacks["detectors"]:
                if detector["rule"] == "callback-family":
                    detector["status"] = "partial"
        case "family_format":
            callbacks["findings"][0]["message"] = "future format"
        case _:
            raise AssertionError(change)
    with pytest.raises(ValueError):
        gate.observations(metrics, callbacks, files)


@pytest.mark.parametrize("change", ["hotspot", "extract", "file_total", "family", "missing_family"])
def test_ratchets_include_whole_file_and_family(evidence: Any, change: str):
    metrics, callbacks, files, policy = copy.deepcopy(evidence)
    if change == "hotspot":
        function = next(f for f in metrics["functions"] if f["name"] == "Fiber.evaluate")
        function["name"] = "Fiber.renamed"
    if change == "extract":
        function = next(f for f in metrics["functions"] if f["name"] == "_run_window.pump")
        helper = {**function, "name": "_new_helper", "cyclomatic": 3, "cognitive": 2}
        function["cyclomatic"] -= 2
        function["cognitive"] -= 2
        metrics["functions"].append(helper)  # same file totals, lower individual scores
    actual = gate.observations(metrics, callbacks, files)
    match change:
        case "hotspot":
            # Even a reviewer updating the inventory cannot accidentally drop a named hotspot.
            policy["files"]["fiber.py"]["inventory"] = actual["files"]["fiber.py"]["inventory"]
        case "extract":
            pass  # the native-metric inventory, not a name-only hotspot lookup, must catch it
        case "file_total":
            actual["files"]["fiber.py"]["branches"] += 1
        case "family":
            actual["families"]["src/peffect/fiber.py:_run_window"][2] += 1
        case "missing_family":
            del actual["families"]["src/peffect/fiber.py:_run_window"]
        case _:
            raise AssertionError(change)
    with pytest.raises(ValueError):
        gate.enforce(actual, policy, metrics)


def test_wrong_engine_fails_before_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_run(*args: Any, **kwargs: Any):
        return subprocess.CompletedProcess(args[0], 0, stdout="harrow 0.4.2\n", stderr="")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--output-dir", str(tmp_path)])
    with pytest.raises(ValueError, match=r"expected public harrow-code==0\.4\.1"):
        gate.main()
