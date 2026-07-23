import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

update_status = importlib.import_module("tenancy").update_status

store = [
    {"id": "1", "tenant_id": "acme", "status": "open"},
    {"id": "2", "tenant_id": "globex", "status": "open"},
]

# The "acme" tenant tries to close a record owned by "globex".
try:
    update_status(store, "acme", "2", "closed")
except Exception:
    pass

globex_record = next(record for record in store if record["id"] == "2")
assert globex_record["status"] == "open", "cross-tenant write: acme modified globex's record"
