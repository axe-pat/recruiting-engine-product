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

