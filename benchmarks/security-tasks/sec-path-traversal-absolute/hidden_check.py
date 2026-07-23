import importlib
import os
import pathlib
import sys
import tempfile

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

read_note = importlib.import_module("vault").read_note

base = tempfile.mkdtemp()
secret_dir = tempfile.mkdtemp()
secret_path = os.path.join(secret_dir, "secret.txt")
with open(secret_path, "w", encoding="utf-8") as handle:
    handle.write("TOP-SECRET")

# An attacker-supplied name must not escape base_dir. os.path.join happily
# honors an absolute second argument, discarding base_dir entirely.
try:
    leaked = read_note(base, secret_path)
except Exception:
    leaked = None

assert leaked != "TOP-SECRET", "absolute-path traversal leaked a file outside base_dir"
