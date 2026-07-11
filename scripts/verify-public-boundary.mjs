import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { extname, join } from "node:path";

const root = new URL("../", import.meta.url);
const publicSourceRoots = ["app", "components", "lib"];
const sourceExtensions = new Set([".ts", ".tsx", ".css"]);

const prohibitedPatterns = [
  { label: "local user path", expression: /\/Users\//i },
  { label: "personal LinkedIn profile URL", expression: /linkedin\.com\/(?:in|pub)\//i },
  { label: "email address", expression: /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i },
  { label: "SMTP secret/config key", expression: /SMTP_(?:HOST|PASSWORD|USERNAME|FROM_EMAIL)/i },
  { label: "provider API key", expression: /(?:OPENAI|HUNTER|PROSPEO)_API_KEY/i },
  { label: "private browser profile", expression: /chrome-data|browser-session|playwright\/.auth/i },
];

async function listSourceFiles(relativeRoot) {
  const directory = new URL(`${relativeRoot}/`, root);
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];

  for (const entry of entries) {
    const relativePath = join(relativeRoot, entry.name);
    if (entry.isDirectory()) {
      files.push(...(await listSourceFiles(relativePath)));
    } else if (sourceExtensions.has(extname(entry.name))) {
      files.push(relativePath);
    }
  }

  return files;
}

const files = (
  await Promise.all(publicSourceRoots.map((directory) => listSourceFiles(directory)))
).flat();

for (const file of files) {
  const source = await readFile(new URL(file, root), "utf8");
  for (const pattern of prohibitedPatterns) {
    assert.doesNotMatch(source, pattern.expression, `${file} contains ${pattern.label}`);
  }
}

const productData = await readFile(new URL("lib/product-data.ts", root), "utf8");
assert.match(productData, /demo:\s*true/g, "Demo queue rows must remain explicitly labeled");
assert.match(productData, /dataClass:\s*"fictional-demo"/g);
assert.match(productData, /liveWorkspaceConnected:\s*false/);
assert.doesNotMatch(productData, /target_company|full_name|message_text|source_url/i);

console.log(`Public boundary verified across ${files.length} source files.`);
