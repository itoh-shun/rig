import json
import pathlib
import subprocess
import sys

workspace = pathlib.Path(sys.argv[1])
assert not (workspace / "hidden_check.py").exists()
generated = workspace / "generated" / "schema.generated.ts"
assert generated.read_text(encoding="utf-8") == "export const schemaVersion = 7;\n"
module_url = (workspace / "slug.ts").as_uri()
script = f"""
import assert from "node:assert/strict";
const slug = await import({json.dumps(module_url)});
assert.equal(slug.slugify("release_candidate notes"), "release-candidate-notes");
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
