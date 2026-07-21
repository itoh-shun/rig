import assert from "node:assert/strict";
import test from "node:test";

import { getSetting, updateSetting } from "./settings.ts";

test("reads an existing setting", () => {
  assert.equal(getSetting("alice"), "light");
});

test("mutation returns the stored value", () => {
  assert.equal(updateSetting("alice", "dark"), "dark");
});
