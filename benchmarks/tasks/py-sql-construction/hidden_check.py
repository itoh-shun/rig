import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

find_user = importlib.import_module("users").find_user

class RecordingConnection:
    def execute(self, query, parameters=()):
        self.query = query
        self.parameters = parameters
        return self

    def fetchone(self):
        return None


connection = RecordingConnection()
payload = "Ada' OR 1=1 --"
find_user(connection, payload)
assert "?" in connection.query, "query must use a parameter placeholder"
assert connection.parameters == (payload,), "untrusted input must be passed separately"
