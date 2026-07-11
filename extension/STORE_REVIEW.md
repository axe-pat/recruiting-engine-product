# Chrome Web Store review notes

## Single-purpose statement

Recruiting Engine Companion is a user-triggered bridge to a Recruiting Engine service running on the user's own computer. It captures only selected text and minimal current-page metadata, accepts direct paste input, saves local intake records, and lets the user confirm a local recipient and complete draft before marking the local record reviewed/approved. It cannot send outreach.

## Permission justifications

| Permission | Why it is required | Boundary |
|---|---|---|
| `activeTab` | Gives temporary access to the current page after the user opens/invokes the extension. | No persistent host access; grant ends on navigation/tab close. |
| `scripting` | Runs one short function after the user presses the capture button to read selection and minimal page metadata. | No registered content scripts, DOM traversal, monitoring, or automation. LinkedIn is blocked. |
| `sidePanel` | Provides the core companion review UI beside the page. | No hidden page or background browsing surface. |
| `storage` | Stores the loopback base URL, bearer token, and pairing timestamps. | Captured context and drafts are not stored by the extension. |
| Optional loopback host access | Calls the user's local companion API after pairing. | Only `localhost` and `127.0.0.1` over HTTP; permission is requested at pair time and removed on disconnect. |

The extension deliberately does **not** request `tabs`, `history`, `webNavigation`, `clipboardRead`, `clipboardWrite`, `cookies`, `downloads`, `notifications`, declarative network access, or broad internet host permissions.

## Reviewer walkthrough

1. Load the extension and click its toolbar icon; Chrome opens the side panel.
2. Without pairing, note that local write/review actions and product links are disabled.
3. On a normal HTTPS page, select a short sentence and press **Capture selected text + page**. The panel shows only that sentence, title, and hostname.
4. Open a LinkedIn page and press the same button. The extension refuses capture.
5. Paste text into the panel. No clipboard permission dialog appears because paste is handled by the standard field.
6. Pair to the supplied review fixture on `127.0.0.1` with the supplied one-time `re_pair_…` token. Chrome asks for only that loopback host; the extension exchanges and verifies the resulting `re_local_…` bearer.
7. Save an intake and inspect it in the fixture.
8. Load a prepared draft. Confirm that the Approve button remains disabled until both recipient and complete-draft checkboxes are selected.
9. Approve and inspect API events: the item transitions `draft` → `reviewed` → `approved`; no request transitions it to `sent` and no channel API is called.
10. Disconnect and verify that configuration and optional loopback-host access are removed.

## Remote code and content security

- All JavaScript ships in the extension package.
- There are no remote scripts, WebAssembly modules, eval-style execution, external fonts, or CDNs.
- The service worker and side-panel script are ES modules loaded from the package.
- Companion responses are inserted with `textContent`, never executable markup.

## Data-use certification notes

- Website content is handled only to provide the user-visible intake feature.
- Authentication information is used only to connect to the user's local companion.
- Data is not transferred to the developer or third parties.
- Data is not used for personalized advertising, lending, insurance, or sale.
- Humans do not review user content through this extension.

## Store assets still needed before submission

- final support email and hosted privacy URL;
- store screenshots at required dimensions;
- promotional tile(s), if used;
- a packaged test fixture and temporary reviewer token;
- completed Chrome Web Store data-use questionnaire matching `PRIVACY.md`.
