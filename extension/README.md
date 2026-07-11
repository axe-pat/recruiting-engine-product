# Recruiting Engine Companion

A Chrome Manifest V3 side-panel companion for the local Recruiting Engine. It gives the user a small, deliberate bridge between a browser page and the private local product:

1. explicitly capture selected text plus minimal current-page metadata, or paste text directly;
2. save that context as a local intake;
3. load a fully prepared local outreach draft with a resolvable recipient;
4. separately confirm the recipient and the complete draft;
5. transition the record from `draft` → `reviewed` → `approved`.

Approval only changes local workflow state. The extension has no send action and never records a message as sent.

## Product boundaries

- No persistent content scripts.
- No background browsing, page polling, tab history, or network interception.
- No LinkedIn capture, scraping, DOM traversal, or automation. Page capture is explicitly blocked for `linkedin.com` and its subdomains.
- No clipboard permission. The paste field accepts a normal user-initiated paste.
- No email or professional-network send capability.
- No remote code, analytics, advertising, or telemetry.
- No access to arbitrary internet hosts. The only optional host patterns are HTTP loopback hosts.

The only injected function runs after the user presses **Capture selected text + page**. It reads `window.getSelection()`, `document.title`, the current URL, language, meta description, and canonical URL. It does not enumerate page elements or extract structured people/company records.

## Install locally

1. Start the local companion (default `http://127.0.0.1:8765`) and obtain its one-time token beginning with `re_pair_` (or an existing bearer beginning with `re_local_`).
2. Open `chrome://extensions`.
3. Enable **Developer mode**.
4. Choose **Load unpacked** and select this `extension/` directory.
5. Pin **Recruiting Engine Companion**, open it from the toolbar, expand **Local companion pairing**, and enter the base URL and token.
6. Chrome requests access only to the selected loopback host pattern. The extension checks public `/api/v1/health`, exchanges `re_pair_` through `/api/v1/pair` when needed, then verifies the bearer against protected `/api/v1/dashboard` before saving it.

Opening the side panel from the toolbar also supplies Chrome's temporary `activeTab` grant for an explicit page capture. Navigating away or closing the tab ends that grant.

## Pairing and local storage

The following object is stored under `recruitingEngineCompanion` in `chrome.storage.local`:

- normalized loopback base URL;
- verified `re_local_` bearer token (a one-time `re_pair_` token is never stored);
- paired-at and last-health timestamps;
- companion version returned by health.

The saved token is never returned to or rendered by the side-panel UI. API requests are made by the service worker with `Authorization: Bearer …`. Disconnect removes the stored object and releases the optional loopback-host permission.

## Canonical companion contract

All protected calls use the saved bearer token.

### Health

```http
GET /api/v1/health
```

Expected shape:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "mode": "local",
  "auth_required": true
}
```

### One-time pairing and protected verification

```http
POST /api/v1/pair
Content-Type: application/json

{"pairing_token":"re_pair_..."}
```

The response is `{"bearer_token":"re_local_...","token_type":"Bearer"}`. The one-time token is consumed. The extension then calls protected `GET /api/v1/dashboard`; it saves the bearer only after that succeeds. A user may also enter an existing `re_local_` bearer, which is verified directly against the dashboard.

### Browser intake

```http
POST /api/v1/intakes
Content-Type: application/json
```

```json
{
  "source_url": "https://example.com/role",
  "title": "Associate Product Manager",
  "selected_text": "User-selected or directly pasted text",
  "notes": "Assess fit and prepare a concise relationship note",
  "kind": "job"
}
```

`kind` is one of `job`, `company`, `contact`, or `note`. The service may create a job record for `job` intake and returns `{ "intake": {...}, "job": {...} | null }`.

### Review queue

```http
GET /api/v1/outreach?limit=100&offset=0
GET /api/v1/outreach/{id}
GET /api/v1/contacts/{id}
GET /api/v1/companies/{id}
GET /api/v1/jobs/{id}
```

The extension considers only `draft` or `reviewed` items with full draft text and a contact ID. It resolves the contact before displaying a review and requires both a display name and an email or profile URL. Unresolvable items remain untouched.

### Explicit review and approval

After both UI confirmations, a `draft` item receives two separate transitions:

```http
PATCH /api/v1/outreach/{id}

{
  "state": "reviewed",
  "actor": "extension-user",
  "reviewed_text": "the exact complete text displayed to the user",
  "note": "Recipient and complete text confirmed in Chrome companion"
}
```

```http
PATCH /api/v1/outreach/{id}

{
  "state": "approved",
  "actor": "extension-user",
  "note": "Explicit user approval from Chrome companion"
}
```

Before the transitions, the service worker re-fetches the record and recipient and fails closed if the recipient, destination, update timestamp, or draft text changed after review. It never calls a `sent` transition.

## Dashboard links

The three fixed destinations open on the hosted HTTPS product surface at `https://axe-pat.github.io`. They do not come from companion response data and do not require broad host permissions:

- `/app` — dashboard;
- `/app/runs` — run evidence;
- `/app/outreach` — reviewed outreach queue.

No arbitrary URL from a companion response is opened.

## Files

```text
extension/
├── manifest.json
├── service-worker.js
├── sidepanel.html
├── sidepanel.css
├── sidepanel.js
├── lib/contract.js
├── icons/
│   ├── icon.svg
│   └── icon-{16,32,48,128}.png
├── tools/generate-icons.mjs
├── tests/
├── PRIVACY.md
└── STORE_REVIEW.md
```

## Validation

The test suite uses only Node's standard library:

```bash
node --test tests/*.test.mjs
node --check service-worker.js
node --check sidepanel.js
node --check lib/contract.js
python3 -m json.tool manifest.json >/dev/null
```

Regenerate deterministic code-native PNG icons with:

```bash
node tools/generate-icons.mjs
```

## Packaging checklist

1. Run validation.
2. Load the directory unpacked and inspect the side panel at narrow and wide panel widths.
3. Pair against a clean local companion fixture.
4. Verify capture on a normal HTTPS page and verify explicit refusal on LinkedIn and internal Chrome pages.
5. Verify intake, stale-review rejection, recipient mismatch rejection, reviewed/approved transitions, and disconnect.
6. Zip the contents of `extension/` (not the parent directory) for store upload.
7. Use [PRIVACY.md](./PRIVACY.md) as the privacy disclosure and [STORE_REVIEW.md](./STORE_REVIEW.md) for permission justifications and reviewer steps.
