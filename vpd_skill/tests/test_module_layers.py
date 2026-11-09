"""Lower layers must not import upper layers, including through function-local imports.

The import graph used to hide a 12-module cycle behind lazy imports: dispatch,
unlock and sysdialog reached into verdict for error attribution. Shared outcome
helpers now live in vpdtest/outcomes.py. This guard keeps the direction fixed.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "vpdtest"

# Modules that execute or observe actions; they may not depend on judgement,
# asset lifecycle, sessions or orchestration.
LOWER = ("outcomes", "dispatch", "unlock", "sysdialog", "observe", "tree", "util", "devices")
UPPER = ("verdict", "cases", "session", "execute", "runner", "commands", "conditions", "readrefs")


def _module(path):
    rel = path.relative_to(ROOT).with_suffix("")
    return ".".join(p for p in rel.parts if p != "__init__")


def _imports(path):
    package = _module(path).split(".")[:-1] if path.name != "__init__.py" else _module(path).split(".")
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = package[:len(package) - (node.level - 1)] if node.level > 1 else package
            target = ".".join([*base, node.module] if node.module else base)
            yield node.lineno, target, [a.name for a in node.names]


def test_lower_layers_do_not_import_upper_layers():
    violations = []
    for path in ROOT.rglob("*.py"):
        module = _module(path)
        if module.split(".")[0] not in LOWER:
            continue
        for line, target, names in _imports(path):
            targets = [target] + [f"{target}.{n}" if target else n for n in names]
            hits = [t for t in targets if t.split(".")[0] in UPPER]
            if hits:
                violations.append(f"{path.relative_to(ROOT)}:{line} -> {hits[0]}")
    assert violations == []


def test_shared_outcome_names_have_one_definition():
    names = {"PASSED", "error_attribution", "collection_error", "execution_error"}
    defined = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                defined.append(f"{path.name}:{node.name}")
            elif isinstance(node, ast.Assign):
                defined += [f"{path.name}:{t.id}" for t in node.targets if isinstance(t, ast.Name) and t.id in names]
    assert sorted(defined) == sorted(f"outcomes.py:{n}" for n in names)
