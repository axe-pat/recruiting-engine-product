import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(readFileSync(resolve(root, "manifest.json"), "utf8"));

test("is a Manifest V3 side-panel extension with a module service worker", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.background.service_worker, "service-worker.js");
  assert.equal(manifest.background.type, "module");
  assert.equal(manifest.side_panel.default_path, "sidepanel.html");
  assert.match(manifest.minimum_chrome_version, /^\d+$/);
});

test("keeps required permissions narrow", () => {
  assert.deepEqual(
    [...manifest.permissions].sort(),
    ["activeTab", "scripting", "sidePanel", "storage"].sort(),
  );
  assert.equal("host_permissions" in manifest, false);
  assert.equal("content_scripts" in manifest, false);
  assert.equal("externally_connectable" in manifest, false);
  assert.equal("web_accessible_resources" in manifest, false);
  assert.equal(manifest.permissions.includes("tabs"), false);
  assert.equal(manifest.permissions.includes("clipboardRead"), false);
  assert.equal(manifest.permissions.includes("webNavigation"), false);
});

test("optional host access is loopback-only", () => {
  assert.deepEqual(new Set(manifest.optional_host_permissions), new Set([
    "http://localhost/*",
    "http://127.0.0.1/*",
  ]));
  assert.equal(manifest.optional_host_permissions.some((pattern) => pattern.includes("linkedin")), false);
  assert.equal(manifest.optional_host_permissions.some((pattern) => pattern.includes("*://*")), false);
});

test("all declared entry points and PNG icons exist", () => {
  const paths = [
    manifest.background.service_worker,
    manifest.side_panel.default_path,
    ...Object.values(manifest.icons),
    ...Object.values(manifest.action.default_icon),
  ];
  for (const path of new Set(paths)) assert.equal(existsSync(resolve(root, path)), true, path);

  for (const [size, path] of Object.entries(manifest.icons)) {
    const png = readFileSync(resolve(root, path));
    assert.deepEqual([...png.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    assert.equal(png.readUInt32BE(16), Number(size));
    assert.equal(png.readUInt32BE(20), Number(size));
  }
});
