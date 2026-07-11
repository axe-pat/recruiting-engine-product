import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const appFrame = await readFile(new URL("../components/AppFrame.tsx", import.meta.url), "utf8");
const onboarding = await readFile(new URL("../components/OnboardingWizard.tsx", import.meta.url), "utf8");
const operatorWorkspace = await readFile(new URL("../components/OperatorWorkspace.tsx", import.meta.url), "utf8");
const operatorContract = await readFile(new URL("../lib/operator-contract.ts", import.meta.url), "utf8");
const operatorDocs = await readFile(new URL("../docs/OPERATOR_COCKPIT.md", import.meta.url), "utf8");
const operatorBackend = await readFile(new URL("../companion/recruiting_companion/operator_backend.py", import.meta.url), "utf8");
const companionDatabase = await readFile(new URL("../companion/recruiting_companion/db.py", import.meta.url), "utf8");
const layout = await readFile(new URL("../app/layout.tsx", import.meta.url), "utf8");
const operatorOverviewContract = operatorContract.slice(
  operatorContract.indexOf("export type OperatorOverview"),
  operatorContract.indexOf("export type OperatorActionResult"),
);

test("hosted pairing is explicit, short-lived, and tab-scoped", () => {
  assert.match(appFrame, /client_type:\s*"web"/);
  assert.match(onboarding, /client_type:\s*"web"/);
  assert.match(appFrame, /sessionStorage\.setItem\(sessionConfigKey/);
  assert.match(onboarding, /sessionStorage\.setItem\(sessionConfigKey/);
  assert.doesNotMatch(appFrame, /localStorage\.setItem\([^\n]+JSON\.stringify\(normalized\)/);
  assert.doesNotMatch(onboarding, /localStorage\.setItem\([^\n]+JSON\.stringify\(pairedConfig\)/);
  assert.match(appFrame, /Already connected\. The pairing token was consumed once/);
  assert.match(appFrame, /Pairing tokens work once/);
  assert.match(appFrame, /credential redacted/);
  assert.match(appFrame, /Connected in this tab/);
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

test("consequential actions use a dedicated exact-target review surface", () => {
  assert.match(appFrame, /\/api\/v1\/operator\/review-targets\/\$\{encodeURIComponent\(targetId\)\}\/detail/);
  assert.match(appFrame, /\/api\/v1\/operator\/reviews\/\$\{encodeURIComponent\(reviewId\)\}\/detail/);
  assert.match(appFrame, /\/api\/v1\/operator\/reviews\/\$\{encodeURIComponent\(reviewId\)\}\/content/);
  assert.match(appFrame, /\/api\/v1\/operator\/reviews/);
  assert.match(appFrame, /method:\s*"PUT"/);
  assert.match(appFrame, /reviewed_text:\s*reviewedText/);
  assert.match(appFrame, /reviewed_subject:\s*reviewedSubject/);
  assert.match(operatorWorkspace, /Exact recipient/);
  assert.match(operatorWorkspace, /Exact recipient · immutable/);
  assert.match(operatorWorkspace, /Exact LinkedIn body · editable/);
  assert.match(operatorWorkspace, /Exact email subject · editable/);
  assert.match(operatorWorkspace, /Exact email body · editable/);
  assert.match(operatorWorkspace, /<textarea value=\{reviewedText\}/);
  assert.match(operatorWorkspace, /type="text" value=\{reviewedSubject\}/);
  assert.match(operatorWorkspace, /UPDATE_EXACT_REVIEW_CONTENT/);
  assert.match(operatorWorkspace, /Update exact review content/);
  assert.match(operatorWorkspace, /storedReview\.operator_review\.reviewed_text/);
  assert.match(operatorWorkspace, /pending again and must be reviewed and approved/);
  assert.match(operatorWorkspace, /APPROVE_EXACT_TARGET/);
  assert.match(operatorWorkspace, /review_id: next\.id, target_id: next\.target_id/);
  assert.match(operatorWorkspace, /prompt-only stopping is insufficient/);
  assert.doesNotMatch(appFrame, /sessionStorage\.setItem\([^\n]+reviewed_(?:text|subject)/);
  assert.match(operatorContract, /type OperatorReviewPrivateDetail[\s\S]*reviewed_subject[\s\S]*reviewed_text/);
  assert.doesNotMatch(operatorOverviewContract, /reviewed_(?:text|subject)/);
  assert.doesNotMatch(operatorWorkspace, /application\.submit/);
  assert.match(operatorBackend, /_APPLY_ASSIST_BLOCKED_REASON/);
  assert.match(operatorBackend, /def _execute_reviewed_apply_assist[\s\S]{0,700}result_code="application_assist_submit_guard_unavailable"[\s\S]{0,100}return/);
  assert.ok(
    [...operatorBackend.matchAll(/_run_reviewed_production_preflight\(/g)].length >= 5,
    "every consequential executor must retain a last-moment production preflight",
  );
  assert.match(companionDatabase, /CREATE UNIQUE INDEX IF NOT EXISTS idx_operator_reviews_one_active[\s\S]{0,180}WHERE state IN \('pending', 'reviewed', 'approved'\)/);
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
  assert.match(operatorWorkspace, /aggregate projection is not a draft queue/);
});
