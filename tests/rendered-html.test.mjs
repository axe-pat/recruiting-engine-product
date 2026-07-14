import assert from "node:assert/strict";
import test from "node:test";

const developmentPreviewMeta =
  /<meta(?=[^>]*\bname=["']codex-preview["'])(?=[^>]*\bcontent=["']development["'])[^>]*>/i;

async function render(path = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${path}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the product surface without starter metadata", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /Recruiting Engine/);
  assert.match(html, /The job search/);
  assert.match(html, /rebuilt as a product/);
  assert.match(html, /Sanitized product snapshot/);
  assert.match(html, /fictionalized/i);
  assert.match(html, /542/);
  assert.doesNotMatch(html, developmentPreviewMeta);
  assert.doesNotMatch(html, /react-loading-skeleton|Your site is taking shape|Codex is working/i);
});

test("server-renders the fact-checked product story", async () => {
  const response = await render("/story");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /How a personal workflow became a production AI product/);
  assert.match(html, /AI agents became the engineering team/);
  assert.match(html, /One wrong invite created a stronger product contract/);
  assert.match(html, /151 commits/);
});

test("server-renders architecture, source state, and guardrails", async () => {
  const response = await render("/architecture");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Decision intelligence/);
  assert.match(html, /Exact-run provenance/);
  assert.match(html, /Fail-closed execution/);
  assert.match(html, /Private-by-design demo/);
  assert.match(html, /Product Strategy/);
});

test("server-renders a truthful connection gate before operational data", async () => {
  const response = await render("/app");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Command center/);
  assert.match(html, /Checking private companion/);
  assert.match(html, /No records will appear until the authenticated local workspace responds/);
  assert.doesNotMatch(html, /Northstar Labs|Parcel Works|Signal House/);
  assert.match(html, /Close navigation/);
  assert.doesNotMatch(html, /auto-send/i);
});

test("server-renders a credential-free permanent-local fallback on hosted settings", async () => {
  const response = await render("/app/settings");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Open permanent local cockpit/);
  assert.match(html, /href="http:\/\/127\.0\.0\.1:8765\/app\/"/);
  assert.match(html, /Optional · pair only this hosted tab for 12 hours/);
  assert.doesNotMatch(html, /re_(?:web|local|pair|ui|activate)_[A-Za-z0-9_-]+/);
});

test("server-renders the private operator cockpit routes without private data", async () => {
  for (const path of ["/app/accounts", "/app/plan", "/app/stories", "/app/operations"]) {
    const response = await render(path);
    assert.equal(response.status, 200);
    const html = await response.text();
    assert.match(html, /Checking private companion/);
    assert.match(html, /No records will appear until the authenticated local workspace responds/);
    assert.doesNotMatch(html, /\/Users\/|@gmail\.com|linkedin\.com\/in\//i);
  }
});

test("server-renders private first-run onboarding", async () => {
  const response = await render("/app/onboarding");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /First-run setup/);
  assert.match(html, /One baseline resume/i);
  assert.match(html, /not uploaded to the hosting server/i);
  assert.match(html, /Private pairing/);
});

test("server-renders no source records before the local workspace is authenticated", async () => {
  const response = await render("/app/sources");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Checking private companion/);
  assert.match(html, /No records will appear until the authenticated local workspace responds/);
  assert.doesNotMatch(html, /Northstar Labs|Parcel Works|Signal House/);
});

test("server-renders the local-first privacy contract", async () => {
  const response = await render("/privacy");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Your search is personal/);
  assert.match(html, /local system of record/);
  assert.match(html, /does not collect browsing history/);
  assert.match(html, /does not sell user data/);
});
