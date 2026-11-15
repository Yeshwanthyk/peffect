"""The kernel boundary lint (scripts/check_boundaries.py), driven through its CLI.

Probes live in a temporary package named `peffect`, so relative imports resolve the way they would
inside src/peffect. Each forbidden line carries `# expect: RULE`; the lint must report exactly
those lines. Docs: docs/INVARIANTS.md, "Kernel boundary".
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "check_boundaries.py"
VIOLATION = re.compile(r"^(?P<path>.+):(?P<line>\d+): (?P<rule>B[123]) .+$")
SUMMARY = re.compile(r"^boundaries: (?P<files>\d+) files checked, (?P<n>\d+) violations$")

ALLOWED = """
from typing import Any

import peffect as pe
from peffect import Effect, Fiber, succeed
from peffect.core import WithFiber as WF, catch_tag, gen
from peffect.fiber import fork, run
from .core import Effect as E2, succeed as s2
from .fiber import Fiber as F2
from .cause import Cause
from . import run as run2
from .context import Tag
from peffect.context import Reference


def read_context(fiber: Fiber[Any, Any]) -> Effect[Any, Any, Any]:
    return succeed(fiber.context)


def scheduler_should_yield(fiber: Fiber[Any, Any]) -> bool:
    return fiber.current_op_count >= 2048


def stop(fiber: "F2[Any, Any]"):
    return fiber.interrupt()


def wait(fiber):
    return fiber.join().flat_map(lambda _: fiber.await_())


def unrelated(fiber):  # same name, not a Fiber and not a WithFiber callback
    return fiber.stack, fiber.running


def shadowing(fiber: pe.Fiber[Any, Any]):
    def inner(fiber):  # shadows the outer Fiber parameter
        return fiber.stack

    return inner, fiber.context


class Plain:
    running = True


def ordinary(obj: Plain):
    return obj.running


ok1 = WF(lambda fiber: succeed(fiber.context))
ok2 = WF(wait)
ok3 = WF(f=read_context)
loose = lambda fiber: fiber.stack  # not handed to WithFiber
pe.succeed(1).map(str)


def shadowed_module(pe):
    return pe.core
"""

FORBIDDEN = """
from typing import Any

import peffect as pe
import peffect.core  # expect: B1
import peffect.fiber as pf  # expect: B1
from peffect import core  # expect: B1
from peffect import _ids  # expect: B1
from peffect.core import Async  # expect: B1
from peffect.core import Effect, Frame  # expect: B1
from peffect.fiber import _start  # expect: B1
from peffect.fiber import Effect as E  # expect: B1
from peffect.cause import *  # expect: B1
from .core import Park  # expect: B1
from . import fiber  # expect: B1
from peffect.core import WithFiber as WF
from peffect import Fiber

Alias = WF
pe.core.Async  # expect: B2
pe._start  # expect: B2

WF(lambda fb: fb.stack)  # expect: B3
Alias(lambda x: succeed(x.interrupted))  # expect: B3


def callback(fiber):
    return fiber.running  # expect: B3


WF(callback)


def helper(f: Fiber[Any, Any]):
    return f.children  # expect: B3


def quoted(f: "pe.Fiber[Any, Any]"):
    f._deferred = True  # expect: B3


def dynamic(f: Fiber[Any, Any]):
    return getattr(f, "stack")  # expect: B3


WF(pe.succeed)  # expect: B3
"""


def lint(*paths: Path) -> tuple[int, list[tuple[str, int, str]], int]:
    """Run the CLI; fail on anything but a clean 0/1 run whose every line parses."""
    p = subprocess.run(
        [sys.executable, SCRIPT, *map(str, paths)], cwd=ROOT, capture_output=True, text=True
    )
    assert p.returncode in (0, 1), f"lint crashed ({p.returncode}): {p.stderr}"
    assert p.stderr == ""
    *lines, last = p.stdout.splitlines()
    summary = SUMMARY.match(last)
    assert summary, f"no summary line: {p.stdout}"
    found = [m.group("path", "line", "rule") for ln in lines if (m := VIOLATION.match(ln))]
    assert len(found) == len(lines), f"unparsed output: {lines}"
    assert int(summary["n"]) == len(found) and (p.returncode == 1) == bool(found)
    return p.returncode, [(f, int(n), r) for f, n, r in found], int(summary["files"])


def probe(tmp_path: Path, name: str, src: str) -> Path:
    pkg = tmp_path / "peffect"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("")
    path = pkg / name
    path.write_text(src)
    return path


def expected(path: Path) -> list[tuple[str, int, str]]:
    lines = path.read_text().splitlines()
    marks = [(i, re.search(r"# expect: (B[123])", s)) for i, s in enumerate(lines, 1)]
    return [(str(path), i, m.group(1)) for i, m in marks if m]


def test_repo_respects_the_boundary():
    rc, found, files = lint()
    assert (rc, found) == (0, [])
    assert files >= 1  # examples/quickstart.py at least


def test_allowed_spellings_pass(tmp_path: Path):
    path = probe(tmp_path, "allowed.py", ALLOWED)
    assert lint(path) == (0, [], 1)


def test_each_forbidden_reach_is_reported_on_its_line(tmp_path: Path):
    path = probe(tmp_path, "forbidden.py", FORBIDDEN)
    rc, found, files = lint(path)
    assert (rc, files) == (1, 1)
    assert sorted(found) == expected(path)


def test_kernel_exemption_is_by_exact_path(tmp_path: Path):
    lookalike = probe(tmp_path, "core.py", "from peffect.core import Async\n")
    rc, found, files = lint(lookalike, ROOT / "src/peffect/core.py")
    assert (rc, files) == (1, 1)
    assert found == [(str(lookalike), 1, "B1")]


def test_unreadable_input_fails_instead_of_passing(tmp_path: Path):
    broken = tmp_path / "broken.py"
    broken.write_text("def (:\n")
    for path in (broken, tmp_path / "missing.py"):
        p = subprocess.run([sys.executable, SCRIPT, str(path)], capture_output=True, text=True)
        assert p.returncode == 2 and str(path) in p.stderr


def test_definition_expressions_use_enclosing_scope(tmp_path: Path):
    bad = probe(
        tmp_path,
        "defaults_bad.py",
        """
import peffect as pe

def default(pe=pe.core): pass  # expect: B2

@pe.core  # expect: B2
def decorated(pe): pass

class Derived(pe.core):  # expect: B2
    pe = None
""",
    )
    assert sorted(lint(bad)[1]) == expected(bad)
    good = probe(
        tmp_path,
        "defaults_good.py",
        """
from types import SimpleNamespace
from peffect import succeed
from peffect.core import WithFiber
f = SimpleNamespace(running=True)
WithFiber(lambda f=f.running: succeed(None))
""",
    )
    assert lint(good) == (0, [], 1)


def test_export_metadata_fails_closed(tmp_path: Path):
    script = tmp_path / "scripts" / "check_boundaries.py"
    script.parent.mkdir()
    script.write_bytes(SCRIPT.read_bytes())
    init = tmp_path / "src" / "peffect" / "__init__.py"
    init.parent.mkdir(parents=True)
    target = tmp_path / "probe.py"
    target.write_text("x = 1\n")
    for declaration in (
        "__all__ = 7",
        '__all__ = ["Effect", 42]',
        '__all__ = ["Effect"]\n__all__ += ["Fiber"]',
        '__all__ = ["Effect"]\n__all__.append("Fiber")',
        '__all__ = ["Effect"]\n__all__[0] = "Fiber"',
    ):
        init.write_text("from peffect.core import Effect\n" + declaration + "\n")
        result = subprocess.run([sys.executable, script, target], capture_output=True, text=True)
        assert result.returncode == 2, (declaration, result.stdout, result.stderr)
        assert "__all__" in result.stderr


def test_removed_fiber_export_reports_violation_not_traceback(tmp_path: Path):
    script = tmp_path / "scripts" / "check_boundaries.py"
    script.parent.mkdir()
    script.write_bytes(SCRIPT.read_bytes())
    init = tmp_path / "src" / "peffect" / "__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text('from peffect.core import Effect\n__all__ = ["Effect"]\n')
    target = tmp_path / "probe.py"
    target.write_text("from peffect.fiber import Fiber\n")
    result = subprocess.run([sys.executable, script, target], capture_output=True, text=True)
    assert result.returncode == 1, result.stderr
    assert result.stderr == ""
    assert ":1: B1" in result.stdout
