import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appFrame = await readFile(new URL("../components/AppFrame.tsx", import.meta.url), "utf8");
const onboarding = await readFile(new URL("../components/OnboardingWizard.tsx", import.meta.url), "utf8");
const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");

test("hosted pairing is explicit, short-lived, and tab-scoped", () => {
  assert.match(appFrame, /client_type:\s*"web"/);
  assert.match(onboarding, /client_type:\s*"web"/);
  assert.match(appFrame, /sessionStorage\.setItem\(sessionConfigKey/);
  assert.match(onboarding, /sessionStorage\.setItem\(sessionConfigKey/);
  assert.doesNotMatch(appFrame, /localStorage\.setItem\([^\n]+JSON\.stringify\(normalized\)/);
  assert.doesNotMatch(onboarding, /localStorage\.setItem\([^\n]+JSON\.stringify\(pairedConfig\)/);
});

test("preview does not probe loopback before user intent", () => {
  assert.match(appFrame, /if \(!nextConfig\.token\)[\s\S]{0,160}setConnection\("preview"\)/);
});

test("hosted dashboard consumes minimized presentation DTOs", () => {
  assert.match(appFrame, /application_items/);
  assert.match(appFrame, /outreach_items/);
  assert.doesNotMatch(appFrame, /optionalCollection/);
  assert.doesNotMatch(appFrame, /api\/v1\/(?:contacts|jobs|companies)"/);
});

test("static document applies a restrictive local-first CSP", () => {
  assert.match(layout, /Content-Security-Policy/);
  assert.match(layout, /connect-src 'self' http:\/\/127\.0\.0\.1:\*/);
  assert.match(layout, /object-src 'none'/);
});
