import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

paginate = importlib.import_module("pagination").paginate
assert paginate(list(range(10)), -1, 2) == [], "negative pages must be outside the result set"
