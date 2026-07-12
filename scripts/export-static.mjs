import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  cp,
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { fileURLToPath } from "node:url";

// Never rewrite the directory a running companion serves. Build in a private
// generation, validate the complete tree, then atomically publish it as the
// installer's pending generation. The installer promotes it only after the old
// service has stopped under the restart interlock.
const stagedRoot = new URL("../static-export.staged/", import.meta.url);
const buildRoot = new URL(
  `../.static-export.staging-${process.pid}-${Date.now()}/`,
  import.meta.url,
);
const displacedRoot = new URL(
  `../.static-export.displaced-${process.pid}-${Date.now()}/`,
  import.meta.url,
);
const productRoot = new URL("../", import.meta.url);
const outputRoot = buildRoot;
const productionOrigin = process.env.PRODUCT_SITE_ORIGIN || "https://axe-pat.github.io";
const productPackage = JSON.parse(
  await readFile(new URL("../package.json", import.meta.url), "utf8"),
);
const productVersion = "1.3.0";
const compatibleCompanionVersion = "0.3.0";
if (productPackage.version !== productVersion) {
  throw new Error(
    `Static compatibility marker expects product ${productVersion}; package.json is ${productPackage.version}`,
  );
}
const compatibilityMarker = {
  schema: "recruiting_engine.static_compatibility",
  schema_version: 1,
  product_version: productVersion,
  compatible_companion_version: compatibleCompanionVersion,
};
const integrityMarkerName = "static-integrity.json";
const routes = [
  { path: "/", output: "index.html" },
  { path: "/story", output: "story/index.html" },
  { path: "/architecture", output: "architecture/index.html" },
  { path: "/privacy", output: "privacy/index.html" },
  { path: "/install", output: "install/index.html" },
  { path: "/app", output: "app/index.html" },
  { path: "/app/onboarding", output: "app/onboarding/index.html" },
  { path: "/app/sources", output: "app/sources/index.html" },
  { path: "/app/queue", output: "app/queue/index.html" },
  { path: "/app/runs", output: "app/runs/index.html" },
  { path: "/app/plan", output: "app/plan/index.html" },
  { path: "/app/applications", output: "app/applications/index.html" },
  { path: "/app/outreach", output: "app/outreach/index.html" },
  { path: "/app/reports", output: "app/reports/index.html" },
  { path: "/app/accounts", output: "app/accounts/index.html" },
  { path: "/app/stories", output: "app/stories/index.html" },
  { path: "/app/operations", output: "app/operations/index.html" },
  { path: "/app/settings", output: "app/settings/index.html" },
];

const workerUrl = new URL("../dist/server/index.js", import.meta.url);
workerUrl.searchParams.set("static-export", `${Date.now()}`);
const { default: worker } = await import(workerUrl.href);

async function integrityEntries(rootUrl, relative = "") {
  const directoryUrl = new URL(relative || "./", rootUrl);
  const entries = await readdir(directoryUrl, { withFileTypes: true });
  const result = [];
  for (const entry of entries.sort((left, right) =>
    left.name < right.name ? -1 : left.name > right.name ? 1 : 0,
  )) {
    const entryRelative = relative ? `${relative}${entry.name}` : entry.name;
    if (entry.isSymbolicLink()) {
      throw new Error(`Static export contains a symbolic link: ${entryRelative}`);
    }
    if (entry.isDirectory()) {
      result.push(...(await integrityEntries(rootUrl, `${entryRelative}/`)));
      continue;
    }
    if (!entry.isFile()) {
      throw new Error(`Static export contains a non-regular entry: ${entryRelative}`);
    }
    if (entryRelative === integrityMarkerName) continue;
    const content = await readFile(new URL(entryRelative, rootUrl));
    result.push({
      path: entryRelative,
      sha256: createHash("sha256").update(content).digest("hex"),
      size_bytes: content.byteLength,
    });
  }
  return result;
}

function validateWithCompanion(rootUrl) {
  const python = process.env.RECRUITING_ENGINE_COMPANION_PYTHON || "python3";
  const companionPath = fileURLToPath(new URL("companion/", productRoot));
  const result = spawnSync(
    python,
    [
      "-c",
      "from pathlib import Path; from recruiting_companion.api import _validated_static_root; _validated_static_root(Path(__import__('sys').argv[1]))",
      fileURLToPath(rootUrl),
    ],
    {
      cwd: fileURLToPath(productRoot),
      env: { ...process.env, PYTHONPATH: companionPath },
      encoding: "utf8",
      timeout: 30_000,
    },
  );
  if (result.status !== 0) {
    throw new Error("Companion rejected the completed staged static export");
  }
}

async function publishValidatedStage() {
  let displaced = false;
  try {
    await rename(stagedRoot, displacedRoot);
    displaced = true;
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  try {
    await rename(buildRoot, stagedRoot);
  } catch (error) {
    if (displaced) await rename(displacedRoot, stagedRoot);
    throw error;
  }
  if (displaced) await rm(displacedRoot, { recursive: true, force: true });
}

await rm(outputRoot, { recursive: true, force: true });
await mkdir(outputRoot, { recursive: true });

try {
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

  await cp(
    new URL("../dist/client/assets/", import.meta.url),
    new URL("assets/", outputRoot),
    { recursive: true },
  );
  await cp(new URL("../public/og.png", import.meta.url), new URL("og.png", outputRoot));
  await writeFile(
    new URL("release-compatibility.json", outputRoot),
    `${JSON.stringify(compatibilityMarker, null, 2)}\n`,
    "utf8",
  );
  await writeFile(new URL(".nojekyll", outputRoot), "", "utf8");
  await cp(new URL("index.html", outputRoot), new URL("404.html", outputRoot));
  const files = (await integrityEntries(outputRoot)).sort((left, right) =>
    left.path < right.path ? -1 : left.path > right.path ? 1 : 0,
  );
  await writeFile(
    new URL(integrityMarkerName, outputRoot),
    `${JSON.stringify(
      {
        schema: "recruiting_engine.static_integrity",
        schema_version: 1,
        files,
      },
      null,
      2,
    )}\n`,
    "utf8",
  );
  validateWithCompanion(outputRoot);
  await publishValidatedStage();
} finally {
  await rm(buildRoot, { recursive: true, force: true });
  await rm(displacedRoot, { recursive: true, force: true });
}

console.log(
  `Static product staged for ${productionOrigin} with ${routes.length} routes; install to promote it.`,
);
