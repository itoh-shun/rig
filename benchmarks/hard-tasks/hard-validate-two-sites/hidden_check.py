import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

users = importlib.import_module("users")
users._users.clear()

# Attack via the single-create path AND the bulk-import path. A fix that only
# guards create_user still lets the bulk path through.
for attacker in (lambda: users.create_user("../etc/passwd"),
                 lambda: users.import_users(["../../secret"])):
    try:
        attacker()
    except Exception:
        pass

assert all("/" not in name for name in users.all_users()), \
    "a username containing a path separator was stored (a call site was left unguarded)"
