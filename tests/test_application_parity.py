"""Application acceptance against real vendored Effect, not an upstream test port."""

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("parity_compare", ROOT / "examples/parity/compare.py")
assert spec is not None and spec.loader is not None
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def test_live_effectjs_and_python_applications_match() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "examples.parity.compare"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "16/16 application scenarios match" in result.stdout


@pytest.mark.parametrize(
    "change",
    ["result", "release", "order", "domain", "replacement", "missing_scenario", "json_type"],
)
def test_comparison_rejects_observable_differences(change: str) -> None:
    # Fixture for the comparator only; acceptance always runs both real programs.
    reference: list[dict[str, Any]] = json.loads(
        (ROOT / "examples/parity/reference.output.json").read_text()
    )["scenarios"]
    python = copy.deepcopy(reference)
    for scenario in python:
        if scenario["outcome"].get("category") == "validation":
            scenario["outcome"]["tag"] = "ValidationError"
            scenario["outcome"]["detail"]["diagnostic_not_compared"] = "Pydantic diagnostic"
    compare.assert_scenarios_match(reference, python)
    match change:
        case "result":
            python[0]["outcome"]["value"] = "wrong greeting"
        case "release":
            python[0]["repo"]["release"] = 0
        case "order":
            events = python[7]["events"]
            release, returned = (
                events.index("repo.release:interrupt"),
                events.index("cancel.returned"),
            )
            events[release], events[returned] = events[returned], events[release]
        case "domain":
            python[3]["outcome"]["detail"]["userId"] = 1
        case "replacement":
            python[6]["events"].append("repo.acquire")
        case "missing_scenario":
            python.pop()
        case "json_type":
            python[0]["repo"]["release"] = True
        case _:
            raise AssertionError(f"unknown mutation {change}")
    with pytest.raises(AssertionError, match="Application parity mismatch"):
        compare.assert_scenarios_match(reference, python)


def test_comparison_catches_an_actual_application_with_release_disabled() -> None:
    mutant = """
import asyncio, json
from examples.parity import python_app as app
from peffect import succeed
record = app.record
app.record = lambda event: succeed(None) if event.startswith("repo.release:") else record(event)
from examples.parity.python_reference import run_scenarios
print(json.dumps(asyncio.run(run_scenarios())))
"""
    result = subprocess.run(
        [sys.executable, "-c", mutant], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    reference = json.loads((ROOT / "examples/parity/reference.output.json").read_text())[
        "scenarios"
    ]
    with pytest.raises(AssertionError, match="Application parity mismatch"):
        compare.assert_scenarios_match(reference, json.loads(result.stdout))


def test_typescript_classifier_does_not_hide_additional_causes() -> None:
    probe = """
import { Cause, Exit } from "effect"
import { UserNotFound } from "./app.ts"
import { outcomeOf } from "./outcomes.ts"
for (const extra of [Cause.die("cleanup defect"), Cause.fail(new UserNotFound({userId: 2}))]) {
  const exit = Exit.failCause(Cause.combine(Cause.fail(new UserNotFound({userId: 99})), extra))
  let rejected = false
  try { outcomeOf(exit) } catch (error) {
    if (!(error instanceof Error) || !error.message.startsWith("unclassified exit:")) throw error
    rejected = true
  }
  if (!rejected) throw new Error("classifier hid an additional cause")
}
"""
    result = subprocess.run(
        ["bun", "-e", probe],
        cwd=ROOT / "examples/parity",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_concurrent_application_runs_keep_their_traces_isolated() -> None:
    probe = """
import asyncio, json
from examples.parity.python_reference import run_scenarios
async def main():
    print(json.dumps(await asyncio.gather(run_scenarios(), run_scenarios())))
asyncio.run(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    reference = json.loads((ROOT / "examples/parity/reference.output.json").read_text())[
        "scenarios"
    ]
    for scenarios in json.loads(result.stdout):
        compare.assert_scenarios_match(reference, scenarios)
