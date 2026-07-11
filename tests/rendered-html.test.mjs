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

test("server-renders the working command center and all human gates", async () => {
  const response = await render("/app");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Command center/);
  assert.match(html, /Fictional preview data/);
  assert.match(html, /Preview run state/);
  assert.match(html, /Pair the local companion/);
  assert.doesNotMatch(html, /auto-send/i);
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

test("server-renders truthful source setup and imports", async () => {
  const response = await render("/app/sources");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Source control/);
  assert.match(html, /Handshake/);
  assert.match(html, /JobSpy/);
  assert.match(html, /public extension does not scrape or automate LinkedIn/i);
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
