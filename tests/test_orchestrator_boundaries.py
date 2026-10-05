"""Optional transport imports must never enter Native execution."""
import ast
import importlib.util
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PACKAGE = "rig_workbench.orchestrate.orchestrators"
MODULES = ROOT / "rig_workbench/orchestrate/orchestrators"


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (item.name for item in node.names)
        if isinstance(node, ast.ImportFrom):
            base = importlib.util.resolve_name("." * node.level + (node.module or ""), PACKAGE) if node.level else node.module or ""
            yield base
            yield from (f"{base}.{item.name}" for item in node.names)


def test_orchestrators_never_import_workspace_runtimes():
    for path in MODULES.glob("*.py"):
        for imported in _imports(path):
            assert not imported.startswith(("rig_workbench.workbench.runtime", "rig_workbench.workbench.orca")), (path, imported)
            assert imported.rsplit(".", 1)[-1] not in {"WorktreeBackend", "WorktreeHandle"}


def test_native_and_core_never_import_t3_or_mcp():
    for name in ("__init__", "base", "config", "native", "bridge"):
        for imported in _imports(MODULES / f"{name}.py"):
            assert not imported.startswith("mcp")
            assert not imported.startswith(f"{PACKAGE}.t3")


def test_fresh_native_process_does_not_load_optional_modules():
    source = '''
import importlib.abc
import sys
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("mcp", "rig_workbench.orchestrate.orchestrators.t3")):
            raise AssertionError("optional integration imported: " + fullname)
sys.meta_path.insert(0, BlockOptional())
from rig_workbench.orchestrate.providers import run_provider
from rig_workbench.orchestrate.orchestrators.bridge import AgentExecutionBridge
assert run_provider("mock", "generator", "work", {"_orchestrator_bridge": AgentExecutionBridge()})[0] == 0
assert not any(name.startswith(("mcp", "rig_workbench.orchestrate.orchestrators.t3")) for name in sys.modules)
'''
    result = subprocess.run([sys.executable, "-c", source], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
