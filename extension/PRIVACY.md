# Privacy disclosure — Recruiting Engine Companion

Last updated: July 11, 2026

## Single purpose

Recruiting Engine Companion lets a user deliberately move limited browser context into a Recruiting Engine service running on that user's own computer and review local outreach drafts. It does not scrape websites or send messages.

## Data the user may choose to process

The extension can process the following only after an explicit user action:

- text the user selects on the current permitted page;
- current-page URL and title;
- page language, meta description, and canonical URL;
- text the user directly pastes or types into the panel;
- a local intake type and note;
- local companion outreach records needed to display a recipient and complete draft for review.

Page capture is disabled on LinkedIn and on browser, file, extension, and other non-HTTP/S pages.

## Local data storage

The extension stores only local companion pairing information in `chrome.storage.local`: loopback base URL, bearer token, health timestamps, and companion version. It does not persist captured page text, pasted text, recipients, or drafts in extension storage.

Captured context and explicit approval transitions are sent only to the paired HTTP loopback host (`localhost` or `127.0.0.1`) and exact configured port. The local companion controls its own on-device retention.

## Data sharing

The extension does not send data to the extension developer, an analytics provider, an advertising network, or another third party. It contains no telemetry or remote code. It does not sell or use data for advertising, credit, insurance, or unrelated profiling.

## User control

- The user decides whether to capture a page, what to select, what to paste, and whether to save an intake.
- Recipient and draft confirmations are separate and required before a local approval.
- Approval changes local workflow state only; it does not send a message.
- Disconnect removes the saved token/configuration and asks Chrome to remove the optional loopback-host grant.
- The user can also remove all extension data by removing the extension or clearing its site/extension data in Chrome.

## Security

- Host access is optional and limited to loopback HTTP host patterns; requests still use only the configured base URL and port.
- Saved tokens are used only in service-worker `Authorization` headers and are never injected into a web page.
- Redirects are rejected for companion API calls.
- Requests time out after ten seconds.
- Review approval fails closed if the recipient, destination, draft text, or record timestamp changes after display.
- No content script remains installed on pages.

## Contact

The hosted policy is <https://axe-pat.github.io/privacy/>. Privacy or security
questions can be filed through the repository's private security-reporting
surface at <https://github.com/axe-pat/recruiting-engine-product/security>.
The Chrome Web Store owner supplies the monitored account contact during submission.
