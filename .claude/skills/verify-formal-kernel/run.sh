#!/usr/bin/env bash
# Check the fiber protocol model, then the tests that tie it to the real code.
#   run.sh                  model (FiberKernel.cfg) + linked tests; exit 0 = all hold
#   run.sh Break_<x>.cfg    one mirrored break only (expected to go red)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../../.." && pwd)"
cfg="${1:-FiberKernel.cfg}"
(cd "$here" && python3 tlc_chart.py run FiberKernel.tla --config "$cfg")
if [ "$cfg" = "FiberKernel.cfg" ]; then
  cd "$root" && uv run pytest -q tests/test_kernel.py tests/test_kernel_props.py
fi
