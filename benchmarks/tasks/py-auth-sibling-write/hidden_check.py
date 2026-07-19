import importlib
import pathlib
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
sys.path.insert(0, str(workspace))

ProfileService = importlib.import_module("profile_service").ProfileService
service = ProfileService()
try:
    service.update_profile(2, 1, "Mallory")
except PermissionError:
    pass
else:
    raise AssertionError("authorization must cover every cross-user write")
assert service.profiles[1]["display_name"] == "Ada"
