"""Selection names and shared CLI options describe the execution orchestrator."""
import ast
import inspect

from rig_workbench.orchestrate import commands, runstate
from rig_workbench.orchestrate.orchestrators.base import Availability
from rig_workbench.orchestrate.orchestrators import selection


def test_selection_exposes_orchestrator_and_closes_its_client(tmp_path):
    class Orchestrator:
        name = "t3"
        project_id = "project"

        def __init__(self):
            self.client = self
            self.closed = 0

        def capabilities(self):
            return frozenset({"agent.run"})

        def close(self):
            self.closed += 1

    orchestrator = Orchestrator()
    selected = selection.Selection("t3", "t3", "cli", "none",
                                   Availability(True, "compatible", "compatible"),
                                   orchestrator=orchestrator,
                                   settings=selection.T3Settings("http://localhost/mcp", "secret"))
    state = runstate.new_state("selection", [], None)
    bridge = selection.bind_selection(selected, state, tmp_path / "state.json", runstate.save_state)
    assert selected.orchestrator is orchestrator and bridge.backend is orchestrator
    assert state["orchestrator"]["ref"]["t3"]["project_id"] == "project"
    selection.close_orchestrator(selected.orchestrator)
    assert orchestrator.closed == 1


def test_orchestrator_value_option_has_one_flag_table_definition():
    tree = ast.parse(inspect.getsource(commands))
    declarations = [node for node in ast.walk(tree) if isinstance(node, ast.Set)
                    and any(isinstance(item, ast.Constant) and item.value == "--orchestrator"
                            for item in node.elts)]
    assert len(declarations) == 1
    assert commands._ORCHESTRATOR_VALUE_FLAGS <= commands._RUN_VALUE_FLAGS
