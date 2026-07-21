import assert from "node:assert/strict";
import test from "node:test";

import { slugify } from "./slug.ts";

test("normalizes spaces", () => {
  assert.equal(slugify("  Release Notes  "), "release-notes");
});
