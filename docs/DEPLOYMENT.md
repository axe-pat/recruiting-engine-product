# Deployment

## Live surfaces

- Product: <https://axe-pat.github.io/>
- Working app: <https://axe-pat.github.io/app/>
- Onboarding: <https://axe-pat.github.io/app/onboarding/>
- Product story: <https://axe-pat.github.io/story/>
- Architecture: <https://axe-pat.github.io/architecture/>
- Privacy policy: <https://axe-pat.github.io/privacy/>
- Source: <https://github.com/axe-pat/recruiting-engine-product>
- Static deployment repository: <https://github.com/axe-pat/axe-pat.github.io>

## Release checks

Run these before publishing:

```bash
npm run verify:privacy
npm run lint
npx tsc --noEmit
npm test
npm audit --omit=dev
```

The privacy check scans the hosted source boundary for personal identifiers,
profile URLs, email addresses, local user paths, credentials, browser state, and
other prohibited production material. Product tests render the public story,
architecture, command center, onboarding, source setup, and privacy routes and
assert that the product boundary, human gates, and release evidence are present.

## Static release

The source remains Cloudflare Workers/Sites compatible. The current workspace did
not have Sites enabled, so the production release uses a deterministic static
export and GitHub Pages:

```bash
npm run export:static
```

`scripts/export-static.mjs` renders the product, story, architecture, privacy,
and every `/app/*` surface from the same server build. It copies the exact client
assets and social image, adds `.nojekyll`, writes a root fallback, emits the
release compatibility marker, hashes the complete tree into
`static-integrity.json`, and validates it with the companion. The completed tree
is atomically published as `static-export.staged/`; the exporter never rewrites
the live directory a local companion may be serving. Publish that exact staged
directory to the deployment repository before a local install consumes it, or
publish the promoted `static-export/` afterward. Never publish the private
`.static-export.staging-*` or rollback directories.
The deployment repository serves the generated output from `main` at the user
Pages origin.

The deployment commit message should include the corresponding source commit so
the public artifact can be traced back to its tested source.

## Data boundary

Deployment never reads the operating repositories or their workspaces. The
public bundle contains only reviewed product-story material and an empty,
connection-gated operational shell—never local operator records. After hosted
pairing, client-side requests go directly to a loopback companion; GitHub Pages
does not proxy or store that traffic. The primary same-origin local surface and
safe refresh process are documented in
[`PRIMARY_LOCAL_UI.md`](PRIMARY_LOCAL_UI.md).

## Companion and extension release

The local companion ships as source under `companion/` and requires only
Python's standard library. The MV3 extension ships under `extension/` and is
packaged independently from the hosted site. Before a store upload, run the
extension validation, create a zip whose root is the contents of `extension/`,
confirm the hosted privacy policy is live, and complete the store data-use form
using `extension/STORE_REVIEW.md`.

macOS operators can run the companion as a loopback-only per-user service using
the reversible, non-secret LaunchAgent flow in
[`OPERATOR_SETUP.md`](OPERATOR_SETUP.md). This local service installation is
separate from publishing the hosted site. The plist runs a supported
non-Desktop Python directly with the product checkout on `PYTHONPATH`, avoiding
macOS's protected Desktop script-execution failure while keeping the live local
engines as the source of truth.

Publishing to the Chrome Web Store is a separate external release gate. It needs
the owner's verified developer account, final support contact, listing assets,
and Google review. A source push or GitHub Pages deploy does not imply store
approval.
