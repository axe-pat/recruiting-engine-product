import { permissionOriginFor } from "./lib/contract.js";

const elements = {
  connectionButton: document.querySelector("#connectionButton"),
  connectionLabel: document.querySelector("#connectionLabel"),
  pairBanner: document.querySelector("#pairBanner"),
  openSettingsButton: document.querySelector("#openSettingsButton"),
  settingsPanel: document.querySelector("#settingsPanel"),
  baseUrlInput: document.querySelector("#baseUrlInput"),
  tokenInput: document.querySelector("#tokenInput"),
  tokenState: document.querySelector("#tokenState"),
  toggleTokenButton: document.querySelector("#toggleTokenButton"),
  pairButton: document.querySelector("#pairButton"),
  disconnectButton: document.querySelector("#disconnectButton"),
  settingsStatus: document.querySelector("#settingsStatus"),
  captureButton: document.querySelector("#captureButton"),
  sourcePreview: document.querySelector("#sourcePreview"),
  sourceTitle: document.querySelector("#sourceTitle"),
  sourceHost: document.querySelector("#sourceHost"),
  sourceSelection: document.querySelector("#sourceSelection"),
  sourceEmpty: document.querySelector("#sourceEmpty"),
  clearSourceButton: document.querySelector("#clearSourceButton"),
  kindInput: document.querySelector("#kindInput"),
  titleInput: document.querySelector("#titleInput"),
  pasteInput: document.querySelector("#pasteInput"),
  pasteCounter: document.querySelector("#pasteCounter"),
  noteInput: document.querySelector("#noteInput"),
  createIntakeButton: document.querySelector("#createIntakeButton"),
  intakeActionNote: document.querySelector("#intakeActionNote"),
  reviewLoader: document.querySelector("#reviewLoader"),
  loadDraftButton: document.querySelector("#loadDraftButton"),
  reviewCard: document.querySelector("#reviewCard"),
  reviewStatus: document.querySelector("#reviewStatus"),
  runReference: document.querySelector("#runReference"),
  openRunButton: document.querySelector("#openRunButton"),
  recipientName: document.querySelector("#recipientName"),
  recipientRole: document.querySelector("#recipientRole"),
  recipientCompany: document.querySelector("#recipientCompany"),
  recipientChannel: document.querySelector("#recipientChannel"),
  recipientDestination: document.querySelector("#recipientDestination"),
  recipientConfirmed: document.querySelector("#recipientConfirmed"),
  draftSubject: document.querySelector("#draftSubject"),
  draftBody: document.querySelector("#draftBody"),
  draftReviewed: document.querySelector("#draftReviewed"),
  approveButton: document.querySelector("#approveButton"),
  linkState: document.querySelector("#linkState"),
  appLinkButtons: [...document.querySelectorAll("[data-app-path]")],
  stepItems: [...document.querySelectorAll(".step-track li")],
  toast: document.querySelector("#toast"),
};

const state = {
  config: null,
  page: null,
  review: null,
  toastTimer: null,
};

void initialize();

elements.connectionButton.addEventListener("click", openSettings);
elements.openSettingsButton.addEventListener("click", openSettings);
elements.toggleTokenButton.addEventListener("click", toggleTokenVisibility);
elements.pairButton.addEventListener("click", pairCompanion);
elements.disconnectButton.addEventListener("click", disconnectCompanion);
elements.captureButton.addEventListener("click", capturePage);
elements.clearSourceButton.addEventListener("click", clearPage);
elements.createIntakeButton.addEventListener("click", createIntake);
elements.loadDraftButton.addEventListener("click", loadNextReview);
elements.openRunButton.addEventListener("click", () => openAppPath("/app/outreach"));
elements.recipientConfirmed.addEventListener("change", updateApprovalState);
elements.draftReviewed.addEventListener("change", updateApprovalState);
elements.approveButton.addEventListener("click", approveReview);
elements.pasteInput.addEventListener("input", updateIntakeState);
elements.noteInput.addEventListener("input", updateIntakeState);
elements.titleInput.addEventListener("input", updateIntakeState);
elements.kindInput.addEventListener("change", updateIntakeState);
for (const button of elements.appLinkButtons) {
  button.addEventListener("click", () => openAppPath(button.dataset.appPath));
}

async function initialize() {
  try {
    const config = await callWorker({ type: "GET_STATE" });
    applyConfig(config);
  } catch (error) {
    showToast(messageFor(error), true);
    applyConfig({ paired: false, baseUrl: "http://127.0.0.1:8765", tokenSaved: false });
  }
  updateIntakeState();
}

function applyConfig(config) {
  state.config = config;
  const paired = Boolean(config?.paired);
  elements.connectionButton.classList.toggle("paired", paired);
  elements.connectionLabel.textContent = paired ? "Paired locally" : "Not paired";
  elements.pairBanner.hidden = paired;
  elements.disconnectButton.hidden = !paired;
  elements.baseUrlInput.value = config?.baseUrl || "http://127.0.0.1:8765";
  elements.tokenState.textContent = config?.tokenSaved ? "Saved locally" : "Required";
  elements.tokenInput.placeholder = config?.tokenSaved ? "Saved token (leave blank to reuse)" : "Paste local token";
  elements.loadDraftButton.disabled = !paired;
  elements.linkState.textContent = "Hosted HTTPS";
  for (const button of elements.appLinkButtons) button.disabled = false;
  elements.intakeActionNote.textContent = paired
    ? "Creates a local intake. It never sends or approves outreach."
    : "Pair the local companion to continue.";
  updateIntakeState();
}

function openSettings() {
  elements.settingsPanel.open = true;
  window.setTimeout(() => elements.baseUrlInput.focus(), 20);
}

function toggleTokenVisibility() {
  const visible = elements.tokenInput.type === "text";
  elements.tokenInput.type = visible ? "password" : "text";
  elements.toggleTokenButton.textContent = visible ? "Show" : "Hide";
  elements.toggleTokenButton.setAttribute("aria-label", visible ? "Show pairing token" : "Hide pairing token");
}

async function pairCompanion() {
  setBusy(elements.pairButton, true, "Verifying…");
  setSettingsStatus("Requesting access to the selected loopback host…");
  try {
    const baseUrl = elements.baseUrlInput.value;
    const origin = permissionOriginFor(baseUrl);
    const granted = await chrome.permissions.request({ origins: [origin] });
    if (!granted) throw new Error("Chrome did not grant access to that local host.");

    const config = await callWorker({
      type: "PAIR_COMPANION",
      payload: {
        baseUrl,
        token: elements.tokenInput.value,
      },
    });
    elements.tokenInput.value = "";
    applyConfig(config);
    setSettingsStatus(
      `Verified ${config.health?.status || "healthy"} companion and protected dashboard.`,
      false,
    );
    elements.settingsPanel.open = false;
    showToast("Local companion paired. Private context remains on this device.");
  } catch (error) {
    setSettingsStatus(messageFor(error), true);
  } finally {
    setBusy(elements.pairButton, false, "Pair + verify");
  }
}

async function disconnectCompanion() {
  if (!window.confirm("Disconnect the local companion and remove its saved token?")) return;
  setBusy(elements.disconnectButton, true, "Disconnecting…");
  try {
    const config = await callWorker({ type: "DISCONNECT_COMPANION" });
    state.review = null;
    elements.reviewCard.hidden = true;
    elements.reviewLoader.hidden = false;
    applyConfig(config);
    setStep(1);
    setSettingsStatus("Disconnected. Saved token and loopback access were removed.");
    showToast("Local companion disconnected.");
  } catch (error) {
    setSettingsStatus(messageFor(error), true);
  } finally {
    setBusy(elements.disconnectButton, false, "Disconnect");
  }
}

async function capturePage() {
  setBusy(elements.captureButton, true, "Capturing selected context…");
  try {
    const page = await callWorker({ type: "CAPTURE_ACTIVE_PAGE" });
    state.page = page;
    renderPage(page);
    if (!elements.titleInput.value.trim()) elements.titleInput.value = page.title || "";
    updateIntakeState();
    showToast(
      page.selectedText
        ? "Selected text and page metadata captured."
        : "Page metadata captured. Select text first if you want it included.",
    );
  } catch (error) {
    showToast(messageFor(error), true);
  } finally {
    setBusy(elements.captureButton, false, null);
  }
}

function renderPage(page) {
  elements.sourceTitle.textContent = page.title || "Untitled page";
  try {
    elements.sourceHost.textContent = new URL(page.url).host;
  } catch {
    elements.sourceHost.textContent = "Captured page";
  }
  elements.sourceSelection.textContent = page.selectedText
    ? excerpt(page.selectedText, 700)
    : "";
  elements.sourceEmpty.hidden = Boolean(page.selectedText);
  elements.sourcePreview.hidden = false;
}

function clearPage() {
  const capturedTitle = state.page?.title || "";
  if (capturedTitle && elements.titleInput.value === capturedTitle) {
    elements.titleInput.value = "";
  }
  state.page = null;
  elements.sourcePreview.hidden = true;
  elements.sourceTitle.textContent = "";
  elements.sourceHost.textContent = "";
  elements.sourceSelection.textContent = "";
  updateIntakeState();
}

function updateIntakeState() {
  const pastedLength = elements.pasteInput.value.length;
  elements.pasteCounter.textContent = `${pastedLength.toLocaleString()} / 20,000`;
  const hasContext = Boolean(state.page || elements.pasteInput.value.trim());
  const titleReady = elements.kindInput.value !== "job" || Boolean(elements.titleInput.value.trim());
  elements.createIntakeButton.disabled = !state.config?.paired || !hasContext || !titleReady;
}

async function createIntake() {
  setBusy(elements.createIntakeButton, true, "Saving intake…");
  try {
    const result = await callWorker({
      type: "CREATE_INTAKE",
      payload: {
        page: state.page,
        kind: elements.kindInput.value,
        title: elements.titleInput.value,
        pastedText: elements.pasteInput.value,
        note: elements.noteInput.value,
      },
    });
    elements.intakeActionNote.textContent = `${capitalize(result.kind)} intake saved · ${shortId(
      result.intakeId,
    )}${result.jobId ? " · job record created" : ""}.`;
    clearPage();
    elements.pasteInput.value = "";
    elements.noteInput.value = "";
    elements.titleInput.value = "";
    updateIntakeState();
    showToast("Context saved to the local intake queue. Nothing was sent or approved.");
  } catch (error) {
    showToast(messageFor(error), true);
  } finally {
    setBusy(elements.createIntakeButton, false, null);
    updateIntakeState();
  }
}

async function loadNextReview() {
  setBusy(elements.loadDraftButton, true, "Loading…");
  try {
    const review = await callWorker({ type: "LOAD_NEXT_REVIEW" });
    state.review = review;
    renderReview(review);
    elements.reviewLoader.hidden = true;
    elements.reviewCard.hidden = false;
    setStep(2);
    elements.reviewCard.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
      block: "start",
    });
  } catch (error) {
    showToast(messageFor(error), true);
  } finally {
    setBusy(elements.loadDraftButton, false, "Load review →");
  }
}

function renderReview(review) {
  elements.reviewStatus.textContent = capitalize(review.state);
  elements.reviewStatus.className = "status-chip";
  elements.runReference.hidden = false;
  elements.openRunButton.textContent = shortId(review.outreachId);
  elements.recipientName.textContent = review.recipient.name;
  elements.recipientRole.textContent = review.recipient.role || "—";
  elements.recipientCompany.textContent = review.recipient.company || "—";
  elements.recipientChannel.textContent = review.recipient.channel || "—";
  elements.recipientDestination.textContent = review.recipient.destination;
  elements.recipientDestination.hidden = false;
  elements.draftSubject.textContent = review.draft.subject || "Message review";
  elements.draftBody.textContent = review.draft.body;
  elements.recipientConfirmed.checked = false;
  elements.draftReviewed.checked = false;
  elements.recipientConfirmed.disabled = false;
  elements.draftReviewed.disabled = false;
  elements.approveButton.disabled = true;
  elements.approveButton.className = "approve-button";
  elements.approveButton.textContent = "Approve for the reviewed queue";
}

function updateApprovalState() {
  const ready =
    Boolean(state.review) &&
    elements.recipientConfirmed.checked &&
    elements.draftReviewed.checked;
  elements.approveButton.disabled = !ready;
}

async function approveReview() {
  if (!state.review) return;
  setBusy(elements.approveButton, true, "Recording explicit approval…");
  try {
    const result = await callWorker({
      type: "APPROVE_OUTREACH",
      payload: {
        outreachId: state.review.outreachId,
        contactId: state.review.contactId,
        updatedAt: state.review.updatedAt,
        draftBody: state.review.draft.body,
        recipientName: state.review.recipient.name,
        recipientDestination: state.review.recipient.destination,
        recipientConfirmed: elements.recipientConfirmed.checked,
        draftReviewed: elements.draftReviewed.checked,
      },
    });
    elements.reviewStatus.textContent = capitalize(result.status);
    elements.reviewStatus.className = "status-chip approved";
    elements.recipientConfirmed.disabled = true;
    elements.draftReviewed.disabled = true;
    elements.approveButton.disabled = true;
    elements.approveButton.className = "approve-button approved";
    elements.approveButton.textContent = "Approved · no message sent";
    elements.reviewLoader.hidden = false;
    setStep(3, true);
    showToast("Review approved in the local engine. No channel action was performed.");
  } catch (error) {
    showToast(messageFor(error), true);
    elements.approveButton.disabled = false;
  } finally {
    elements.approveButton.classList.remove("busy");
    elements.approveButton.setAttribute("aria-busy", "false");
  }
}

async function openAppPath(path) {
  try {
    await callWorker({ type: "OPEN_APP_PATH", payload: { path } });
  } catch (error) {
    showToast(messageFor(error), true);
  }
}

function setStep(active, completed = false) {
  for (const [index, item] of elements.stepItems.entries()) {
    const step = index + 1;
    item.classList.toggle("active", step === active);
    item.classList.toggle("complete", step < active || (completed && step === active));
  }
}

function setBusy(button, busy, busyLabel) {
  const textOnlyButton = button.childElementCount === 0;
  if (textOnlyButton && !button.dataset.restingLabel) {
    button.dataset.restingLabel = button.textContent.trim();
  }
  button.classList.toggle("busy", busy);
  button.setAttribute("aria-busy", String(busy));
  button.disabled = busy;
  if (textOnlyButton && busy && busyLabel) button.textContent = busyLabel;
  if (textOnlyButton && !busy) button.textContent = busyLabel || button.dataset.restingLabel;
}

function setSettingsStatus(message, error = false) {
  elements.settingsStatus.textContent = message;
  elements.settingsStatus.classList.toggle("error", error);
}

function showToast(message, error = false) {
  window.clearTimeout(state.toastTimer);
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", error);
  elements.toast.hidden = false;
  state.toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, error ? 6_500 : 4_000);
}

function callWorker(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      if (!response?.ok) {
        reject(new Error(response?.error || "The local companion action failed."));
        return;
      }
      resolve(response.data);
    });
  });
}

function excerpt(value, limit) {
  const text = String(value ?? "").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function shortId(value) {
  const text = String(value ?? "");
  return text.length > 18 ? `${text.slice(0, 10)}…${text.slice(-5)}` : text;
}

function capitalize(value) {
  const text = String(value ?? "");
  return text ? `${text[0].toUpperCase()}${text.slice(1)}` : "";
}

function messageFor(error) {
  return error instanceof Error && error.message
    ? error.message
    : "The companion could not complete that action.";
}
