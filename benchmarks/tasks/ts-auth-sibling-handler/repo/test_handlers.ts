import assert from "node:assert/strict";
import test from "node:test";

import { deleteDocument, getDocument } from "./handlers.ts";

test("read handler rejects another owner", () => {
  assert.throws(() => getDocument("alice", "b"), /forbidden/);
});

test("owner can delete a document", () => {
  assert.equal(deleteDocument("bob", "b"), true);
});
