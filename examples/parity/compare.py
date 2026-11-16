"""Run both applications fresh and compare every scenario, retaining the raw evidence.

    uv run python -m examples.parity.compare [--output-dir PATH]

Only validation error class names and diagnostic text differ by design. No lifecycle
event, event order, count, domain error, or result value is normalized.
"""

import argparse
import copy
import difflib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PIN = "289a53f7b1fc40049219c6e69022db2fb859c356"
SCENARIOS = [
    "success",
    "invalid_input_range",
    "invalid_input_type",
    "domain_failure_after_acquire",
    "domain_recovery",
    "shared_layer",
    "service_replacement",
    "cancellation_after_acquire",
    "composed_success",
    "composed_user_failure",
    "composed_stock_failure",
    "composed_payment_failure",
    "composed_payment_recovery",
    "composed_payment_replacement",
    "composed_parallel_batch",
    "composed_batch_cancellation",
    "batch_settle_mixed",
    "batch_settle_audited_payments",
    "batch_settle_custom_audit",
    "batch_settle_cancellation",
    "batch_settle_ledger_full",
]


def normalized(scenarios: list[dict[str, Any]], validation_tag: str) -> list[dict[str, Any]]:
    result = copy.deepcopy(scenarios)
    for scenario in result:
        outcome = scenario["outcome"]
        if outcome.get("category") == "validation":
            assert outcome["kind"] == "failure", outcome
            assert outcome["tag"] == validation_tag, outcome
            assert set(outcome["detail"]) == {"diagnostic_not_compared"}, outcome
            assert isinstance(outcome["detail"]["diagnostic_not_compared"], str), outcome
            outcome["tag"] = "validation-error"
            outcome["detail"]["diagnostic_not_compared"] = "<library-specific diagnostic>"
    return result


def assert_scenarios_match(
    reference: list[dict[str, Any]],
    python: list[dict[str, Any]],
) -> None:
    assert [s["name"] for s in reference] == SCENARIOS, "reference scenario set changed"
    expected = normalized(reference, "SchemaError")
    actual = normalized(python, "ValidationError")
    # Python considers True == 1. JSON types are observable, so compare their encodings.
    expected_json = json.dumps(expected, indent=2, sort_keys=True)
    actual_json = json.dumps(actual, indent=2, sort_keys=True)
    if expected_json != actual_json:
        diff = "\n".join(
            difflib.unified_diff(
                expected_json.splitlines(),
                actual_json.splitlines(),
                fromfile="EffectJS",
                tofile="peffect",
                lineterm="",
            )
        )
        raise AssertionError(f"Application parity mismatch:\n{diff}")


def command(args: list[str], cwd: Path = ROOT) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"{args!r} exited {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def compare(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.find_spec("peffect")
    if (
        spec is None
        or spec.origin is None
        or Path(spec.origin).resolve() != (ROOT / "src/peffect/__init__.py")
    ):
        raise RuntimeError("Python must import peffect from this checkout")
    bun = shutil.which("bun")
    if bun is None:
        raise RuntimeError("Bun is required to run the real vendored TypeScript reference")
    vendor = ROOT / "vendor/effect-v4"
    pin = command(["git", "rev-parse", "HEAD"], vendor).strip()
    if pin != PIN:
        raise RuntimeError(f"Effect vendor pin changed: expected {PIN}, got {pin}")
    command(["git", "diff", "--exit-code", "HEAD", "--", "packages/effect/src"], vendor)
    resolved = command([bun, "-e", 'console.log(import.meta.resolve("effect"))'], HERE).strip()
    resolved_path = unquote(urlparse(resolved).path) if resolved.startswith("file:") else resolved
    if Path(resolved_path).resolve() != vendor / "packages/effect/src/index.ts":
        raise RuntimeError(f"Reference imports a different Effect: {resolved}")

    # No cached JSON fallback: both processes must run successfully on every comparison.
    reference_text = command([bun, "run", "reference.ts"], HERE)
    (output / "reference.output.json").write_text(reference_text)
    python_text = command([sys.executable, "-m", "examples.parity.python_reference"])
    (output / "python.output.json").write_text(python_text)
    reference = json.loads(reference_text)
    python = json.loads(python_text)
    assert_scenarios_match(reference["scenarios"], python["scenarios"])
    report = {
        "vendor_commit": pin,
        "peffect_commit": command(["git", "rev-parse", "HEAD"]).strip(),
        "working_tree_dirty": bool(command(["git", "status", "--porcelain"]).strip()),
        "python_source": spec.origin,
        "effect_resolved": resolved,
        "matched": SCENARIOS,
        "normalized": ["validation error class name", "validation diagnostic text"],
    }
    (output / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{len(SCENARIOS)}/{len(SCENARIOS)} application scenarios match\nevidence: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir or Path(tempfile.mkdtemp(prefix="peffect-parity-"))
    compare(output.resolve())


if __name__ == "__main__":
    main()
