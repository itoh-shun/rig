import json
import pathlib
import subprocess
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
module_url = (workspace / "math.ts").as_uri()
script = f"""
import assert from "node:assert/strict";
const math = await import({json.dumps(module_url)});
assert.equal(math.times(3, 4), 12);
assert.equal(math.times(5), 10);
"""
result = subprocess.run(
    ["node", "--input-type=module", "--eval", script],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
)
if result.returncode:
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    raise SystemExit(result.returncode)
