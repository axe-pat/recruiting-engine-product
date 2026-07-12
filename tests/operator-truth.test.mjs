import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const workspace = await readFile(
  new URL("../components/OperatorWorkspace.tsx", import.meta.url),
  "utf8",
);
const contract = await readFile(
  new URL("../lib/operator-contract.ts", import.meta.url),
  "utf8",
);

test("source surface names the exact manifest source-family contract", () => {
  assert.match(workspace, /exact manifest source-family rows/);
  assert.match(workspace, /No exact manifest source-family metrics are eligible yet/);
  assert.match(workspace, /Manifest source families/);
  assert.doesNotMatch(workspace, /No exact source metrics are eligible yet/);
});

test("review queue contract exposes bounded recent-review metadata", () => {
  assert.match(contract, /recent_reviews_items_returned\?: number/);
  assert.match(contract, /recent_reviews_items_total\?: number/);
  assert.match(contract, /recent_reviews_truncated\?: boolean/);
  assert.match(contract, /recent_reviews_meta\?:/);
});
