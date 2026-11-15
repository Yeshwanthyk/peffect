"""The type encoding is the product: pin both what passes and what must fail."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
PYRIGHT = Path(sys.executable).parent / "pyright"


def pyright(path: str) -> list[str]:
    out = subprocess.run([PYRIGHT, path], cwd=ROOT, capture_output=True, text=True).stdout
    return [ln for ln in out.splitlines() if " - error:" in ln]


def test_should_pass_typechecks() -> None:
    assert pyright("typetests/should_pass.py") == []


def test_every_mistake_is_rejected() -> None:
    errors = pyright("typetests/should_fail.py")
    lines = {int(m.group(1)) for e in errors if (m := re.search(r":(\d+):\d+ - error", e))}
    src = (ROOT / "typetests/should_fail.py").read_text().splitlines()
    # each "# N. NAME" block must produce at least one error on its following statement lines
    blocks = [i for i, s in enumerate(src, 1) if re.match(r"\s*# \d+\. [A-Z_]+", s)]
    ends = [*blocks[1:], len(src) + 1]
    missing = [
        src[b - 1] for b, e in zip(blocks, ends, strict=True) if not lines & set(range(b, e))
    ]
    assert missing == [], f"mistakes pyright accepted: {missing}"


def test_each_negative_block_requires_its_own_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    errors = pyright("typetests/should_fail.py")
    src = (ROOT / "typetests/should_fail.py").read_text().splitlines()
    starts = [
        i
        for i, line in enumerate(src, 1)
        if line.lstrip().startswith("# ") and line.lstrip()[2:].split(".", 1)[0].isdigit()
    ]
    assert starts
    for start, end in zip(starts, [*starts[1:], len(src) + 1], strict=True):
        remaining = [
            error
            for error in errors
            if not (match := re.search(r":(\d+):\d+ - error", error))
            or not start <= int(match.group(1)) < end
        ]

        def filtered(_: str, result: list[str] = remaining) -> list[str]:
            return result

        monkeypatch.setitem(globals(), "pyright", filtered)
        with pytest.raises(AssertionError, match="mistakes pyright accepted"):
            test_every_mistake_is_rejected()
