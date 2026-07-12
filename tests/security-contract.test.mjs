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

test("hosted preview probes only its own loopback origin before user intent", () => {
  assert.match(appFrame, /if \(isLoopbackOrigin\(window\.location\.origin\)\)[\s\S]{0,180}localPrimaryBootstrap/);
  assert.match(appFrame, /if \(!nextConfig\.token\)[\s\S]{0,120}setConnection\("preview"\)/);
  assert.doesNotMatch(appFrame, /fetch\(["'`]http:\/\/(?:127\.0\.0\.1|localhost)/);
  assert.match(appFrame, /view !== "settings" && connection !== "connected"/);
  assert.match(appFrame, /fictional preview records are no longer rendered on operational routes/);
});

test("the same-origin local cockpit authenticates without browser-readable tokens", () => {
  assert.match(appFrame, /localUiServerHeader = "X-Recruiting-Engine-Local-UI-Server"/);
  assert.match(appFrame, /response\.headers\.get\(localUiServerHeader\) !== "1"/);
  assert.match(appFrame, /compatibleCompanionVersion = "0\.3\.0"/);
  assert.match(appFrame, /payload\.compatible_companion_version !== compatibleCompanionVersion/);
  assert.match(appFrame, /const primaryConfig = \{ baseUrl: window\.location\.origin, token: "" \}/);
  assert.match(appFrame, /headers: \{ \[localUiHeader\]: "1" \}/);
  assert.match(appFrame, /credentials: localPrimary \? "same-origin" : "omit"/);
  assert.match(appFrame, /local_ui_activation_required/);
  assert.match(appFrame, /scripts\/open-operator-cockpit\.sh/);
  assert.match(appFrame, /This Mac · always connected/);
  assert.match(appFrame, /no token session to disconnect/);
  assert.doesNotMatch(appFrame, /localStorage\.setItem\([^\n]+re_ui_/);
  assert.doesNotMatch(appFrame, /sessionStorage\.setItem\([^\n]+re_ui_/);
  assert.doesNotMatch(appFrame, /port === "8765"/);
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
  assert.match(appFrame, /\/api\/v1\/operator\/progress/);
  assert.match(appFrame, /\/api\/v1\/operator\/jobs/);
  assert.match(appFrame, /JSON\.stringify\(\{ command_id: commandId, confirmation, parameters \}\)/);
  assert.match(operatorWorkspace, /\{ job_id: jobId \}/);
  assert.match(operatorWorkspace, /There is no arbitrary shell/);
  assert.match(operatorWorkspace, /Only this named capability can run/);
  assert.doesNotMatch(operatorWorkspace, /exec\(|spawn\(|child_process|command_line|argv/);
});

test("progress compatibility fallback is limited to unavailable or legacy-scoped routes", () => {
  assert.match(appFrame, /error\.status === 404/);
  assert.match(appFrame, /error\.status === 403 && error\.code === "insufficient_scope"/);
  assert.match(appFrame, /if \(!legacyRouteUnavailable\) throw error/);
  assert.doesNotMatch(appFrame, /error\.status === 404 \|\| error\.status === 403\s*[);]/);
});

test("dashboard refreshes cannot overwrite a newer connection generation", () => {
  assert.match(appFrame, /dashboardLoadRef = useRef<\{ generation: number; controller: AbortController \| null \}>/);
  assert.match(appFrame, /dashboardLoadRef\.current\.controller\?\.abort\(\)/);
  assert.match(appFrame, /\{ signal: controller\.signal \}/);
  assert.match(appFrame, /dashboardLoadRef\.current\.generation === generation/);
  assert.match(appFrame, /sameCompanionConfig\(nextConfig, activeConfigRef\.current\)/);
  assert.match(appFrame, /invalidateDashboardLoads\(\)[\s\S]{0,500}sessionStorage\.removeItem\(sessionConfigKey\)/);
});

test("live polling fails closed with auth-aware state and bounded retries", () => {
  assert.match(appFrame, /apiError\?\.status === 401/);
  assert.match(appFrame, /localPrimary \? "activation" : "error"/);
  assert.match(appFrame, /consecutiveFailures >= 3/);
  assert.match(appFrame, /Math\.min\(30_000, 4000 \* \(2 \*\* consecutiveFailures\)\)/);
  assert.match(appFrame, /Last successful live update/);
  assert.match(appFrame, /markPollingUnavailable/);
  assert.match(appFrame, /status: "partial"[\s\S]{0,160}is_current: false[\s\S]{0,160}status: "stale"/);
  assert.match(appFrame, /recent_jobs: \[\]/);
});

test("live polling performs a full refresh only for meaningful plan changes", () => {
  assert.match(appFrame, /const enteredAttention =/);
  assert.match(appFrame, /const scoringErrorsIncreased =/);
  assert.match(appFrame, /const refreshCurrentPlan = enteredAttention/);
  assert.match(appFrame, /if \(nextRunActive && refreshCurrentPlan\) \{[\s\S]{0,120}await loadDashboard\(config\)/);
  assert.match(appFrame, /scoringErrorsIncreased && !scoringIncreaseRefreshed/);
});

test("the global E2E control opens the reviewed production nightly target", () => {
  assert.match(appFrame, /window\.location\.assign\(scheduledRunActive \? "\/app\/runs" : cockpitJobActive \? "\/app\/operations" : "\/app\/runs\?start=nightly"\)/);
  assert.match(appFrame, /"Run E2E"/);
  assert.match(appFrame, /"View live run"/);
  assert.match(appFrame, /setAutoReviewCommandId\("nightly\.run"\)/);
  assert.match(operatorWorkspace, /candidate\.command_id === autoReviewCommandId/);
  assert.match(operatorWorkspace, /void selectReviewTarget\(target\)/);
  assert.match(operatorWorkspace, /review_id: next\.id, target_id: next\.target_id/);
});

test("exact reports load only through the paired companion and render sandboxed", () => {
  assert.match(appFrame, /\/api\/v1\/operator\/reports\/\$\{encodeURIComponent\(runId\)\}\/html/);
  assert.match(operatorWorkspace, /function ExactReportViewer/);
  assert.match(operatorWorkspace, /<iframe[\s\S]{0,220}sandbox=""[\s\S]{0,220}srcDoc=\{sandboxedHtml\}/);
  assert.match(operatorWorkspace, /default-src 'none'/);
  assert.match(operatorWorkspace, /scripts, forms, top-level navigation, and remote subresources disabled/);
  assert.doesNotMatch(operatorWorkspace, /dangerouslySetInnerHTML/);
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
