import test from "node:test";
import assert from "node:assert/strict";

import {
  APP_PATHS,
  allowedAppPath,
  approvalPath,
  blockedPageReason,
  buildIntakePayload,
  normalizeCapturedPage,
  normalizeIntakeResponse,
  normalizeLoopbackBaseUrl,
  normalizeOutreachReview,
  hostedAppUrl,
  permissionOriginFor,
  pickReviewCandidate,
} from "../lib/contract.js";

test("normalizes only explicit loopback companion URLs", () => {
  assert.equal(normalizeLoopbackBaseUrl("http://127.0.0.1:8765/"), "http://127.0.0.1:8765");
  assert.equal(permissionOriginFor("http://localhost:8765"), "http://localhost/*");
  assert.throws(() => normalizeLoopbackBaseUrl("http://localhost:8765/base"), /without an extra path/i);
  assert.throws(() => normalizeLoopbackBaseUrl("https://localhost:9443/base/"), /loopback HTTP/i);
  assert.throws(() => normalizeLoopbackBaseUrl("http://example.com"), /limited to localhost/i);
  assert.throws(() => normalizeLoopbackBaseUrl("file:///tmp/companion"), /loopback HTTP/i);
  assert.throws(() => normalizeLoopbackBaseUrl("http://user:secret@localhost:8765"), /credentials/i);
});

test("blocks LinkedIn and privileged pages while accepting normal web pages", () => {
  assert.match(blockedPageReason("https://www.linkedin.com/feed/"), /disabled on LinkedIn/i);
  assert.match(blockedPageReason("https://news.linkedin.com/article"), /disabled on LinkedIn/i);
  assert.match(blockedPageReason("chrome://extensions"), /cannot be captured/i);
  assert.equal(blockedPageReason("https://example.com/role"), null);
});

test("normalizes page metadata without retaining arbitrary document content", () => {
  const page = normalizeCapturedPage({
    url: "https://example.com/role",
    title: "Product role",
    selectedText: `  ${"x".repeat(9_000)}  `,
    description: "A page description",
    canonicalUrl: "https://example.com/canonical",
    language: "en-US",
    capturedAt: "2026-07-11T10:00:00Z",
    html: "<main>must not survive</main>",
  });
  assert.equal(page.selectedText.length, 8_000);
  assert.equal("html" in page, false);
  assert.equal(page.url, "https://example.com/role");
});

test("builds the canonical flat intake body", () => {
  const payload = buildIntakePayload({
    kind: "job",
    title: "Associate Product Manager",
    page: {
      url: "https://example.com/role",
      title: "Example page",
      selectedText: "selected evidence",
      description: "description",
    },
    pastedText: "pasted evidence",
    note: "Assess fit",
  });
  assert.deepEqual(payload, {
    source_url: "https://example.com/role",
    title: "Associate Product Manager",
    selected_text: "selected evidence\n\npasted evidence",
    notes: "Assess fit",
    kind: "job",
  });
  assert.throws(
    () => buildIntakePayload({ kind: "job", pastedText: "JD without a title" }),
    /title for a job intake/i,
  );
});

test("normalizes the canonical intake receipt", () => {
  assert.deepEqual(
    normalizeIntakeResponse({
      intake: {
        id: "int_123",
        kind: "job",
        title: "APM",
        job_id: "job_456",
        created_at: "2026-07-11T10:00:00Z",
      },
      job: { id: "job_456" },
    }),
    {
      intakeId: "int_123",
      jobId: "job_456",
      kind: "job",
      title: "APM",
      createdAt: "2026-07-11T10:00:00Z",
    },
  );
});

test("selects only reviewable outreach states with recipient IDs and full text", () => {
  const selected = pickReviewCandidate({
    items: [
      { id: "out_approved", state: "approved", contact_id: "ctc_1", draft_text: "done" },
      { id: "out_missing", state: "draft", contact_id: "", draft_text: "draft" },
      { id: "out_reviewed", state: "reviewed", contact_id: "ctc_2", reviewed_text: "reviewed" },
      { id: "out_draft", state: "draft", contact_id: "ctc_3", draft_text: "full draft" },
    ],
  });
  assert.equal(selected.id, "out_draft");
});

test("requires a displayable recipient and returns the complete local draft", () => {
  const review = normalizeOutreachReview(
    {
      outreach: {
        id: "out_1",
        contact_id: "ctc_1",
        company_id: "cmp_1",
        state: "draft",
        channel: "email",
        draft_text: "Hello — this is the complete draft.",
        updated_at: "2026-07-11T10:02:00Z",
      },
    },
    { contact: { id: "ctc_1", name: "Casey", email: "casey@example.com", relationship: "alumni" } },
    { company: { id: "cmp_1", name: "Northstar" } },
    null,
  );
  assert.equal(review.contactId, "ctc_1");
  assert.equal(review.recipient.name, "Casey");
  assert.equal(review.recipient.destination, "casey@example.com");
  assert.equal(review.draft.body, "Hello — this is the complete draft.");

  assert.throws(
    () =>
      normalizeOutreachReview(
        { id: "out_2", contact_id: "ctc_2", state: "draft", draft_text: "draft" },
        { contact: { id: "ctc_2", name: "No Destination" } },
        null,
        null,
      ),
    /email or profile URL/i,
  );
});

test("allows only fixed local product destinations and encodes outreach IDs", () => {
  for (const path of Object.values(APP_PATHS)) assert.equal(allowedAppPath(path), path);
  assert.throws(() => allowedAppPath("https://example.com"), /not allowed/i);
  assert.equal(approvalPath("out/a b"), "/api/v1/outreach/out%2Fa%20b");
  assert.equal(hostedAppUrl("/app/runs"), "https://axe-pat.github.io/app/runs");
});
