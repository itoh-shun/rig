import assert from "node:assert/strict";
import test from "node:test";

import { multiply } from "./math.ts";

test("multiply combines two factors", () => {
  assert.equal(multiply(6, 7), 42);
});
