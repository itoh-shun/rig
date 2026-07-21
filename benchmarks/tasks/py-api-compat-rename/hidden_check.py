import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

format_message = importlib.import_module("messages").format_message
assert format_message("Lin", prefix="Welcome") == "Welcome, Lin!"
