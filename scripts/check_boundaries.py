"""Kernel boundary lint: code outside the kernel uses peffect's public surface only.

    uv run python scripts/check_boundaries.py [PATH ...]

PATHs are files or directories (searched recursively for *.py). The default is src/peffect and
examples/. The kernel files (src/peffect/{core,fiber,cause,__init__}.py, by exact path) are exempt.
Exit 0: no violations. 1: violations, one `path:line: RULE message` per line. 2: a path could not
be read or parsed, or the export list is malformed. The contract (B1-B3) and the data flow this
check cannot follow are in docs/INVARIANTS.md.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "src" / "peffect"
KERNEL_FILES = frozenset(PKG / f for f in ("core.py", "fiber.py", "cause.py", "__init__.py"))
KERNEL_MODULES = frozenset({"peffect.core", "peffect.fiber", "peffect.cause"})
ESCAPE_HATCH = ("peffect.core", "WithFiber")
# The Fiber members code outside the kernel may touch (INVARIANTS.md B3). Everything else on a
# Fiber is run-loop state or an unsafe primitive, even when its name has no underscore.
FIBER_PUBLIC = frozenset({"context", "await_", "join", "interrupt"})

type Kind = Literal["peffect", "withfiber", "fibertype", "fiber", "def", "other"]
type Scoped = ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef


class InputError(Exception):
    pass


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule} {self.detail}"


def load_exports(init: Path) -> dict[str, str]:
    """Public name -> the kernel module it is re-exported from, read from __init__.py."""
    tree = parse(init)
    source: dict[str, str] = {}
    public: list[str] | None = None
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for a in node.names:
                source[a.asname or a.name] = node.module
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            if public is not None or len(node.targets) != 1:
                raise InputError(f"{init}: __all__ requires one literal declaration")
            if not isinstance(node.value, ast.List | ast.Tuple):
                raise InputError(f"{init}: __all__ must be a literal list or tuple of strings")
            public = []
            for element in node.value.elts:
                if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                    raise InputError(f"{init}: __all__ entries must be string literals")
                public.append(element.value)
        elif any(isinstance(n, ast.Name) and n.id == "__all__" for n in ast.walk(node)):
            raise InputError(f"{init}: __all__ must not be mutated or computed")
    if public is None:
        raise InputError(f"{init}: no __all__ list")
    missing = [n for n in public if n not in source]
    if missing:
        raise InputError(f"{init}: __all__ names without a from-import: {missing}")
    return {n: source[n] for n in public}


def parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as e:
        raise InputError(f"{path}: cannot read or parse: {e}") from e


def package_of(path: Path) -> list[str]:
    """Dotted package containing `path`, found by walking up through __init__.py files."""
    parts: list[str] = []
    d = path.resolve().parent
    while (d / "__init__.py").exists():
        parts.insert(0, d.name)
        d = d.parent
    return parts


def bound_names(node: Scoped) -> list[tuple[str, ast.AST]]:
    """Every (name, binding node) of a scope, without entering nested scopes."""
    out: list[tuple[str, ast.AST]] = []
    if isinstance(node, ast.Lambda | ast.FunctionDef | ast.AsyncFunctionDef):
        a = node.args
        out += [(p.arg, p) for p in [*a.posonlyargs, *a.args, *a.kwonlyargs]]
        out += [(p.arg, p) for p in (a.vararg, a.kwarg) if p]
    if isinstance(node, ast.Lambda):
        return out
    todo: list[ast.AST] = list(node.body)
    while todo:
        n = todo.pop(0)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out.append((n.name, n))
            continue
        if isinstance(n, ast.Lambda):
            continue
        if isinstance(n, ast.Import | ast.ImportFrom):
            out += [((a.asname or a.name).split(".")[0], n) for a in n.names]
        elif isinstance(n, ast.Assign):  # keep the Assign so `Alias = WithFiber` resolves
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out.append((t.id, n))
                else:
                    todo.append(t)
            todo.append(n.value)
            continue
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.append((n.id, n))
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.append((n.name, n))
        todo += list(ast.iter_child_nodes(n))
    return out


class Checker(ast.NodeVisitor):
    """Two passes over one file. Pass 1 finds the callbacks handed to WithFiber; pass 2 reports."""

    def __init__(self, path: Path, exports: dict[str, str]) -> None:
        self.path = path
        self.exports = exports
        self.package = package_of(path)
        self.scopes: list[tuple[Scoped, dict[str, Kind]]] = []
        self.callbacks: set[ast.AST] = set()
        self.report = False
        self.found: list[Violation] = []

    def run(self, tree: ast.Module) -> list[Violation]:
        self.visit(tree)
        self.report = True
        self.visit(tree)
        return self.found

    def flag(self, node: ast.stmt | ast.expr, rule: str, detail: str) -> None:
        if self.report:
            self.found.append(Violation(str(self.path), node.lineno, rule, detail))

    # -- scopes ------------------------------------------------------------------------------------

    def resolve(self, name: str) -> Kind | None:
        for i, (node, kinds) in enumerate(reversed(self.scopes)):
            if isinstance(node, ast.ClassDef) and i > 0:
                continue  # class bodies are not visible to the functions inside them
            if name in kinds:
                return kinds[name]
        return None

    def kind_of(self, name: str, binding: ast.AST, scope: Scoped) -> Kind:
        if isinstance(binding, ast.arg):
            callback = scope if isinstance(scope, ast.FunctionDef | ast.Lambda) else None
            if callback in self.callbacks and callback and binding is first_param(callback):
                return "fiber"
            return (
                "fiber"
                if binding.annotation and self.is_fiber_type(binding.annotation)
                else "other"
            )
        if isinstance(binding, ast.FunctionDef | ast.AsyncFunctionDef):
            return "def"
        if isinstance(binding, ast.Import):
            for a in binding.names:
                if a.asname == name:
                    return "peffect" if a.name == "peffect" else "other"
                if not a.asname and a.name.split(".")[0] == name == "peffect":
                    return "peffect"
            return "other"
        if isinstance(binding, ast.ImportFrom):
            module = self.absolute(binding)
            for a in binding.names:
                if (a.asname or a.name) != name:
                    continue
                if (module, a.name) == ESCAPE_HATCH:
                    return "withfiber"
                if a.name == "Fiber" and module in ("peffect", self.exports["Fiber"]):
                    return "fibertype"
            return "other"
        if isinstance(binding, ast.Assign) and isinstance(binding.value, ast.Name):
            alias = self.resolve(binding.value.id)  # `Alias = WithFiber`
            if alias in ("withfiber", "fibertype"):
                return alias
        return "other"

    def enter(self, scope: Scoped) -> None:
        kinds: dict[str, Kind] = {}
        self.scopes.append((scope, kinds))
        for name, binding in bound_names(scope):
            kind = self.kind_of(name, binding, scope)
            kinds[name] = kind if kinds.get(name, kind) == kind else "other"

    def scoped(self, node: Scoped) -> None:
        # Defaults, decorators and class bases are evaluated before entering the new scope.
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for decorator in node.decorator_list:
                self.visit(decorator)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            self.visit(node.args)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns:
                self.visit(node.returns)
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword.value)
        self.enter(node)
        if isinstance(node, ast.Lambda):
            self.visit(node.body)
        else:
            for statement in node.body:
                self.visit(statement)
        self.scopes.pop()

    visit_Module = visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = visit_ClassDef = (
        scoped
    )

    def is_fiber_type(self, ann: ast.expr) -> bool:
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                ann = ast.parse(ann.value, mode="eval").body
            except SyntaxError:
                return False
        if isinstance(ann, ast.Subscript):
            ann = ann.value
        if isinstance(ann, ast.Name):
            return self.resolve(ann.id) == "fibertype"
        if isinstance(ann, ast.Attribute) and isinstance(ann.value, ast.Name):
            return ann.attr == "Fiber" and self.resolve(ann.value.id) == "peffect"
        return False

    # -- B1: imports -------------------------------------------------------------------------------

    def absolute(self, node: ast.ImportFrom) -> str | None:
        if node.level == 0:
            return node.module
        if node.level > len(self.package):
            return None
        base = self.package[: len(self.package) - node.level + 1]
        return ".".join([*base, *([node.module] if node.module else [])])

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if a.name in KERNEL_MODULES or a.name.startswith(
                tuple(m + "." for m in KERNEL_MODULES)
            ):
                self.flag(node, "B1", f"`import {a.name}`: import public names from it instead")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = self.absolute(node)
        if module is None:
            self.flag(node, "B1", "relative import outside a package")
            return
        for a in node.names:
            if module == "peffect" and a.name not in self.exports and a.name != "*":
                self.flag(node, "B1", f"`{a.name}` is not in peffect.__all__")
            elif module in KERNEL_MODULES and (module, a.name) != ESCAPE_HATCH:
                if self.exports.get(a.name) != module:
                    self.flag(node, "B1", f"`{a.name}` from {module} is kernel-internal")

    # -- B2, B3: attribute reach ------------------------------------------------------------------

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.value, ast.Name):
            kind = self.resolve(node.value.id)
            if kind == "fiber" and node.attr not in FIBER_PUBLIC:
                self.flag(node, "B3", f"Fiber.{node.attr} is not public (allowed: {_fp()})")
            elif kind == "peffect" and node.attr not in self.exports:
                self.flag(node, "B2", f"peffect.{node.attr} is not in peffect.__all__")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        kind = self.resolve(func.id) if isinstance(func, ast.Name) else None
        if kind == "withfiber":
            args = [*node.args[:1], *[k.value for k in node.keywords if k.arg == "f"]]
            for cb in args:
                target = self.callback_def(cb)
                if target is None:
                    self.flag(node, "B3", "WithFiber callback must be a lambda or a named def")
                else:
                    self.callbacks.add(target)
        if isinstance(func, ast.Name) and func.id in ("getattr", "setattr", "delattr", "vars"):
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Name) and self.resolve(first.id) == "fiber":
                self.flag(node, "B3", f"`{func.id}` on a Fiber: name the member directly")
        self.generic_visit(node)

    def callback_def(self, cb: ast.expr) -> ast.AST | None:
        if isinstance(cb, ast.Lambda):
            return cb
        if isinstance(cb, ast.Name) and self.resolve(cb.id) == "def":
            for node, kinds in reversed(self.scopes):
                if cb.id in kinds:
                    for name, binding in bound_names(node):
                        if name == cb.id and isinstance(binding, ast.FunctionDef):
                            return binding
                    return None
        return None


def first_param(fn: ast.FunctionDef | ast.Lambda) -> ast.arg | None:
    params = [*fn.args.posonlyargs, *fn.args.args]
    return params[0] if params else None


def _fp() -> str:
    return ", ".join(sorted(FIBER_PUBLIC))


def files_under(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if not p.exists():
            raise InputError(f"{p}: no such file or directory")
        found = sorted(p.rglob("*.py")) if p.is_dir() else [p]
        out += [f for f in found if f.resolve() not in KERNEL_FILES]
    return out


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or [PKG, ROOT / "examples"]
    try:
        exports = load_exports(PKG / "__init__.py")
        files = files_under(paths)
        found = [v for f in files for v in Checker(f, exports).run(parse(f))]
    except InputError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    for v in found:
        print(v)
    print(f"boundaries: {len(files)} files checked, {len(found)} violations")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
