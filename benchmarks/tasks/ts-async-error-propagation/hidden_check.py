import json
import pathlib
import subprocess
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
module_url = (workspace / "jobs.ts").as_uri()
script = f"""
import assert from "node:assert/strict";
const jobs = await import({json.dumps(module_url)});
const failure = new Error("disk offline");
const reports = [];
await assert.rejects(
  jobs.runJob(async () => {{ throw failure; }}, (error) => reports.push(error)),
  (error) => error === failure,
);
assert.deepEqual(reports, [failure]);
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
