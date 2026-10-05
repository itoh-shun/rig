"""Enforce execution/workspace separation and lazy optional transport imports."""
import ast
import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "rig_workbench" / "orchestrate" / "orchestrators"
OPTIONAL = {"rig_workbench.orchestrate.orchestrators.t3",
            "rig_workbench.orchestrate.orchestrators.t3_client",
            "rig_workbench.orchestrate.orchestrators.t3_contract", "mcp"}
OPTIONAL_IMPORT_BLOCKER = '''
import importlib.abc, sys
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "mcp" or fullname.startswith("mcp.") or fullname.startswith("rig_workbench.orchestrate.orchestrators.t3"):
            raise AssertionError("optional import attempted: " + fullname)
sys.meta_path.insert(0, Blocker())
'''


def _imports(path, *, root=ROOT):
    module = ".".join(path.relative_to(root).with_suffix("").parts)
    package = module.removesuffix(".__init__") if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    tree = ast.parse(path.read_text())
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = importlib.util.resolve_name("." * node.level + (node.module or ""), package) if node.level else node.module or ""
            names = [base, *(base + "." + alias.name for alias in node.names)]
        elif isinstance(node, ast.Call) and (
            isinstance(node.func, ast.Name) and node.func.id == "__import__"
            or isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
        ):
            literal = node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
            if not literal:
                assert path.parent != PACKAGE, f"{path}:{node.lineno}: computed dynamic import is prohibited"
                continue
            name = node.args[0].value
            if name.startswith("."):
                assert len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                name = importlib.util.resolve_name(name, node.args[1].value)
            names = [name]
        owner = node
        function = None
        while owner in parents:
            owner = parents[owner]
            if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = owner.name
                break
        for name in names:
            yield name, function, node


def _assert_optional_import_boundaries(path, *, root=ROOT):
    for name, owner, node in _imports(path, root=root):
        optional = any(name == item or name.startswith(item + ".") for item in OPTIONAL)
        if path.stem in {"base", "native", "bridge", "config", "__init__"}:
            assert not optional, (path, node.lineno, name)
        if name.startswith("rig_workbench.orchestrate.orchestrators.t3") and path.stem in {"selection", "__init__"}:
            assert owner == "_load_t3", (path, node.lineno, name)
        if name == "mcp" or name.startswith("mcp."):
            assert path.stem == "t3_client" and owner == "_sdk_factory", (path, node.lineno, name)


def test_orchestrators_never_import_workspace_runtimes():
    for path in PACKAGE.rglob("*.py"):
        for name, _, node in _imports(path):
            assert not any(name == forbidden or name.startswith(forbidden + ".") for forbidden in (
                "rig_workbench.workbench.runtime", "rig_workbench.workbench.orca")), (path, node.lineno, name)
            assert name.rsplit(".", 1)[-1] not in {"WorktreeBackend", "WorktreeHandle"}, (path, node.lineno, name)


def test_native_and_core_never_import_t3_or_mcp():
    graph = {}
    for path in (ROOT / "rig_workbench").rglob("*.py"):
        module = ".".join(path.relative_to(ROOT).with_suffix("").parts).removesuffix(".__init__")
        graph[module] = {name for name, _, _ in _imports(path)}
    for path in PACKAGE.rglob("*.py"):
        _assert_optional_import_boundaries(path)
    # Follow all declared internal package dependencies, including function-local imports.
    for stem in ("base", "native", "bridge"):
        pending, visited = [f"rig_workbench.orchestrate.orchestrators.{stem}"], set()
        while pending:
            name = pending.pop()
            if name in visited:
                continue
            visited.add(name)
            assert not any(name == item or name.startswith(item + ".") for item in OPTIONAL)
            pending.extend(graph.get(name, ()))


@pytest.mark.parametrize(("stem", "source"), [
    ("native", "def run():\n    from .t3 import x\n"),
    ("bridge", "import rig_workbench.orchestrate.orchestrators.t3\n"),
    ("selection", "from .t3 import x\n"),
    ("selection", "def _load_t3():\n    def nested():\n        from .t3 import x\n"),
])
def test_ast_optional_import_boundaries_detect_forbidden_synthetic_imports(tmp_path, stem, source):
    path = tmp_path / "rig_workbench" / "orchestrate" / "orchestrators" / f"{stem}.py"
    path.parent.mkdir(parents=True)
    path.write_text(source)
    with pytest.raises(AssertionError, match="rig_workbench.orchestrate.orchestrators.t3"):
        _assert_optional_import_boundaries(path, root=tmp_path)


def test_ast_optional_import_boundaries_allow_only_innermost_t3_loader(tmp_path):
    path = tmp_path / "rig_workbench" / "orchestrate" / "orchestrators" / "selection.py"
    path.parent.mkdir(parents=True)
    path.write_text("def outer():\n    def _load_t3():\n        from .t3 import x\n")
    _assert_optional_import_boundaries(path, root=tmp_path)


@pytest.mark.parametrize("module", ["mcp", "rig_workbench.orchestrate.orchestrators.t3"])
def test_fresh_process_optional_import_blocker_rejects_deliberate_imports(tmp_path, module):
    result = subprocess.run(
        [sys.executable, "-c", OPTIONAL_IMPORT_BLOCKER + f"\nimport {module}\n"],
        cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(ROOT)),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert "optional import attempted: " + module in result.stderr


@pytest.mark.parametrize("choice", ["native", "auto"])
def test_fresh_native_and_unconfigured_auto_processes_do_not_load_optional_modules(tmp_path, choice):
    source = OPTIONAL_IMPORT_BLOCKER + '''
import json, pathlib
from rig_workbench.orchestrate.commands import cmd_run
try:
    cmd_run(["feature", "--provider", "mock", "--verifier-provider", "mock", "--orchestrator", sys.argv[1], "--out", "state.json", "--max-steps", "1"])
except SystemExit as result:
    assert result.code == 0, result.code
state = json.loads(pathlib.Path("state.json").read_text())
assert state["orchestrator"]["name"] == "native"
assert not any(name == "mcp" or name.startswith("mcp.") or name.startswith("rig_workbench.orchestrate.orchestrators.t3") for name in sys.modules)
'''
    env = dict(os.environ, PYTHONPATH=str(ROOT), RIG_HOME=str(ROOT), RIG_SKIP_GH_CHECK="1")
    for key in ("RIG_T3_MCP_URL", "RIG_T3_MCP_TOKEN", "RIG_T3_PROJECT_ID"):
        env.pop(key, None)
    result = subprocess.run([sys.executable, "-c", source, choice], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    if choice == "auto":
        assert "falling back to native" in result.stderr
