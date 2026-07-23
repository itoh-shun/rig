import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

find_record = importlib.import_module("records").find_record

store = [
    {"id": "1", "tenant_id": "acme", "body": "acme-data"},
    {"id": "2", "tenant_id": "globex", "body": "globex-secret"},
]

# The "acme" tenant asks for a record id that belongs to "globex".
try:
    leaked = find_record(store, "acme", "2")
except Exception:
    leaked = None

assert leaked is None or leaked.get("tenant_id") == "acme", \
    "cross-tenant read: acme retrieved globex's record"
