import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const worker = readFileSync(resolve(root, "service-worker.js"), "utf8");
const panel = readFileSync(resolve(root, "sidepanel.js"), "utf8");
const html = readFileSync(resolve(root, "sidepanel.html"), "utf8");

test("contains no passive browser monitoring or markup injection surface", () => {
  for (const forbidden of [
    "chrome.tabs.onUpdated",
    "chrome.tabs.onActivated",
    "chrome.webNavigation",
    "chrome.history",
    "chrome.cookies",
    "querySelectorAll",
  ]) {
    assert.equal(worker.includes(forbidden), false, forbidden);
  }
  assert.equal(panel.includes("innerHTML"), false);
  assert.equal(panel.includes("insertAdjacentHTML"), false);
  assert.equal(worker.includes("eval("), false);
  assert.equal(panel.includes("eval("), false);
});

test("the approval path is state-only and contains no send transition", () => {
  assert.match(worker, /state:\s*"reviewed"/);
  assert.match(worker, /state:\s*"approved"/);
  assert.doesNotMatch(worker, /state:\s*"sent"/);
  assert.match(html, /This does not send a message/);
});

test("the UI exposes separate recipient and draft confirmations", () => {
  assert.match(html, /id="recipientConfirmed"/);
  assert.match(html, /id="draftReviewed"/);
  assert.match(html, /id="approveButton"[^>]*disabled/);
  assert.match(html, /blocked on LinkedIn/);
  assert.match(html, /Clipboard access is not requested/);
});

test("interactive controls have unique IDs and labels target real fields", () => {
  const ids = [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(new Set(ids).size, ids.length, "duplicate HTML id");
  const fields = new Set(ids);
  for (const match of html.matchAll(/<label[^>]+for="([^"]+)"/g)) {
    assert.equal(fields.has(match[1]), true, `missing labelled field ${match[1]}`);
  }
  for (const match of html.matchAll(/<button\b([^>]*)>/g)) {
    assert.match(match[1], /\btype="button"/, "button missing explicit type");
  }
});
