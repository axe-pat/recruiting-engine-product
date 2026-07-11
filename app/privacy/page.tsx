import type { Metadata } from "next";

import { SiteFooter } from "@/components/SiteFooter";
import { SiteNav } from "@/components/SiteNav";

export const metadata: Metadata = {
  title: "Privacy",
  description: "How the Recruiting Engine website, local companion, and Chrome extension handle data.",
};

const sections = [
  {
    title: "Hosted website",
    body: [
      "The public website is a static product surface. It has no account system, analytics SDK, advertising tracker, cookie session, or hosted database.",
      "Its preview workspace contains fictional records and reviewed non-identifying aggregates. It does not fetch private operating repositories or personal artifacts.",
    ],
  },
  {
    title: "Local companion",
    body: [
      "When a user pairs the app, documents and operational records travel directly from the browser to a loopback service on that user’s device. They are stored in the companion’s private local data directory, not by this website.",
      "The hosted app persists only the loopback address. It exchanges a one-time pairing code for a 30-minute web token kept in tab-scoped session storage. The Chrome companion has separate extension-local storage for durable pairing.",
    ],
  },
  {
    title: "Chrome companion",
    body: [
      "The extension stores its loopback connection settings on the device. It can read a page title, page address, and user-selected text only after an explicit user action and only for the visible product feature the user invoked.",
      "It does not collect browsing history, scrape professional-network pages, automate clicks, send connection requests, or send messages. Draft review requires the user to confirm both recipient and content.",
    ],
  },
  {
    title: "Connected engines and providers",
    body: [
      "Portable mode starts with local imports and deterministic decision reports. Existing-engine mode can surface an operator’s separately installed Recruiting Engine and its configured providers through a narrow local adapter.",
      "The hosted website does not receive provider credentials. Any provider processing is governed by the installed engine’s own configuration and the user’s direct relationship with that provider.",
    ],
  },
  {
    title: "Retention and deletion",
    body: [
      "Preview data ships with the public application. Private data remains in the local companion until the user deletes records or removes its local data directory.",
      "Removing the extension deletes its extension-local settings. A user can revoke a device token from the companion and clear the website’s device-local connection configuration at any time.",
    ],
  },
  {
    title: "Use and disclosure",
    body: [
      "Recruiting Engine does not sell user data, use it for advertising, transfer it to data brokers, or use it for credit, lending, or unrelated profiling.",
      "Data is used only to provide the user-facing recruiting workflow the user requested. It is not transferred to humans except when the user deliberately acts through an external channel.",
    ],
  },
] as const;

export default function PrivacyPage() {
  return (
    <main className="inner-page">
      <SiteNav />
      <header className="policy-hero">
        <span className="eyebrow">Privacy · Effective July 11, 2026</span>
        <h1>Your search is personal. The product architecture treats it that way.</h1>
        <p>
          Recruiting Engine separates a static public interface from a private local system of
          record. This policy covers the website, local companion, and Chrome companion.
        </p>
      </header>

      <section className="policy-layout">
        <aside>
          <strong>Plain-language promise</strong>
          <p>Your resumes, people, drafts, applications, and credentials do not become a public-site database.</p>
          <a href="/app/onboarding">See private onboarding →</a>
        </aside>
        <div className="policy-sections">
          {sections.map((section, index) => (
            <article key={section.title}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div>
                <h2>{section.title}</h2>
                {section.body.map((paragraph) => <p key={paragraph}>{paragraph}</p>)}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="policy-contact section-shell">
        <span className="section-index">Questions or deletion help</span>
        <h2>Open a private security or privacy report through the project repository.</h2>
        <a href="https://github.com/axe-pat/recruiting-engine-product/security" target="_blank" rel="noreferrer">
          Project security contact ↗
        </a>
      </section>
      <SiteFooter />
    </main>
  );
}
