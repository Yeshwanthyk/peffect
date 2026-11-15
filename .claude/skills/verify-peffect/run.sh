#!/usr/bin/env bash
# Verify peffect on its real surface: doctor, the public-API examples, the kernel boundary lint,
# then the full test, lint and type checks. Each step's command, output and exit code are saved.
#   run.sh        exit 0 = every step passed; 1 = a step failed (doctor failing stops the run)
# Evidence: $PEFFECT_EVIDENCE/<UTC time>-<pid>/, default base ${TMPDIR:-/tmp}/peffect-verify
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
tmp="${TMPDIR:-/tmp}"
out="${PEFFECT_EVIDENCE:-${tmp%/}/peffect-verify}/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$out" || exit 1
cd "$root" || exit 1
failed=""

step() {
  local name=$1 rc=0
  shift
  { printf '$'; printf ' %q' "$@"; echo; } >"$out/$name.log"
  "$@" >>"$out/$name.log" 2>&1 || rc=$?
  echo "$rc" >"$out/$name.exit"
  printf '%-10s exit %s\n' "$name" "$rc" | tee -a "$out/summary.txt"
  if [ "$rc" -ne 0 ]; then
    failed="$failed $name"
    tail -n 30 "$out/$name.log"
  fi
  return "$rc"
}

doctor='
import pathlib, sys
import peffect
src = pathlib.Path(peffect.__file__).resolve()
assert sys.version_info >= (3, 13), sys.version
assert src == pathlib.Path.cwd().resolve() / "src/peffect/__init__.py", f"peffect from {src}"
print("python", sys.version.split()[0], "| peffect from", src)
'
if ! step doctor uv run python -c "$doctor"; then
  echo "doctor failed, nothing driven. evidence: $out"
  exit 1
fi
step example uv run python examples/quickstart.py
step context uv run python examples/context.py
step boundaries uv run python scripts/check_boundaries.py
step pytest uv run pytest -q
step ruff uv run ruff check .
step pyright uv run pyright

echo "evidence: $out"
if [ -n "$failed" ]; then
  echo "FAILED:$failed" | tee -a "$out/summary.txt"
  exit 1
fi
echo "all steps passed" | tee -a "$out/summary.txt"
