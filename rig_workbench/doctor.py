"""Read-only composition of execution selection and observed workspace context."""
import json

from .ports.local import CONSOLE, OS_ENV
from .orchestrate.recipes import load_manifest
from .orchestrate.orchestrators.base import OrchestratorUnavailable
from .orchestrate.orchestrators.config import MISSING, parse_orchestrator_config
from .orchestrate.orchestrators.credentials import capture_t3_token, t3_token
from .orchestrate.orchestrators.selection import close_orchestrator, select_orchestrator
from .workbench.orca import report as orca_report


class _Diagnostics:
    def __init__(self):
        self.lines = []

    def out(self, text=""):
        self.lines.append(text)

    def err(self, text=""):
        self.lines.append(text)


def _redact(value, token):
    if isinstance(value, str):
        return value.replace(token, "[redacted]") if token else value
    if isinstance(value, dict):
        return {key: _redact(item, token) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, token) for item in value]
    return value


def main(args, *, out=CONSOLE, env=OS_ENV, factory=None):
    """All diagnostic failures, including argument failures, return zero."""
    capture_t3_token()
    if ("--help" in args or "-h" in args) and "--json" not in args:
        out.out("usage: rig-wb doctor [--json] [--orchestrator auto|native|t3]")
        return 0
    diagnostics = _Diagnostics()
    errors = []
    choice = None
    cursor = 0
    while cursor < len(args):
        flag = args[cursor]
        if flag in ("--json", "--help", "-h"):
            cursor += 1
        elif flag == "--orchestrator" and cursor + 1 < len(args) and args[cursor + 1] in ("native", "auto", "t3"):
            choice = args[cursor + 1]
            cursor += 2
        else:
            errors.append("invalid_arguments")
            break
    selection = None
    preferred, selected_by, fallback = choice or "auto", "cli" if choice else "default", "native"
    t3 = {"available": None, "reason_code": "not_probed", "detail": "not probed", "capabilities": []}
    try:
        manifest = load_manifest(read_only=True, out=diagnostics, env=env)
        value = manifest.get("orchestrator", MISSING)
        config = parse_orchestrator_config(value)
        preferred = choice or config.preferred
        selected_by = "cli" if choice else "default" if value is MISSING else "manifest"
        fallback = config.fallback
        if not errors:
            selection = select_orchestrator(value, cli=choice, env=env, factory=factory, emit=False)
            if preferred == "native":
                t3["detail"] = "not probed (native requested)"
            else:
                availability = selection.availability
                t3.update(available=selection.name == "t3", reason_code=availability.reason_code,
                          detail=availability.detail,
                          capabilities=sorted(selection.orchestrator.capabilities()) if selection.name == "t3" else [])
                if selection.name == "t3":
                    for note in ("MCP connected", "compatible contract", "live execution unverified"):
                        if note not in t3["detail"]:
                            t3["detail"] += "; " + note
    except ValueError:
        errors.append("invalid_orchestrator_config")
    except OrchestratorUnavailable as error:
        errors.append(error.reason_code)
        t3.update(available=False, reason_code=error.reason_code, detail="T3 selection unavailable")
    except Exception:
        errors.append("diagnosis_failed")
    except SystemExit:
        errors.append("manifest_read_failed")
    finally:
        close_orchestrator(selection.orchestrator if selection else None)
    try:
        orca = orca_report(env.snapshot())
    except Exception:
        errors.append("workspace_diagnosis_failed")
        orca = {"session": {"observed": False, "present": None},
                "cli": {"observed": False, "reason": "CLI not probed"}}
    data = {
        "schema_version": 1,
        "execution_backends": {
            "native": {"available": True, "reason_code": "builtin", "detail": "Rig Native Runtime",
                       "capabilities": ["agent.parallel", "agent.run"]}, "t3": t3},
        "workspace_runtimes": {"orca": orca},
        "selection": {"preferred": preferred, "selected_by": selected_by,
                      "active": selection.name if selection else None, "fallback": fallback,
                      "effective_fallback": "disabled by explicit t3" if preferred == "t3" else fallback},
        "errors": errors,
        "reasons": diagnostics.lines,
    }
    data = _redact(data, t3_token(env))
    if "--json" in args:
        out.out(json.dumps(data, ensure_ascii=False))
    else:
        out.out("Execution backends")
        out.out("✓ Rig Native Runtime (orchestrator: native)")
        out.out(f"{'✓' if t3['available'] else '○'} T3 Code: {data['execution_backends']['t3']['detail']}")
        orca = data["workspace_runtimes"]["orca"]
        session = "not observed" if not orca["session"]["observed"] else "detected" if orca["session"]["present"] else "not detected"
        out.out(f"○ Orca (workspace runtime): session {session}; CLI not probed")
        out.out(f"Active orchestrator: {data['selection']['active'] or 'unavailable'} (selection for a new run)")
        out.out(f"Fallback: {data['selection']['effective_fallback']}")
        out.out("Individual role/cwd compatibility and live T3 execution are unverified.")
        for reason in data["reasons"] + data["errors"]:
            out.out(reason)
    return 0
