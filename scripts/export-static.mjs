import { cp, mkdir, rm, writeFile } from "node:fs/promises";

const outputRoot = new URL("../static-export/", import.meta.url);
const productionOrigin = process.env.PRODUCT_SITE_ORIGIN || "https://axe-pat.github.io";
const routes = [
  { path: "/", output: "index.html" },
  { path: "/story", output: "story/index.html" },
  { path: "/architecture", output: "architecture/index.html" },
];

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("static-export", `${Date.now()}`);
const { default: worker } = await import(workerUrl.href);

await rm(outputRoot, { recursive: true, force: true });
await mkdir(outputRoot, { recursive: true });

for (const route of routes) {
  const response = await worker.fetch(
    new Request(`${productionOrigin}${route.path}`, {
      headers: {
        accept: "text/html",
        host: new URL(productionOrigin).host,
        "x-forwarded-host": new URL(productionOrigin).host,
        "x-forwarded-proto": new URL(productionOrigin).protocol.replace(":", ""),
      },
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

  if (!response.ok) {
    throw new Error(`Static render failed for ${route.path}: ${response.status}`);
  }

  const outputUrl = new URL(route.output, outputRoot);
  await mkdir(new URL("./", outputUrl), { recursive: true });
  await writeFile(outputUrl, await response.text(), "utf8");
}

await cp(new URL("../dist/client/assets/", import.meta.url), new URL("assets/", outputRoot), {
  recursive: true,
});
await cp(new URL("../public/og.png", import.meta.url), new URL("og.png", outputRoot));
await writeFile(new URL(".nojekyll", outputRoot), "", "utf8");
await cp(new URL("index.html", outputRoot), new URL("404.html", outputRoot));

console.log(`Static product exported for ${productionOrigin} with ${routes.length} routes.`);
