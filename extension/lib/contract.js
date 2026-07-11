export const API_PATHS = Object.freeze({
  health: "/api/v1/health",
  pair: "/api/v1/pair",
  dashboard: "/api/v1/dashboard",
  intakes: "/api/v1/intakes",
  outreach: "/api/v1/outreach",
  contacts: "/api/v1/contacts",
  companies: "/api/v1/companies",
  jobs: "/api/v1/jobs",
});

export const APP_PATHS = Object.freeze({
  dashboard: "/app",
  runs: "/app/runs",
  outreach: "/app/outreach",
});

export const HOSTED_APP_BASE = "https://axe-pat.github.io";

export const LIMITS = Object.freeze({
  selectedText: 8_000,
  pastedText: 20_000,
  note: 2_000,
  title: 500,
  description: 1_500,
  url: 2_048,
  token: 4_096,
});

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1"]);
const BLOCKED_HOSTS = new Set(["linkedin.com", "www.linkedin.com"]);

function cleanText(value, limit) {
  return String(value ?? "")
    .replaceAll("\u0000", "")
    .trim()
    .slice(0, limit);
}

export function normalizeLoopbackBaseUrl(value) {
  const raw = String(value ?? "").trim();
  if (!raw) throw new Error("Enter the local companion URL.");

  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new Error("Enter a valid URL, such as http://127.0.0.1:8765.");
  }

  if (url.protocol !== "http:") {
    throw new Error("The local companion URL must use loopback HTTP.");
  }
  if (!LOOPBACK_HOSTS.has(url.hostname)) {
    throw new Error("For safety, pairing is limited to localhost or 127.0.0.1.");
  }
  if (url.username || url.password) {
    throw new Error("Do not put credentials in the companion URL.");
  }
  if (url.search || url.hash) {
    throw new Error("The companion URL cannot contain a query string or fragment.");
  }
  if (url.pathname && url.pathname !== "/") {
    throw new Error("Use the companion origin only, without an extra path.");
  }

  url.pathname = "/";
  return url.toString().replace(/\/$/, "");
}

export function permissionOriginFor(baseUrl) {
  const url = new URL(normalizeLoopbackBaseUrl(baseUrl));
  return `${url.protocol}//${url.hostname}/*`;
}

export function joinBaseUrl(baseUrl, path) {
  const normalized = normalizeLoopbackBaseUrl(baseUrl);
  const normalizedPath = String(path ?? "").startsWith("/") ? path : `/${path}`;
  return `${normalized}${normalizedPath}`;
}

export function blockedPageReason(value) {
  let url;
  try {
    url = new URL(String(value ?? ""));
  } catch {
    return "This tab does not expose a valid web-page URL.";
  }

  if (!["http:", "https:"].includes(url.protocol)) {
    return "Browser, file, extension, and internal pages cannot be captured.";
  }

  const hostname = url.hostname.toLowerCase();
  if (BLOCKED_HOSTS.has(hostname) || hostname.endsWith(".linkedin.com")) {
    return "Page capture is disabled on LinkedIn. Paste only text you are permitted to use.";
  }

  return null;
}

export function normalizeCapturedPage(input, now = new Date().toISOString()) {
  if (!input || typeof input !== "object") {
    throw new Error("The page did not return capture metadata.");
  }
  const url = cleanText(input.url, LIMITS.url);
  const reason = blockedPageReason(url);
  if (reason) throw new Error(reason);
  return {
    url,
    title: cleanText(input.title, LIMITS.title),
    selectedText: cleanText(input.selectedText, LIMITS.selectedText),
    description: cleanText(input.description, LIMITS.description),
    canonicalUrl: cleanText(input.canonicalUrl, LIMITS.url),
    language: cleanText(input.language, 32),
    capturedAt: cleanText(input.capturedAt, 64) || now,
  };
}

export function buildIntakePayload(input, now = new Date().toISOString()) {
  const page = input?.page ? normalizeCapturedPage(input.page, now) : null;
  const pastedText = cleanText(input?.pastedText, LIMITS.pastedText);
  const note = cleanText(input?.note, LIMITS.note);
  const kind = cleanText(input?.kind || "note", 40).toLowerCase();
  if (!["job", "company", "contact", "note"].includes(kind)) {
    throw new Error("Choose a valid intake type.");
  }

  if (!page && !pastedText) {
    throw new Error("Capture a permitted page or paste context before creating an intake.");
  }

  const title = cleanText(input?.title || page?.title, 1_000);
  if (kind === "job" && !title) {
    throw new Error("Add a title for a job intake.");
  }
  const pageText = page?.selectedText || (!pastedText ? page?.description : "") || "";
  const selectedText = [pageText, pastedText].filter(Boolean).join("\n\n").slice(0, 100_000);

  return {
    source_url: page?.url || "",
    title,
    selected_text: selectedText,
    notes: note,
    kind,
  };
}

function firstObject(...values) {
  return values.find((value) => value && typeof value === "object") ?? {};
}

function firstString(...values) {
  for (const value of values) {
    const text = cleanText(value, 20_000);
    if (text) return text;
  }
  return "";
}

export function normalizeIntakeResponse(payload) {
  if (!payload || typeof payload !== "object") {
    throw new Error("The companion returned an invalid intake response.");
  }

  const intake = firstObject(payload.intake, payload.data, payload);
  const job = firstObject(payload.job);
  const intakeId = firstString(intake.id, payload.intakeId, payload.intake_id);
  if (!intakeId) throw new Error("The companion did not return an intake ID.");
  return {
    intakeId,
    jobId: firstString(job.id, intake.job_id, intake.jobId),
    kind: firstString(intake.kind, "note"),
    title: firstString(intake.title),
    createdAt: firstString(intake.created_at, intake.createdAt),
  };
}

export function pickReviewCandidate(payload) {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const candidates = items.filter((item) => {
    const state = firstString(item?.state).toLowerCase();
    return (
      ["draft", "reviewed"].includes(state) &&
      Boolean(firstString(item?.id)) &&
      Boolean(firstString(item?.contact_id, item?.contactId)) &&
      Boolean(firstString(item?.reviewed_text, item?.draft_text))
    );
  });
  candidates.sort((left, right) => {
    const leftState = firstString(left.state).toLowerCase() === "draft" ? 0 : 1;
    const rightState = firstString(right.state).toLowerCase() === "draft" ? 0 : 1;
    return leftState - rightState;
  });
  return candidates[0] ?? null;
}

export function resourcePath(resource, id) {
  const base = API_PATHS[resource];
  const cleanedId = cleanText(id, 500);
  if (!base || !cleanedId) throw new Error("Missing local resource reference.");
  return `${base}/${encodeURIComponent(cleanedId)}`;
}

export function normalizeOutreachReview(outreachInput, contactInput, companyInput, jobInput) {
  const outreach = firstObject(outreachInput?.outreach, outreachInput);
  const contact = firstObject(contactInput?.contact, contactInput);
  const company = firstObject(companyInput?.company, companyInput);
  const job = firstObject(jobInput?.job, jobInput);
  const outreachId = firstString(outreach.id);
  const contactId = firstString(outreach.contact_id, outreach.contactId);
  const name = firstString(contact.name, contact.full_name, contact.fullName);
  const destination = firstString(contact.email, contact.profile_url, contact.profileUrl);
  const draftBody = firstString(outreach.reviewed_text, outreach.draft_text);

  if (!outreachId || !contactId) {
    throw new Error("This draft does not have a confirmed recipient record.");
  }
  if (!name || !destination) {
    throw new Error("The recipient needs both a display name and an email or profile URL.");
  }
  if (!draftBody) throw new Error("The local outreach record does not contain a full draft.");

  return {
    outreachId,
    contactId,
    updatedAt: firstString(outreach.updated_at, outreach.updatedAt),
    state: firstString(outreach.state, "draft").toLowerCase(),
    recipient: {
      name,
      role: firstString(contact.relationship, contact.status),
      company: firstString(company.name, job.company_name, job.company, "Company not linked"),
      channel: firstString(outreach.channel, "Review queue"),
      destination,
    },
    draft: {
      subject: firstString(job.title, company.name, "Message review"),
      body: draftBody,
    },
  };
}

export function approvalPath(outreachId) {
  const id = cleanText(outreachId, 500);
  if (!id) throw new Error("Missing outreach review ID.");
  return `${API_PATHS.outreach}/${encodeURIComponent(id)}`;
}

export function allowedAppPath(path) {
  const values = Object.values(APP_PATHS);
  if (!values.includes(path)) throw new Error("That companion destination is not allowed.");
  return path;
}

export function hostedAppUrl(path) {
  return `${HOSTED_APP_BASE}${allowedAppPath(path)}`;
}
