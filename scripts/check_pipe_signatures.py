"""Check the two precise pipe overload families against their shared 0..20 contract.

Source only; never imports peffect. Run: uv run python scripts/check_pipe_signatures.py
Separate declarations preserve inference without a runtime mixin or generated source.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIVERS = {"Effect": "Effect[A_co, E_co, R_co]", "Layer": "Layer[ROut, E, RIn]"}


def check(source: str, owner: str) -> None:
    tree = ast.parse(source)
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner]
    if len(classes) != 1:
        raise ValueError(f"{owner}: expected exactly one class")
    methods = [
        node
        for node in classes[0].body
        if isinstance(node, ast.FunctionDef) and node.name == "pipe"
    ]
    if len(methods) != 22:
        raise ValueError(f"{owner}.pipe: expected 21 overloads and one implementation")
    receiver = RECEIVERS[owner]
    for arity, method in enumerate(methods[:-1]):
        types = [f"T{i}" for i in range(1, arity + 1)]
        params = ["self"] + [
            f"f{i}: _F[{receiver if i == 1 else f'T{i - 1}'}, T{i}]" for i in range(1, arity + 1)
        ]
        generics = f"[{', '.join(types)}]" if types else ""
        expected = ast.parse(
            f"@overload\ndef pipe{generics}({', '.join(params)}, /) -> "
            f"{types[-1] if types else receiver}: ..."
        ).body[0]
        if ast.dump(method) != ast.dump(expected):
            raise ValueError(f"{owner}.pipe: overload {arity} differs from the chained contract")
    if methods[-1].decorator_list:
        raise ValueError(f"{owner}.pipe: implementation must not be another overload")


def main() -> None:
    for owner, module in (("Effect", "core"), ("Layer", "layer")):
        check((ROOT / f"src/peffect/{module}.py").read_text(), owner)
    print("Effect/Layer pipe: exact 0..20 overload contracts match")


if __name__ == "__main__":
    main()
