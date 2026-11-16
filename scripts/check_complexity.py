"""Fail-closed policy over Harrow 0.4.1 reports; never analyzes/imports target code.

Install: uv sync --locked (includes harrow-code==0.4.1)
Run: uv run python scripts/check_complexity.py [--output-dir PATH]
Policy changes require whole-family review (docs/COMPLEXITY.md), not auto-baselining.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY = Path(__file__).with_name("complexity-policy.json")
FAMILY = re.compile(
    r"Lexical family (.+): (\d+) scopes, nested depth (\d+), aggregate cognitive (\d+) "
    r"\(per-function scores remain independent\), (\d+) captured binding\(s\) with direct writes; "
    r"review ownership, phases and reentry together"
)


def inventory(names: list[str]) -> str:
    # Lambda coordinates move with unrelated lines; retain their owner and multiplicity.
    stable = sorted(re.sub(r"<lambda@\d+:\d+>", "<lambda>", name) for name in names)
    return hashlib.sha256(json.dumps(stable).encode()).hexdigest()


def observations(
    metrics: dict[str, Any], callbacks: dict[str, Any], files: set[str]
) -> dict[str, Any]:
    if metrics["schema_version"] != "native-metrics-v1" or metrics["metric_version"] != (
        "harrow-python-metrics-v1"
    ):
        raise ValueError("unsupported Harrow metrics schema/version")
    if callbacks["schema_version"] != "harrow-check-v1":
        raise ValueError("unsupported Harrow check schema")
    if metrics["diagnostics"] != [] or callbacks["diagnostics"] != []:
        raise ValueError("Harrow diagnostics: incomplete evidence")
    if sorted(metrics["files"]) != sorted(files):
        raise ValueError("metrics omitted/duplicated/unexpected source files")
    expected = sorted("src/peffect/" + path for path in files)
    if sorted(callbacks["files"]) != expected:
        raise ValueError("callback report omitted/duplicated/unexpected source files")
    summary = callbacks["summary"]
    if (
        callbacks["omitted_findings"] != 0
        or summary["suppressed"] != 0
        or summary["baselined"] != 0
        or summary["total"] != len(callbacks["findings"])
    ):
        raise ValueError("omitted/suppressed/baselined callback findings")
    detectors = [d for d in callbacks["detectors"] if d["rule"] == "callback-family"]
    if len(detectors) != 1 or detectors[0]["status"] != "ran":
        raise ValueError("callback-family detector did not complete")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for function in metrics["functions"]:
        if function["path"] not in files or not isinstance(function["name"], str):
            raise ValueError("invalid function identity")
        for field in ("cyclomatic", "cognitive"):
            if type(function[field]) is not int or function[field] < 0:
                raise ValueError("invalid metric value")
        grouped[function["path"]].append(function)
    file_metrics = {}
    for path in sorted(files):
        functions = grouped[path]
        file_metrics[path] = {
            "inventory": inventory([f["name"] for f in functions]),
            "branches": sum(f["cyclomatic"] - 1 for f in functions),
            "cognitive": sum(f["cognitive"] for f in functions),
            "max_cyclomatic": max((f["cyclomatic"] for f in functions), default=0),
            "max_cognitive": max((f["cognitive"] for f in functions), default=0),
        }
    families = {}
    for finding in callbacks["findings"]:
        if finding["code"] != "callback-family":
            raise ValueError("unexpected finding rule")
        match = FAMILY.fullmatch(finding["message"])
        if match is None:
            raise ValueError("unsupported callback-family evidence format")
        name, *values = match.groups()
        key = finding["path"] + ":" + name
        if key in families:
            raise ValueError("duplicate callback family")
        families[key] = [int(value) for value in values]
    return {"files": file_metrics, "families": families}


def enforce(actual: dict[str, Any], policy: dict[str, Any], metrics: dict[str, Any]) -> None:
    if policy["schema"] != "peffect-complexity-policy-v1" or policy["engine"] != "harrow 0.4.1":
        raise ValueError("unsupported policy schema/engine")
    for section in ("files", "families"):
        if actual[section].keys() != policy[section].keys():
            raise ValueError(f"{section}: identity changed; whole-family review required")
        for key, values in actual[section].items():
            limits = policy[section][key]
            if section == "files":
                if values["inventory"] != limits["inventory"]:
                    raise ValueError(f"{key}: function inventory changed; review extracted helpers")
                pairs = [
                    (values[field], limits[field])
                    for field in ("branches", "cognitive", "max_cyclomatic", "max_cognitive")
                ]
            else:
                if len(values) != 4 or len(limits) != 4:
                    raise ValueError("invalid family policy")
                pairs = list(zip(values, limits, strict=True))
            if any(value > limit for value, limit in pairs):
                raise ValueError(f"{key}: complexity/family burden increased: {values} > {limits}")
    for key, limits in policy["hotspots"].items():
        path, name = key.split(":", 1)
        found = [f for f in metrics["functions"] if f["path"] == path and f["name"] == name]
        if len(found) != 1:
            raise ValueError(f"{key}: named hotspot missing/renamed/duplicated")
        if found[0]["cyclomatic"] > limits[0] or found[0]["cognitive"] > limits[1]:
            raise ValueError(f"{key}: named hotspot exceeded its ceiling")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    out = args.output_dir or Path(tempfile.mkdtemp(prefix="peffect-complexity-"))
    out.mkdir(parents=True, exist_ok=True)

    def command(name: str, argv: list[str], accepted: tuple[int, ...] = (0,)) -> str:
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=60)
        (out / f"{name}.stdout").write_text(result.stdout)
        (out / f"{name}.stderr").write_text(result.stderr)
        if result.returncode not in accepted:
            raise ValueError(f"{argv}: exit {result.returncode}; evidence {out}")
        return result.stdout

    version = command("version", ["harrow-code", "--version"]).strip()
    if version != "harrow 0.4.1":
        raise ValueError(f"expected public harrow-code==0.4.1, got {version!r}")
    metrics = json.loads(command("metrics", ["harrow-code", "metrics", "src/peffect"]))
    callbacks = json.loads(
        command(
            "callbacks",
            [
                "harrow-code",
                "check",
                ".",
                "--select",
                "callback-family",
                "--format",
                "json",
                "--max-findings",
                "10000",
            ],
            (0, 1),
        )
    )
    files = {str(path.relative_to(ROOT / "src/peffect")) for path in (ROOT / "src").rglob("*.py")}
    actual = observations(metrics, callbacks, files)
    enforce(actual, json.loads(POLICY.read_text()), metrics)
    command("pipe", [sys.executable, str(ROOT / "scripts/check_pipe_signatures.py")])
    print(f"all {len(files)} source files: metric, inventory, family and pipe gates pass; {out}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"complexity gate FAILED: {error}", file=sys.stderr)
        sys.exit(1)
