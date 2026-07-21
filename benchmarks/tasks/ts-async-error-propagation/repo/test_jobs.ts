import assert from "node:assert/strict";
import test from "node:test";

import { runJob } from "./jobs.ts";

test("returns a successful asynchronous result", async () => {
  const reports: unknown[] = [];

  const result = await runJob(async () => "complete", (error) => reports.push(error));

  assert.equal(result, "complete");
  assert.deepEqual(reports, []);
});
