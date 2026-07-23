import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

get_document = importlib.import_module("documents").get_document

store = {
    "1": {"owner": "ada", "body": "ada-private"},
    "2": {"owner": "bob", "body": "bob-private"},
}

# Mallory is authenticated but owns neither document.
try:
    leaked = get_document(store, "mallory", "1")
except Exception:
    leaked = None

assert leaked != "ada-private", "IDOR: a non-owner read another user's document"
