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
and every `/app/*` surface from the same validated server build. It copies the
exact client assets and social image, adds `.nojekyll`, and writes a root fallback.
The deployment repository serves the generated output from `main` at the user
Pages origin.

The deployment commit message should include the corresponding source commit so
the public artifact can be traced back to its tested source.

## Data boundary

Deployment never reads the operating repositories or their workspaces. The
public bundle contains only reviewed aggregates and fictional demo rows. After
pairing, client-side requests go directly to a loopback companion; GitHub Pages
does not proxy or store that traffic. The safe refresh process is documented in
`product-notes/data-portability.md`.

## Companion and extension release

The local companion ships as source under `companion/` and requires only
Python's standard library. The MV3 extension ships under `extension/` and is
packaged independently from the hosted site. Before a store upload, run the
extension validation, create a zip whose root is the contents of `extension/`,
confirm the hosted privacy policy is live, and complete the store data-use form
using `extension/STORE_REVIEW.md`.

Publishing to the Chrome Web Store is a separate external release gate. It needs
the owner's verified developer account, final support contact, listing assets,
and Google review. A source push or GitHub Pages deploy does not imply store
approval.
