"""Being inside Orca is not the same as Orca's CLI working (#472).

Both were measured true and false respectively on the same host, in the same shell, at the
same moment. Every test here exists to keep those two apart, because a detector that
conflates them hands `--runtime orca` a backend that dies in `create()`.
"""

import ast
import pathlib
import subprocess
import sys

import pytest

from rig_workbench.workbench import orca
from rig_workbench.workbench.orca import WORKSPACE_VAR, WORKTREE_VAR, detect, report

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_UUID = "d6d9468d-9473-4ddf-a688-cfe5d45ec3da"


def _env(worktree=None, workspace=None):
    env = {}
    if worktree is not None:
        env[WORKTREE_VAR] = worktree
    if workspace is not None:
        env[WORKSPACE_VAR] = workspace
    return env


# ── what the environment says ────────────────────────────────────────────────
def test_no_orca_variables_means_no_session(monkeypatch):
    """An explicitly empty mapping means *this* environment, not the ambient one.

    Asserted with the real variables set, because otherwise the test passes on a host that
    has no Orca session for a reason that has nothing to do with the code — and fails to
    notice a `detect` that consults `os.environ` whenever the caller's mapping is falsy.
    The distinction is the whole reason the parameter exists: every other test here passes
    a constructed environment and would be reading the developer's own session without it.
    """
    monkeypatch.setenv(WORKTREE_VAR, f"{_UUID}::/somewhere/real")
    monkeypatch.setenv(WORKSPACE_VAR, f"{_UUID}::/somewhere/real")
    assert detect(_env()) is None
    assert detect(None) is not None   # …and the ambient one is still read when asked for


def test_the_shape_orca_actually_exports_is_read(monkeypatch):
    """`<uuid>::<absolute path>`, taken verbatim from a live session rather than a guess."""
    session = detect(_env(worktree=f"{_UUID}::/home/itoshun/works/rig",
                          workspace=f"{_UUID}::/home/itoshun/works/rig"))
    assert session.worktree_id == _UUID
    assert session.workspace_id == _UUID
    assert session.worktree_path == "/home/itoshun/works/rig"


def test_one_variable_is_enough_to_say_there_is_a_session():
    """Either name is evidence. Requiring both would report "no session" for a version of
    Orca that exports one of them, which is a claim about Orca this code cannot make."""
    assert detect(_env(workspace=f"{_UUID}::/x")) is not None
    assert detect(_env(worktree=f"{_UUID}::/x")) is not None


def test_an_empty_value_is_the_same_as_an_absent_one():
    """An exported-but-empty variable is how a shell says nothing, not how it says Orca."""
    assert detect(_env(worktree="", workspace="")) is None


# ── a value it cannot read is not a value it invents ─────────────────────────
def test_a_value_with_no_separator_is_not_read_as_a_bare_identifier():
    """rig has one host's worth of evidence about this format and has never seen Orca
    export an id without a path. Keeping the blob as `worktree_id` would be inventing an
    identifier out of a string that failed to be one — and `as_ref()` exists to be recorded.
    """
    session = detect(_env(worktree="just-an-id"))
    assert session.worktree_id is None
    assert session.worktree_path is None


def test_a_relative_path_is_not_taken_as_a_path():
    """A fragment is worse than nothing here: it is the kind of value something later joins
    onto and writes to, and it would resolve against whatever directory that code ran in."""
    assert detect(_env(worktree=f"{_UUID}::relative/path")).worktree_path is None


def test_an_id_with_an_empty_path_yields_no_path():
    assert detect(_env(worktree=f"{_UUID}::")).worktree_path is None


def test_a_path_with_no_id_in_front_of_it_is_not_a_readable_value(monkeypatch):
    """`::/home/itoshun/works/rig` has a perfectly good path and no worktree to attach it
    to. Taking the path anyway would hand a later caller an identifier of `""` — which
    names nothing, and which a backend would carry into `ref` and eventually into a record
    of where a task's work happened."""
    session = detect(_env(worktree="::/home/itoshun/works/rig"))
    assert session is not None
    assert session.worktree_path is None
    assert session.worktree_id is None


def test_a_path_containing_the_separator_survives_whole():
    """Split once from the left. Splitting on every occurrence would truncate a real path
    at its first `::`, and the result would still look like a path."""
    session = detect(_env(worktree=f"{_UUID}::/home/a::b/rig"))
    assert session.worktree_path == "/home/a::b/rig"


def test_a_session_that_could_not_be_parsed_is_still_a_session():
    """Three distinct states, and this one sits between the other two: there is no session;
    there is one and here is its identity; there is one and its identity could not be read.
    That a variable was set is measured. What it contained is not claimed."""
    session = detect(_env(worktree="garbage"))
    assert session is not None
    assert session.worktree_id is None
    assert session.worktree_path is None


def test_one_unreadable_variable_does_not_spoil_the_other(monkeypatch):
    """The mutation the reviewer named that no test would have caught: with both variables
    empty `detect` returns before `_split` is ever reached, so `_split`'s own handling of an
    empty value was only ever exercised through a path that never called it."""
    session = detect(_env(worktree="", workspace=f"{_UUID}::/x"))
    assert session is not None
    assert session.worktree_id is None
    assert session.workspace_id == _UUID


def test_an_identifier_that_can_lie_about_itself_is_not_an_identifier():
    """The rule `--caller` is held to (#429). A zero-width or bidi character inside an id
    that `as_ref()` will put into a record lets the record read as something it is not, and
    the id is the half of this value that gets stored and compared."""
    assert detect(_env(worktree=f"{_UUID}\u200b::/x")).worktree_id is None
    assert detect(_env(worktree=f"{_UUID}\n::/x")).worktree_id is None
    # …and an ordinary id still reads normally, so the guard is not simply refusing everything.
    assert detect(_env(worktree=f"{_UUID}::/x")).worktree_id == _UUID


def test_an_id_that_is_not_a_uuid_is_still_an_id():
    """Declined from review, deliberately. The id was a UUID on the one host this was
    measured on, and enforcing that from a single observation would report "no session" for
    a real one the day Orca spells its ids differently — the same over-claim this module
    exists to avoid, pointing the other way. What is enforced is the property rig needs and
    can justify: that an id reaching a record cannot lie about itself."""
    assert detect(_env(worktree="not-a-uuid::/x")).worktree_id == "not-a-uuid"


# ── the two axes stay apart ──────────────────────────────────────────────────
def test_the_cli_is_reported_as_unmeasured_not_as_working():
    """The load-bearing test. On the host this was written on, the session variables were
    present and every `orca` subcommand failed straight after its handshake. A report that
    let `session.present` stand in for the CLI would have said that host was ready."""
    result = report(_env(worktree=f"{_UUID}::/x"))
    assert result["session"]["present"] is True
    assert result["cli"]["observed"] is False
    assert "reason" in result["cli"]
    # `observed: false` and not `usable: false` — nobody looked, which is not a verdict.
    assert "usable" not in result["cli"]


def test_the_session_axis_says_it_was_measured_even_when_there_is_none():
    """Absence found by looking is a different fact from absence nobody checked, and this
    module is entitled to claim the first one."""
    result = report(_env())
    assert result["session"]["observed"] is True
    assert result["session"]["present"] is False


def test_the_reference_shape_carries_what_a_backend_would_need():
    session = detect(_env(worktree=f"{_UUID}::/x", workspace=f"{_UUID}::/x"))
    assert session.as_ref() == {"workspace_id": _UUID, "worktree_id": _UUID,
                                "worktree_path": "/x"}


# ── detection starts nothing ─────────────────────────────────────────────────
#: Everything detection is allowed to import. Kept as an allowlist because the question it
#: answers — *what does this module depend on* — is one the grammar does answer honestly.
#: The question it used to be asked, *does this module start a process*, is not; three
#: rounds of review walked past three different spellings, and the fix is below.
_ALLOWED_IMPORTS = {"__future__", "annotations", "dataclasses", "os", "injection",
                    "INVISIBLE_RE"}

#: Everything the probe refuses. `socket.__new__` and not `socket.socket`: CPython emits the
#: former when a socket is constructed, and listing a name no interpreter ever raises is a
#: guard that reads as coverage and is not. `open` is on the list outright — see below for
#: why that is possible.
_REFUSED = ("subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.fork",
            "os.posix_spawn", "socket.__new__", "socket.socket", "socket.connect",
            "socket.getaddrinfo", "socket.gethostbyname", "urllib.Request", "open")

#: The module is read and compiled, and its dependencies imported, **before** the hook goes
#: on; then its compiled code runs under it. Nothing the loader does happens inside the
#: window, so `open` needs no exception and the probe needs no way to tell a loader's read
#: from the module's own — a distinction three rounds of review kept finding holes in,
#: because `open("anything.py")` looks exactly like a loader reading code.
_AUDIT_PROBE = """
import sys, types, pathlib

REFUSED = %r

# Everything the module legitimately needs, resolved while reading files is still allowed.
import dataclasses, os
from rig_workbench.workbench import injection
from rig_workbench.workbench import orca as _located

source = pathlib.Path(_located.__file__).read_text(encoding="utf-8")
code = compile(source, _located.__file__, "exec")

def hook(event, args):
    if event.startswith(REFUSED):
        raise RuntimeError("detection reached for " + event)

sys.addaudithook(hook)

module = types.ModuleType("orca_under_audit")
module.__dict__["__name__"] = "rig_workbench.workbench.orca"
module.__dict__["__package__"] = "rig_workbench.workbench"
module.__dict__["__file__"] = _located.__file__
exec(code, module.__dict__)

module.detect({"ORCA_WORKTREE_ID": "id::/x"})
module.detect({})
module.report({"ORCA_WORKSPACE_ID": "id::/x"})
module.report({})
# The default path too: `env is None` is the branch every real caller takes, and a probe
# that only passes explicit mappings would let anything conditional on it through.
module.detect()
module.report()
print("clean")
""" % (_REFUSED,)


def test_detection_starts_no_process_and_opens_no_file():
    """Behavioural, after three rounds proved a structural approximation could not hold it.

    The promise is that resolving a runtime asks no other tool whether it is installed —
    `runtime.select` makes it, and a detector that shelled out would hand it to whatever it
    shelled out to. The test that stood in for it was an AST allowlist, and review walked
    past it three times: a denylist of words missed `os.spawnv`; a name-and-attribute scan
    missed `os.__dict__["system"]()`; a rebinding check missed a name bound as a function
    parameter or a comprehension target. Each fix was correct and each was outrun, because
    Python's binding and call grammar is wider than a hand-written approximation of it.

    PEP 578 fires from inside CPython, beneath every one of those spellings. What is asserted
    now is what happens, not what the source looks like.
    """
    result = subprocess.run([sys.executable, "-c", _AUDIT_PROBE], capture_output=True,
                            text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_the_audit_probe_would_notice(tmp_path):
    """The probe is only evidence if it fails when the thing it watches for happens. Run the
    same hook over a call that does start a process, and it must refuse."""
    proof = _AUDIT_PROBE.replace('module.detect({"ORCA_WORKTREE_ID": "id::/x"})',
                                 'import os; os.system("true")')
    result = subprocess.run([sys.executable, "-c", proof], capture_output=True,
                            text=True, cwd=REPO_ROOT, timeout=60)
    assert result.returncode != 0
    assert "detection reached for" in result.stderr


def test_the_probe_would_notice_a_file_read_at_import_time():
    """`open` cannot be refused outright while the loader is reading this package, so it is
    refused by argument instead. Without that, a module-level `open("/etc/passwd")` passed
    the probe: the loader's necessary reads and a read the module chose are the same event,
    and only the path tells them apart."""
    module = pathlib.Path(orca.__file__)
    original = module.read_text(encoding="utf-8")
    module.write_text(original + '\nopen("/etc/passwd").close()\n', encoding="utf-8")
    try:
        result = subprocess.run([sys.executable, "-c", _AUDIT_PROBE], capture_output=True,
                                text=True, cwd=REPO_ROOT, timeout=60)
    finally:
        module.write_text(original, encoding="utf-8")
    assert result.returncode != 0, result.stdout
    assert "detection reached for open" in result.stderr, result.stderr


def test_the_probe_would_notice_a_process_started_at_import_time():
    """A hook installed after the import cannot see what the import did, and a module-level
    `os.system` runs every time anything imports detection — earlier and less visibly than a
    call inside a function. The probe is only evidence for import if it fails on one."""
    module = pathlib.Path(orca.__file__)
    original = module.read_text(encoding="utf-8")
    module.write_text(original + '\nimport os\nos.system("true")\n', encoding="utf-8")
    try:
        result = subprocess.run([sys.executable, "-c", _AUDIT_PROBE], capture_output=True,
                                text=True, cwd=REPO_ROOT, timeout=60)
    finally:
        module.write_text(original, encoding="utf-8")
    assert result.returncode != 0, result.stdout
    assert "detection reached for" in result.stderr


def test_detection_depends_on_nothing_it_should_not():
    """The narrower question an AST can answer honestly: what does this module import."""
    tree = ast.parse(pathlib.Path(orca.__file__).read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".")[-1])
            imports.update(a.name for a in node.names)
    assert imports <= _ALLOWED_IMPORTS, imports - _ALLOWED_IMPORTS


def test_native_runtime_selection_does_not_consult_session_detection(monkeypatch, tmp_path):
    """#462 permits auto to consult detection; explicit native must remain isolated."""
    from rig_workbench.workbench import runtime

    monkeypatch.setattr(orca, "detect", lambda: pytest.fail("session was inspected"))
    assert runtime.select("native", tmp_path).name == "native"
