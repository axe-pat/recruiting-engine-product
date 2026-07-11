import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appFrame = await readFile(new URL("../components/AppFrame.tsx", import.meta.url), "utf8");
const onboarding = await readFile(new URL("../components/OnboardingWizard.tsx", import.meta.url), "utf8");
const operatorWorkspace = await readFile(new URL("../components/OperatorWorkspace.tsx", import.meta.url), "utf8");
const operatorDocs = await readFile(new URL("../docs/OPERATOR_COCKPIT.md", import.meta.url), "utf8");
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

test("operator controls call only the fixed local job registry", () => {
  assert.match(appFrame, /\/api\/v1\/operator\/overview/);
  assert.match(appFrame, /\/api\/v1\/operator\/jobs/);
  assert.match(appFrame, /JSON\.stringify\(\{ command_id: commandId, confirmation, parameters \}\)/);
  assert.match(operatorWorkspace, /\{ job_id: jobId \}/);
  assert.match(operatorWorkspace, /There is no arbitrary shell/);
  assert.match(operatorWorkspace, /Only this named capability can run/);
  assert.doesNotMatch(operatorWorkspace, /exec\(|spawn\(|child_process|command_line|argv/);
});

test("existing mode chooses the minimized read model before the portable dashboard", () => {
  assert.match(appFrame, /\/api\/v1\/preferences/);
  assert.match(appFrame, /if \(nextMode === "existing"\) \{[\s\S]*\/api\/v1\/operator\/overview[\s\S]*\} else \{[\s\S]*\/api\/v1\/dashboard/);
  assert.match(appFrame, /operatorShellSnapshot/);
  assert.match(operatorDocs, /never calls the portable dashboard endpoint/);
});

test("operator surfaces preserve item guards and truthful aggregate semantics", () => {
  assert.match(operatorWorkspace, /item\.actions\?\.find/);
  assert.match(operatorWorkspace, /Per-item guard state is unavailable/);
  assert.match(operatorWorkspace, /Action did not start/);
  assert.match(operatorWorkspace, /Release attestation configured/);
  assert.doesNotMatch(operatorWorkspace, /Release guard verified/);
  assert.match(operatorWorkspace, /Paid model action/);
  assert.match(operatorWorkspace, /function ApplicationHistorySurface/);
  assert.match(operatorWorkspace, /function VerifiedRunsSurface/);
  assert.match(operatorWorkspace, /function SourceRows/);
  assert.match(operatorWorkspace, /function ReportRows/);
  assert.match(operatorWorkspace, /Filename-classified, not canonical/);
  assert.match(operatorWorkspace, /does not claim to be a draft queue/);
});
