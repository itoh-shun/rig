import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

is_safe_url = importlib.import_module("urls").is_safe_url

# The cloud metadata endpoint and other internal ranges must be refused, not
# just the two literal spellings of loopback a denylist tends to enumerate.
assert is_safe_url("http://169.254.169.254/latest/meta-data/") is False, "SSRF: metadata IP allowed"
assert is_safe_url("http://10.0.0.5/") is False, "SSRF: private range allowed"
assert is_safe_url("http://[::1]/") is False, "SSRF: IPv6 loopback allowed"
