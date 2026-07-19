import json
import pathlib
import subprocess
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
module_url = (workspace / "handlers.ts").as_uri()
script = f"""
import assert from "node:assert/strict";
const handlers = await import({json.dumps(module_url)});
assert.throws(() => handlers.deleteDocument("bob", "a"), /forbidden/);
assert.equal(handlers.getDocument("alice", "a").title, "Roadmap");
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
