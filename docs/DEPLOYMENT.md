# Deployment

## Live surfaces

- Product: <https://axe-pat.github.io/>
- Product story: <https://axe-pat.github.io/story/>
- Architecture: <https://axe-pat.github.io/architecture/>
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
other prohibited production material. Product tests render all three routes and
assert that the case-study, architecture, disclosure, and release evidence are
present.

## Static release

The source remains Cloudflare Workers/Sites compatible. The current workspace did
not have Sites enabled, so the production release uses a deterministic static
export and GitHub Pages:

```bash
npm run export:static
```

`scripts/export-static.mjs` renders `/`, `/story`, and `/architecture` from the
same validated server build, copies the exact client assets and social image,
adds `.nojekyll`, and writes a root fallback. The deployment repository serves
the generated output from `main` at the user Pages origin.

The deployment commit message should include the corresponding source commit so
the public artifact can be traced back to its tested source.

## Data boundary

Deployment never reads the operating repositories or their workspaces. The
public bundle consumes only `lib/product-data.ts`, which contains reviewed
aggregates and fictional demo rows. The safe refresh process is documented in
`product-notes/data-portability.md`.
