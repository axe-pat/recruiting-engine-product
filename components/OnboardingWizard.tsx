"use client";

/* eslint-disable @next/next/no-html-link-for-pages -- static export uses full-page route navigation */

import { useMemo, useState } from "react";

import {
  companionHeaders,
  companionUrl,
  defaultCompanionConfig,
  type CompanionConfig,
} from "@/lib/app-contract";

const sessionConfigKey = "recruiting-engine.companion-session.v1";
const originStorageKey = "recruiting-engine.companion-origin.v1";

type FormState = {
  name: string;
  headline: string;
  roles: string;
  locations: string;
  workAuthorization: string;
  story: string;
  mode: "portable" | "existing";
};

const initialForm: FormState = {
  name: "",
  headline: "",
  roles: "Product Management, Product Operations",
  locations: "",
  workAuthorization: "",
  story: "",
  mode: "portable",
};

export function OnboardingWizard() {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState(initialForm);
  const [config, setConfig] = useState<CompanionConfig>(defaultCompanionConfig);
  const [resume, setResume] = useState<File | null>(null);
  const [profileExport, setProfileExport] = useState<File | null>(null);
  const [supporting, setSupporting] = useState<File[]>([]);
  const [status, setStatus] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const roles = useMemo(() => form.roles.split(",").map((role) => role.trim()).filter(Boolean), [form.roles]);
  const locations = useMemo(() => form.locations.split(",").map((location) => location.trim()).filter(Boolean), [form.locations]);

  const next = () => setStep((value) => Math.min(4, value + 1));
  const back = () => setStep((value) => Math.max(1, value - 1));

  const upload = async (file: File, kind: string, activeConfig: CompanionConfig) => {
    const payload = new FormData();
    payload.set("file", file);
    payload.set("kind", kind);
    const response = await fetch(companionUrl(activeConfig.baseUrl, "/api/v1/documents"), {
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      redirect: "error",
      referrerPolicy: "no-referrer",
      headers: companionHeaders(activeConfig.token),
      body: payload,
    });
    if (!response.ok) throw new Error(`Could not store ${file.name}`);
  };

  const finish = async () => {
    if (!resume) {
      setStatus("Add one baseline resume to create the workspace.");
      setStep(2);
      return;
    }
    if (!config.token.trim()) {
      setStatus("Start the local companion and paste its pairing token.");
      return;
    }
    setSubmitting(true);
    setStatus("Pairing and creating your private workspace…");
    let pairedConfig = config;
    try {
      if (config.token.startsWith("re_local_")) {
        throw new Error("Use a one-time pairing token here. Keep the long-lived local token in the Chrome companion.");
      }
      if (config.token.startsWith("re_pair_")) {
        const pairResponse = await fetch(companionUrl(config.baseUrl, "/api/v1/pair"), {
          method: "POST",
          cache: "no-store",
          credentials: "omit",
          redirect: "error",
          referrerPolicy: "no-referrer",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pairing_token: config.token, client_type: "web" }),
        });
        if (!pairResponse.ok) throw new Error("The one-time pairing token was rejected or already used.");
        const pairPayload = (await pairResponse.json()) as { bearer_token?: string };
        if (!pairPayload.bearer_token) throw new Error("The companion did not return a device token.");
        pairedConfig = { ...config, token: pairPayload.bearer_token };
        setConfig({ baseUrl: pairedConfig.baseUrl, token: "" });
        window.localStorage.setItem(originStorageKey, pairedConfig.baseUrl);
        window.sessionStorage.setItem(sessionConfigKey, JSON.stringify(pairedConfig));
      }
      if (!pairedConfig.token.startsWith("re_web_")) {
        throw new Error("Enter the one-time pairing token printed by the local companion.");
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Pairing failed.");
      setSubmitting(false);
      return;
    }
    window.localStorage.setItem(originStorageKey, pairedConfig.baseUrl);
    window.sessionStorage.setItem(sessionConfigKey, JSON.stringify(pairedConfig));
    const headers = {
      ...companionHeaders(pairedConfig.token),
      "Content-Type": "application/json",
    };
    try {
      const health = await fetch(companionUrl(pairedConfig.baseUrl, "/api/v1/health"), { headers, cache: "no-store", credentials: "omit", redirect: "error", referrerPolicy: "no-referrer" });
      if (!health.ok) throw new Error("The companion rejected this pairing token.");
      const profile = await fetch(companionUrl(pairedConfig.baseUrl, "/api/v1/profile"), {
        method: "PUT",
        headers,
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        referrerPolicy: "no-referrer",
        body: JSON.stringify({
          profile: {
            display_label: form.name,
            headline: form.headline,
            summary: form.story,
            target_roles: roles,
            mode: form.mode,
          },
        }),
      });
      if (!profile.ok) throw new Error("The profile could not be saved.");
      const preferences = await fetch(companionUrl(pairedConfig.baseUrl, "/api/v1/preferences"), {
        method: "PUT",
        headers,
        cache: "no-store",
        credentials: "omit",
        redirect: "error",
        referrerPolicy: "no-referrer",
        body: JSON.stringify({ preferences: { target_roles: roles, locations, work_authorization: form.workAuthorization, mode: form.mode } }),
      });
      if (!preferences.ok) throw new Error("Target preferences could not be saved.");
      await upload(resume, "baseline_resume", pairedConfig);
      if (profileExport) await upload(profileExport, "profile_export", pairedConfig);
      for (const file of supporting) await upload(file, "supporting_material", pairedConfig);
      window.localStorage.setItem(originStorageKey, pairedConfig.baseUrl);
      window.sessionStorage.setItem(sessionConfigKey, JSON.stringify(pairedConfig));
      setStatus("Workspace created. Opening your command center…");
      window.location.assign("/app");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Onboarding could not finish.");
      setSubmitting(false);
    }
  };

  return (
    <main className="onboarding-page">
      <header className="onboarding-nav">
        <a className="app-brand" href="/"><span>RE</span><div><strong>Recruiting Engine</strong><small>Private workspace setup</small></div></a>
        <a href="/app">Explore preview →</a>
      </header>

      <div className="onboarding-layout">
        <aside className="onboarding-aside">
          <span className="app-kicker">First-run setup</span>
          <h1>Give the engine a strong starting point—not your entire life story.</h1>
          <p>One baseline resume, a few target roles, and the context only you know. The system can grow with every reviewed run.</p>
          <ol>
            {["Your direction", "Core evidence", "Search boundaries", "Private pairing"].map((label, index) => (
              <li className={step === index + 1 ? "active" : step > index + 1 ? "complete" : ""} key={label}>
                <span>{step > index + 1 ? "✓" : String(index + 1).padStart(2, "0")}</span>{label}
              </li>
            ))}
          </ol>
          <div className="privacy-promise"><strong>Your files are not uploaded to the hosting server.</strong><span>The browser sends them directly to the companion running on your device.</span></div>
        </aside>

        <section className="onboarding-card">
          <div className="onboarding-progress" role="progressbar" aria-label="Onboarding progress" aria-valuemin={1} aria-valuemax={4} aria-valuenow={step}><i style={{ width: `${step * 25}%` }} /></div>
          {step === 1 ? (
            <div className="wizard-step">
              <span>01 · Direction</span><h2>What should the engine optimize for?</h2><p>This becomes the first targeting frame. You can create more role surfaces later.</p>
              <div className="field-grid">
                <label>Preferred name<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="What should we call you?" /></label>
                <label>Current headline<input value={form.headline} onChange={(event) => setForm({ ...form, headline: event.target.value })} placeholder="MBA candidate · Product builder" /></label>
              </div>
              <label>Target roles<input value={form.roles} onChange={(event) => setForm({ ...form, roles: event.target.value })} /><small>Comma-separated · start with 3–5 adjacent roles.</small></label>
              <label>A short product or career through-line<textarea value={form.story} onChange={(event) => setForm({ ...form, story: event.target.value })} placeholder="What connects the work you have done to the work you want next?" /></label>
            </div>
          ) : null}
          {step === 2 ? (
            <div className="wizard-step">
              <span>02 · Evidence</span><h2>Start with one truthful base.</h2><p>The engine extracts evidence and proposes gaps. It does not need a hundred historical resumes.</p>
              <UploadField label="Baseline resume" note="Required · PDF, DOCX, or TXT" file={resume} onChange={(files) => setResume(files[0] ?? null)} />
              <UploadField label="Profile export" note="Optional · your own PDF or data export" file={profileExport} onChange={(files) => setProfileExport(files[0] ?? null)} />
              <UploadField label="Supporting evidence" note="Optional · portfolio, story bank, or prior framing" files={supporting} multiple onChange={setSupporting} />
            </div>
          ) : null}
          {step === 3 ? (
            <div className="wizard-step">
              <span>03 · Boundaries</span><h2>Define what a good opportunity means.</h2><p>Hard boundaries stop bad automation earlier than another ranking prompt can.</p>
              <label>Locations<input value={form.locations} onChange={(event) => setForm({ ...form, locations: event.target.value })} placeholder="Los Angeles, San Francisco, Remote" /></label>
              <label>Work authorization context<input value={form.workAuthorization} onChange={(event) => setForm({ ...form, workAuthorization: event.target.value })} placeholder="Only what is relevant to eligibility checks" /></label>
              <div className="mode-picker">
                <button className={form.mode === "portable" ? "active" : ""} type="button" aria-pressed={form.mode === "portable"} onClick={() => setForm({ ...form, mode: "portable" })}><span>New user</span><strong>Portable workspace</strong><small>Start from uploads and reviewed imports.</small></button>
                <button className={form.mode === "existing" ? "active" : ""} type="button" aria-pressed={form.mode === "existing"} onClick={() => setForm({ ...form, mode: "existing" })}><span>Operator</span><strong>Existing engine</strong><small>Bind to installed ResumeGenerator + Outreach.</small></button>
              </div>
            </div>
          ) : null}
          {step === 4 ? (
            <div className="wizard-step">
              <span>04 · Pair</span><h2>Keep the system of record on your device.</h2><p>Run the companion, then paste the one-time device token it prints. File bytes go from this browser directly to loopback—not to the hosting server.</p>
              <label>Companion address<input type="url" value={config.baseUrl} onChange={(event) => setConfig({ ...config, baseUrl: event.target.value })} /></label>
              <label>Pairing token<input type="password" value={config.token} onChange={(event) => setConfig({ ...config, token: event.target.value })} placeholder="Device token" /></label>
              <div className="review-summary"><div><span>Roles</span><strong>{roles.length || 0}</strong></div><div><span>Locations</span><strong>{locations.length || 0}</strong></div><div><span>Files</span><strong>{Number(Boolean(resume)) + Number(Boolean(profileExport)) + supporting.length}</strong></div><div><span>Mode</span><strong>{form.mode}</strong></div></div>
              <a className="text-link" href="/install">Companion install guide →</a>
            </div>
          ) : null}

          {status ? <p className="wizard-status" role="status">{status}</p> : null}
          <footer className="wizard-actions">
            <button type="button" onClick={back} disabled={step === 1}>Back</button>
            {step < 4 ? <button className="run-button" type="button" onClick={next}>Continue <b>→</b></button> : <button className="run-button" type="button" onClick={finish} disabled={submitting}>{submitting ? "Creating…" : "Create workspace"} <b>→</b></button>}
          </footer>
        </section>
      </div>
    </main>
  );
}

function UploadField({ label, note, file, files, multiple = false, onChange }: { label: string; note: string; file?: File | null; files?: File[]; multiple?: boolean; onChange: (files: File[]) => void }) {
  const names = file ? file.name : files?.map((item) => item.name).join(", ");
  return (
    <label className="upload-field">
      <input type="file" accept=".pdf,.doc,.docx,.txt,.md" multiple={multiple} onChange={(event) => onChange(Array.from(event.target.files ?? []))} />
      <span>＋</span><div><strong>{names || label}</strong><small>{names ? "Ready for local upload" : note}</small></div><b>{names ? "Replace" : "Choose"}</b>
    </label>
  );
}
