import {
  API_PATHS,
  LIMITS,
  allowedAppPath,
  approvalPath,
  blockedPageReason,
  buildIntakePayload,
  hostedAppUrl,
  joinBaseUrl,
  normalizeCapturedPage,
  normalizeIntakeResponse,
  normalizeLoopbackBaseUrl,
  normalizeOutreachReview,
  pickReviewCandidate,
  permissionOriginFor,
  resourcePath,
} from "./lib/contract.js";

const CONFIG_KEY = "recruitingEngineCompanion";
const FETCH_TIMEOUT_MS = 10_000;

chrome.runtime.onInstalled.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

chrome.runtime.onStartup.addListener(() => {
  void chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  void handleMessage(message)
    .then((data) => sendResponse({ ok: true, data }))
    .catch((error) => sendResponse({ ok: false, error: userFacingError(error) }));
  return true;
});

async function handleMessage(message) {
  switch (message?.type) {
    case "GET_STATE":
      return publicConfig(await getConfig());
    case "PAIR_COMPANION":
      return pairCompanion(message.payload);
    case "DISCONNECT_COMPANION":
      return disconnectCompanion();
    case "CAPTURE_ACTIVE_PAGE":
      return captureActivePage();
    case "CREATE_INTAKE":
      return createIntake(message.payload);
    case "LOAD_NEXT_REVIEW":
      return loadNextReview();
    case "APPROVE_OUTREACH":
      return approveOutreach(message.payload);
    case "OPEN_APP_PATH":
      return openAppPath(message.payload?.path);
    default:
      throw new Error("Unknown companion action.");
  }
}

async function getConfig() {
  const stored = await chrome.storage.local.get(CONFIG_KEY);
  return stored[CONFIG_KEY] ?? null;
}

function publicConfig(config) {
  if (!config) {
    return {
      paired: false,
      baseUrl: "http://127.0.0.1:8765",
      tokenSaved: false,
      pairedAt: null,
      lastHealthAt: null,
    };
  }
  return {
    paired: true,
    baseUrl: config.baseUrl,
    tokenSaved: Boolean(config.token),
    pairedAt: config.pairedAt ?? null,
    lastHealthAt: config.lastHealthAt ?? null,
  };
}

async function pairCompanion(payload) {
  const current = await getConfig();
  const baseUrl = normalizeLoopbackBaseUrl(payload?.baseUrl ?? current?.baseUrl);
  const suppliedToken = String(payload?.token ?? "").trim();
  const token = suppliedToken || (current?.baseUrl === baseUrl ? current?.token : "") || "";
  if (!token) throw new Error("Enter a companion token.");
  if (token.length > LIMITS.token) throw new Error("The companion token is unexpectedly long.");

  const origin = permissionOriginFor(baseUrl);
  const permitted = await chrome.permissions.contains({ origins: [origin] });
  if (!permitted) {
    throw new Error("Approve access to the selected local companion before pairing.");
  }

  const health = await apiFetch({ baseUrl, token: "" }, API_PATHS.health, { method: "GET" });
  if (health?.status !== "ok" || health?.auth_required !== true) {
    throw new Error("That loopback service is not a compatible Recruiting Engine companion.");
  }
  let bearer = token;
  if (token.startsWith("re_pair_")) {
    const exchange = await apiFetch({ baseUrl, token: "" }, API_PATHS.pair, {
      method: "POST",
      body: JSON.stringify({ pairing_token: token }),
    });
    bearer = String(exchange?.bearer_token ?? "").trim();
    if (!bearer.startsWith("re_local_")) {
      throw new Error("The companion did not return a valid local bearer token.");
    }
  } else if (!token.startsWith("re_local_")) {
    throw new Error("Use a one-time re_pair_ token or an existing re_local_ bearer.");
  }

  const dashboard = await apiFetch({ baseUrl, token: bearer }, API_PATHS.dashboard, {
    method: "GET",
  });
  if (!dashboard?.snapshot || typeof dashboard.snapshot !== "object") {
    throw new Error("The protected companion dashboard returned an invalid response.");
  }
  const timestamp = new Date().toISOString();
  const config = {
    baseUrl,
    token: bearer,
    pairedAt: current?.pairedAt ?? timestamp,
    lastHealthAt: timestamp,
    companionVersion: String(health?.version ?? health?.apiVersion ?? ""),
  };
  await chrome.storage.local.set({ [CONFIG_KEY]: config });
  return {
    ...publicConfig(config),
    health,
    dashboardVerified: Boolean(dashboard?.snapshot),
  };
}

async function disconnectCompanion() {
  const current = await getConfig();
  await chrome.storage.local.remove(CONFIG_KEY);
  if (current?.baseUrl) {
    await chrome.permissions.remove({ origins: [permissionOriginFor(current.baseUrl)] });
  }
  return {
    ...publicConfig(null),
    releasedOrigin: current?.baseUrl ? permissionOriginFor(current.baseUrl) : null,
  };
}

async function captureActivePage() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab?.id) throw new Error("No active browser tab is available.");

  const reason = blockedPageReason(tab.url);
  if (reason) throw new Error(reason);

  let injection;
  try {
    [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        const selectedText = String(window.getSelection?.() ?? "").trim();
        const description = document.querySelector('meta[name="description"]')?.content ?? "";
        const canonicalUrl = document.querySelector('link[rel="canonical"]')?.href ?? "";
        return {
          url: window.location.href,
          title: document.title,
          selectedText,
          description,
          canonicalUrl,
          language: document.documentElement.lang || navigator.language || "",
          capturedAt: new Date().toISOString(),
        };
      },
    });
  } catch (error) {
    throw new Error(
      `Chrome did not grant access to this tab. Re-open the panel from the toolbar and try again. ${
        error instanceof Error ? error.message : ""
      }`.trim(),
    );
  }

  const page = injection?.result;
  if (!page) throw new Error("The page did not return capture metadata.");
  return normalizeCapturedPage(page, page.capturedAt);
}

async function createIntake(payload) {
  const config = await requireConfig();
  const body = buildIntakePayload(payload);
  const response = await apiFetch(config, API_PATHS.intakes, {
    method: "POST",
    body: JSON.stringify(body),
  });
  return normalizeIntakeResponse(response);
}

async function loadNextReview() {
  const config = await requireConfig();
  const response = await apiFetch(config, `${API_PATHS.outreach}?limit=100&offset=0`, {
    method: "GET",
  });
  const remaining = Array.isArray(response?.items) ? [...response.items] : [];

  while (remaining.length > 0) {
    const candidate = pickReviewCandidate({ items: remaining });
    if (!candidate) break;
    const index = remaining.findIndex((item) => item?.id === candidate.id);
    if (index >= 0) remaining.splice(index, 1);

    const detail = await apiFetch(
      config,
      resourcePath("outreach", candidate.id),
      { method: "GET", allowNotFound: true },
    );
    if (!detail) continue;
    const outreach = detail.outreach ?? detail;
    const contactId = outreach.contact_id ?? outreach.contactId;
    const contact = await apiFetch(
      config,
      resourcePath("contacts", contactId),
      { method: "GET", allowNotFound: true },
    );
    if (!contact) continue;

    let job = null;
    if (outreach.job_id ?? outreach.jobId) {
      job = await apiFetch(
        config,
        resourcePath("jobs", outreach.job_id ?? outreach.jobId),
        { method: "GET", allowNotFound: true },
      );
    }
    const jobValue = job?.job ?? job;
    const companyId =
      outreach.company_id ?? outreach.companyId ?? jobValue?.company_id ?? jobValue?.companyId;
    let company = null;
    if (companyId) {
      company = await apiFetch(
        config,
        resourcePath("companies", companyId),
        { method: "GET", allowNotFound: true },
      );
    }

    try {
      return normalizeOutreachReview(detail, contact, company, job);
    } catch {
      // Keep unresolved records in Draft. Try the next fully reviewable local record.
    }
  }

  throw new Error(
    "No reviewable draft has a resolvable recipient and full text. Open Outreach to prepare one.",
  );
}

async function approveOutreach(payload) {
  if (!payload?.recipientConfirmed || !payload?.draftReviewed) {
    throw new Error("Confirm both the recipient and draft before approval.");
  }
  const config = await requireConfig();
  const path = approvalPath(payload.outreachId);
  const currentPayload = await apiFetch(config, path, { method: "GET" });
  const current = currentPayload?.outreach ?? currentPayload;
  const currentState = String(current?.state ?? "").toLowerCase();
  const currentText = String(current?.reviewed_text || current?.draft_text || "").trim();
  if (!["draft", "reviewed"].includes(currentState)) {
    throw new Error(`This outreach item is now ${currentState || "unavailable"}; reload the review.`);
  }
  if (String(current?.contact_id ?? "") !== String(payload?.contactId ?? "")) {
    throw new Error("The recipient changed after review. Reload and confirm the current recipient.");
  }
  const currentContactPayload = await apiFetch(
    config,
    resourcePath("contacts", current.contact_id),
    { method: "GET" },
  );
  const currentContact = currentContactPayload?.contact ?? currentContactPayload;
  const currentRecipientName = String(currentContact?.name ?? "").trim();
  const currentDestination = String(
    currentContact?.email || currentContact?.profile_url || "",
  ).trim();
  if (
    currentRecipientName !== String(payload?.recipientName ?? "").trim() ||
    currentDestination !== String(payload?.recipientDestination ?? "").trim()
  ) {
    throw new Error("The recipient details changed after review. Reload and confirm them again.");
  }
  if (String(current?.updated_at ?? "") !== String(payload?.updatedAt ?? "")) {
    throw new Error("The draft changed after it was loaded. Reload and review the latest version.");
  }
  if (!currentText || currentText !== String(payload?.draftBody ?? "").trim()) {
    throw new Error("The reviewed text no longer matches the local record. Reload the draft.");
  }

  if (currentState === "draft") {
    await apiFetch(config, path, {
      method: "PATCH",
      body: JSON.stringify({
        state: "reviewed",
        actor: "extension-user",
        reviewed_text: currentText,
        note: "Recipient and complete text confirmed in Chrome companion",
      }),
    });
  }
  const response = await apiFetch(config, path, {
    method: "PATCH",
    body: JSON.stringify({
      state: "approved",
      actor: "extension-user",
      note: "Explicit user approval from Chrome companion",
    }),
  });
  return {
    outreachId: String(payload.outreachId),
    status: String(response?.outreach?.state ?? response?.state ?? "approved"),
  };
}

async function openAppPath(path) {
  const safePath = allowedAppPath(path);
  const url = hostedAppUrl(safePath);
  await chrome.tabs.create({ url });
  return { url };
}

async function requireConfig() {
  const config = await getConfig();
  if (!config?.baseUrl || !config?.token) {
    throw new Error("Pair the local companion first.");
  }
  return config;
}

async function apiFetch(config, path, init) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const { allowNotFound = false, ...requestInit } = init ?? {};
    const response = await fetch(joinBaseUrl(config.baseUrl, path), {
      ...requestInit,
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(config.token ? { Authorization: `Bearer ${config.token}` } : {}),
        ...(requestInit.body ? { "Content-Type": "application/json" } : {}),
        ...(requestInit.headers ?? {}),
      },
    });
    if (allowNotFound && response.status === 404) return null;
    if (!response.ok) {
      const message = await readErrorMessage(response);
      throw new Error(`Companion request failed (${response.status}).${message ? ` ${message}` : ""}`);
    }
    if (response.status === 204) return {};
    return await response.json();
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error("The local companion did not respond within 10 seconds.");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

async function readErrorMessage(response) {
  try {
    const payload = await response.json();
    const value = payload?.error?.message ?? payload?.error ?? payload?.message ?? "";
    return String(value).slice(0, 300);
  } catch {
    return "";
  }
}

function userFacingError(error) {
  if (error instanceof Error && error.message) return error.message;
  return "The companion could not complete that action.";
}
