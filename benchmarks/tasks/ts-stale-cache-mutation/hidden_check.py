import json
import pathlib
import subprocess
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
module_url = (workspace / "settings.ts").as_uri()
script = f"""
import assert from "node:assert/strict";
const settings = await import({json.dumps(module_url)});
assert.equal(settings.getSetting("bob"), "compact");
settings.updateSetting("bob", "expanded");
assert.equal(settings.getSetting("bob"), "expanded");
"""
result = subprocess.run(
    ["node", "--input-type=module", "--eval", script],
    capture_output=True,
    text=True,
)
if result.returncode:
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    raise SystemExit(result.returncode)
