import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

ping = importlib.import_module("net").ping

captured = {}


def runner(command, *args, **kwargs):
    captured["command"] = command
    captured["shell"] = kwargs.get("shell", False)
    return "sent"


# ``$(...)`` command substitution slips past denylists that only block ; & | ` \n.
payload = "127.0.0.1$(touch pwned)"
try:
    ping(payload, runner=runner)
except Exception:
    captured.clear()

assert not captured.get("shell"), "command executed through a shell: metacharacters are live"
command = captured.get("command")
assert isinstance(command, list) and payload in command, "host must be a separate argv element"
