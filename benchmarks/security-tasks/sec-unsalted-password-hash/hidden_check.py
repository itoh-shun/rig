import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

auth = importlib.import_module("auth")

first = auth.hash_password("hunter2")
second = auth.hash_password("hunter2")

# Without a per-password salt, identical passwords collide: rainbow-table fuel
# and a direct leak that two accounts share a password.
assert first != second, "identical passwords hash to identical digests (no per-password salt)"
assert auth.verify_password("hunter2", first), "salted hash must still verify"
