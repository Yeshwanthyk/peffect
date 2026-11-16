"""The consistency gate rejects drift even when both public APIs drift together."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "pipe_check", ROOT / "scripts/check_pipe_signatures.py"
)
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


@pytest.mark.parametrize("owner,module", [("Effect", "core"), ("Layer", "layer")])
def test_pipe_signatures_and_mutations(owner: str, module: str):
    source = (ROOT / f"src/peffect/{module}.py").read_text()
    checker.check(source, owner)
    mutations = [
        ("f2: _F[T1, T2]", "f2: _F[T2, T2]"),
        ("/) -> T1: ...", "/) -> Any: ..."),
        ("    @overload\n    def pipe(self, /)", "    def pipe(self, /)"),
        ("f20: _F[T19, T20]", "f20: _F[T18, T20]"),
    ]
    for old, new in mutations:
        assert old in source
        with pytest.raises(ValueError, match="contract"):
            checker.check(source.replace(old, new), owner)
